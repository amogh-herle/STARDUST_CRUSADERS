from __future__ import annotations
import os
import json
import re
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import networkx as nx
from analytics_config import (
    TRAIL_MAX_HOPS, TRAIL_MIN_MATCH_RATIO, TRAIL_MAX_HOP_HOURS,
)

# ── Old/Existing dataclasses and BFS traces ──────────────────────────

@dataclass
class TrailHop:
    hop_number:      int
    from_account:    str
    to_account:      str
    amount:          float
    date:            str
    timestamp:       datetime
    utr_ref:         str
    narration:       str
    channel:         str
    match_ratio:     float         # credit / previous debit
    cumulative_loss: float         # total % skimmed off the original amount


@dataclass
class MoneyTrail:
    root_account:  str
    direction:     str             # "forward" | "backward"
    seed_amount:   float
    hops:          list[TrailHop] = field(default_factory=list)
    terminal_node: Optional[str]  = None
    terminal_type: str            = ""   # e.g. "ATM", "CRYPTO", "INTERNATIONAL", "dead_end"
    total_hops:    int            = 0
    amount_recovered: float      = 0.0   # amount at end of trail

    def to_records(self) -> list[dict]:
        base = {
            "root_account": self.root_account,
            "direction":    self.direction,
            "seed_amount":  self.seed_amount,
            "terminal":     self.terminal_node,
            "terminal_type":self.terminal_type,
        }
        return [{**base, **asdict(h)} for h in self.hops]


def trace_forward(
    account_id: str,
    txn_graph:  nx.MultiDiGraph,
    df:         pd.DataFrame,
    seed_txn:   Optional[dict] = None,
) -> list[MoneyTrail]:
    return _bfs_trace(account_id, txn_graph, df, direction="forward",
                      seed_txn=seed_txn)


def trace_backward(
    account_id: str,
    txn_graph:  nx.MultiDiGraph,
    df:         pd.DataFrame,
) -> list[MoneyTrail]:
    return _bfs_trace(account_id, txn_graph, df, direction="backward")


def _bfs_trace(
    root:      str,
    txn_graph: nx.MultiDiGraph,
    df:        pd.DataFrame,
    direction: str,
    seed_txn:  Optional[dict] = None,
) -> list[MoneyTrail]:
    from graph_builder import is_merchant

    tx_index = _build_tx_index(df)

    if seed_txn:
        seed_amount = seed_txn["amount"]
        seed_time   = seed_txn["timestamp"]
        seed_utr    = seed_txn.get("utr_ref", "")
    else:
        root_txns   = tx_index.get(root, [])
        if not root_txns:
            return []
        outflows    = [t for t in root_txns if t["direction"] == "debit"] if direction == "forward" \
                      else [t for t in root_txns if t["direction"] == "credit"]
        if not outflows:
            return []
        outflows.sort(key=lambda t: t["amount"], reverse=True)
        seed_amount = outflows[0]["amount"]
        seed_time   = outflows[0]["timestamp"]
        seed_utr    = outflows[0].get("utr_ref", "")

    completed_trails: list[MoneyTrail] = []

    queue = deque()
    queue.append((root, seed_amount, seed_time, seed_utr, 0, [], {root}))

    while queue:
        node, amount, last_time, prev_utr, hop_count, hops, visited = queue.popleft()

        if hop_count >= TRAIL_MAX_HOPS:
            _finalise_trail(root, direction, seed_amount, hops,
                            node, "max_hops_reached", completed_trails)
            continue

        candidates = []
        if direction == "forward":
            neighbors = list(txn_graph.successors(node))
            for neighbor in neighbors:
                if neighbor in visited:
                    continue
                if is_merchant(neighbor):
                    continue
                edges = txn_graph.get_edge_data(node, neighbor)
                if not edges:
                    continue
                for edge_key, edge_data in edges.items():
                    ts = edge_data.get("timestamp")
                    if not isinstance(ts, datetime) or ts < last_time:
                        continue
                    time_diff = (ts - last_time).total_seconds()
                    if time_diff > TRAIL_MAX_HOP_HOURS * 3600:
                        continue
                    
                    candidates.append((neighbor, edge_data, ts, time_diff))
        else:
            neighbors = list(txn_graph.predecessors(node))
            for neighbor in neighbors:
                if neighbor in visited:
                    continue
                if is_merchant(neighbor):
                    continue
                edges = txn_graph.get_edge_data(neighbor, node)
                if not edges:
                    continue
                for edge_key, edge_data in edges.items():
                    ts = edge_data.get("timestamp")
                    if not isinstance(ts, datetime) or ts > last_time:
                        continue
                    time_diff = (last_time - ts).total_seconds()
                    if time_diff > TRAIL_MAX_HOP_HOURS * 3600:
                        continue
                    
                    candidates.append((neighbor, edge_data, ts, time_diff))

        if not candidates:
            if hops:
                _finalise_trail(root, direction, seed_amount, hops,
                                node, "dead_end", completed_trails)
            continue

        scored_candidates = []
        for neighbor, edge_data, ts, time_diff in candidates:
            node_data = txn_graph.nodes.get(neighbor, {})
            is_internal = node_data.get("is_internal", False)
            edge_amt = edge_data["amount"]

            match_ratio = edge_amt / amount if amount > 0 else 0.0

            if match_ratio < TRAIL_MIN_MATCH_RATIO:
                continue

            penalty = 0.0
            
            if not is_internal:
                penalty += 10000.0
            
            utr_ref = edge_data.get("utr_ref", "")
            utr_match = False
            if prev_utr and utr_ref and str(prev_utr).strip() == str(utr_ref).strip():
                utr_match = True
            
            if not utr_match:
                penalty += 5000.0
            
            amt_dev = abs(edge_amt - amount) / amount if amount > 0 else 1.0
            penalty += amt_dev * 2000.0
            
            time_diff_hours = time_diff / 3600.0
            penalty += time_diff_hours * 10.0

            scored_candidates.append({
                "neighbor": neighbor,
                "edge_data": edge_data,
                "ts": ts,
                "match_ratio": match_ratio,
                "penalty": penalty,
                "is_internal": is_internal,
                "utr_ref": utr_ref
            })

        if not scored_candidates:
            if hops:
                _finalise_trail(root, direction, seed_amount, hops,
                                node, "dead_end", completed_trails)
            continue

        scored_candidates.sort(key=lambda x: x["penalty"])

        branching_factor = 2
        top_candidates = scored_candidates[:branching_factor]

        found_next = False
        for cand in top_candidates:
            neighbor = cand["neighbor"]
            edge_data = cand["edge_data"]
            edge_amount = edge_data["amount"]
            match_ratio = cand["match_ratio"]

            hop = TrailHop(
                hop_number      = hop_count + 1,
                from_account    = node if direction == "forward" else neighbor,
                to_account      = neighbor if direction == "forward" else node,
                amount          = edge_amount,
                date            = edge_data.get("date", ""),
                timestamp       = cand["ts"],
                utr_ref         = cand["utr_ref"],
                narration       = edge_data.get("narration", ""),
                channel         = edge_data.get("channel", ""),
                match_ratio     = round(match_ratio, 4),
                cumulative_loss = round(1 - (edge_amount / seed_amount), 4) if seed_amount > 0 else 0.0,
            )

            new_hops = hops + [hop]

            terminal_type = _classify_terminal(neighbor, txn_graph, direction)
            if not cand["is_internal"]:
                _finalise_trail(root, direction, seed_amount, new_hops,
                                neighbor, "EXTERNAL_ACCOUNT", completed_trails)
                found_next = True
            elif terminal_type:
                _finalise_trail(root, direction, seed_amount, new_hops,
                                neighbor, terminal_type, completed_trails)
                found_next = True
            else:
                queue.append((
                    neighbor, edge_amount, cand["ts"], cand["utr_ref"],
                    hop_count + 1, new_hops, visited | {neighbor}
                ))
                found_next = True

        if not found_next and hops:
            _finalise_trail(root, direction, seed_amount, hops,
                            node, "dead_end", completed_trails)

    return completed_trails


def _build_tx_index(df: pd.DataFrame) -> dict:
    index = {}
    for _, row in df.iterrows():
        acc = row["account_id"]
        ts  = _parse_ts(row)
        debit_amt  = float(row.get("debit", 0) or 0)
        credit_amt = float(row.get("credit", 0) or 0)
        direction  = "debit" if debit_amt > 0 else "credit"
        # BUGFIX: amount must come from whichever column actually holds
        # the transaction value for this row's direction. Previously this
        # always read `debit`, which silently zeroed out every credit
        # transaction's amount (debit=0 on a credit row) and made
        # backward tracing dead-end at hop 0 for every account, 100% of
        # the time (seed_amount = 0.0 -> match_ratio always 0.0 -> every
        # candidate fails TRAIL_MIN_MATCH_RATIO).
        amount = debit_amt if direction == "debit" else credit_amt
        tx  = {
            "account_id":      acc,
            "amount":          amount,
            "direction":       direction,
            "timestamp":       ts,
            "date":            str(row.get("date", "")),
            "utr_ref":         str(row.get("utr_ref", "")),
            "narration":       str(row.get("narration", "")),
            "channel":         str(row.get("channel", "")),
            "counterparty":    str(row.get("counterparty_name", "")),
        }
        index.setdefault(acc, []).append(tx)

    for acc in index:
        index[acc].sort(key=lambda t: t["timestamp"])

    return index


def _classify_terminal(
    node: str,
    txn_graph: nx.MultiDiGraph,
    direction: str,
) -> str:
    if direction == "forward":
        out_degree = txn_graph.out_degree(node)
    else:
        out_degree = txn_graph.in_degree(node)

    if out_degree == 0:
        return "dead_end"

    exit_keywords = ["ATM", "CASH WITHDRAWAL", "CRYPTO", "INTERNATIONAL",
                     "REMITTANCE", "WIRE", "FOREX"]
    if direction == "forward":
        for _, _, data in txn_graph.out_edges(node, data=True):
            narration = str(data.get("narration", "")).upper()
            for kw in exit_keywords:
                if kw in narration:
                    return kw.replace(" ", "_")

    return ""


def _finalise_trail(
    root, direction, seed_amount, hops, terminal, terminal_type, results
):
    if not hops:
        return
    trail = MoneyTrail(
        root_account     = root,
        direction        = direction,
        seed_amount      = seed_amount,
        hops             = hops,
        terminal_node    = terminal,
        terminal_type    = terminal_type,
        total_hops       = len(hops),
        amount_recovered = hops[-1].amount if hops else 0.0,
    )
    results.append(trail)


def _filter_account_rows(df: pd.DataFrame, account_id) -> pd.DataFrame:
    """
    Return every row belonging to `account_id`, regardless of what dtype
    pandas gave the `account_id` column.

    BUGFIX: the previous code branched on
    `df['account_id'].dtype == 'object'` to decide between a string
    comparison and a raw `==` against an int. On pandas >= 2.x with
    `dtype=str` loading (or pandas 3.x, whose default string dtype reprs
    as "str" rather than "object"), that check is False, so the code fell
    into the `else` branch and compared a string Series to a Python int
    with `==` — which is always False. That silently zeroed out every
    account's transactions and made trace_forward_fifo / trace_backward_fifo
    / generate_investigator_ledger return no results for any account, with
    no error raised. Always normalising both sides to string comparison
    is correct regardless of the column's underlying dtype.
    """
    try:
        acc_id_int = int(float(account_id))
        target = str(acc_id_int)
    except (ValueError, TypeError):
        target = str(account_id).strip()
    return df[df['account_id'].astype(str).str.strip() == target]


def _parse_ts(row) -> datetime:
    date_str = str(row.get("date", "")).strip()
    time_str = str(row.get("time", "00:00:00")).strip()
    if not time_str or time_str == "nan":
        time_str = "00:00:00"
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return datetime(2000, 1, 1)

# ── New FIFO Money Trail and Source-of-Funds Tracing Logic ───────────

def _counterparty_identity(row) -> str:
    for col in ("counterparty_account", "counterparty_name"):
        val = row.get(col, "")
        if pd.isna(val):
            continue
        s = str(val).strip()
        if s and s.lower() not in {"nan", "none", "null"}:
            return re.sub(r"\s+", " ", s).upper()
    return ""

def build_all_local_ledgers(df: pd.DataFrame) -> dict:
    internal_accounts = df["account_id"].unique()
    ledgers = {}
    
    for acc in internal_accounts:
        acc_df = df[df["account_id"] == acc].copy()
        acc_df["_timestamp"] = acc_df.apply(_parse_ts, axis=1)
        acc_df = acc_df.sort_values("_timestamp")
        
        credit_queue = []
        allocations = {}
        
        for idx, row in acc_df.iterrows():
            debit = float(row.get("debit", 0.0) or 0.0)
            credit = float(row.get("credit", 0.0) or 0.0)
            
            if credit > 0:
                credit_queue.append({
                    "idx": idx,
                    "amount": credit,
                    "remaining": credit
                })
                allocations[idx] = []
                
            if debit > 0:
                remaining_debit = debit
                while remaining_debit > 0 and credit_queue:
                    oldest = credit_queue[0]
                    allocated = min(remaining_debit, oldest["remaining"])
                    
                    oldest["remaining"] -= allocated
                    remaining_debit -= allocated
                    
                    allocations[oldest["idx"]].append({
                        "debit_txn_idx": idx,
                        "allocated_amount": allocated
                    })
                    
                    if oldest["remaining"] <= 0:
                        credit_queue.pop(0)
                        
                if remaining_debit > 0:
                    allocations.setdefault("PRIOR_BALANCE", []).append({
                        "debit_txn_idx": idx,
                        "allocated_amount": remaining_debit
                    })
                    
        ledgers[acc] = allocations
        
    return ledgers

def find_matching_credit_txn(df, sender_acc, receiver_acc, debit_amount, debit_timestamp, debit_utr):
    receiver_df = df[df["account_id"].astype(str) == str(receiver_acc)]
    credits = receiver_df[receiver_df["credit"] > 0]
    if credits.empty:
        return None
    
    if debit_utr and str(debit_utr).strip() and str(debit_utr).strip().lower() not in {"nan", "none", ""}:
        utr_matches = credits[credits["utr_ref"].astype(str).str.strip() == str(debit_utr).strip()]
        if not utr_matches.empty:
            utr_matches = utr_matches.copy()
            utr_matches["_ts"] = utr_matches.apply(_parse_ts, axis=1)
            utr_matches["diff"] = (utr_matches["_ts"] - debit_timestamp).abs()
            best = utr_matches.sort_values("diff").iloc[0]
            return best.name
            
    dt_min = debit_timestamp - timedelta(hours=24)
    dt_max = debit_timestamp + timedelta(hours=24)
    
    candidates = []
    for idx, row in credits.iterrows():
        ts = row["_timestamp"]
        if dt_min <= ts <= dt_max:
            cred_amt = float(row["credit"])
            if abs(cred_amt - debit_amount) / max(1.0, debit_amount) < 0.05:
                cp_acc = str(row.get("counterparty_account", "")).strip()
                cp_name = str(row.get("counterparty_name", "")).strip()
                
                is_sender_match = (
                    str(sender_acc).strip() == cp_acc or
                    str(sender_acc).strip() in cp_name.upper() or
                    cp_name.upper() in str(sender_acc).strip()
                )
                
                time_diff = abs((ts - debit_timestamp).total_seconds())
                penalty = time_diff
                if not is_sender_match:
                    penalty += 100000.0
                
                candidates.append((idx, penalty))
                
    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
        
    return None

def trace_forward_fifo(
    account_id: str,
    df: pd.DataFrame,
) -> list[MoneyTrail]:
    trails = []
    try:
        acc_txns = _filter_account_rows(df, account_id).copy()
    except (ValueError, TypeError):
        return []

    if acc_txns.empty:
        return []

    acc_txns['_timestamp'] = acc_txns.apply(_parse_ts, axis=1)
    acc_txns = acc_txns.sort_values('_timestamp').reset_index(drop=True)

    credits = acc_txns[acc_txns['credit'] > 0]
    if credits.empty:
        return []

    for credit_idx, credit_row in credits.iterrows():
        credit_amount = float(credit_row['credit'])
        remaining_balance = credit_amount
        trail_hops = []

        future_txns = acc_txns[acc_txns.index > credit_idx]
        debits_only = future_txns[future_txns['debit'] > 0]

        if debits_only.empty:
            continue

        for debit_idx, debit_row in debits_only.iterrows():
            debit_amount = float(debit_row['debit'])
            debit_ts = debit_row['_timestamp']
            allocated = min(debit_amount, remaining_balance)

            if allocated > 0:
                cp_account = str(debit_row.get('counterparty_account', '')).strip()
                cp_name = str(debit_row.get('counterparty_name', '')).strip()
                to_account = cp_account if cp_account and cp_account.lower() not in {'nan', 'none'} else cp_name

                hop = TrailHop(
                    hop_number=len(trail_hops) + 1,
                    from_account=str(account_id),
                    to_account=to_account if to_account else 'UNKNOWN',
                    amount=allocated,
                    date=str(debit_row.get('date', '')),
                    timestamp=debit_ts,
                    utr_ref=str(debit_row.get('utr_ref', '')),
                    narration=str(debit_row.get('narration', '')),
                    channel=str(debit_row.get('channel', '')),
                    match_ratio=round(allocated / credit_amount, 4) if credit_amount > 0 else 0.0,
                    cumulative_loss=round((credit_amount - remaining_balance + allocated) / credit_amount, 4) if credit_amount > 0 else 0.0,
                )
                trail_hops.append(hop)
                remaining_balance -= allocated

                if remaining_balance <= 0:
                    break

        if trail_hops:
            trail = MoneyTrail(
                root_account=str(account_id),
                direction="forward_fifo",
                seed_amount=credit_amount,
                hops=trail_hops,
                terminal_node=trail_hops[-1].to_account if trail_hops else None,
                terminal_type="balance_depleted" if remaining_balance <= 0 else "partial",
                total_hops=len(trail_hops),
                amount_recovered=credit_amount - remaining_balance,
            )
            trails.append(trail)

    return trails

def trace_backward_fifo(
    account_id: str,
    df: pd.DataFrame,
) -> list[MoneyTrail]:
    """
    Hackathon-compliant backward trace.

    trace_backward() (above) answers "what does this account's outgoing
    money resemble elsewhere in the network" via heuristic beneficiary
    matching. It does NOT answer the actual required question: "this
    debit went out — which earlier credit(s), by FIFO order, actually
    funded it, and how much (if any) came out of the pre-existing
    balance instead?"

    This function answers that question directly, by replaying the same
    FIFO credit-consumption simulation used in trace_forward_fifo /
    build_all_local_ledgers, but reporting it from the debit's point of
    view: for every outgoing debit, walk back through the account's own
    prior credit history (oldest un-consumed credit first) and record
    exactly which credit(s) paid for it.
    """
    trails: list[MoneyTrail] = []
    try:
        acc_txns = _filter_account_rows(df, account_id).copy()
    except (ValueError, TypeError):
        return []

    if acc_txns.empty:
        return []

    acc_txns['_timestamp'] = acc_txns.apply(_parse_ts, axis=1)
    acc_txns = acc_txns.sort_values('_timestamp').reset_index(drop=True)

    credit_queue: list[dict] = []  # FIFO queue: oldest un-consumed credit first

    for _, row in acc_txns.iterrows():
        debit_amt  = float(row.get('debit', 0.0) or 0.0)
        credit_amt = float(row.get('credit', 0.0) or 0.0)

        if credit_amt > 0:
            cp_account = str(row.get('counterparty_account', '')).strip()
            cp_name    = str(row.get('counterparty_name', '')).strip()
            source = cp_account if cp_account and cp_account.lower() not in {'nan', 'none'} else cp_name
            credit_queue.append({
                'amount':    credit_amt,
                'remaining': credit_amt,
                'date':      str(row.get('date', '')),
                'timestamp': row['_timestamp'],
                'source':    source if source and source.lower() != 'nan' else 'UNKNOWN',
                'utr_ref':   str(row.get('utr_ref', '')),
                'channel':   str(row.get('channel', '')),
            })

        if debit_amt > 0:
            remaining_debit = debit_amt
            trail_hops: list[TrailHop] = []

            while remaining_debit > 0 and credit_queue:
                oldest = credit_queue[0]
                allocated = min(remaining_debit, oldest['remaining'])
                if allocated <= 0:
                    break

                trail_hops.append(TrailHop(
                    hop_number      = len(trail_hops) + 1,
                    from_account    = oldest['source'],
                    to_account      = str(account_id),
                    amount          = allocated,
                    date            = oldest['date'],
                    timestamp       = oldest['timestamp'],
                    utr_ref         = oldest['utr_ref'],
                    narration       = str(row.get('narration', '')),
                    channel         = oldest['channel'],
                    match_ratio     = round(allocated / debit_amt, 4) if debit_amt > 0 else 0.0,
                    cumulative_loss = round((debit_amt - remaining_debit + allocated) / debit_amt, 4) if debit_amt > 0 else 0.0,
                ))
                oldest['remaining'] -= allocated
                remaining_debit -= allocated
                if oldest['remaining'] <= 0:
                    credit_queue.pop(0)

            funded_from_prior_balance = remaining_debit  # unmatched remainder = pre-existing balance

            if trail_hops:
                trail = MoneyTrail(
                    root_account     = str(account_id),
                    direction        = "backward_fifo",
                    seed_amount      = debit_amt,
                    hops             = trail_hops,
                    terminal_node    = trail_hops[0].from_account,  # earliest / deepest credit source reached
                    terminal_type    = "prior_balance_reached" if funded_from_prior_balance > 0 else "fully_traced",
                    total_hops       = len(trail_hops),
                    amount_recovered = debit_amt - funded_from_prior_balance,
                )
                trails.append(trail)

    return trails

def trace_all_money_trails(
    df: pd.DataFrame,
    top_accounts: list,
    risk_scores_dict: dict,
    node_meta: dict,
    node_roles: dict,
) -> tuple[list[dict], list[dict], dict]:
    ledgers = build_all_local_ledgers(df)
    
    laundering_chains = []
    destination_accounts = []
    
    cy_nodes = {}
    cy_edges = {}
    
    trail_counter = 0
    internal_accounts = set(df["account_id"].unique())
    
    df_with_ts = df.copy()
    df_with_ts["_timestamp"] = df_with_ts.apply(_parse_ts, axis=1)
    
    for root_acc in top_accounts:
        root_df = df_with_ts[df_with_ts["account_id"] == root_acc]
        credits = root_df[root_df["credit"] > 0]
        
        for root_idx, credit_row in credits.iterrows():
            credit_amount = float(credit_row["credit"])
            credit_ts = credit_row["_timestamp"]
            credit_utr = str(credit_row.get("utr_ref", ""))
            
            trail_counter += 1
            trail_id = f"TR_{trail_counter:04d}"
            
            queue = deque([(root_acc, root_idx, credit_amount, 1, [], {root_acc})])
            
            while queue:
                acc_id, credit_idx, trace_amount, hop_number, path, visited = queue.popleft()
                
                if hop_number > 10:
                    _record_destination(
                        destination_accounts,
                        trail_id, root_acc, credit_amount,
                        acc_id, node_meta, node_roles, trace_amount,
                        hop_number - 1, "MAX_HOPS_EXCEEDED"
                    )
                    _add_path_to_chains(laundering_chains, trail_id, path, node_meta)
                    _add_path_to_graph(cy_nodes, cy_edges, path, trail_id, node_meta, node_roles)
                    continue
                    
                acc_allocations = ledgers.get(acc_id, {}).get(credit_idx, [])
                
                if not acc_allocations:
                    _record_destination(
                        destination_accounts,
                        trail_id, root_acc, credit_amount,
                        acc_id, node_meta, node_roles, trace_amount,
                        hop_number - 1, "NO_OUTGOING_DEBITS"
                    )
                    _add_path_to_chains(laundering_chains, trail_id, path, node_meta)
                    _add_path_to_graph(cy_nodes, cy_edges, path, trail_id, node_meta, node_roles)
                    continue
                    
                orig_credit_amt = float(df_with_ts.loc[credit_idx, "credit"])
                
                for alloc in acc_allocations:
                    debit_idx = alloc["debit_txn_idx"]
                    alloc_amt = alloc["allocated_amount"]
                    
                    traced_alloc_amt = trace_amount * (alloc_amt / orig_credit_amt)
                    
                    debit_row = df_with_ts.loc[debit_idx]
                    debit_ts = debit_row["_timestamp"]
                    debit_utr = debit_row.get("utr_ref", "")
                    
                    dest_acc = _counterparty_identity(debit_row)
                    if not dest_acc:
                        dest_acc = "UNKNOWN"
                        
                    dest_is_internal = False
                    matched_internal_id = None
                    for i_id in internal_accounts:
                        if str(i_id).strip() == str(dest_acc).strip():
                            dest_is_internal = True
                            matched_internal_id = i_id
                            break
                            
                    edge_info = {
                        "from_account": acc_id,
                        "to_account": matched_internal_id if dest_is_internal else dest_acc,
                        "amount": traced_alloc_amt,
                        "date": str(debit_row.get("date", "")),
                        "channel": str(debit_row.get("channel", "")),
                    }
                    new_path = path + [edge_info]
                    
                    dest_role = node_roles.get(str(edge_info["to_account"]), "unknown")
                    
                    if dest_role == "merchant":
                        _record_destination(
                            destination_accounts,
                            trail_id, root_acc, credit_amount,
                            edge_info["to_account"], node_meta, node_roles, traced_alloc_amt,
                            hop_number, "MERCHANT_REACHED"
                        )
                        _add_path_to_chains(laundering_chains, trail_id, new_path, node_meta)
                        _add_path_to_graph(cy_nodes, cy_edges, new_path, trail_id, node_meta, node_roles)
                        
                    elif dest_role == "collector":
                        _record_destination(
                            destination_accounts,
                            trail_id, root_acc, credit_amount,
                            edge_info["to_account"], node_meta, node_roles, traced_alloc_amt,
                            hop_number, "COLLECTOR_REACHED"
                        )
                        _add_path_to_chains(laundering_chains, trail_id, new_path, node_meta)
                        _add_path_to_graph(cy_nodes, cy_edges, new_path, trail_id, node_meta, node_roles)
                        
                    elif not dest_is_internal:
                        _record_destination(
                            destination_accounts,
                            trail_id, root_acc, credit_amount,
                            edge_info["to_account"], node_meta, node_roles, traced_alloc_amt,
                            hop_number, "EXTERNAL_ACCOUNT"
                        )
                        _add_path_to_chains(laundering_chains, trail_id, new_path, node_meta)
                        _add_path_to_graph(cy_nodes, cy_edges, new_path, trail_id, node_meta, node_roles)
                        
                    elif edge_info["to_account"] in visited:
                        _record_destination(
                            destination_accounts,
                            trail_id, root_acc, credit_amount,
                            edge_info["to_account"], node_meta, node_roles, traced_alloc_amt,
                            hop_number, "CYCLE_DETECTED"
                        )
                        _add_path_to_chains(laundering_chains, trail_id, new_path, node_meta)
                        _add_path_to_graph(cy_nodes, cy_edges, new_path, trail_id, node_meta, node_roles)
                        
                    else:
                        next_credit_idx = find_matching_credit_txn(
                            df_with_ts, acc_id, edge_info["to_account"],
                            alloc_amt, debit_ts, debit_utr
                        )
                        
                        if next_credit_idx is not None:
                            queue.append((
                                edge_info["to_account"],
                                next_credit_idx,
                                traced_alloc_amt,
                                hop_number + 1,
                                new_path,
                                visited | {edge_info["to_account"]}
                            ))
                        else:
                            _record_destination(
                                destination_accounts,
                                trail_id, root_acc, credit_amount,
                                edge_info["to_account"], node_meta, node_roles, traced_alloc_amt,
                                hop_number, "UNMATCHED_TRANSFER"
                            )
                            _add_path_to_chains(laundering_chains, trail_id, new_path, node_meta)
                            _add_path_to_graph(cy_nodes, cy_edges, new_path, trail_id, node_meta, node_roles)
                            
    return laundering_chains, destination_accounts, {"nodes": list(cy_nodes.values()), "edges": list(cy_edges.values())}

def _record_destination(
    destination_accounts,
    trail_id, source_credit_account, source_credit_amount,
    destination_account, node_meta, node_roles, amount_received,
    hop_count, termination_reason
):
    meta = node_meta.get(str(destination_account), {})
    destination_accounts.append({
        "trail_id": trail_id,
        "source_credit_account": source_credit_account,
        "source_credit_amount": source_credit_amount,
        "destination_account": destination_account,
        "destination_holder": meta.get("account_holder", meta.get("label", str(destination_account))),
        "destination_bank": meta.get("bank_name", "UNKNOWN"),
        "destination_type": node_roles.get(str(destination_account), "unknown"),
        "amount_received": round(amount_received, 2),
        "hop_count": hop_count,
        "termination_reason": termination_reason
    })

def _add_path_to_chains(laundering_chains, trail_id, path, node_meta):
    for idx, hop in enumerate(path):
        to_acc_str = str(hop["to_account"])
        meta = node_meta.get(to_acc_str, {})
        laundering_chains.append({
            "trail_id": trail_id,
            "hop_number": idx + 1,
            "from_account": hop["from_account"],
            "to_account": hop["to_account"],
            "amount": round(hop["amount"], 2),
            "date": hop["date"],
            "channel": hop["channel"],
            "risk_score": meta.get("risk_score", 0.0),
            "community_id": meta.get("community_id", "UNKNOWN"),
        })

def _add_path_to_graph(cy_nodes, cy_edges, path, trail_id, node_meta, node_roles):
    node_ids = set()
    for hop in path:
        node_ids.add(str(hop["from_account"]))
        node_ids.add(str(hop["to_account"]))
        
    for nid in node_ids:
        if nid not in cy_nodes:
            meta = node_meta.get(nid, {})
            ntype = node_roles.get(nid, "unknown")
            cy_nodes[nid] = {
                "data": {
                    "id": nid,
                    "label": meta.get("account_holder", meta.get("label", nid)),
                    "risk_score": meta.get("risk_score", 0.0),
                    "risk_level": meta.get("risk_tier", "LOW"),
                    "community_id": meta.get("community_id", "UNKNOWN"),
                    "type": ntype
                }
            }
            
    for idx, hop in enumerate(path):
        src = str(hop["from_account"])
        dst = str(hop["to_account"])
        edge_key = f"{src}->{dst}"
        
        if edge_key not in cy_edges:
            cy_edges[edge_key] = {
                "data": {
                    "id": f"mt_e_{src}_{dst}",
                    "source": src,
                    "target": dst,
                    "amount": round(hop["amount"], 2),
                    "date": hop["date"],
                    "channel": hop["channel"],
                    "trail_ids": [trail_id]
                }
            }
        else:
            existing_edge = cy_edges[edge_key]
            if trail_id not in existing_edge["data"]["trail_ids"]:
                existing_edge["data"]["trail_ids"].append(trail_id)
            existing_edge["data"]["amount"] = round(existing_edge["data"]["amount"] + hop["amount"], 2)

def compile_node_metadata(
    risk_df: pd.DataFrame,
    txn_graph: nx.MultiDiGraph,
    account_graph: nx.DiGraph,
    out_dir: str
) -> tuple[dict, dict]:
    from graph_builder import is_merchant
    
    node_meta = {}
    node_roles = {}
    
    for _, row in risk_df.iterrows():
        acc_id = str(row["account_id"]).strip()
        node_meta[acc_id] = {
            "id": acc_id,
            "label": row.get("account_holder", "") if pd.notna(row.get("account_holder", "")) and str(row.get("account_holder", "")).strip() else acc_id,
            "risk_score": float(row.get("risk_score", 0.0)),
            "risk_tier": row.get("risk_tier", "LOW"),
            "bank_name": row.get("bank_name", "UNKNOWN"),
            "account_holder": row.get("account_holder", "UNKNOWN"),
            "community_id": row.get("community_id", "UNKNOWN"),
            "is_internal": True
        }
        
    all_nodes = set(str(n).strip() for n in txn_graph.nodes)
    
    fan_in_accounts = set()
    smurfing_accounts = set()
    layering_accounts = set()
    
    fi_path = os.path.join(out_dir, "fan_in.csv")
    if os.path.exists(fi_path) and os.path.getsize(fi_path) > 10:
        try:
            fi_df = pd.read_csv(fi_path, dtype=str)
            fan_in_accounts.update(fi_df["collector"].dropna().unique())
        except Exception:
            pass
            
    sm_path = os.path.join(out_dir, "smurfing.csv")
    if os.path.exists(sm_path) and os.path.getsize(sm_path) > 10:
        try:
            sm_df = pd.read_csv(sm_path, dtype=str)
            smurfing_accounts.update(sm_df["account"].dropna().unique())
        except Exception:
            pass
            
    lay_path = os.path.join(out_dir, "layering_chains.csv")
    if os.path.exists(lay_path) and os.path.getsize(lay_path) > 10:
        try:
            lay_df = pd.read_csv(lay_path, dtype=str)
            for _, r in lay_df.iterrows():
                chain_str = r.get("chain")
                if chain_str and pd.notna(chain_str):
                    parts = [p.strip() for p in chain_str.split("→")]
                    layering_accounts.update(parts)
        except Exception:
            pass

    for node in all_nodes:
        is_internal = node in node_meta
        
        g_node = None
        if node in account_graph:
            g_node = node
        elif node.isdigit() and int(node) in account_graph:
            g_node = int(node)
            
        indegree = 0
        outdegree = 0
        if g_node is not None:
            indegree = account_graph.in_degree(g_node)
            outdegree = account_graph.out_degree(g_node)
            
        if is_merchant(node):
            role = "merchant"
        elif node in fan_in_accounts:
            role = "collector"
        elif node in smurfing_accounts or node in layering_accounts or (is_internal and indegree > 0 and outdegree > 0):
            role = "mule"
        elif not is_internal:
            # BUGFIX: an external node was previously always labelled
            # "victim" just for being non-internal. That mislabels every
            # external *recipient* of outgoing mule/layering transfers
            # (e.g. "GST", a bare beneficiary account number) as a fraud
            # victim, which is backwards — a victim is the source of
            # funds flowing INTO the network, not the final recipient of
            # funds flowing out. `outdegree` on account_graph for an
            # external node is only > 0 when it appears as the sender on
            # a credit edge (i.e. it actually sent money into an internal
            # account), so it's the correct signal for "true source".
            role = "victim" if outdegree > 0 else "unknown"
        else:
            role = "unknown"
            
        node_roles[node] = role
        
        if node not in node_meta:
            node_meta[node] = {
                "id": node,
                "label": node,
                "risk_score": 0.0,
                "risk_tier": "LOW",
                "bank_name": "UNKNOWN",
                "account_holder": node,
                "community_id": "UNKNOWN",
                "is_internal": is_internal
            }
            
    return node_meta, node_roles

def generate_investigator_ledger(
    account_id: str,
    df: pd.DataFrame,
    out_dir: str,
    risk_scores_dict: dict,
):
    try:
        acc_txns = _filter_account_rows(df, account_id).copy()
    except (ValueError, TypeError):
        return

    if acc_txns.empty:
        return

    acc_txns['_timestamp'] = acc_txns.apply(_parse_ts, axis=1)
    acc_txns = acc_txns.sort_values('_timestamp').reset_index(drop=True)

    credit_queue = []
    ledger_rows = []

    for idx, row in acc_txns.iterrows():
        debit_amt = float(row.get('debit', 0.0) or 0.0)
        credit_amt = float(row.get('credit', 0.0) or 0.0)
        date_str = str(row.get('date', ''))

        has_flags = False
        for flag_col in ['is_round_trip', 'is_layering', 'is_fan_in', 'is_fan_out', 'is_smurfing', 'is_odd_hour']:
            if flag_col in row and row[flag_col] in [True, 'True', '1', 1]:
                has_flags = True

        if credit_amt > 0:
            cp_acc = str(row.get('counterparty_account', '')).strip()
            cp_name = str(row.get('counterparty_name', '')).strip()
            source = cp_acc if cp_acc and cp_acc.lower() not in {'nan', 'none'} else cp_name
            if not source or source.lower() == 'nan':
                source = 'UNKNOWN'
                
            credit_queue.append({
                'date': date_str,
                'amount': credit_amt,
                'source': source,
                'remaining': credit_amt
            })

        if debit_amt > 0:
            cp_acc = str(row.get('counterparty_account', '')).strip()
            cp_name = str(row.get('counterparty_name', '')).strip()
            destination = cp_acc if cp_acc and cp_acc.lower() not in {'nan', 'none'} else cp_name
            if not destination or destination.lower() == 'nan':
                destination = 'UNKNOWN'

            dest_risk = risk_scores_dict.get(destination, 'LOW')
            risk_flag = 'SUSPICIOUS' if (dest_risk in ['HIGH', 'CRITICAL'] or has_flags) else 'NORMAL'

            remaining_debit = debit_amt
            while remaining_debit > 0 and credit_queue:
                oldest_credit = credit_queue[0]
                allocated = min(remaining_debit, oldest_credit['remaining'])

                oldest_credit['remaining'] -= allocated
                remaining_debit -= allocated

                ledger_rows.append({
                    'credit_date': oldest_credit['date'],
                    'credit_amount': oldest_credit['amount'],
                    'credit_source': oldest_credit['source'],
                    'debit_date': date_str,
                    'debit_amount': debit_amt,
                    'debit_destination': destination,
                    'allocation_amount': allocated,
                    'remaining_credit': oldest_credit['remaining'],
                    'risk_flag': risk_flag
                })

                if oldest_credit['remaining'] <= 0:
                    credit_queue.pop(0)

            if remaining_debit > 0:
                ledger_rows.append({
                    'credit_date': 'PRIOR_BALANCE',
                    'credit_amount': 0.0,
                    'credit_source': 'PRIOR_BALANCE',
                    'debit_date': date_str,
                    'debit_amount': debit_amt,
                    'debit_destination': destination,
                    'allocation_amount': remaining_debit,
                    'remaining_credit': 0.0,
                    'risk_flag': risk_flag
                })

    ledger_path = os.path.join(out_dir, f"ledger_{account_id}.csv")
    if ledger_rows:
        pd.DataFrame(ledger_rows).to_csv(ledger_path, index=False)
        print(f"[FIFO Ledger] Exported {len(ledger_rows)} records for account {account_id} -> {ledger_path}")
    else:
        cols = ['credit_date', 'credit_amount', 'credit_source', 'debit_date', 'debit_amount', 'debit_destination', 'allocation_amount', 'remaining_credit', 'risk_flag']
        pd.DataFrame(columns=cols).to_csv(ledger_path, index=False)

def generate_money_trail_outputs(
    out_dir: str,
    df: pd.DataFrame,
    risk_df: pd.DataFrame,
    txn_graph: nx.MultiDiGraph,
    account_graph: nx.DiGraph,
    top_trails: int
):
    print("\n  [+] Generating Money Trail & Fund Tracing outputs ...")
    
    node_meta, node_roles = compile_node_metadata(risk_df, txn_graph, account_graph, out_dir)
    
    top_accounts = []
    if not risk_df.empty:
        top_accounts = risk_df.head(top_trails)["account_id"].tolist()
        
    risk_scores_dict = {str(row["account_id"]): str(row["risk_tier"]) for _, row in risk_df.iterrows()}
    
    for acc in top_accounts:
        generate_investigator_ledger(str(acc), df, out_dir, risk_scores_dict)
        
    laundering_chains, destination_accounts, cy_graph = trace_all_money_trails(
        df, top_accounts, risk_scores_dict, node_meta, node_roles
    )
    
    chains_df = pd.DataFrame(laundering_chains)
    chains_csv_path = os.path.join(out_dir, "laundering_chains.csv")
    chains_df.to_csv(chains_csv_path, index=False)
    print(f"        Saved laundering chains ({len(laundering_chains)} hops) → {chains_csv_path}")
    
    dests_df = pd.DataFrame(destination_accounts)
    dests_csv_path = os.path.join(out_dir, "destination_accounts.csv")
    dests_df.to_csv(dests_csv_path, index=False)
    print(f"        Saved destination accounts ({len(destination_accounts)} records) → {dests_csv_path}")
    
    graph_json_path = os.path.join(out_dir, "money_trail_graph.json")
    with open(graph_json_path, "w") as f:
        json.dump(cy_graph, f, indent=2)
    print(f"        Saved money trail graph JSON → {graph_json_path}")
