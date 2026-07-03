"""
Community detection, SCC cycle detection, and community risk aggregation
for Phase 8.

Provides:
 - detect_communities(account_graph) -> (member_map, community_summary)
 - detect_scc_cycles(account_graph) -> list of cycles / SCC summaries
 - compute_community_risk(account_graph, community_map, risk_df) -> community risk table
"""
from __future__ import annotations
import networkx as nx
import math
import statistics
from collections import defaultdict
from typing import Tuple, Dict, List

try:
    # python-louvain (community) is preferred
    import community as community_louvain  # type: ignore
    _HAS_LOUVAIN = True
except Exception:
    _HAS_LOUVAIN = False


def detect_communities(account_graph: nx.DiGraph, method: str = "louvain") -> Tuple[Dict[str, int], List[dict]]:
    """Detect communities on the account_graph.

    Returns (member_map, community_summary_list) where member_map maps node->community_id.
    community_summary_list contains dicts with community_id, size, total_flow, internal_ratio, top_accounts.
    """
    if account_graph is None or account_graph.number_of_nodes() == 0:
        return {}, []

    # use undirected view for community detection
    undirected = account_graph.to_undirected(reciprocal=False)

    if method == "louvain" and _HAS_LOUVAIN and hasattr(community_louvain, "best_partition"):
        try:
            part = community_louvain.best_partition(undirected)
            member_map = {str(n): int(c) for n, c in part.items()}
        except Exception:
            # fallback if best_partition unavailable or errors
            from networkx.algorithms import community as nx_comm
            # For large graphs, use fast label-propagation (asynchronous LPA) as a pragmatic fallback
            if account_graph.number_of_nodes() > 2000 and hasattr(nx_comm, 'asyn_lpa_communities'):
                groups = list(nx_comm.asyn_lpa_communities(undirected, weight='total_amount' if account_graph.number_of_edges() else None))
            else:
                groups = list(nx_comm.greedy_modularity_communities(undirected, weight='total_amount' if account_graph.number_of_edges() else None))
            member_map = {}
            for i, g in enumerate(groups):
                for n in g:
                    member_map[str(n)] = i
    else:
        # fallback to greedy modularity communities (or label propagation on big graphs)
        from networkx.algorithms import community as nx_comm
        if account_graph.number_of_nodes() > 2000 and hasattr(nx_comm, 'asyn_lpa_communities'):
            groups = list(nx_comm.asyn_lpa_communities(undirected, weight='total_amount' if account_graph.number_of_edges() else None))
        else:
            groups = list(nx_comm.greedy_modularity_communities(undirected, weight='total_amount' if account_graph.number_of_edges() else None))
        member_map = {}
        for i, g in enumerate(groups):
            for n in g:
                member_map[str(n)] = i

    # Summarize communities
    communities = defaultdict(list)
    for node, cid in member_map.items():
        communities[cid].append(node)

    summaries = []
    for cid, members in communities.items():
        size = len(members)
        total_flow = 0.0
        internal_count = 0
        for u in members:
            for v in account_graph.successors(u):
                if v in members and account_graph.has_edge(u, v):
                    data = account_graph.get_edge_data(u, v)
                    if data is None:
                        continue
                    # MultiDiGraph returns dict-of-dicts, DiGraph returns a single dict
                    try:
                        # detect Multi edge data (values are dicts)
                        if isinstance(data, dict) and all(isinstance(d, dict) for d in data.values()):
                            vals = [float(d.get("total_amount", d.get("amount", 0))) for d in data.values()]
                            total_flow += sum(vals)
                        elif isinstance(data, dict):
                            total_flow += float(data.get("total_amount", data.get("amount", 0)))
                        else:
                            total_flow += float(data)
                    except Exception:
                        continue
        for n in members:
            if account_graph.nodes.get(n, {}).get("is_internal"):
                internal_count += 1
        internal_ratio = internal_count / size if size else 0.0
        # top accounts by out-degree inside community
        outdeg = sorted(members, key=lambda x: account_graph.out_degree(x) if x in account_graph else 0, reverse=True)[:5]
        summaries.append({
            "community_id": int(cid),
            "size": int(size),
            "total_flow": round(float(total_flow), 2),
            "internal_ratio": round(float(internal_ratio), 3),
            "top_accounts": outdeg,
        })

    return member_map, sorted(summaries, key=lambda x: x["total_flow"], reverse=True)


def detect_scc_cycles(account_graph: nx.DiGraph, max_cycles_per_scc: int = 50, max_cycle_length: int = 8) -> List[dict]:
    """Detect strongly connected components and enumerate simple cycles within them.

    Returns list of cycle summaries: {scc_id, members, cycle_length, cycle_nodes, total_flow}
    """
    results = []
    if account_graph is None or account_graph.number_of_nodes() == 0:
        return results

    sccs = list(nx.strongly_connected_components(account_graph))
    scc_id = 0
    for comp in sccs:
        if len(comp) <= 1:
            continue
        sub = account_graph.subgraph(comp).copy()
        # find simple cycles within subgraph (limit search)
        cycles = []
        try:
            for i, cyc in enumerate(nx.simple_cycles(sub)):
                if i >= max_cycles_per_scc:
                    break
                if len(cyc) > max_cycle_length:
                    continue
                # compute total flow along cycle edges
                flow = 0.0
                for a, b in zip(cyc, cyc[1:] + [cyc[0]]):
                    if sub.has_edge(a, b):
                        # sum multi-edge amounts
                        data = sub.get_edge_data(a, b)
                        if isinstance(data, dict):
                            # networkx MultiDiGraph gives dict of dicts
                            vals = []
                            for k, d in data.items():
                                vals.append(float(d.get("amount", d.get("total_amount", 0))))
                            flow += sum(vals)
                        else:
                            flow += float(data.get("amount", data.get("total_amount", 0)))
                cycles.append({"cycle_nodes": list(cyc), "cycle_length": len(cyc), "cycle_flow": round(flow, 2)})
        except Exception:
            cycles = []

        results.append({
            "scc_id": scc_id,
            "members": sorted(list(comp)),
            "n_members": len(comp),
            "n_cycles_found": len(cycles),
            "cycles": cycles[:max_cycles_per_scc],
        })
        scc_id += 1

    return results


def compute_community_risk(account_graph: nx.DiGraph, member_map: Dict[str, int], risk_df) -> List[dict]:
    """Aggregate per-account risk into per-community risk summaries."""
    if not member_map:
        return []
    community_scores = defaultdict(list)
    for _, row in risk_df.iterrows():
        acc = str(row.get("account_id"))
        cid = member_map.get(acc)
        if cid is None:
            continue
        community_scores[cid].append(float(row.get("risk_score", 0)))

    summaries = []
    for cid, scores in community_scores.items():
        n = len(scores)
        avg = statistics.mean(scores) if scores else 0.0
        mx = max(scores) if scores else 0.0
        summaries.append({
            "community_id": int(cid),
            "n_accounts": int(n),
            "avg_risk": round(avg, 2),
            "max_risk": round(mx, 2),
        })
    return sorted(summaries, key=lambda x: x["avg_risk"], reverse=True)
