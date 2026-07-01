"""
Phase 8 — Beneficiary Analysis & Risk Scorer

Risk is an investigator-priority score, not a fraud verdict. It combines
Phase 8 pattern intensity, Phase 7 data/behaviour flags, beneficiary novelty,
and NetworkX graph metrics. Amount-dependent features are ratio/rank based.
"""

from __future__ import annotations
from collections import Counter, defaultdict

import networkx as nx
import numpy as np
import pandas as pd

from analytics_config import (
    RISK_WEIGHTS, RISK_TIERS,
    BENE_HIGH_VALUE_ZSCORE, BENE_NEW_HIGH_VALUE_RATIO,
    ODD_HOUR_START, ODD_HOUR_END,
    RISK_TIER_FALLBACK_ENABLED, RISK_TIER_FALLBACK_HIGH, RISK_TIER_FALLBACK_CRITICAL,
)


def analyse_beneficiaries(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    acct_stats = _compute_account_stats(df)
    debit_df = df[df["debit"] > 0].copy()
    debit_df["_date_parsed"] = pd.to_datetime(debit_df["date"], errors="coerce")
    debit_df["_counterparty"] = debit_df.apply(_counterparty_identity, axis=1)
    debit_df = debit_df[debit_df["_counterparty"] != ""]

    for (acc_id, cp), group in debit_df.groupby(["account_id", "_counterparty"], sort=False):
        total_sent = group["debit"].sum()
        txn_count = len(group)
        max_single = group["debit"].max()
        first_date = group["_date_parsed"].min()
        last_date = group["_date_parsed"].max()
        is_new = txn_count == 1
        st = acct_stats.get(acc_id, {})
        mean, std, acct_max = st.get("debit_mean", 0), st.get("debit_std", 1), st.get("debit_max", 1)
        zscore = (max_single - mean) / std if std > 0 else 0
        new_hv = is_new and acct_max > 0 and (max_single / acct_max) >= BENE_NEW_HIGH_VALUE_RATIO
        records.append({
            "account_id": acc_id,
            "counterparty_name": cp,
            "total_sent": round(float(total_sent), 2),
            "sent_share_of_account": round(float(total_sent / max(st.get("total_debit", total_sent), 1)), 4),
            "txn_count": int(txn_count),
            "max_single_txn": round(float(max_single), 2),
            "first_date": "" if pd.isna(first_date) else str(first_date.date()),
            "last_date": "" if pd.isna(last_date) else str(last_date.date()),
            "amount_zscore": round(float(zscore), 3),
            "is_new_high_value_bene": bool(new_hv),
            "is_high_value_bene": bool(zscore >= BENE_HIGH_VALUE_ZSCORE),
        })

    cols = ["account_id", "counterparty_name", "total_sent", "sent_share_of_account", "txn_count",
            "max_single_txn", "first_date", "last_date", "amount_zscore",
            "is_new_high_value_bene", "is_high_value_bene"]
    bene_df = pd.DataFrame(records, columns=cols)
    if bene_df.empty:
        return bene_df
    return bene_df.sort_values(["account_id", "total_sent"], ascending=[True, False]).reset_index(drop=True)


def compute_graph_metrics(account_graph: nx.DiGraph, internal_accounts: set[str]) -> pd.DataFrame:
    if account_graph is None or account_graph.number_of_nodes() == 0:
        return pd.DataFrame(columns=["account_id", "pagerank", "betweenness", "in_degree", "out_degree", "degree_centrality", "graph_risk_score"])
    pr = _weighted_pagerank(account_graph) if account_graph.number_of_edges() else {}
    if account_graph.number_of_nodes() > 2 and account_graph.number_of_edges() > 0:
        sample_k = min(200, account_graph.number_of_nodes())
        btw = nx.betweenness_centrality(account_graph, k=sample_k, weight=None, normalized=True, seed=42)
    else:
        btw = {}
    deg = nx.degree_centrality(account_graph) if account_graph.number_of_nodes() > 1 else {}
    rows = []
    raw_scores = []
    for acc in internal_accounts:
        in_deg = account_graph.in_degree(acc) if acc in account_graph else 0
        out_deg = account_graph.out_degree(acc) if acc in account_graph else 0
        score_raw = pr.get(acc, 0) + btw.get(acc, 0) + deg.get(acc, 0)
        raw_scores.append(score_raw)
        rows.append({
            "account_id": acc,
            "pagerank": float(pr.get(acc, 0)),
            "betweenness": float(btw.get(acc, 0)),
            "in_degree": int(in_deg),
            "out_degree": int(out_deg),
            "degree_centrality": float(deg.get(acc, 0)),
            "_graph_raw": float(score_raw),
        })
    if not rows:
        return pd.DataFrame()
    max_raw = max(raw_scores) if raw_scores else 0
    for r in rows:
        r["graph_risk_score"] = round((r.pop("_graph_raw") / max_raw) if max_raw > 0 else 0.0, 4)
        r["pagerank"] = round(r["pagerank"], 6)
        r["betweenness"] = round(r["betweenness"], 6)
        r["degree_centrality"] = round(r["degree_centrality"], 6)
    return pd.DataFrame(rows)


def compute_risk_scores(
    df: pd.DataFrame,
    round_trips: list[dict],
    layering: list[dict],
    fan_in: list[dict],
    fan_out: list[dict],
    smurfing: list[dict],
    odd_hours: list[dict],
    bene_df: pd.DataFrame,
    account_graph: nx.DiGraph | None = None,
    member_map: dict[str, int] | None = None,
) -> pd.DataFrame:
    accounts = df[["account_id", "account_holder", "bank_name"]].drop_duplicates("account_id").set_index("account_id")
    internal_accounts = set(accounts.index.astype(str))
    graph_df = compute_graph_metrics(account_graph, internal_accounts).set_index("account_id") if account_graph is not None else pd.DataFrame()

    rt_counts = Counter([f["account_a"] for f in round_trips] + [f["account_b"] for f in round_trips])
    lay_counts = Counter(acc for f in layering for acc in f.get("accounts", []))
    fi_counts = Counter(f["collector"] for f in fan_in)
    fo_counts = Counter(f["distributor"] for f in fan_out)
    sm_counts = Counter(f["account"] for f in smurfing)
    oh_counts = Counter(f["account"] for f in odd_hours)

    nhv_accts = set()
    if not bene_df.empty and "is_new_high_value_bene" in bene_df.columns:
        nhv_accts = set(bene_df[bene_df["is_new_high_value_bene"] == True]["account_id"])

    rows = []
    for acc_id, meta in accounts.iterrows():
        group = df[df["account_id"] == acc_id]
        n = max(len(group), 1)
        intensities = {
            "round_trip": _count_score(rt_counts[acc_id]),
            "layering": _count_score(lay_counts[acc_id]),
            "fan_in": _count_score(fi_counts[acc_id]),
            "fan_out": _count_score(fo_counts[acc_id]),
            "smurfing": _count_score(sm_counts[acc_id]),
            "odd_hour": _count_score(oh_counts[acc_id]),
            "velocity": _rate_score(group.get("is_velocity_flag", pd.Series(False, index=group.index))),
            "high_value": _rate_score(group.get("is_high_value_flag", pd.Series(False, index=group.index))),
            "balance_breach": _rate_score(group.get("is_balance_breach", pd.Series(False, index=group.index))),
            "new_hv_bene": 1.0 if acc_id in nhv_accts else 0.0,
            "graph": float(graph_df.loc[acc_id, "graph_risk_score"]) if not graph_df.empty and acc_id in graph_df.index else 0.0,
        }
        raw_score = sum(RISK_WEIGHTS.get(k, 0) * v for k, v in intensities.items()) * 100
        score = min(round(raw_score, 1), 100.0)
        tier = _score_to_tier(score)
        flags = {k: v > 0 for k, v in intensities.items() if k != "graph"}
        active = [k.upper() for k, v in flags.items() if v]
        if intensities["graph"] >= 0.65:
            active.append("GRAPH_CENTRAL")
        gvals = graph_df.loc[acc_id].to_dict() if not graph_df.empty and acc_id in graph_df.index else {}
        rows.append({
            "account_id": acc_id,
            "account_holder": meta.get("account_holder", ""),
            "bank_name": meta.get("bank_name", ""),
            "community_id": member_map.get(acc_id) if member_map else None,
            "risk_score": score,
            "risk_tier": tier,
            "flag_round_trip": flags["round_trip"],
            "flag_layering": flags["layering"],
            "flag_fan_in": flags["fan_in"],
            "flag_fan_out": flags["fan_out"],
            "flag_smurfing": flags["smurfing"],
            "flag_odd_hour": flags["odd_hour"],
            "flag_velocity": flags["velocity"],
            "flag_high_value": flags["high_value"],
            "flag_balance_breach": flags["balance_breach"],
            "flag_new_hv_bene": flags["new_hv_bene"],
            "pagerank": gvals.get("pagerank", 0.0),
            "betweenness": gvals.get("betweenness", 0.0),
            "in_degree": gvals.get("in_degree", 0),
            "out_degree": gvals.get("out_degree", 0),
            "degree_centrality": gvals.get("degree_centrality", 0.0),
            "graph_risk_score": gvals.get("graph_risk_score", 0.0),
            "active_patterns": " | ".join(active) if active else "NONE",
            "risk_reasoning": _build_reasoning(acc_id, flags, intensities, round_trips, layering, fan_in, fan_out, smurfing, odd_hours),
        })
    risk_df = pd.DataFrame(rows).sort_values(["risk_score", "graph_risk_score"], ascending=False).reset_index(drop=True)
    if RISK_TIER_FALLBACK_ENABLED and not risk_df["risk_tier"].isin(["HIGH", "CRITICAL"]).any():
        risk_df["risk_tier"] = risk_df["risk_score"].apply(_fallback_tier)
    return risk_df


def _score_to_tier(score: float) -> str:
    return next(t for t, threshold in sorted(RISK_TIERS.items(), key=lambda x: -x[1]) if score >= threshold)


def _fallback_tier(score: float) -> str:
    if score >= RISK_TIER_FALLBACK_CRITICAL:
        return "CRITICAL"
    if score >= RISK_TIER_FALLBACK_HIGH:
        return "HIGH"
    return _score_to_tier(score)


def _weighted_pagerank(graph: nx.DiGraph, damping: float = 0.85, max_iter: int = 100, tol: float = 1e-9) -> dict:
    nodes = list(graph.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    idx = {node: i for i, node in enumerate(nodes)}
    rank = np.full(n, 1.0 / n)
    out_weight = np.zeros(n)
    incoming = [[] for _ in range(n)]
    for u, v, data in graph.edges(data=True):
        w = float(data.get("total_amount", 1.0) or 1.0)
        ui, vi = idx[u], idx[v]
        out_weight[ui] += w
        incoming[vi].append((ui, w))
    teleport = (1.0 - damping) / n
    for _ in range(max_iter):
        new_rank = np.full(n, teleport)
        dangling = rank[out_weight == 0].sum()
        if dangling:
            new_rank += damping * dangling / n
        for vi, inc in enumerate(incoming):
            acc = 0.0
            for ui, w in inc:
                if out_weight[ui] > 0:
                    acc += rank[ui] * (w / out_weight[ui])
            new_rank[vi] += damping * acc
        if np.abs(new_rank - rank).sum() < tol:
            rank = new_rank
            break
        rank = new_rank
    return {node: float(rank[idx[node]]) for node in nodes}


def _compute_account_stats(df: pd.DataFrame) -> dict:
    stats = {}
    for acc_id, group in df[df["debit"] > 0].groupby("account_id"):
        debits = group["debit"].astype(float).values
        stats[acc_id] = {
            "debit_mean": float(np.mean(debits)),
            "debit_std": float(np.std(debits)) if len(debits) > 1 else 1.0,
            "debit_max": float(np.max(debits)),
            "total_debit": float(np.sum(debits)),
        }
    return stats


def _counterparty_identity(row) -> str:
    for col in ("counterparty_account", "counterparty_name"):
        val = row.get(col, "")
        if pd.isna(val):
            continue
        s = str(val).strip()
        if s and s.lower() not in {"nan", "none", "null"}:
            return " ".join(s.upper().split())
    return ""


def _rate_score(series: pd.Series) -> float:
    vals = series.fillna(False).astype(bool)
    if len(vals) == 0:
        return 0.0
    # sqrt prevents a very large account from dominating only because it has many rows.
    return min(float(np.sqrt(vals.mean())), 1.0)


def _count_score(count: int) -> float:
    return min(np.log1p(max(count, 0)) / np.log1p(5), 1.0)


def _build_reasoning(acc_id, flags, intensities, round_trips, layering, fan_in, fan_out, smurfing, odd_hours) -> str:
    parts = []
    if flags["round_trip"]:
        parts.append(f"Round-trip indicators present ({intensities['round_trip']:.2f} intensity)")
    if flags["layering"]:
        parts.append("Participates in temporal internal transfer chain")
    if flags["fan_in"]:
        hit = next((f for f in fan_in if f["collector"] == acc_id), None)
        parts.append(f"Collector behaviour: {hit.get('sender_count')} senders" if hit else "Collector behaviour")
    if flags["fan_out"]:
        hit = next((f for f in fan_out if f["distributor"] == acc_id), None)
        parts.append(f"Distributor behaviour: {hit.get('receiver_count')} receivers" if hit else "Distributor behaviour")
    if flags["smurfing"]:
        parts.append("Structured similarly sized transfers relative to own activity")
    if flags["odd_hour"]:
        parts.append(f"Real timestamped odd-hour activity between {ODD_HOUR_START:02d}:00-{ODD_HOUR_END:02d}:00")
    if flags["velocity"]:
        parts.append("Velocity bursts from Phase 7")
    if flags["high_value"]:
        parts.append("Per-account high-value outliers from Phase 7")
    if flags["balance_breach"]:
        parts.append("Statement balance continuity issues")
    if flags["new_hv_bene"]:
        parts.append("New high-value beneficiary")
    if intensities.get("graph", 0) >= 0.65:
        parts.append("Graph-central account by PageRank/betweenness/degree")
    return " | ".join(parts) if parts else "No material analytics patterns detected"
