import os
import json
import re
import numpy as np
import pandas as pd
import networkx as nx

STOP_WORDS = {
    "neft", "upi", "imps", "rtgs", "transfer", "to", "from", "by", "for", "in", "of", "and", 
    "the", "a", "an", "on", "at", "with", "payment", "txn", "tx", "trans", "cr", "dr", 
    "val", "ref", "utr", "no", "id", "self", "amount", "rupees", "rs", "inr"
}

def _clean_counterparty(val) -> str:
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if s and s.lower() not in {"nan", "none", "null"}:
        return " ".join(s.upper().split())
    return ""

def get_outbound_counterparties(acc_id: str, df: pd.DataFrame) -> set[str]:
    acc_df = df[df["account_id"].astype(str) == acc_id]
    debits = acc_df[acc_df["debit"] > 0]
    cps = set()
    for col in ["counterparty_account", "counterparty_name"]:
        if col in debits.columns:
            for val in debits[col].dropna():
                cleaned = _clean_counterparty(val)
                if cleaned:
                    cps.add(cleaned)
    return cps

def get_inbound_counterparties(acc_id: str, df: pd.DataFrame) -> set[str]:
    acc_df = df[df["account_id"].astype(str) == acc_id]
    credits = acc_df[acc_df["credit"] > 0]
    cps = set()
    for col in ["counterparty_account", "counterparty_name"]:
        if col in credits.columns:
            for val in credits[col].dropna():
                cleaned = _clean_counterparty(val)
                if cleaned:
                    cps.add(cleaned)
    return cps

def get_narrative_vocabulary(acc_id: str, df: pd.DataFrame) -> set[str]:
    acc_df = df[df["account_id"].astype(str) == acc_id]
    narrations = acc_df["narration"].dropna().astype(str).str.lower().tolist()
    tokens = set()
    for narr in narrations:
        words = re.findall(r"\b[a-z0-9]{3,}\b", narr)
        for w in words:
            if w not in STOP_WORDS:
                tokens.add(w)
    return tokens

def jaccard_similarity(set_a: set, set_b: set) -> float:
    if not set_a or not set_b:
        return 0.0
    union = set_a.union(set_b)
    if not union:
        return 0.0
    return len(set_a.intersection(set_b)) / len(union)

def analyse_relationships(
    out_dir: str,
    df: pd.DataFrame,
    txn_graph: nx.MultiDiGraph,
    account_graph: nx.DiGraph,
    risk_df: pd.DataFrame
):
    """
    Calculates pairwise relationship scores, builds network outputs, runs community detection,
    propagates risk scores, and highlights hidden dependencies.
    """
    print("\n  [+] Starting Relationship Analysis Engine ...")
    
    # Get internal accounts list
    internal_accounts = risk_df["account_id"].astype(str).tolist()
    risk_scores_dict = {}
    for _, row in risk_df.iterrows():
        acc_str = str(row["account_id"]).strip()
        risk_scores_dict[acc_str] = {
            "risk_score": float(row.get("risk_score", 0.0)),
            "community_id": row.get("community_id", "UNKNOWN"),
            "account_holder": row.get("account_holder", "UNKNOWN"),
            "bank_name": row.get("bank_name", "UNKNOWN"),
            "risk_tier": row.get("risk_tier", "LOW")
        }

    # Pre-extract sets for faster pairwise calculations
    outbounds = {acc: get_outbound_counterparties(acc, df) for acc in internal_accounts}
    inbounds = {acc: get_inbound_counterparties(acc, df) for acc in internal_accounts}
    narratives = {acc: get_narrative_vocabulary(acc, df) for acc in internal_accounts}

    # Nodes in graph mapping for type safety
    nodes_in_g = {str(n).strip(): n for n in account_graph.nodes}

    relationships = []
    
    # Double loop for pairs
    for i in range(len(internal_accounts)):
        for j in range(i + 1, len(internal_accounts)):
            a = internal_accounts[i]
            b = internal_accounts[j]
            
            # 1. Direct transfer score
            direct_score = 0.0
            total_amt = 0.0
            txn_cnt = 0
            na = nodes_in_g.get(a)
            nb = nodes_in_g.get(b)
            has_direct_txn = False
            
            if na is not None and nb is not None:
                if account_graph.has_edge(na, nb):
                    data = account_graph[na][nb]
                    total_amt += float(data.get("total_amount", 0.0))
                    txn_cnt += int(data.get("txn_count", 1))
                    has_direct_txn = True
                if account_graph.has_edge(nb, na):
                    data = account_graph[nb][na]
                    total_amt += float(data.get("total_amount", 0.0))
                    txn_cnt += int(data.get("txn_count", 1))
                    has_direct_txn = True
                    
            if txn_cnt > 0:
                direct_score = min(100.0, 15.0 * txn_cnt + 10.0 * np.log1p(total_amt))
                
            # 2. Shared beneficiary score
            shared_bene_score = jaccard_similarity(outbounds[a], outbounds[b]) * 100.0
            
            # 3. Shared source score
            shared_source_score = jaccard_similarity(inbounds[a], inbounds[b]) * 100.0
            
            # 4. Community overlap score
            comm_a = risk_scores_dict.get(a, {}).get("community_id")
            comm_b = risk_scores_dict.get(b, {}).get("community_id")
            comm_score = 0.0
            if comm_a is not None and comm_b is not None and pd.notna(comm_a) and pd.notna(comm_b):
                if str(comm_a).strip().lower() not in {"", "nan", "none", "unknown"} and str(comm_a).strip() == str(comm_b).strip():
                    comm_score = 100.0
                    
            # 5. Temporal correlation score
            # Pearson daily correlation
            dates_a = pd.to_datetime(df[df["account_id"].astype(str) == a]["date"]).dropna().dt.date
            dates_b = pd.to_datetime(df[df["account_id"].astype(str) == b]["date"]).dropna().dt.date
            
            corr = 0.0
            if not dates_a.empty and not dates_b.empty:
                all_dates = sorted(list(set(dates_a).union(set(dates_b))))
                counts_a = dates_a.value_counts().reindex(all_dates, fill_value=0)
                counts_b = dates_b.value_counts().reindex(all_dates, fill_value=0)
                if counts_a.std() > 0 and counts_b.std() > 0:
                    corr = float(counts_a.corr(counts_b))
                    if pd.isna(corr) or corr < 0:
                        corr = 0.0
                        
            # Transaction co-occurrence in 2-hour window
            ts_a = pd.to_datetime(df[df["account_id"].astype(str) == a]["date"].astype(str) + " " + df[df["account_id"].astype(str) == a]["time"].fillna("00:00:00").astype(str), errors="coerce").dropna().tolist()
            ts_b = pd.to_datetime(df[df["account_id"].astype(str) == b]["date"].astype(str) + " " + df[df["account_id"].astype(str) == b]["time"].fillna("00:00:00").astype(str), errors="coerce").dropna().tolist()
            
            proximity_score = 0.0
            if ts_a and ts_b:
                ts_a = sorted(ts_a)
                ts_b = sorted(ts_b)
                co_occurrences = 0
                j_ptr = 0
                m_len = len(ts_b)
                for ta in ts_a:
                    while j_ptr < m_len and (ta - ts_b[j_ptr]).total_seconds() > 7200.0:
                        j_ptr += 1
                    if j_ptr < m_len and abs((ta - ts_b[j_ptr]).total_seconds()) <= 7200.0:
                        co_occurrences += 1
                proximity_score = co_occurrences / max(1, len(ts_a))
                
            temporal_score = (0.5 * corr + 0.5 * proximity_score) * 100.0
            
            # 6. Shared narrative score
            shared_narrative_score = jaccard_similarity(narratives[a], narratives[b]) * 100.0
            
            # Combine scores
            rel_score = min(100.0, 0.3 * direct_score + 0.2 * shared_bene_score + 0.2 * shared_source_score + 0.1 * comm_score + 0.1 * temporal_score + 0.1 * shared_narrative_score)
            
            # Relationship Type
            if direct_score >= 50.0:
                rel_type = "Direct Transactor"
            elif shared_bene_score >= 40.0 and shared_source_score >= 40.0:
                rel_type = "Shared Infrastructure"
            elif temporal_score >= 40.0 and comm_score >= 50.0:
                rel_type = "Co-active Layering"
            elif rel_score >= 25.0:
                rel_type = "Suspicious Association"
            elif rel_score >= 5.0:
                rel_type = "Weak Association"
            else:
                rel_type = "Unrelated"
                
            # Confidence Calculation
            sub_scores = [direct_score, shared_bene_score, shared_source_score, comm_score, temporal_score, shared_narrative_score]
            num_active = sum(1 for s in sub_scores if s >= 10.0)
            total_txns = len(ts_a) + len(ts_b)
            confidence = min(100.0, 20.0 * num_active + 10.0 * np.log1p(total_txns))
            
            relationships.append({
                "account_a": a,
                "account_b": b,
                "direct_transfer_score": round(direct_score, 1),
                "shared_beneficiary_score": round(shared_bene_score, 1),
                "shared_source_score": round(shared_source_score, 1),
                "community_overlap_score": round(comm_score, 1),
                "temporal_correlation_score": round(temporal_score, 1),
                "shared_narrative_score": round(shared_narrative_score, 1),
                "relationship_score": round(rel_score, 1),
                "relationship_type": rel_type,
                "confidence": round(confidence, 1),
                "has_direct_txn": has_direct_txn
            })
            
    # Export relationship_network.csv
    network_df = pd.DataFrame(relationships)
    network_csv_path = os.path.join(out_dir, "relationship_network.csv")
    network_df.to_csv(network_csv_path, index=False)
    print(f"        Saved relationship network → {network_csv_path}")

    # Build NetworkX Relationship Graph
    rel_graph = nx.Graph()
    rel_graph.add_nodes_from(internal_accounts)
    for r in relationships:
        if r["relationship_score"] >= 5.0:
            rel_graph.add_edge(r["account_a"], r["account_b"], weight=r["relationship_score"])
            
    # 1. Community detection based on relationship profiles
    import networkx.algorithms.community as nx_comm
    try:
        communities = list(nx_comm.greedy_modularity_communities(rel_graph, weight="weight"))
        rel_member_map = {}
        for c_idx, comm_set in enumerate(communities):
            for node in comm_set:
                rel_member_map[node] = c_idx
    except Exception:
        rel_member_map = {node: 0 for node in internal_accounts}
        
    comm_rows = [{"account_id": acc, "relationship_community_id": rel_member_map.get(acc, 0)} for acc in internal_accounts]
    comm_df = pd.DataFrame(comm_rows)
    comm_csv_path = os.path.join(out_dir, "relationship_communities.csv")
    comm_df.to_csv(comm_csv_path, index=False)
    print(f"        Saved relationship communities → {comm_csv_path}")

    # 2. Propagate Risk Scores
    orig_risk = {acc: risk_scores_dict.get(acc, {}).get("risk_score", 0.0) for acc in internal_accounts}
    sum_orig = sum(orig_risk.values())
    if sum_orig > 0:
        personalization = {node: orig_risk.get(node, 0.0) / sum_orig for node in internal_accounts}
    else:
        personalization = {node: 1.0 / len(internal_accounts) for node in internal_accounts}
        
    if rel_graph.number_of_edges() > 0:
        try:
            pr_scores = nx.pagerank(rel_graph, alpha=0.85, personalization=personalization, weight="weight")
            max_pr = max(pr_scores.values()) if pr_scores else 0.0
            propagated_risk = {}
            for node in internal_accounts:
                val = pr_scores.get(node, 0.0)
                scaled_pr = (val / max_pr) * 100.0 if max_pr > 0 else 0.0
                propagated_risk[node] = round(0.6 * orig_risk.get(node, 0.0) + 0.4 * scaled_pr, 1)
        except Exception:
            propagated_risk = orig_risk.copy()
    else:
        propagated_risk = orig_risk.copy()
        
    risk_rows = []
    for acc in internal_accounts:
        meta = risk_scores_dict.get(acc, {})
        risk_rows.append({
            "account_id": acc,
            "account_holder": meta.get("account_holder"),
            "bank_name": meta.get("bank_name"),
            "original_risk_score": orig_risk[acc],
            "propagated_risk_score": propagated_risk[acc],
            "risk_elevation": round(propagated_risk[acc] - orig_risk[acc], 1)
        })
    risk_out_df = pd.DataFrame(risk_rows).sort_values("propagated_risk_score", ascending=False)
    risk_csv_path = os.path.join(out_dir, "propagated_risk_scores.csv")
    risk_out_df.to_csv(risk_csv_path, index=False)
    print(f"        Saved propagated risk scores → {risk_csv_path}")

    # 3. Highlight Hidden Dependencies (high score but no direct transaction)
    hidden_deps = [r for r in relationships if not r["has_direct_txn"] and r["relationship_score"] >= 5.0]
    hidden_df = pd.DataFrame(hidden_deps)
    if not hidden_df.empty:
        hidden_df = hidden_df.sort_values("relationship_score", ascending=False)
    hidden_csv_path = os.path.join(out_dir, "hidden_dependencies.csv")
    hidden_df.to_csv(hidden_csv_path, index=False)
    print(f"        Saved hidden dependencies ({len(hidden_deps)} found) → {hidden_csv_path}")

    # 4. Generate relationship_summary.csv
    high_relationship_count = sum(1 for r in relationships if r["relationship_score"] >= 5.0)
    top_pairs = sorted(relationships, key=lambda x: x["relationship_score"], reverse=True)[:5]
    top_pairs_desc = [f"{p['account_a']}<->{p['account_b']} ({p['relationship_score']} score)" for p in top_pairs]
    
    summary_data = [{
        "total_pairs_analyzed": len(relationships),
        "high_relationship_pairs": high_relationship_count,
        "hidden_dependencies_found": len(hidden_deps),
        "relationship_communities_count": len(set(rel_member_map.values())),
        "top_relationship_pair": top_pairs_desc[0] if top_pairs_desc else "None"
    }]
    summary_df = pd.DataFrame(summary_data)
    summary_csv_path = os.path.join(out_dir, "relationship_summary.csv")
    summary_df.to_csv(summary_csv_path, index=False)
    print(f"        Saved relationship summary → {summary_csv_path}")

    # 5. Save Cytoscape JSON (relationship_network.json)
    cytoscape_nodes = []
    for acc in internal_accounts:
        meta = risk_scores_dict.get(acc, {})
        cytoscape_nodes.append({
            "data": {
                "id": acc,
                "label": meta.get("account_holder", acc),
                "original_risk_score": orig_risk[acc],
                "propagated_risk_score": propagated_risk[acc],
                "bank_name": meta.get("bank_name"),
                "risk_tier": meta.get("risk_tier")
            }
        })
        
    cytoscape_edges = []
    edge_idx = 0
    for r in relationships:
        if r["relationship_score"] >= 5.0:
            edge_idx += 1
            cytoscape_edges.append({
                "data": {
                    "id": f"rel_e{edge_idx}",
                    "source": r["account_a"],
                    "target": r["account_b"],
                    "relationship_score": r["relationship_score"],
                    "relationship_type": r["relationship_type"],
                    "confidence": r["confidence"]
                }
            })
            
    cy_data = {
        "nodes": cytoscape_nodes,
        "edges": cytoscape_edges
    }
    json_path = os.path.join(out_dir, "relationship_network.json")
    with open(json_path, "w") as f:
        json.dump(cy_data, f, indent=2)
    print(f"        Saved relationship network JSON → {json_path}")
    print("  [+] Relationship Analysis Engine complete.")
