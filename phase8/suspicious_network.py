import os
import json
import ast
import pandas as pd
import networkx as nx

def build_suspicious_network(
    out_dir: str,
    txn_graph: nx.MultiDiGraph,
    account_graph: nx.DiGraph,
    df: pd.DataFrame,
    risk_df: pd.DataFrame
):
    """
    Builds a Cytoscape-compatible suspicious network JSON file using
    txn_graph, account_graph, and engine findings as primary evidence.
    """
    from graph_builder import is_merchant

    # 1. Load metadata for internal accounts
    node_meta = {}
    for _, row in risk_df.iterrows():
        acc_id = str(row["account_id"]).strip()
        node_meta[acc_id] = {
            "id": acc_id,
            "label": row.get("account_holder", "") if pd.notna(row.get("account_holder", "")) and row.get("account_holder", "").strip() else acc_id,
            "risk_score": float(row.get("risk_score", 0.0)),
            "risk_tier": row.get("risk_tier", "LOW"),
            "bank_name": row.get("bank_name", "UNKNOWN"),
            "account_holder": row.get("account_holder", "UNKNOWN"),
            "community_id": row.get("community_id", "UNKNOWN"),
            "is_internal": True
        }

    # 2. Identify High and Critical accounts
    high_critical_nodes = set()
    for acc_id, meta in node_meta.items():
        if meta["risk_tier"] in ["HIGH", "CRITICAL"]:
            high_critical_nodes.add(acc_id)

    # 3. Find 1-hop and 2-hop neighbors in account_graph
    neighbor_nodes = set()
    for root in high_critical_nodes:
        # Check if root is stored as int or str in account_graph
        g_root = None
        if root in account_graph:
            g_root = root
        elif root.isdigit() and int(root) in account_graph:
            g_root = int(root)

        if g_root is not None:
            # 1-hop
            hop1 = set(account_graph.successors(g_root)) | set(account_graph.predecessors(g_root))
            for n1 in hop1:
                neighbor_nodes.add(str(n1).strip())
                # 2-hop
                if n1 in account_graph:
                    hop2 = set(account_graph.successors(n1)) | set(account_graph.predecessors(n1))
                    for n2 in hop2:
                        neighbor_nodes.add(str(n2).strip())

    # Combine High/Critical nodes and their neighbors
    selected_nodes = high_critical_nodes | neighbor_nodes

    # 4. Load external files findings to make sure we don't miss linked suspicious nodes
    fan_in_accounts = set()
    smurfing_accounts = set()
    layering_accounts = set()
    round_trip_accounts = set()
    fan_out_accounts = set()

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
            for _, r in sm_df.iterrows():
                dests_str = r.get("destinations")
                if dests_str and pd.notna(dests_str):
                    try:
                        dests = ast.literal_eval(dests_str)
                        if isinstance(dests, list):
                            smurfing_accounts.update([str(d) for d in dests])
                    except Exception:
                        pass
        except Exception:
            pass

    lay_path = os.path.join(out_dir, "layering_chains.csv")
    if os.path.exists(lay_path) and os.path.getsize(lay_path) > 10:
        try:
            lay_df = pd.read_csv(lay_path, dtype=str)
            for _, row in lay_df.iterrows():
                chain_str = row.get("chain")
                if chain_str and pd.notna(chain_str):
                    parts = [p.strip() for p in chain_str.split("→")]
                    layering_accounts.update(parts)
        except Exception:
            pass

    rt_path = os.path.join(out_dir, "round_trips.csv")
    if os.path.exists(rt_path) and os.path.getsize(rt_path) > 10:
        try:
            rt_df = pd.read_csv(rt_path, dtype=str)
            for _, row in rt_df.iterrows():
                for col in ["account_a", "account_b"]:
                    val = row.get(col)
                    if val and pd.notna(val):
                        round_trip_accounts.add(str(val).strip())
        except Exception:
            pass

    fo_path = os.path.join(out_dir, "fan_out.csv")
    if os.path.exists(fo_path) and os.path.getsize(fo_path) > 10:
        try:
            fo_df = pd.read_csv(fo_path, dtype=str)
            fan_out_accounts.update(fo_df["distributor"].dropna().unique())
        except Exception:
            pass

    # Add all findings linked accounts
    selected_nodes.update(fan_in_accounts)
    selected_nodes.update(smurfing_accounts)
    selected_nodes.update(layering_accounts)
    selected_nodes.update(round_trip_accounts)
    selected_nodes.update(fan_out_accounts)

    # Convert to strings for consistent set checks
    selected_nodes = {str(n).strip() for n in selected_nodes if str(n).strip()}

    # 5. Populate final node properties
    final_nodes = []
    for node in selected_nodes:
        # Check if internal or external
        is_internal = False
        node_lbl = node
        risk_score = 0.0
        risk_tier = "LOW"
        bank_name = "UNKNOWN"
        account_holder = "UNKNOWN"
        community_id = "UNKNOWN"

        # Check in node_meta first
        if node in node_meta:
            meta = node_meta[node]
            is_internal = True
            node_lbl = meta["label"]
            risk_score = meta["risk_score"]
            risk_tier = meta["risk_tier"]
            bank_name = meta["bank_name"]
            account_holder = meta["account_holder"]
            community_id = meta["community_id"]
        elif node.isdigit() and len(node) >= 6:
            is_internal = True

        # Total Flow Calculation
        total_flow = 0.0
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
            # Sum up total flow
            for successor in account_graph.successors(g_node):
                total_flow += float(account_graph[g_node][successor].get("total_amount", 0.0))
            for predecessor in account_graph.predecessors(g_node):
                total_flow += float(account_graph[predecessor][g_node].get("total_amount", 0.0))

        # Node role determination
        if is_merchant(node):
            node_role = "merchant"
        elif node in fan_in_accounts:
            node_role = "collector"
        elif node in smurfing_accounts or node in layering_accounts or (is_internal and indegree > 0 and outdegree > 0):
            node_role = "mule"
        elif not is_internal:
            node_role = "victim"
        else:
            node_role = "unknown"

        # Compile suspicious patterns
        suspicious_patterns = []
        if node in round_trip_accounts:
            suspicious_patterns.append("round_trip")
        if node in smurfing_accounts:
            suspicious_patterns.append("smurfing")
        if node in fan_in_accounts:
            suspicious_patterns.append("fan_in")
        if node in fan_out_accounts:
            suspicious_patterns.append("fan_out")
        if node in layering_accounts:
            suspicious_patterns.append("layering")

        # Fallback check from transaction DataFrame
        try:
            if node.isdigit():
                node_txns = df[df["account_id"] == int(node)]
            else:
                node_txns = df[df["account_id"].astype(str) == node]
            if not node_txns.empty:
                for col_name, pat_name in [
                    ("is_round_trip", "round_trip"),
                    ("is_layering", "layering"),
                    ("is_fan_in", "fan_in"),
                    ("is_fan_out", "fan_out"),
                    ("is_smurfing", "smurfing"),
                    ("is_odd_hour", "odd_hour"),
                ]:
                    if col_name in node_txns.columns and (node_txns[col_name] in [True, "True", 1, "1"]).any():
                        if pat_name not in suspicious_patterns:
                            suspicious_patterns.append(pat_name)
        except Exception:
            pass

        final_nodes.append({
            "data": {
                "id": node,
                "label": node_lbl,
                "risk_score": float(risk_score),
                "risk_tier": risk_tier,
                "bank_name": bank_name,
                "account_holder": account_holder,
                "community_id": community_id,
                "is_internal": is_internal,
                "node_role": node_role,
                "total_flow": round(total_flow, 2),
                "suspicious_patterns": suspicious_patterns
            }
        })

    # 6. Extract edges between the selected nodes from the account graph
    final_edges = []
    edge_id_counter = 0

    for u, v, data in account_graph.edges(data=True):
        u_str = str(u).strip()
        v_str = str(v).strip()
        if u_str in selected_nodes and v_str in selected_nodes:
            edge_id_counter += 1
            final_edges.append({
                "data": {
                    "id": f"e{edge_id_counter}",
                    "source": u_str,
                    "target": v_str,
                    "amount": float(data.get("total_amount", 0.0)),
                    "txn_count": int(data.get("txn_count", 1))
                }
            })

    # 7. Save Cytoscape JSON
    cytoscape_data = {
        "nodes": final_nodes,
        "edges": final_edges
    }

    out_json_path = os.path.join(out_dir, "suspicious_network.json")
    with open(out_json_path, "w") as f:
        json.dump(cytoscape_data, f, indent=2)

    print(f"[Suspicious Network] Created network graph with {len(final_nodes)} nodes and {len(final_edges)} edges.")
    print(f"[Suspicious Network] Saved → {out_json_path}")
