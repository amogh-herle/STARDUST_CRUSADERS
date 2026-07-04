"""
money_flow_graph.py
─────────────────────
Builds the FULL investigative graph around one or more suspicious seed
accounts — not just a single trail, but the entire web of dependency
accounts connected to it — then renders it as an interactive HTML network
diagram for investigators.

Covers all 3 requirements:
  1. Dependency accounts + their money relationships
     -> extract_subgraph()  (N-hop neighborhood around seed accounts)
  2. Destination / accumulation account identification
     -> classify_nodes()    (source / intermediary / destination / cycle)
  3. Full visual graph investigators can read
     -> render()            (interactive Pyvis HTML: nodes=accounts, edges=money)

Usage:
    python money_flow_graph.py --seed 24704559049070
    python money_flow_graph.py --seed 24704559049070 --hops 3
    python money_flow_graph.py --top-suspicious 5   (auto-picks top flagged accounts)
"""

import pandas as pd
import networkx as nx
from pyvis.network import Network
import argparse
import os
import sys

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORED_CSV = r"C:\Users\dhanu\OneDrive\Documents\GitHub\STARDUST_CRUSADERS\aml_model\ground_truth_evaluator\outputs\reports\isolation_forest_scored_transactions.csv"    
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs", "reports")


class MoneyFlowGraph:

    def __init__(self, scored_csv: str = SCORED_CSV):
        self.df = self._load(scored_csv)
        self.full_graph = self._build_full_graph(self.df)
        self.account_risk = self._compute_account_risk(self.df)

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self, path: str) -> pd.DataFrame:
        df = pd.read_csv(path, dtype=str, low_memory=False)
        df["debit"]  = pd.to_numeric(df.get("debit_clean", 0), errors="coerce").fillna(0)
        df["credit"] = pd.to_numeric(df.get("credit_clean", 0), errors="coerce").fillna(0)
        df["amount"] = df["credit"] - df["debit"]
        df["abs_amount"] = df["amount"].abs()
        if "is_flagged" in df.columns:
            df["is_flagged"] = pd.to_numeric(df["is_flagged"], errors="coerce").fillna(0).astype(int)
        else:
            df["is_flagged"] = 0
        df["datetime"] = pd.to_datetime(df.get("datetime", df.get("date")), errors="coerce")
        df["counterparty_account"] = df.get("counterparty_account_id")
        return df

    # ── requirement 2 groundwork: per-account risk summary ──────────────────

    def _compute_account_risk(self, df: pd.DataFrame) -> pd.DataFrame:
        summary = df.groupby("account_id").agg(
            total_txns=("account_id", "count"),
            flagged_txns=("is_flagged", "sum"),
            total_credit=("credit", "sum"),
            total_debit=("debit", "sum"),
        )
        summary["flag_pct"] = (summary["flagged_txns"] / summary["total_txns"] * 100).round(1)
        summary["net_flow"] = summary["total_credit"] - summary["total_debit"]
        return summary

    # ── build the COMPLETE transaction graph (all accounts) ─────────────────

    def _build_full_graph(self, df: pd.DataFrame) -> nx.MultiDiGraph:
        """Every debit row = a directed edge: account_id -> counterparty_account"""
        G = nx.MultiDiGraph()
        edges = df[(df["debit"] > 0) & df.get("counterparty_account", pd.Series()).notna()]

        for _, row in edges.iterrows():
            src, dst = row["account_id"], row["counterparty_account"]
            if pd.isna(src) or pd.isna(dst) or src == "" or dst == "":
                continue
            G.add_edge(
                src, dst,
                amount=float(row["debit"]),
                datetime=row["datetime"],
                narration=str(row.get("narration", ""))[:60],
            )
        return G

    # ── REQUIREMENT 1: dependency subgraph around seed account(s) ───────────

    def extract_subgraph(self, seeds: list, hops: int = 2) -> nx.MultiDiGraph:
        """
        Returns the N-hop neighborhood around the seed accounts —
        every account that sent to, or received from, the seed (directly
        or transitively up to `hops` steps), plus all edges between them.
        """
        if isinstance(seeds, str):
            seeds = [seeds]

        undirected = self.full_graph.to_undirected()
        keep_nodes = set(seeds)

        frontier = set(seeds)
        for _ in range(hops):
            next_frontier = set()
            for node in frontier:
                if node in undirected:
                    next_frontier.update(undirected.neighbors(node))
            keep_nodes.update(next_frontier)
            frontier = next_frontier

        return self.full_graph.subgraph(keep_nodes).copy()

    # ── REQUIREMENT 2: classify every node's role in the flow ───────────────

    def classify_nodes(self, G: nx.MultiDiGraph) -> dict:
        """
        Returns {account_id: role} where role is one of:
          "source"        — mostly sends money out, few/no incoming edges
          "destination"    — mostly receives money, doesn't forward it (accumulation point)
          "intermediary"   — both receives and forwards (layering / pass-through)
          "isolated"       — no clear pattern
        """
        roles = {}
        for node in G.nodes():
            in_deg  = G.in_degree(node)
            out_deg = G.out_degree(node)

            in_amount  = sum(d["amount"] for _, _, d in G.in_edges(node, data=True))
            out_amount = sum(d["amount"] for _, _, d in G.out_edges(node, data=True))

            if in_deg > 0 and out_deg == 0:
                roles[node] = "destination"
            elif out_deg > 0 and in_deg == 0:
                roles[node] = "source"
            elif in_deg > 0 and out_deg > 0:
                # accumulator: receives much more than it forwards
                if in_amount > out_amount * 1.3:
                    roles[node] = "destination"
                else:
                    roles[node] = "intermediary"
            else:
                roles[node] = "isolated"
        return roles

    def find_accumulation_points(self, G: nx.MultiDiGraph, top_n: int = 10) -> pd.DataFrame:
        """
        Ranks nodes by how much money accumulates there:
        accumulation_score = total received - total forwarded
        High positive score = strong candidate for final destination.
        """
        rows = []
        for node in G.nodes():
            in_amount  = sum(d["amount"] for _, _, d in G.in_edges(node, data=True))
            out_amount = sum(d["amount"] for _, _, d in G.out_edges(node, data=True))
            rows.append({
                "account_id": node,
                "total_received": in_amount,
                "total_forwarded": out_amount,
                "net_accumulation": in_amount - out_amount,
                "in_degree": G.in_degree(node),
                "out_degree": G.out_degree(node),
                "risk_flag_pct": self.account_risk.loc[node, "flag_pct"]
                                 if node in self.account_risk.index else None,
            })
        result = pd.DataFrame(rows).sort_values("net_accumulation", ascending=False)
        return result.head(top_n)

    def find_cycles(self, G: nx.MultiDiGraph) -> list:
        """Detects circular money flows (A -> B -> C -> A) = classic layering signature."""
        simple = nx.DiGraph(G)  # collapse multigraph to simple graph for cycle detection
        return list(nx.simple_cycles(simple))

    # ── REQUIREMENT 3: render as an interactive investigator-readable graph ─

    def render(self, G: nx.MultiDiGraph, output_name: str = "money_flow_graph"):
        roles = self.classify_nodes(G)
        role_colors = {
            "source":       "#F4A261",  # orange — where money originates
            "intermediary": "#457B9D",  # blue — pass-through / layering
            "destination":  "#E63946",  # red — accumulation point
            "isolated":     "#AAAAAA",
        }

        net = Network(height="800px", width="100%", directed=True,
                      bgcolor="#0f1117", font_color="white", notebook=False)
        net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=150)

        for node in G.nodes():
            role = roles.get(node, "isolated")
            risk = self.account_risk.loc[node, "flag_pct"] if node in self.account_risk.index else 0
            size = 15 + min(risk, 100) * 0.3   # bigger node = higher risk

            label = f"{node}\n({role})"
            title = (f"Account: {node}<br>"
                     f"Role: {role}<br>"
                     f"Risk (flag %): {risk}<br>"
                     f"In-degree: {G.in_degree(node)}  Out-degree: {G.out_degree(node)}")

            net.add_node(node, label=label, title=title,
                         color=role_colors[role], size=size)

        for src, dst, data in G.edges(data=True):
            amount = data.get("amount", 0)
            label = f"₹{amount:,.0f}"
            title = f"{src} → {dst}<br>Amount: ₹{amount:,.2f}<br>{data.get('narration','')}"
            width = 1 + min(amount / 50000, 8)  # thicker edge = larger amount

            net.add_edge(src, dst, label=label, title=title, width=width, color="#8892a6")

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, f"{output_name}.html")
        net.write_html(out_path, open_browser=False, notebook=False)
        print(f"[MoneyFlowGraph] Interactive graph saved -> {out_path}")
        return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", nargs="+", default=None,
                        help="One or more account_id(s) to trace from")
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--top-suspicious", type=int, default=None,
                        help="Auto-select top N most-flagged accounts as seeds")
    args = parser.parse_args()

    mfg = MoneyFlowGraph()

    if args.top_suspicious:
        seeds = (
            mfg.account_risk.sort_values("flag_pct", ascending=False)
            .head(args.top_suspicious).index.tolist()
        )
        print(f"[Auto-selected top {args.top_suspicious} suspicious accounts]: {seeds}")
    elif args.seed:
        seeds = args.seed
    else:
        print("Provide --seed <account_id> or --top-suspicious N")
        sys.exit(1)

    print(f"\n[1] Extracting {args.hops}-hop dependency subgraph around: {seeds}")
    sub = mfg.extract_subgraph(seeds, hops=args.hops)
    print(f"    Subgraph: {sub.number_of_nodes()} accounts, {sub.number_of_edges()} transactions")

    print("\n[2] Classifying account roles …")
    roles = mfg.classify_nodes(sub)
    for role_type in ["source", "intermediary", "destination"]:
        matches = [n for n, r in roles.items() if r == role_type]
        print(f"    {role_type:15s}: {len(matches)} accounts -> {matches[:5]}")

    print("\n[3] Top accumulation points (likely final destinations) …")
    accum = mfg.find_accumulation_points(sub, top_n=10)
    print(accum.to_string(index=False))

    print("\n[4] Checking for circular money flows (layering) …")
    cycles = mfg.find_cycles(sub)
    if cycles:
        print(f"    Found {len(cycles)} cycle(s):")
        for c in cycles[:5]:
            print(f"    {' -> '.join(c)} -> {c[0]}")
    else:
        print("    No cycles detected in this subgraph.")

    print("\n[5] Rendering interactive graph …")
    out_path = mfg.render(sub, output_name=f"money_flow_{seeds[0]}")
    print(f"\nDone. Open this file in a browser: {out_path}")


if __name__ == "__main__":
    main()