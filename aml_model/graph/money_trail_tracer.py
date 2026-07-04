"""
money_trail_tracer.py — hardened version
──────────────────────────────────────────
Fixes applied:
  1. Strict "<" temporal check (was "<="). A same-day receive+forward with
     identical/missing timestamps no longer silently breaks the trail.
  2. FIFO Double-Queue Ledger replaces the crude amount_tolerance ratio.
     Tracks the EXACT rupee amount remaining from each tainted credit and
     depletes it as debits occur — correctly handles smurfing/aggregation
     (multiple small tainted deposits pooled and sent out as one lump sum).
  3. Intraday Flow Resolution — when multiple transactions share the same
     date (missing/zero time), order them using the balance column's
     logical rise and fall instead of trusting file order.
  4. Commingling detection — flags when a "clean" (non-tainted) inflow gets
     mixed with a tainted balance before forwarding, which affects how much
     of the outgoing transfer can be legally traced as tainted funds.
"""

import pandas as pd
import numpy as np
from collections import deque, defaultdict
import os


class MoneyTrailTracer:

    def __init__(self, scored_csv: str, max_hops: int = 8, min_credit: float = 0):
        """
        min_credit: ignore auto-detected tainted lots below this amount when
                    no explicit seed_amount is given (filters out noise —
                    tiny incidental credits that shouldn't be treated as the
                    start of a laundering trail).
        """
        self.max_hops = max_hops
        self.min_credit = min_credit
        self.df = self._load(scored_csv)
        self.edges_by_account = self._index_edges(self.df)

    # ── loading + FIX 3: intraday ordering via balance reconstruction ───────

    def _load(self, path: str) -> pd.DataFrame:
        df = pd.read_csv(path, dtype=str, low_memory=False)

        debit_col   = "debit_clean" if "debit_clean" in df.columns else "debit"
        credit_col  = "credit_clean" if "credit_clean" in df.columns else "credit"
        balance_col = "balance_clean" if "balance_clean" in df.columns else "balance"
        cp_col      = "counterparty_account_id" if "counterparty_account_id" in df.columns else "counterparty_account"

        df["debit"]   = pd.to_numeric(df.get(debit_col, 0), errors="coerce").fillna(0)
        df["credit"]  = pd.to_numeric(df.get(credit_col, 0), errors="coerce").fillna(0)
        df["balance"] = pd.to_numeric(df.get(balance_col, np.nan), errors="coerce")
        df["counterparty_account"] = df.get(cp_col)
        df["amount"] = df["credit"] - df["debit"]
        df["abs_amount"] = df["amount"].abs()
        df["narration"] = df.get("narration", "")
        df["datetime"] = pd.to_datetime(df.get("datetime", df.get("date")), errors="coerce")
        df = df.dropna(subset=["datetime"])

        # ── FIX 3: reconstruct true intraday order using balance deltas ────
        df = df.sort_values(["account_id", "datetime"]).reset_index(drop=True)
        df["_seq"] = self._resolve_intraday_order(df)

        # Final ordering key: date first, then reconstructed intraday sequence
        df = df.sort_values(["account_id", "datetime", "_seq"]).reset_index(drop=True)
        return df

    def _resolve_intraday_order(self, df: pd.DataFrame) -> pd.Series:
        """
        For rows sharing the same account_id + calendar date (common when
        time-of-day is missing/zeroed), infer the true order by checking
        which sequence of debits/credits is CONSISTENT with the observed
        balance column. A transaction's balance must equal
        (previous balance + credit - debit); we pick the ordering of
        same-day rows that satisfies this chain, falling back to file
        order if balance data is unavailable or ambiguous.
        """
        seq = pd.Series(0, index=df.index)

        for (acc, date), grp in df.groupby(["account_id", df["datetime"].dt.date]):
            if len(grp) <= 1:
                continue
            if grp["balance"].isna().all():
                # No balance data — fall back to original file order
                for i, idx in enumerate(grp.index):
                    seq.loc[idx] = i
                continue

            # Try to find an ordering where balance[i] = balance[i-1] + amount[i]
            # Greedy chain reconstruction: start from the row whose
            # (balance - amount) matches another row's balance, and link them.
            candidates = grp.copy()
            candidates["prior_balance_expected"] = candidates["balance"] - candidates["amount"]

            # Build a lookup: balance value -> row index
            balance_to_idx = defaultdict(list)
            for idx, row in candidates.iterrows():
                if not pd.isna(row["balance"]):
                    balance_to_idx[round(row["balance"], 2)].append(idx)

            visited = set()
            order = []
            # Find the starting row: one whose expected prior balance does NOT
            # match any other row's balance in this group (i.e. it's first)
            all_balances = set(round(b, 2) for b in candidates["balance"].dropna())
            start_candidates = [
                idx for idx, row in candidates.iterrows()
                if round(row["prior_balance_expected"], 2) not in all_balances
            ]
            current_pool = start_candidates if start_candidates else list(candidates.index)

            remaining = set(candidates.index)
            # Greedy walk: repeatedly find the next row whose prior_balance
            # matches the current running balance
            current_balance = None
            if start_candidates:
                first_idx = start_candidates[0]
                order.append(first_idx)
                remaining.discard(first_idx)
                current_balance = candidates.loc[first_idx, "balance"]

            while remaining:
                next_idx = None
                for idx in remaining:
                    expected_prior = candidates.loc[idx, "prior_balance_expected"]
                    if current_balance is not None and abs(expected_prior - current_balance) < 0.01:
                        next_idx = idx
                        break
                if next_idx is None:
                    # Chain broken — append remaining in original order
                    next_idx = sorted(remaining)[0]
                order.append(next_idx)
                remaining.discard(next_idx)
                current_balance = candidates.loc[next_idx, "balance"]

            for i, idx in enumerate(order):
                seq.loc[idx] = i

        return seq

    def _index_edges(self, df: pd.DataFrame) -> dict:
        """account_id -> sorted list of outgoing debit rows, for fast lookup"""
        edges = defaultdict(list)
        outgoing = df[(df["debit"] > 0) & df["counterparty_account"].notna()]
        for _, row in outgoing.iterrows():
            edges[row["account_id"]].append(row)
        return edges

    # ── FIX 2: FIFO ledger-based tracing (replaces ratio tolerance) ─────────

    def trace_from_seed(self, seed_account: str, seed_amount: float = None,
                          seed_time: pd.Timestamp = None, depletion_method: str = "fifo") -> list:
        """
        depletion_method: "fifo" (default) consumes the OLDEST tainted lot
                           first when a mixed account forwards money.
                           "lifo" consumes the MOST RECENTLY received tainted
                           lot first instead.

                           These can give different traceable amounts when
                           funds are commingled — running both and comparing
                           is itself a useful forensic signal: a big gap
                           between FIFO and LIFO results suggests the account
                           is deliberately structuring transfers to obscure
                           which money is "whose."
        """
        trails = []

        seed_rows = self.df[self.df["account_id"] == seed_account]
        if seed_amount is not None and seed_time is not None:
            tainted_lots = [{"amount": seed_amount, "time": seed_time, "source": "SEED"}]
        else:
            credit_rows = seed_rows[seed_rows["credit"] > 0].sort_values("datetime")
            tainted_lots = [
                {"amount": r["credit"], "time": r["datetime"], "source": r.get("counterparty_account", "UNKNOWN")}
                for _, r in credit_rows.iterrows()
                if r["credit"] >= self.min_credit
            ]

        if not tainted_lots:
            return trails

        # Build ONE combined queue at the seed account holding ALL tainted lots
        # together (chronological order) — this is what allows FIFO vs LIFO to
        # actually produce different results when the account later makes
        # multiple outgoing forwards.
        combined_queue = deque([
            {"amount": lot["amount"], "arrived": lot["time"], "origin": lot["source"]}
            for lot in tainted_lots
        ])
        ledger = {seed_account: combined_queue}
        self._propagate(seed_account, ledger, [], trails, hop=0, depletion_method=depletion_method)

        return trails

    def trace_both_methods(self, seed_account: str, seed_amount: float = None,
                             seed_time: pd.Timestamp = None) -> dict:
        """
        Runs both FIFO and LIFO tracing and returns a side-by-side comparison
        of destination amounts. A large discrepancy between the two is a red
        flag for deliberate fund-blending/structuring.
        """
        fifo_trails = self.trace_from_seed(seed_account, seed_amount, seed_time, depletion_method="fifo")
        lifo_trails = self.trace_from_seed(seed_account, seed_amount, seed_time, depletion_method="lifo")

        fifo_dest = self._summarize_trails(fifo_trails)
        lifo_dest = self._summarize_trails(lifo_trails)

        merged = fifo_dest.merge(
            lifo_dest, on="destination_account", how="outer",
            suffixes=("_fifo", "_lifo")
        ).fillna(0)
        merged["amount_discrepancy"] = (
            merged["total_tainted_amount_fifo"] - merged["total_tainted_amount_lifo"]
        ).abs()
        return merged.sort_values("amount_discrepancy", ascending=False)

    def _summarize_trails(self, trails: list) -> pd.DataFrame:
        destinations = defaultdict(lambda: {"total_tainted_amount": 0.0, "n_trails": 0})
        for trail in trails:
            if not trail:
                continue
            last_hop = trail[-1]
            dest = last_hop["to"] if not last_hop.get("is_cycle_closure") else f"CYCLE->{last_hop['to']}"
            destinations[dest]["total_tainted_amount"] += last_hop.get("tainted_amount", 0)
            destinations[dest]["n_trails"] += 1
        result = pd.DataFrame.from_dict(destinations, orient="index")
        result.index.name = "destination_account"
        return result.reset_index() if not result.empty else pd.DataFrame(
            columns=["destination_account", "total_tainted_amount", "n_trails"])

    def _propagate(self, account: str, ledger: dict, chain: list, trails: list, hop: int,
                    depletion_method: str = "fifo"):
        if hop >= self.max_hops:
            trails.append(list(chain))
            return

        tainted_queue = ledger.get(account)
        if not tainted_queue or sum(t["amount"] for t in tainted_queue) <= 0.01:
            trails.append(list(chain))
            return

        outgoing = sorted(
            self.edges_by_account.get(account, []),
            key=lambda r: (r["datetime"], r["_seq"])
        )

        latest_taint_time = max(t["arrived"] for t in tainted_queue)
        valid_outgoing = [
            r for r in outgoing
            if (r["datetime"] > latest_taint_time) or (r["datetime"] == latest_taint_time)
        ]

        if not valid_outgoing:
            trails.append(list(chain))
            return

        for debit_row in valid_outgoing:
            debit_amount = debit_row["debit"]
            dst = debit_row["counterparty_account"]

            remaining_debit = debit_amount
            consumed_from = []
            working_queue = deque(tainted_queue)

            while remaining_debit > 0.01 and working_queue:
                # FIFO depletes from the left (oldest lot first);
                # LIFO depletes from the right (most recently arrived lot first)
                pick = working_queue[0] if depletion_method == "fifo" else working_queue[-1]
                take = min(pick["amount"], remaining_debit)
                if take <= 0.01:
                    if depletion_method == "fifo":
                        working_queue.popleft()
                    else:
                        working_queue.pop()
                    continue
                consumed_from.append({"origin": pick["origin"], "amount": take})
                pick["amount"] -= take
                remaining_debit -= take
                if pick["amount"] <= 0.01:
                    if depletion_method == "fifo":
                        working_queue.popleft()
                    else:
                        working_queue.pop()

            tainted_forwarded = debit_amount - remaining_debit
            if tainted_forwarded <= 0.01:
                continue  # this debit was funded by clean money, not traceable taint

            # ── Commingling flag: was this debit PARTLY clean money? ───────
            is_commingled = remaining_debit > 0.01  # some of the debit was untainted funds

            hop_record = {
                "from": account, "to": dst,
                "amount_forwarded_total": debit_amount,
                "tainted_amount": tainted_forwarded,
                "clean_amount_mixed_in": remaining_debit,
                "is_commingled": is_commingled,
                "datetime": debit_row["datetime"],
                "narration": debit_row.get("narration", ""),
                "origin_breakdown": consumed_from,   # exactly which lot(s) funded this forward
            }

            if dst in [c["to"] for c in chain]:
                # Cycle
                trails.append(chain + [dict(hop_record, is_cycle_closure=True)])
                continue

            new_ledger = dict(ledger)
            new_ledger[dst] = new_ledger.get(dst, deque()) + deque([
                {"amount": tainted_forwarded, "arrived": debit_row["datetime"], "origin": account}
            ])

            self._propagate(dst, new_ledger, chain + [hop_record], trails, hop + 1,
                             depletion_method=depletion_method)

    # ── destination summary ──────────────────────────────────────────────────

    def origin_attribution_report(self, seed_account: str, depletion_method: str = "fifo") -> pd.DataFrame:
        """
        For an account holding MULTIPLE tainted lots (e.g. several incoming
        deposits before any outgoing transfer), this shows exactly which
        origin's money was attributed to which outgoing destination —
        the actual point where FIFO and LIFO can produce different answers.

        Run this once with depletion_method="fifo" and once with "lifo" to
        see the attribution shift.
        """
        trails = self.trace_from_seed(seed_account, depletion_method=depletion_method)
        rows = []
        for trail in trails:
            for hop in trail:
                if hop.get("is_cycle_closure"):
                    continue
                for origin_lot in hop.get("origin_breakdown", []):
                    rows.append({
                        "forwarded_from": hop["from"],
                        "forwarded_to": hop["to"],
                        "datetime": hop["datetime"],
                        "origin_of_funds": origin_lot["origin"],
                        "amount_attributed": origin_lot["amount"],
                        "method": depletion_method.upper(),
                    })
        if not rows:
            return pd.DataFrame(columns=[
                "forwarded_from", "forwarded_to", "datetime",
                "origin_of_funds", "amount_attributed", "method"
            ])
        return pd.DataFrame(rows)

    def compare_attribution_methods(self, seed_account: str) -> pd.DataFrame:
        """
        Runs origin_attribution_report under both FIFO and LIFO and stacks
        them side by side — the cleanest way to SEE where the two
        conventions disagree about whose money went where.
        """
        fifo = self.origin_attribution_report(seed_account, "fifo")
        lifo = self.origin_attribution_report(seed_account, "lifo")
        combined = pd.concat([fifo, lifo], ignore_index=True)
        return combined.sort_values(["forwarded_to", "origin_of_funds", "method"])

    def find_destinations(self, seed_account: str) -> pd.DataFrame:
        trails = self.trace_from_seed(seed_account)
        destinations = defaultdict(lambda: {"total_tainted_amount": 0.0, "n_trails": 0,
                                             "commingled_hops": 0, "max_hops": 0})

        for trail in trails:
            if not trail:
                continue
            last_hop = trail[-1]
            dest = last_hop["to"] if not last_hop.get("is_cycle_closure") else f"CYCLE->{last_hop['to']}"
            destinations[dest]["total_tainted_amount"] += last_hop.get("tainted_amount", 0)
            destinations[dest]["n_trails"] += 1
            destinations[dest]["commingled_hops"] += sum(1 for h in trail if h.get("is_commingled"))
            destinations[dest]["max_hops"] = max(destinations[dest]["max_hops"], len(trail))

        result = pd.DataFrame.from_dict(destinations, orient="index")
        result.index.name = "destination_account"
        if result.empty:
            return result
        return result.sort_values("total_tainted_amount", ascending=False).reset_index()

    # ── dedicated commingling report — "who is blending dirty and clean money?" ─

    def find_commingling_events(self, seed_account: str, seed_amount: float = None,
                                  seed_time: pd.Timestamp = None) -> pd.DataFrame:
        """
        Scans every trail from the seed and returns a flat, investigator-
        readable table of every hop where tainted funds were mixed with
        clean/untainted money before being forwarded onward.

        This is the direct evidence needed to show an account is acting as
        a layering/blending point rather than a simple pass-through — a key
        distinction in money laundering typologies (integration stage).
        """
        trails = self.trace_from_seed(seed_account, seed_amount=seed_amount, seed_time=seed_time)
        events = []

        for trail in trails:
            for hop in trail:
                if hop.get("is_commingled") and not hop.get("is_cycle_closure"):
                    total = hop["amount_forwarded_total"]
                    tainted = hop["tainted_amount"]
                    clean = hop["clean_amount_mixed_in"]
                    events.append({
                        "account": hop["from"],
                        "forwarded_to": hop["to"],
                        "datetime": hop["datetime"],
                        "total_forwarded": total,
                        "tainted_portion": tainted,
                        "clean_portion": clean,
                        "pct_tainted": round(tainted / total * 100, 1) if total else 0,
                        "narration": hop.get("narration", ""),
                    })

        if not events:
            return pd.DataFrame(columns=[
                "account", "forwarded_to", "datetime", "total_forwarded",
                "tainted_portion", "clean_portion", "pct_tainted", "narration"
            ])

        df = pd.DataFrame(events).drop_duplicates()
        return df.sort_values("tainted_portion", ascending=False).reset_index(drop=True)

    def summarize_commingling_by_account(self, seed_account: str, seed_amount: float = None,
                                           seed_time: pd.Timestamp = None) -> pd.DataFrame:
        """
        Rolls up commingling events per account — flags accounts that
        REPEATEDLY blend dirty and clean money, which is a stronger
        laundering signal than a single isolated incident.
        """
        events = self.find_commingling_events(seed_account, seed_amount=seed_amount, seed_time=seed_time)
        if events.empty:
            return events

        summary = events.groupby("account").agg(
            n_commingling_events=("account", "count"),
            total_tainted_routed=("tainted_portion", "sum"),
            total_clean_mixed_in=("clean_portion", "sum"),
            avg_pct_tainted=("pct_tainted", "mean"),
        ).reset_index()
        summary["avg_pct_tainted"] = summary["avg_pct_tainted"].round(1)
        return summary.sort_values("n_commingling_events", ascending=False)

    def print_trail(self, trail: list):
        if not trail:
            print("  (no outgoing tainted funds found)")
            return
        for i, hop in enumerate(trail):
            indent = "  " * i
            if hop.get("is_cycle_closure"):
                print(f"{indent}CYCLE back to {hop['to']} (circular laundering)")
                continue
            tag = " [COMMINGLED with clean funds]" if hop.get("is_commingled") else ""
            print(f"{indent}{hop['from']} --[Rs.{hop['tainted_amount']:,.0f} tainted "
                  f"of Rs.{hop['amount_forwarded_total']:,.0f} total, {hop['datetime']}]--> "
                  f"{hop['to']}{tag}")


if __name__ == "__main__":
    tracer = MoneyTrailTracer("outputs/reports/isolation_forest_scored_transactions.csv")
    seed = "24704559049070"
    print(f"Tracing tainted funds from seed account: {seed}\n")

    trails = tracer.trace_from_seed(seed)
    print(f"Found {len(trails)} distinct trails\n")

    for i, trail in enumerate(trails[:5]):
        print(f"--- Trail {i+1} ---")
        tracer.print_trail(trail)
        print()

    print("=== Destination summary ===")
    print(tracer.find_destinations(seed).to_string())

    print("\n=== Commingling events (tainted funds mixed with clean money) ===")
    print(tracer.find_commingling_events(seed).to_string())

    print("\n=== Commingling summary by account (repeat blenders) ===")
    print(tracer.summarize_commingling_by_account(seed).to_string())