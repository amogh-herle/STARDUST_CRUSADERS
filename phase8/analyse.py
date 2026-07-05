"""
Phase 8 — Investigation Analytics Engine
Main orchestrator

Input : cleaned_transactions.csv  (Phase 7 output)
Output directory contains:

  analytics_transactions.csv     ← original rows + all Phase 8 flags
  round_trips.csv                ← every detected direct (2-hop) round-trip
  round_trip_cycles.csv          ← every detected multi-hop (3+ hop) round-trip cycle
  layering_chains.csv            ← every detected layering chain
  fan_in.csv                     ← collector account findings
  fan_out.csv                    ← distribution account findings
  smurfing.csv                   ← structuring findings
  odd_hours.csv                  ← odd-hour activity findings
  beneficiaries.csv              ← per-account beneficiary profile table
  risk_scores.csv                ← per-account risk score + tier + reasoning
  money_trails/                  ← forward/backward trails for top-risk accounts
      trail_<ACC>_forward.csv
      trail_<ACC>_backward.csv
  graph_summary.json             ← graph statistics
  analytics_report.json          ← full machine-readable findings summary
  analytics_summary.txt          ← human-readable narrative for investigators

Usage:
    python analyse.py --input ../phase7/cleaned/cleaned_transactions.csv
                      --out-dir analytics/
    python analyse.py --input cleaned_transactions.csv --out-dir analytics/ --top-trails 10
"""

import os
import json
import argparse
import pandas as pd
import networkx as nx
from datetime import datetime
from pathlib import Path

from graph_builder    import build_graphs, graph_summary
from pattern_detectors import (
    detect_round_trips, detect_round_trip_cycles, detect_layering,
    detect_fan_in, detect_fan_out,
    detect_smurfing, detect_odd_hours,
)
from community import detect_communities, detect_scc_cycles, compute_community_risk
from aml_inference import get_account_isolation_scores
from money_trail      import trace_forward, trace_backward, trace_forward_fifo, generate_investigator_ledger
from reporting       import generate_investigator_report
from risk_scorer      import analyse_beneficiaries, compute_risk_scores, compute_graph_metrics
from analytics_config import ANALYTICS_FLAG_COLS


def run_analytics(
    input_path:  str,
    out_dir:     str,
    top_trails:  int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Full Phase 8 pipeline.
    Returns (analytics_df, risk_df).
    """
    os.makedirs(out_dir, exist_ok=True)
    trails_dir = os.path.join(out_dir, "money_trails")
    os.makedirs(trails_dir, exist_ok=True)
    for stale in Path(trails_dir).glob("trail_*.csv"):
        stale.unlink()

    print(f"\n{'='*65}")
    print("  Phase 8 — Investigation Analytics Engine")
    print(f"{'='*65}")

    # ── Load Phase 7 output ─────────────────────────────────────────────
    df = pd.read_csv(input_path, dtype=str)
    for col in ("debit", "credit", "balance"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    bool_cols = [
        "is_duplicate", "is_balance_breach", "is_high_value_flag", "is_ocr_row",
        "is_velocity_flag", "is_utr_collision", "is_self_transfer", "is_malformed_ifsc",
    ]
    for col in bool_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin(["true", "1", "yes"])
        else:
            df[col] = False

    print(f"\n  Loaded : {len(df):,} rows | {df['account_id'].nunique()} accounts")

    report = {
        "run_timestamp": datetime.now().isoformat(),
        "input_file":    os.path.basename(input_path),
        "rows_input":    len(df),
        "accounts":      int(df["account_id"].nunique()),
    }

    # Initialise Phase 8 flag columns
    df["is_round_trip"]    = False
    df["is_round_trip_cycle"] = False
    df["is_layering"]      = False
    df["is_fan_in"]        = False
    df["is_fan_out"]       = False
    df["is_smurfing"]      = False
    df["is_odd_hour"]      = False
    df["analytics_flags"]  = ""

    # ── Step 1: Build transaction graph ────────────────────────────────
    print("\n  [1/8] Building transaction graph ...")
    txn_graph, account_graph = build_graphs(df)
    g_summary = graph_summary(txn_graph, account_graph)
    report["graph"] = g_summary
    print(f"        Nodes : {g_summary['txn_graph_nodes']:,}  |  "
          f"Edges : {g_summary['txn_graph_edges']:,}  |  "
          f"Components : {g_summary['weakly_connected_comps']}")

    # Community detection (Louvain preferred; falls back to greedy modularity)
    print("\n  [1.1] Detecting communities and SCC cycles ...")
    member_map, community_summaries = detect_communities(account_graph)
    scc_cycles = detect_scc_cycles(account_graph)
    report["communities"] = len(community_summaries)
    report["scc_components_with_cycles"] = len([s for s in scc_cycles if s.get("n_cycles_found", 0) > 0])
    # save community membership file
    try:
        import pandas as _pd
        cm_rows = [{"account_id": a, "community_id": c} for a, c in member_map.items()]
        if cm_rows:
            _pd.DataFrame(cm_rows).to_csv(os.path.join(out_dir, "communities.csv"), index=False)
        _pd.DataFrame(community_summaries).to_csv(os.path.join(out_dir, "community_summaries.csv"), index=False)
        _pd.DataFrame(scc_cycles).to_csv(os.path.join(out_dir, "scc_cycles.csv"), index=False)
    except Exception:
        pass

    # ── Step 2: Round-trip detection ───────────────────────────────────
    print("\n  [2/8] Round-trip detection ...")
    rt_findings, rt_idx = detect_round_trips(df, txn_graph)
    df.loc[list(rt_idx), "is_round_trip"] = True
    _append_flag(df, list(rt_idx), "ROUND_TRIP")
    report["round_trips"] = len(rt_findings)
    print(f"        Found : {len(rt_findings)} round-trips "
          f"({len(rt_idx)} transactions flagged)")

    # ── Step 2b: Multi-hop round-trip cycle detection (3+ hops) ────────
    print("\n  [2b/8] Multi-hop round-trip cycle detection (A->B->C->...->A) ...")
    rtc_findings, rtc_idx = detect_round_trip_cycles(df, txn_graph)
    df.loc[list(rtc_idx), "is_round_trip_cycle"] = True
    _append_flag(df, list(rtc_idx), "ROUND_TRIP_CYCLE")
    report["round_trip_cycles"] = len(rtc_findings)
    print(f"        Found : {len(rtc_findings)} multi-hop round-trip cycles "
          f"({len(rtc_idx)} transactions flagged)")

    # ── Step 3: Layering detection ─────────────────────────────────────
    print("\n  [3/8] Layering chain detection ...")
    lay_findings, lay_idx = detect_layering(df, txn_graph)
    df.loc[list(lay_idx), "is_layering"] = True
    _append_flag(df, list(lay_idx), "LAYERING")
    report["layering_chains"] = len(lay_findings)
    print(f"        Found : {len(lay_findings)} layering chains "
          f"({len(lay_idx)} transactions flagged)")
    for f in lay_findings:
        print(f"          {f['chain']}  [{f['severity']}]")

    # ── Step 4: Fan-in detection ───────────────────────────────────────
    print("\n  [4/8] Fan-in (collector) detection ...")
    fi_findings, fi_idx = detect_fan_in(df)
    df.loc[list(fi_idx), "is_fan_in"] = True
    _append_flag(df, list(fi_idx), "FAN_IN")
    report["fan_in"] = len(fi_findings)
    print(f"        Found : {len(fi_findings)} collector accounts "
          f"({len(fi_idx)} transactions flagged)")

    # ── Step 5: Fan-out detection ──────────────────────────────────────
    print("\n  [5/8] Fan-out (distribution) detection ...")
    fo_findings, fo_idx = detect_fan_out(df)
    df.loc[list(fo_idx), "is_fan_out"] = True
    _append_flag(df, list(fo_idx), "FAN_OUT")
    report["fan_out"] = len(fo_findings)
    print(f"        Found : {len(fo_findings)} distribution accounts "
          f"({len(fo_idx)} transactions flagged)")

    # ── Step 6: Smurfing detection ─────────────────────────────────────
    print("\n  [6/8] Smurfing / structuring detection ...")
    sm_findings, sm_idx = detect_smurfing(df)
    df.loc[list(sm_idx), "is_smurfing"] = True
    _append_flag(df, list(sm_idx), "SMURFING")
    report["smurfing"] = len(sm_findings)
    print(f"        Found : {len(sm_findings)} smurfing patterns "
          f"({len(sm_idx)} transactions flagged)")

    # ── Step 7: Odd-hour detection ─────────────────────────────────────
    print("\n  [7/8] Odd-hour activity detection ...")
    oh_findings, oh_idx = detect_odd_hours(df)
    df.loc[list(oh_idx), "is_odd_hour"] = True
    _append_flag(df, list(oh_idx), "ODD_HOUR")
    report["odd_hours"] = len(oh_findings)
    print(f"        Found : {len(oh_findings)} accounts with odd-hour activity "
          f"({len(oh_idx)} transactions flagged)")

    # ── Step 8: Beneficiary analysis + Risk scoring ────────────────────
    print("\n  [8/8] Beneficiary analysis + Risk scoring ...")
    bene_df = analyse_beneficiaries(df)
    # Optional: run AML isolation forest inference from aml_model if available
    try:
        isolation_scores = get_account_isolation_scores(df)
    except Exception:
        isolation_scores = {}

    risk_df = compute_risk_scores(
        df, rt_findings, lay_findings, fi_findings,
        fo_findings, sm_findings, oh_findings, bene_df, account_graph,
        member_map,
    )
    # attach isolation scores to risk_df if present
    if isolation_scores:
        risk_df["isolation_mean_score"] = risk_df["account_id"].map(lambda a: isolation_scores.get(str(a), {}).get("mean_score", 0.0))
        risk_df["isolation_max_score"] = risk_df["account_id"].map(lambda a: isolation_scores.get(str(a), {}).get("max_score", 0.0))
    graph_metrics_df = compute_graph_metrics(account_graph, set(df["account_id"].astype(str).unique()))
    report["accounts_scored"] = len(risk_df)
    report["critical_accounts"] = int((risk_df["risk_tier"] == "CRITICAL").sum())
    report["high_accounts"]     = int((risk_df["risk_tier"] == "HIGH").sum())
    report["medium_accounts"]   = int((risk_df["risk_tier"] == "MEDIUM").sum())

    print(f"        Accounts scored : {len(risk_df)}")
    print(f"        CRITICAL        : {report['critical_accounts']}")
    print(f"        HIGH            : {report['high_accounts']}")
    print(f"        MEDIUM          : {report['medium_accounts']}")

    # ── Money trails for top-N highest-risk accounts ───────────────────
    print(f"\n  [+] Tracing money trails for top {top_trails} accounts ...")
    top_accounts = risk_df.head(top_trails)["account_id"].tolist()
    trail_manifest = []

    # Create risk scores dictionary for ledger generation
    risk_scores_dict = {}
    if not risk_df.empty:
        for _, r in risk_df.iterrows():
            risk_scores_dict[str(r["account_id"])] = str(r["risk_tier"])

    all_fwd_trails = []

    for acc_id in top_accounts:
        # Generate investigator ledger
        generate_investigator_ledger(str(acc_id), df, out_dir, risk_scores_dict)

        # Forward (graph-based, requires Change 1 fix)
        fwd_trails = trace_forward(acc_id, txn_graph, df)
        all_fwd_trails.extend(fwd_trails)
        fwd_rows   = [r for t in fwd_trails for r in t.to_records()]
        fwd_path   = os.path.join(trails_dir, f"trail_{acc_id}_forward.csv")
        pd.DataFrame(fwd_rows, columns=_trail_cols()).to_csv(fwd_path, index=False)
        trail_manifest.append({
            "account":   acc_id,
            "direction": "forward",
            "hops":      len(fwd_rows),
            "trails":    len(fwd_trails),
            "status":    "ok" if fwd_rows else "no_hops",
            "file":      os.path.basename(fwd_path),
        })

        # Forward FIFO (balance-based, hackathon-compliant)
        fifo_trails = trace_forward_fifo(str(acc_id), df)
        print(f"            DEBUG FIFO {acc_id}: df.account_id.dtype={df['account_id'].dtype}, matches={len(df[df['account_id'] == acc_id])}, trails={len(fifo_trails)}")
        fifo_rows   = [r for t in fifo_trails for r in t.to_records()]
        print(f"            DEBUG FIFO {acc_id}: {len(fifo_trails)} trails, {len(fifo_rows)} rows")
        fifo_path   = os.path.join(trails_dir, f"trail_{acc_id}_fifo.csv")
        pd.DataFrame(fifo_rows, columns=_trail_cols()).to_csv(fifo_path, index=False)
        trail_manifest.append({
            "account":   acc_id,
            "direction": "forward_fifo",
            "hops":      len(fifo_rows),
            "trails":    len(fifo_trails),
            "status":    "ok" if fifo_rows else "no_credits",
            "file":      os.path.basename(fifo_path),
        })

        # Backward
        bwd_trails = trace_backward(acc_id, txn_graph, df)
        bwd_rows   = [r for t in bwd_trails for r in t.to_records()]
        bwd_path   = os.path.join(trails_dir, f"trail_{acc_id}_backward.csv")
        pd.DataFrame(bwd_rows, columns=_trail_cols()).to_csv(bwd_path, index=False)
        trail_manifest.append({
            "account":   acc_id,
            "direction": "backward",
            "hops":      len(bwd_rows),
            "trails":    len(bwd_trails),
            "status":    "ok" if bwd_rows else "no_hops",
            "file":      os.path.basename(bwd_path),
        })

    report["trail_manifest"] = trail_manifest

    # Export investigator-grade money trail outputs
    try:
        from money_trail import generate_money_trail_outputs
        generate_money_trail_outputs(out_dir, df, risk_df, txn_graph, account_graph, top_trails)
    except Exception as exc:
        print(f"        Money trail output generation failed: {exc}")

    # Compute community-level risk after account scoring
    try:
        comm_risk = compute_community_risk(account_graph, member_map, risk_df)
        import pandas as _pd
        if comm_risk:
            _pd.DataFrame(comm_risk).to_csv(os.path.join(out_dir, "community_risk.csv"), index=False)
        report["communities_risk_count"] = len(comm_risk)
    except Exception:
        comm_risk = []
        report["communities_risk_count"] = 0

    try:
        generate_investigator_report(out_dir, report, risk_df, community_summaries, comm_risk)
    except Exception as exc:
        print(f"        Investigator report generation failed: {exc}")

    # ── Export all outputs ─────────────────────────────────────────────
    print("\n  Exporting outputs ...")

    # 0. Export account_graph for Phase 9 (community detection, PageRank, centrality)
    import pickle
    graph_pkl_path = os.path.join(out_dir, "account_graph.pkl")
    with open(graph_pkl_path, "wb") as f:
        pickle.dump(account_graph, f)
    print(f"        account_graph.pkl saved → Phase 9 ready")

    try:
        graph_gexf_path = os.path.join(out_dir, "account_graph.gexf")
        nx.write_gexf(account_graph, graph_gexf_path)
        print(f"        account_graph.gexf saved → visualization/export")
    except Exception:
        pass

    # 1. analytics_transactions.csv — all rows + Phase 8 flags
    out_cols = [c for c in ANALYTICS_FLAG_COLS if c in df.columns]
    df[out_cols].to_csv(
        os.path.join(out_dir, "analytics_transactions.csv"), index=False
    )

    # 2. Per-pattern finding files
    _save(rt_findings,  os.path.join(out_dir, "round_trips.csv"))
    _save(rtc_findings, os.path.join(out_dir, "round_trip_cycles.csv"))
    _save(lay_findings, os.path.join(out_dir, "layering_chains.csv"))
    _save(fi_findings,  os.path.join(out_dir, "fan_in.csv"))
    _save(fo_findings,  os.path.join(out_dir, "fan_out.csv"))
    _save(sm_findings,  os.path.join(out_dir, "smurfing.csv"))
    _save(oh_findings,  os.path.join(out_dir, "odd_hours.csv"))

    # 3. Beneficiary profiles
    bene_df.to_csv(os.path.join(out_dir, "beneficiaries.csv"), index=False)

    # 4. Risk scores and graph metrics
    risk_df.to_csv(os.path.join(out_dir, "risk_scores.csv"), index=False)
    graph_metrics_df.to_csv(os.path.join(out_dir, "graph_metrics.csv"), index=False)

    # 4.1 Build Suspicious Network Graph (Cytoscape JSON)
    try:
        from suspicious_network import build_suspicious_network
        build_suspicious_network(out_dir, txn_graph, account_graph, df, risk_df)
    except Exception as exc:
        print(f"        Suspicious network graph build failed: {exc}")

    # 4.2 Build Relationship Network (CSV, JSON, summaries)
    try:
        from relationship_engine import analyse_relationships
        analyse_relationships(out_dir, df, txn_graph, account_graph, risk_df)
    except Exception as exc:
        print(f"        Relationship network build failed: {exc}")

    # 5. Graph summary
    with open(os.path.join(out_dir, "graph_summary.json"), "w") as f:
        json.dump(g_summary, f, indent=2)

    # 6. Full analytics report
    report["rows_output"] = len(df)
    report["total_flagged_rows"] = int(
        df[["is_round_trip", "is_round_trip_cycle", "is_layering", "is_fan_in",
            "is_fan_out", "is_smurfing", "is_odd_hour"]].any(axis=1).sum()
    )
    with open(os.path.join(out_dir, "analytics_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)

    # 7. Human-readable summary
    _write_summary(report, risk_df, rt_findings, rtc_findings, lay_findings,
                   fi_findings, fo_findings, sm_findings, oh_findings, out_dir)

    _print_final_summary(report, out_dir)
    return df, risk_df, account_graph, txn_graph


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _append_flag(df: pd.DataFrame, idxs: list, flag: str):
    """Append flag text to the analytics_flags column for flagged rows."""
    if not idxs:
        return
    idxs = list(dict.fromkeys(idxs))
    current = df.loc[idxs, "analytics_flags"].fillna("").astype(str)
    df.loc[idxs, "analytics_flags"] = current.apply(
        lambda x: f"{x} | {flag}".strip(" | ") if x and x.lower() != "nan" else flag
    )


def _save(findings: list, path: str):
    cols = sorted({k for f in findings for k in f.keys()}) if findings else ["pattern"]
    pd.DataFrame(findings, columns=cols).to_csv(path, index=False)


def _trail_cols() -> list[str]:
    return [
        "root_account", "direction", "seed_amount", "terminal", "terminal_type",
        "hop_number", "from_account", "to_account", "amount", "date", "timestamp",
        "utr_ref", "narration", "channel", "match_ratio", "cumulative_loss",
    ]


def _write_summary(
    report, risk_df,
    rt, rtc, lay, fi, fo, sm, oh,
    out_dir,
):
    lines = [
        "=" * 70,
        "PHASE 8 — INVESTIGATION ANALYTICS SUMMARY",
        f"Run at : {report['run_timestamp']}",
        f"Input  : {report['input_file']}",
        "=" * 70,
        "",
        "OVERVIEW",
        "-" * 40,
        f"  Rows analysed           : {report['rows_input']:,}",
        f"  Accounts analysed       : {report['accounts']}",
        f"  Rows with analytics flag: {report.get('total_flagged_rows', 0):,}",
        "",
        "GRAPH STATISTICS",
        "-" * 40,
        f"  Graph nodes             : {report['graph']['txn_graph_nodes']}",
        f"  Graph edges (transfers) : {report['graph']['txn_graph_edges']}",
        f"  Connected components    : {report['graph']['weakly_connected_comps']}",
        "",
        "PATTERN DETECTION RESULTS",
        "-" * 40,
        f"  Round-trips (2-hop)     : {len(rt)}",
        f"  Round-trip cycles (3+ hop): {len(rtc)}",
        f"  Layering chains         : {len(lay)}",
        f"  Fan-in collectors       : {len(fi)}",
        f"  Fan-out distributors    : {len(fo)}",
        f"  Smurfing / structuring  : {len(sm)}",
        f"  Odd-hour accounts       : {len(oh)}",
        "",
        "RISK SCORE DISTRIBUTION",
        "-" * 40,
        f"  CRITICAL (≥75)          : {report['critical_accounts']}",
        f"  HIGH     (≥50)          : {report['high_accounts']}",
        f"  MEDIUM   (≥25)          : {report['medium_accounts']}",
        f"  LOW      (<25)          : "
        f"{report['accounts_scored'] - report['critical_accounts'] - report['high_accounts'] - report['medium_accounts']}",
        "",
    ]

    # Top 10 highest-risk accounts
    if not risk_df.empty:
        lines += ["TOP 10 HIGHEST-RISK ACCOUNTS", "-" * 40]
        for _, row in risk_df.head(10).iterrows():
            lines.append(
                f"  [{row['risk_tier']:8s}] {row['account_id']:12s} "
                f"Score:{row['risk_score']:5.1f}  {row['account_holder']}"
            )
            lines.append(f"             Patterns : {row['active_patterns']}")
            lines.append(f"             Reasoning: {row['risk_reasoning'][:100]}")
            lines.append("")

    # Layering chain detail
    if lay:
        lines += ["LAYERING CHAINS DETAIL", "-" * 40]
        for f in lay:
            lines.append(f"  [{f['severity']:8s}] {f['chain']}")
            lines.append(
                f"             {f['chain_length']} hops | "
                f"₹{f['start_amount']:,.0f} → ₹{f['end_amount']:,.0f} "
                f"({f['skim_ratio']*100:.1f}% skimmed)"
            )
            lines.append("")

    # Round-trips
    if rt:
        lines += ["ROUND-TRIP DETAIL", "-" * 40]
        for f in rt[:10]:
            lines.append(f"  {f['description']}")

    lines += [
        "",
        "OUTPUT FILES",
        "-" * 40,
        "  account_graph.pkl            ← NetworkX DiGraph → feed directly to Phase 9",
        "  account_graph.gexf           ← export for graph visualization tools",
        "  analytics_transactions.csv   ← all rows + Phase 8 flags → Feed to Phase 9/10",
        "  round_trips.csv              ← direct (2-hop) round-trip findings",
        "  round_trip_cycles.csv        ← multi-hop (3+ hop) round-trip cycle findings",
        "  layering_chains.csv          ← layering chain findings",
        "  fan_in.csv                   ← collector account findings",
        "  fan_out.csv                  ← distribution account findings",
        "  smurfing.csv                 ← structuring findings",
        "  odd_hours.csv                ← odd-hour findings",
        "  beneficiaries.csv            ← beneficiary profiles per account",
        "  risk_scores.csv              ← per-account risk score, tier, reasoning",
        "  community_risk.csv           ← aggregated community risk summaries",
        "  graph_metrics.csv            ← PageRank / betweenness / degree features",
        "  money_trails/                ← forward/backward fund traces for top accounts",
        "  graph_summary.json           ← graph statistics",
        "  analytics_report.json        ← machine-readable full report",
        "  analytics_summary.txt        ← this file",
        "=" * 70,
    ]

    with open(os.path.join(out_dir, "analytics_summary.txt"), "w") as f:
        f.write("\n".join(lines))


def _print_final_summary(report, out_dir):
    print(f"\n{'='*65}")
    print("  PHASE 8 COMPLETE")
    print(f"{'='*65}")
    print(f"  Round-trips detected     : {report['round_trips']}")
    print(f"  Round-trip cycles (3+ hop): {report.get('round_trip_cycles', 0)}")
    print(f"  Layering chains detected : {report['layering_chains']}")
    print(f"  Fan-in collectors        : {report['fan_in']}")
    print(f"  Fan-out distributors     : {report['fan_out']}")
    print(f"  Smurfing patterns        : {report['smurfing']}")
    print(f"  Odd-hour accounts        : {report['odd_hours']}")
    print(f"  CRITICAL risk accounts   : {report['critical_accounts']}")
    print(f"  HIGH risk accounts       : {report['high_accounts']}")
    print(f"\n  Outputs → {os.path.abspath(out_dir)}/")
    print(f"    • analytics_transactions.csv  ← feed to Phase 9/10")
    print(f"    • risk_scores.csv             ← ranked account list")
    print(f"    • layering_chains.csv         ← money trail chains")
    print(f"    • money_trails/               ← per-account hop traces")
    print(f"    • analytics_summary.txt       ← human-readable report")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Phase 8 — Investigation Analytics Engine"
    )
    ap.add_argument("--input",      required=True,
                    help="cleaned_transactions.csv from Phase 7")
    ap.add_argument("--out-dir",    default="analytics",
                    help="Output directory")
    ap.add_argument("--top-trails", type=int, default=5,
                    help="Number of top-risk accounts to trace money trails for")
    args = ap.parse_args()
    run_analytics(args.input, args.out_dir, args.top_trails)