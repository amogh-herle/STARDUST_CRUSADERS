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
