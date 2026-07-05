"""
Phase 8 — Pattern Detectors

The detectors below use account-relative or dataset-relative thresholds.
They do not use hardcoded rupee amounts. Counterparty account numbers are
preferred over names; names are used only as external identities when account
numbers are unavailable.
"""

from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta
import re

import numpy as np
import pandas as pd
import networkx as nx

from analytics_config import (
    ROUND_TRIP_MAX_DAYS, ROUND_TRIP_MIN_RETURN_RATIO, ROUND_TRIP_MAX_RETURN_RATIO,
    ROUND_TRIP_TOP_QUANTILE, ROUND_TRIP_MAX_FINDINGS_PER_PAIR,
    LAYERING_MIN_CHAIN, LAYERING_MAX_CHAIN, LAYERING_MAX_HOP_HOURS,
    LAYERING_MIN_KEEP_RATIO, LAYERING_TOP_QUANTILE,
    FAN_IN_MIN_SENDERS, FAN_IN_WINDOW_HOURS, FAN_IN_TOP_QUANTILE,
    FAN_OUT_MIN_RECEIVERS, FAN_OUT_WINDOW_HOURS, FAN_OUT_TOP_QUANTILE,
    SMURF_MIN_TXNS, SMURF_WINDOW_DAYS, SMURF_MIN_UNIQUE_DEST,
    SMURF_UPPER_QUANTILE, SMURF_LOWER_RATIO, SMURF_MIN_ACCOUNT_TXNS,
    ODD_HOUR_START, ODD_HOUR_END, ODD_HOUR_MIN_TXNS,
    ODD_HOUR_MIN_TIMED_TXN_RATIO, ODD_HOUR_MIN_ODD_RATIO,
)


def detect_round_trips(df: pd.DataFrame, txn_graph: nx.MultiDiGraph) -> tuple[list[dict], set]:
    findings, flagged_idx = [], set()
    tx = _prepare_tx(df)
    debits = tx[tx["debit"] > 0].copy()
    credits = tx[tx["credit"] > 0].copy()
    if debits.empty or credits.empty:
        return findings, flagged_idx

    debit_floor = _account_amount_floor(debits, "account_id", "debit", ROUND_TRIP_TOP_QUANTILE)
    credit_by_acc = {acc: g.sort_values("_ts") for acc, g in credits.groupby("account_id")}
    seen_pairs = defaultdict(int)

    for idx, d in debits.sort_values("_ts").iterrows():
        src = d["account_id"]
        cp = d["_counterparty"]
        if not cp or d["debit"] < debit_floor.get(src, 0):
            continue

        candidates = credit_by_acc.get(src)
        if candidates is None:
            continue
        start, end = d["_ts"], d["_ts"] + timedelta(days=ROUND_TRIP_MAX_DAYS)
        cands = candidates[(candidates["_ts"] > start) & (candidates["_ts"] <= end)].copy()
        if cands.empty:
            continue

        # Require same-counterparty evidence. Amount-only round trips create
        # excessive false positives on dense real statements.
        cands["_same_party"] = cands["_counterparty"].eq(cp)
        cands["_ratio"] = cands["credit"] / d["debit"]
        cands = cands[
            cands["_same_party"] &
            (cands["_ratio"] >= ROUND_TRIP_MIN_RETURN_RATIO) &
            (cands["_ratio"] <= ROUND_TRIP_MAX_RETURN_RATIO)
        ]
        if cands.empty:
            continue
        cands["_rank"] = (cands["_ratio"] - 1).abs()
        c = cands.sort_values(["_rank", "_ts"]).iloc[0]
        pair_key = (src, cp)
        if seen_pairs[pair_key] >= ROUND_TRIP_MAX_FINDINGS_PER_PAIR:
            continue
        seen_pairs[pair_key] += 1
        gap_days = (c["_ts"] - d["_ts"]).total_seconds() / 86400
        findings.append({
            "pattern": "ROUND_TRIP",
            "account_a": src,
            "account_b": cp,
            "outflow_amount": round(float(d["debit"]), 2),
            "return_amount": round(float(c["credit"]), 2),
            "return_ratio": round(float(c["_ratio"]), 4),
            "outflow_date": str(d["_ts"].date()),
            "return_date": str(c["_ts"].date()),
            "gap_days": round(gap_days, 1),
            "evidence": "same_counterparty" if bool(c["_same_party"]) else "amount_time_match",
            "severity": "HIGH" if gap_days <= 3 and bool(c["_same_party"]) else "MEDIUM",
            "description": f"{src} sent {d['debit']:,.2f} to {cp}; {c['credit']:,.2f} returned after {gap_days:.1f} days",
        })
        flagged_idx.update([idx, c.name])

    return findings, flagged_idx


def detect_round_trip_cycles(df: pd.DataFrame, txn_graph: nx.MultiDiGraph) -> tuple[list[dict], set]:
    """
    Multi-hop round-trip detector: A -> B -> C -> ... -> A (3 to
    ROUND_TRIP_CYCLE_MAX_HOPS edges).

    detect_round_trips() above can only ever surface the direct 2-hop
    case (A sends to B, B sends back to A) because it explicitly requires
    the return leg's counterparty to equal the original debit's
    counterparty. It never walks the graph, so a launderer who routes
    money through one or more intermediary "layering" accounts before it
    comes back to the source is invisible to it. This function finds
    those longer cycles by doing a bounded DFS over internal-to-internal
    edges in txn_graph (same edge index style as detect_layering) and,
    at every step, checking whether the next hop closes back on the
    account that originated the cycle.

    A candidate cycle must satisfy, simultaneously:
      - chronological order: each hop's timestamp is after the previous hop's
      - total elapsed time <= ROUND_TRIP_CYCLE_MAX_DAYS
      - per-hop gap <= ROUND_TRIP_CYCLE_MAX_HOP_HOURS (no dangling old edges)
      - amount conservation: each hop retains >= ROUND_TRIP_CYCLE_MIN_KEEP_RATIO
        of the previous hop's amount (so it isn't just noise threaded together)
      - the closing leg's amount is within
        [ROUND_TRIP_CYCLE_CLOSE_MIN_RATIO, ROUND_TRIP_CYCLE_CLOSE_MAX_RATIO]
        of the ORIGINAL seed amount (so the loop is actually "the same money"
        coming back, not an unrelated transfer that happens to close a path)
      - every account in the cycle is distinct (simple cycle, no repeats)

    Returns (findings, flagged_idx) in the same shape as the other detectors.
    """
    from analytics_config import (
        ROUND_TRIP_CYCLE_MIN_HOPS, ROUND_TRIP_CYCLE_MAX_HOPS,
        ROUND_TRIP_CYCLE_MAX_DAYS, ROUND_TRIP_CYCLE_MAX_HOP_HOURS,
        ROUND_TRIP_CYCLE_MIN_KEEP_RATIO, ROUND_TRIP_CYCLE_CLOSE_MIN_RATIO,
        ROUND_TRIP_CYCLE_CLOSE_MAX_RATIO, ROUND_TRIP_CYCLE_TOP_QUANTILE,
        ROUND_TRIP_CYCLE_MAX_FINDINGS,
    )

    findings, flagged_idx = [], set()
    internal_nodes = {n for n, data in txn_graph.nodes(data=True) if data.get("is_internal")}
    if len(internal_nodes) < ROUND_TRIP_CYCLE_MIN_HOPS:
        return findings, flagged_idx

    edge_index = defaultdict(list)
    for u, v, data in txn_graph.edges(data=True):
        if u not in internal_nodes or v not in internal_nodes:
            continue
        edge_index[u].append((data["timestamp"], v, float(data["amount"]), data.get("row_idx")))
    for src in edge_index:
        edge_index[src].sort(key=lambda x: x[0])

    if not edge_index:
        return findings, flagged_idx

    # Per-source-account floor (not one dataset-wide floor) so a small
    # mule ring isn't masked just because one large, unrelated account
    # also happens to be in the graph.
    amount_floor = {
        src: float(np.quantile([e[2] for e in edges], ROUND_TRIP_CYCLE_TOP_QUANTILE))
        for src, edges in edge_index.items()
        if len(edges) >= 5  # too few points to make a stable quantile; don't floor them
    }

    raw_cycles = []

    def dfs(start, node, path, visited, last_ts, first_ts, seed_amt, last_amt, idxs):
        if len(raw_cycles) >= ROUND_TRIP_CYCLE_MAX_FINDINGS * 4:
            return
        for ts, dst, amt, row_idx in edge_index.get(node, []):
            if amt < amount_floor.get(node, 0.0):
                continue
            if path:
                if ts <= last_ts:
                    continue
                if (ts - last_ts).total_seconds() / 3600 > ROUND_TRIP_CYCLE_MAX_HOP_HOURS:
                    continue
                if (ts - first_ts).days > ROUND_TRIP_CYCLE_MAX_DAYS:
                    continue
                if last_amt > 0 and (amt / last_amt) < ROUND_TRIP_CYCLE_MIN_KEEP_RATIO:
                    continue

            new_path = path + [(node, dst, amt, ts, row_idx)]

            if dst == start and len(new_path) >= ROUND_TRIP_CYCLE_MIN_HOPS:
                ratio = amt / seed_amt if seed_amt > 0 else 0
                if ROUND_TRIP_CYCLE_CLOSE_MIN_RATIO <= ratio <= ROUND_TRIP_CYCLE_CLOSE_MAX_RATIO:
                    raw_cycles.append((list(new_path), ratio))
                # A cycle can still legitimately continue on to a longer
                # loop through the same start node again, but to keep this
                # a *simple* cycle search we stop extending here.
                continue

            if dst in visited or len(new_path) >= ROUND_TRIP_CYCLE_MAX_HOPS:
                continue

            dfs(start, dst, new_path, visited | {dst}, ts,
                first_ts if path else ts, seed_amt if path else amt, amt, idxs + [row_idx])

    for start in list(edge_index):
        dfs(start, start, [], {start}, None, None, 0.0, 0.0, [])

    # Deduplicate cycles that are rotations of each other (A->B->C->A found
    # starting the walk from A, B, or C should count once).
    seen_keys = set()
    deduped = []
    for cycle, ratio in raw_cycles:
        accounts = [hop[0] for hop in cycle]
        min_i = accounts.index(min(accounts, key=str))
        canon = tuple(accounts[min_i:] + accounts[:min_i])
        if canon in seen_keys:
            continue
        seen_keys.add(canon)
        deduped.append((cycle, ratio))

    deduped.sort(key=lambda c: (len(c[0]), c[0][0][3]), reverse=True)

    for cycle, ratio in deduped[:ROUND_TRIP_CYCLE_MAX_FINDINGS]:
        accounts = [hop[0] for hop in cycle] + [cycle[-1][1]]
        amounts = [hop[2] for hop in cycle]
        dates = [str(hop[3].date()) for hop in cycle]
        idxs = [hop[4] for hop in cycle if hop[4] is not None]
        elapsed_days = (cycle[-1][3] - cycle[0][3]).total_seconds() / 86400
        findings.append({
            "pattern": "ROUND_TRIP_CYCLE",
            "hop_count": len(cycle),
            "cycle": " -> ".join(str(a) for a in accounts),
            "accounts": accounts,
            "seed_account": accounts[0],
            "seed_amount": round(amounts[0], 2),
            "closing_amount": round(amounts[-1], 2),
            "closing_ratio": round(float(ratio), 4),
            "hop_amounts": [round(a, 2) for a in amounts],
            "hop_dates": dates,
            "elapsed_days": round(elapsed_days, 1),
            "row_indices": idxs,
            "severity": "CRITICAL" if len(cycle) >= 4 else "HIGH",
            "description": (
                f"{len(cycle)}-hop round trip: {' -> '.join(str(a) for a in accounts)} "
                f"starting {amounts[0]:,.2f}, closing {amounts[-1]:,.2f} "
                f"({ratio:.0%} of seed) after {elapsed_days:.1f} days"
            ),
        })
        flagged_idx.update(idxs)

    return findings, flagged_idx


def detect_layering(df: pd.DataFrame, txn_graph: nx.MultiDiGraph) -> tuple[list[dict], set]:
    findings, flagged_idx = [], set()
    internal_nodes = {n for n, data in txn_graph.nodes(data=True) if data.get("is_internal")}
    edge_index = defaultdict(list)

    for u, v, key, data in txn_graph.edges(keys=True, data=True):
        if u not in internal_nodes or v not in internal_nodes:
            continue
        edge_index[u].append((data["timestamp"], v, float(data["amount"]), data.get("row_idx")))
    for src in edge_index:
        edge_index[src].sort(key=lambda x: x[0])

    all_amounts = [e[2] for edges in edge_index.values() for e in edges]
    if not all_amounts:
        return findings, flagged_idx
    global_floor = float(np.quantile(all_amounts, LAYERING_TOP_QUANTILE))

    def dfs(node, chain, last_ts, last_amt, visited, idxs):
        if len(chain) >= LAYERING_MIN_CHAIN:
            finding = _make_layering_finding(chain, idxs)
            findings.append(finding)
            flagged_idx.update(i for i in idxs if i is not None)
        if len(chain) >= LAYERING_MAX_CHAIN:
            return
        for ts, dst, amt, row_idx in edge_index.get(node, []):
            if dst in visited or amt < global_floor:
                continue
            if chain:
                gap = (ts - last_ts).total_seconds() / 3600
                if gap < 0:
                    continue
                if gap > LAYERING_MAX_HOP_HOURS:
                    break
                if last_amt > 0 and (amt / last_amt) < LAYERING_MIN_KEEP_RATIO:
                    continue
            dfs(dst, chain + [(node, dst, amt, str(ts.date()))], ts, amt, visited | {dst}, idxs + [row_idx])

    for start in list(edge_index):
        dfs(start, [], datetime(2000, 1, 1), 0.0, {start}, [])

    findings = _dedup_layering(findings)
    return findings, flagged_idx


def detect_fan_in(df: pd.DataFrame) -> tuple[list[dict], set]:
    return _detect_fan(df, direction="in")


def detect_fan_out(df: pd.DataFrame) -> tuple[list[dict], set]:
    return _detect_fan(df, direction="out")


def detect_smurfing(df: pd.DataFrame) -> tuple[list[dict], set]:
    findings, flagged_idx = [], set()
    tx = _prepare_tx(df)
    debit_df = tx[(tx["debit"] > 0) & (tx["_counterparty"] != "")].copy()

    for acc_id, group in debit_df.groupby("account_id"):
        if len(group) < SMURF_MIN_ACCOUNT_TXNS:
            continue
        amounts = group["debit"]
        upper = float(amounts.quantile(SMURF_UPPER_QUANTILE))
        lower = upper * SMURF_LOWER_RATIO
        if upper <= 0 or lower <= 0:
            continue
        band = group[(group["debit"] >= lower) & (group["debit"] <= upper)].sort_values("_ts")
        rows = band.to_dict("records")
        idxs = band.index.tolist()
        for i, row_i in enumerate(rows):
            window_end = row_i["_ts"] + timedelta(days=SMURF_WINDOW_DAYS)
            win_rows, win_idxs = [], []
            for j in range(i, len(rows)):
                if rows[j]["_ts"] > window_end:
                    break
                win_rows.append(rows[j]); win_idxs.append(idxs[j])
            if len(win_rows) < SMURF_MIN_TXNS:
                continue
            destinations = {r["_counterparty"] for r in win_rows if r["_counterparty"]}
            if len(destinations) < SMURF_MIN_UNIQUE_DEST:
                continue
            total = sum(float(r["debit"]) for r in win_rows)
            findings.append({
                "pattern": "SMURFING",
                "account": acc_id,
                "txn_count": len(win_rows),
                "total_amount": round(total, 2),
                "amount_band_low": round(lower, 2),
                "amount_band_high": round(upper, 2),
                "unique_destinations": len(destinations),
                "destinations": sorted(destinations)[:25],
                "window_start": str(win_rows[0]["_ts"].date()),
                "window_end": str(win_rows[-1]["_ts"].date()),
                "severity": "HIGH" if len(win_rows) >= SMURF_MIN_TXNS * 2 else "MEDIUM",
                "description": f"{acc_id} made {len(win_rows)} similarly sized transfers to {len(destinations)} destinations inside {SMURF_WINDOW_DAYS} days",
            })
            flagged_idx.update(win_idxs)
            break
    return findings, flagged_idx


def detect_odd_hours(df: pd.DataFrame) -> tuple[list[dict], set]:
    findings, flagged_idx = [], set()
    tx = _prepare_tx(df)
    tx["_hour"] = tx["time"].apply(_extract_hour)
    tx["_has_real_time"] = ~tx["time"].fillna("").astype(str).str.strip().isin(["", "nan", "00:00:00", "00:00"])

    for acc_id, group in tx.groupby("account_id"):
        timed = group[group["_has_real_time"]]
        if len(timed) < ODD_HOUR_MIN_TXNS:
            continue
        timed_ratio = len(timed) / max(len(group), 1)
        if timed_ratio < ODD_HOUR_MIN_TIMED_TXN_RATIO:
            continue
        odd = timed[(timed["_hour"] >= ODD_HOUR_START) & (timed["_hour"] < ODD_HOUR_END)]
        if len(odd) < ODD_HOUR_MIN_TXNS:
            continue
        odd_ratio = len(odd) / len(timed)
        if odd_ratio < ODD_HOUR_MIN_ODD_RATIO:
            continue
        findings.append({
            "pattern": "ODD_HOUR",
            "account": acc_id,
            "odd_hour_txns": int(len(odd)),
            "timed_txns": int(len(timed)),
            "odd_hour_ratio": round(float(odd_ratio), 4),
            "total_debit": round(float(odd["debit"].sum()), 2),
            "total_credit": round(float(odd["credit"].sum()), 2),
            "hours_active": sorted(odd["_hour"].unique().tolist()),
            "first_date": str(odd["date"].min()),
            "last_date": str(odd["date"].max()),
            "severity": "HIGH" if odd_ratio >= 0.5 else "MEDIUM",
            "description": f"{acc_id} has {len(odd)} real timestamped odd-hour transactions ({odd_ratio:.0%} of timed activity)",
        })
        flagged_idx.update(odd.index.tolist())
    return findings, flagged_idx


def _detect_fan(df: pd.DataFrame, direction: str) -> tuple[list[dict], set]:
    findings, flagged_idx = [], set()
    tx = _prepare_tx(df)
    if direction == "in":
        work = tx[(tx["credit"] > 0) & (tx["_counterparty"] != "")].copy()
        amount_col, min_parties, window_hours, quantile = "credit", FAN_IN_MIN_SENDERS, FAN_IN_WINDOW_HOURS, FAN_IN_TOP_QUANTILE
        acct_label, party_label, pattern = "collector", "senders", "FAN_IN"
    else:
        work = tx[(tx["debit"] > 0) & (tx["_counterparty"] != "")].copy()
        amount_col, min_parties, window_hours, quantile = "debit", FAN_OUT_MIN_RECEIVERS, FAN_OUT_WINDOW_HOURS, FAN_OUT_TOP_QUANTILE
        acct_label, party_label, pattern = "distributor", "receivers", "FAN_OUT"

    floor_by_acc = _account_amount_floor(work, "account_id", amount_col, quantile)
    for acc_id, group in work.groupby("account_id"):
        floor = floor_by_acc.get(acc_id, 0)
        group = group[group[amount_col] >= floor].sort_values("_ts")
        rows, idxs = group.to_dict("records"), group.index.tolist()
        for i, row_i in enumerate(rows):
            window_end = row_i["_ts"] + timedelta(hours=window_hours)
            win_rows, win_idxs = [], []
            for j in range(i, len(rows)):
                if rows[j]["_ts"] > window_end:
                    break
                win_rows.append(rows[j]); win_idxs.append(idxs[j])
            parties = {r["_counterparty"] for r in win_rows if r["_counterparty"]}
            if len(parties) < min_parties:
                continue
            total = sum(float(r[amount_col]) for r in win_rows)
            findings.append({
                "pattern": pattern,
                acct_label: acc_id,
                f"{party_label[:-1]}_count": len(parties),
                party_label: sorted(parties)[:50],
                "total_inflow" if direction == "in" else "total_outflow": round(total, 2),
                "window_start": str(win_rows[0]["_ts"].date()),
                "window_end": str(win_rows[-1]["_ts"].date()),
                "txn_count": len(win_rows),
                "amount_floor_used": round(float(floor), 2),
                "severity": "CRITICAL" if len(parties) >= min_parties * 2 else "HIGH",
                "description": f"{acc_id} shows {pattern}: {len(parties)} counterparties in {window_hours}h using account-relative amount floor",
            })
            flagged_idx.update(win_idxs)
            break
    return findings, flagged_idx


def _prepare_tx(df: pd.DataFrame) -> pd.DataFrame:
    tx = df.copy()
    tx["_ts"] = tx.apply(_ts, axis=1)
    tx["_counterparty"] = tx.apply(_counterparty_identity, axis=1)
    return tx


def _counterparty_identity(row) -> str:
    for col in ("counterparty_account", "counterparty_name"):
        val = row.get(col, "")
        if pd.isna(val):
            continue
        s = str(val).strip()
        if s and s.lower() not in {"nan", "none", "null"}:
            return re.sub(r"\s+", " ", s).upper()
    return ""


def _account_amount_floor(df: pd.DataFrame, account_col: str, amount_col: str, quantile: float) -> dict:
    floors = {}
    for acc, group in df.groupby(account_col):
        vals = pd.to_numeric(group[amount_col], errors="coerce").dropna()
        vals = vals[vals > 0]
        floors[acc] = float(vals.quantile(quantile)) if len(vals) else 0.0
    return floors


def _make_layering_finding(chain, chain_idxs) -> dict:
    accounts = [c[0] for c in chain] + [chain[-1][1]]
    amounts = [c[2] for c in chain]
    dates = [c[3] for c in chain]
    skim = 1 - (amounts[-1] / amounts[0]) if amounts[0] > 0 else 0
    return {
        "pattern": "LAYERING",
        "chain": " -> ".join(accounts),
        "chain_length": len(chain),
        "start_amount": round(amounts[0], 2),
        "end_amount": round(amounts[-1], 2),
        "skim_ratio": round(skim, 4),
        "start_date": dates[0],
        "end_date": dates[-1],
        "accounts": accounts,
        "row_indices": [i for i in chain_idxs if i is not None],
        "severity": "CRITICAL" if len(chain) >= 5 else "HIGH",
        "description": f"Layering chain {len(chain)} hops through {' -> '.join(accounts)} with {skim:.1%} retained/skewed flow",
    }


def _dedup_layering(findings: list[dict]) -> list[dict]:
    findings = sorted(findings, key=lambda f: (f["chain_length"], f["start_amount"]), reverse=True)
    kept, seen = [], set()
    for f in findings:
        key = tuple(f["accounts"])
        if any(set(key).issubset(set(k)) for k in seen):
            continue
        kept.append(f); seen.add(key)
    return kept


def _ts(row) -> datetime:
    date_str = str(row.get("date", "")).strip()
    time_str = str(row.get("time", "00:00:00")).strip()
    if not time_str or time_str in ("nan", ""):
        time_str = "00:00:00"
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return datetime(2000, 1, 1)


def _extract_hour(time_str: str) -> int:
    try:
        return int(str(time_str).split(":")[0])
    except (ValueError, IndexError):
        return 12