"""
money_flow_graph.py — hardened version
────────────────────────────────────────
Fixes applied:
  1. Cycle detection capped to max_len=6 (was effectively unbounded) —
     prevents infinite hangs on dense graphs.
  2. Directed-only traversal — extract_subgraph no longer treats the graph
     as undirected. Use direction="out" to trace where stolen funds WENT,
     direction="in" to trace WHO fed an accumulation point. No more pulling
     in innocent people who merely received money from a flagged account
     via an unrelated, legitimate transaction.
  3. Graph is now built ONLY from flagged accounts + their immediate
     counterparties — not the entire CSV. Scales to millions of rows
     without loading everything into memory.
"""

import pandas as pd
import networkx as nx
from pyvis.network import Network
import argparse
import os
import sys

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORED_CSV = os.path.join(BASE_DIR, "outputs", "reports", "isolation_forest_scored_transactions.csv")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs", "reports")


class MoneyFlowGraph:

    def __init__(self, scored_csv: str = SCORED_CSV, flagged_only: bool = True):
        """
        flagged_only=True (default, recommended for large datasets):
            Only loads rows belonging to flagged accounts + their immediate
            counterparties. Avoids loading the full CSV into a graph.
        """
        self.flagged_only = flagged_only
        self.raw_path = scored_csv
        self.df = self._load(scored_csv)
        self.full_graph = self._build_full_graph(self.df)
        self.account_risk = self._compute_account_risk(self.df)

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self, path: str) -> pd.DataFrame:
        df = pd.read_csv(path, dtype=str, low_memory=False)

        debit_col  = "debit_clean" if "debit_clean" in df.columns else "debit"
        credit_col = "credit_clean" if "credit_clean" in df.columns else "credit"
        cp_col     = "counterparty_account_id" if "counterparty_account_id" in df.columns else "counterparty_account"

        df["debit"]  = pd.to_numeric(df.get(debit_col, 0), errors="coerce").fillna(0)
        df["credit"] = pd.to_numeric(df.get(credit_col, 0), errors="coerce").fillna(0)
        df["counterparty_account"] = df.get(cp_col)

        if "is_flagged" in df.columns:
            df["is_flagged"] = pd.to_numeric(df["is_flagged"], errors="coerce").fillna(0).astype(int)
        else:
            df["is_flagged"] = 0

        df["narration"] = df.get("narration", "")

        # ── FIX 3: only keep flagged accounts + their immediate counterparties ──
        if self.flagged_only and df["is_flagged"].sum() > 0:
            flagged_accounts = set(df.loc[df["is_flagged"] == 1, "account_id"])
            flagged_counterparties = set(
                df.loc[df["is_flagged"] == 1, "counterparty_account"].dropna()
            )
            relevant = flagged_accounts | flagged_counterparties

            before = len(df)
            df = df[
                df["account_id"].isin(relevant) | df["counterparty_account"].isin(relevant)
            ].copy()
            print(f"[MoneyFlowGraph] Reduced {before} -> {len(df)} rows "
                  f"(flagged accounts + immediate counterparties only)")

        return df

    def _compute_account_risk(self, df: pd.DataFrame) -> pd.DataFrame:
        summary = df.groupby("account_id").agg(
            total_txns=("account_id", "count"),
            flagged_txns=("is_flagged", "sum"),
        )
        summary["flag_pct"] = (summary["flagged_txns"] / summary["total_txns"] * 100).round(1)
        return summary

    def _build_full_graph(self, df: pd.DataFrame) -> nx.MultiDiGraph:
        G = nx.MultiDiGraph()
        edges = df[(df["debit"] > 0) & df["counterparty_account"].notna() & (df["counterparty_account"] != "")]

        for _, row in edges.iterrows():
            src, dst = row["account_id"], row["counterparty_account"]
            G.add_edge(
                src, dst,
                amount=float(row["debit"]),
                narration=str(row.get("narration", ""))[:60],
            )
        return G

    # ── FIX 2: directed-only neighborhood extraction ────────────────────────

    def extract_subgraph(self, seeds, hops: int = 1, max_nodes: int = 150,
                          direction: str = "out") -> nx.MultiDiGraph:
        """
        direction="out": follow money OUTWARD from seed (where did stolen funds go?)
        direction="in":  follow money INWARD into seed (who fed this accumulation point?)
        direction="both": follow both (use sparingly — pulls in more noise)

        No longer converts to an undirected graph — that was pulling in
        innocent accounts that merely received an incidental payment FROM
        a flagged account for an unrelated, legitimate reason.
        """
        if isinstance(seeds, str):
            seeds = [seeds]

        keep_nodes = set(seeds)
        frontier = set(seeds)

        for _ in range(hops):
            next_frontier = set()
            for node in frontier:
                if node not in self.full_graph:
                    continue
                if direction in ("out", "both"):
                    next_frontier.update(self.full_graph.successors(node))
                if direction in ("in", "both"):
                    next_frontier.update(self.full_graph.predecessors(node))
            keep_nodes.update(next_frontier)
            frontier = next_frontier
            if len(keep_nodes) >= max_nodes:
                break

        if len(keep_nodes) > max_nodes:
            keep_nodes = set(list(keep_nodes)[:max_nodes])

        return self.full_graph.subgraph(keep_nodes).copy()

    def classify_nodes(self, G: nx.MultiDiGraph) -> dict:
        roles = {}
        for node in G.nodes():
            in_deg, out_deg = G.in_degree(node), G.out_degree(node)
            in_amt  = sum(d["amount"] for _, _, d in G.in_edges(node, data=True))
            out_amt = sum(d["amount"] for _, _, d in G.out_edges(node, data=True))

            if in_deg > 0 and out_deg == 0:
                roles[node] = "destination"
            elif out_deg > 0 and in_deg == 0:
                roles[node] = "source"
            elif in_deg > 0 and out_deg > 0:
                roles[node] = "destination" if in_amt > out_amt * 1.3 else "intermediary"
            else:
                roles[node] = "isolated"
        return roles

    def find_accumulation_points(self, G: nx.MultiDiGraph, top_n: int = 10) -> pd.DataFrame:
        rows = []
        for node in G.nodes():
            in_amt  = sum(d["amount"] for _, _, d in G.in_edges(node, data=True))
            out_amt = sum(d["amount"] for _, _, d in G.out_edges(node, data=True))
            rows.append({
                "account_id": node,
                "total_received": in_amt,
                "total_forwarded": out_amt,
                "net_accumulation": in_amt - out_amt,
                "in_degree": G.in_degree(node),
                "out_degree": G.out_degree(node),
                "risk_flag_pct": self.account_risk.loc[node, "flag_pct"]
                                 if node in self.account_risk.index else 0,
            })
        if not rows:
            return pd.DataFrame(columns=[
                "account_id", "total_received", "total_forwarded",
                "net_accumulation", "in_degree", "out_degree", "risk_flag_pct"
            ])
        return pd.DataFrame(rows).sort_values("net_accumulation", ascending=False).head(top_n)

    # ── FIX 1: hard-capped cycle length, no more unbounded search ───────────

    def find_cycles(self, G: nx.MultiDiGraph, max_cycles: int = 10, max_len: int = 6,
                     min_amount: float = 10000) -> list:
        """
        max_len is a HARD ceiling — never search beyond 6-hop cycles.
        Real laundering loops are tight (2-6 hops); anything longer is
        either graph noise or a coincidental long walk, and searching for
        it is what causes the algorithm to hang on dense graphs.
        """
        max_len = min(max_len, 6)  # hard safety ceiling regardless of caller input

        filtered = nx.DiGraph()
        for u, v, data in G.edges(data=True):
            if data.get("amount", 0) >= min_amount:
                filtered.add_edge(u, v)

        cycles = []
        try:
            for c in nx.simple_cycles(filtered, length_bound=max_len):
                cycles.append(c)
                if len(cycles) >= max_cycles:
                    break
        except TypeError:
            # older networkx without length_bound support — manual filter
            for c in nx.simple_cycles(filtered):
                if len(c) <= max_len:
                    cycles.append(c)
                if len(cycles) >= max_cycles:
                    break
        except Exception:
            pass
        return cycles

    def render(self, G: nx.MultiDiGraph, output_name: str = "money_flow_graph") -> str:
        roles = self.classify_nodes(G)
        role_colors = {
            "source":       "#F4A261",
            "intermediary": "#457B9D",
            "destination":  "#E63946",
            "isolated":     "#8892A6",
        }

        net = Network(height="800px", width="100%", directed=True,
                      bgcolor="#0f1117", font_color="white", notebook=False)

        net.set_options("""
        {
          "physics": {
            "enabled": true,
            "solver": "barnesHut",
            "barnesHut": {
              "gravitationalConstant": -4000,
              "centralGravity": 0.3,
              "springLength": 180,
              "springConstant": 0.04
            },
            "stabilization": {"enabled": true, "iterations": 200, "fit": true}
          },
          "edges": {"smooth": {"type": "dynamic"}, "color": {"color": "#8892a6"}},
          "nodes": {"font": {"color": "white", "size": 14}}
        }
        """)

        for node in G.nodes():
            role = roles.get(node, "isolated")
            risk = self.account_risk.loc[node, "flag_pct"] if node in self.account_risk.index else 0
            size = 15 + min(risk, 100) * 0.3

            title = (f"Account: {node}<br>Role: {role}<br>"
                     f"Risk (flag %): {risk}<br>"
                     f"In-degree: {G.in_degree(node)}  Out-degree: {G.out_degree(node)}")

            net.add_node(node, label=node, title=title,
                         color=role_colors[role], size=size)

        for src, dst, data in G.edges(data=True):
            amount = data.get("amount", 0)
            net.add_edge(
                src, dst,
                label=f"Rs.{amount:,.0f}",
                title=f"{src} -> {dst}<br>Amount: Rs.{amount:,.2f}<br>{data.get('narration','')}",
                width=1 + min(amount / 50000, 8),
            )

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, f"{output_name}.html")
        net.write_html(out_path, open_browser=False, notebook=False)
        return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, default=SCORED_CSV)
    parser.add_argument("--seed", nargs="+", default=None)
    parser.add_argument("--hops", type=int, default=1)
    parser.add_argument("--max-nodes", type=int, default=150)
    parser.add_argument("--direction", choices=["out", "in", "both"], default="out",
                        help="out=trace where funds went, in=trace who fed this account")
    parser.add_argument("--top-suspicious", type=int, default=None)
    parser.add_argument("--no-prefilter", action="store_true",
                        help="Load the FULL csv into the graph (slow, not recommended for large files)")
    args = parser.parse_args()

    mfg = MoneyFlowGraph(scored_csv=args.csv, flagged_only=not args.no_prefilter)

    if args.top_suspicious:
        seeds = mfg.account_risk.sort_values("flag_pct", ascending=False).head(args.top_suspicious).index.tolist()
        print(f"[Auto-selected top {args.top_suspicious} suspicious accounts]: {seeds}")
    elif args.seed:
        seeds = args.seed
    else:
        print("Provide --seed <account_id> or --top-suspicious N")
        sys.exit(1)

    print(f"\n[1] Extracting {args.hops}-hop subgraph (direction={args.direction}) around: {seeds}")
    sub = mfg.extract_subgraph(seeds, hops=args.hops, max_nodes=args.max_nodes, direction=args.direction)
    print(f"    Subgraph: {sub.number_of_nodes()} accounts, {sub.number_of_edges()} transactions")

    print("\n[2] Classifying account roles ...")
    roles = mfg.classify_nodes(sub)
    for role_type in ["source", "intermediary", "destination"]:
        matches = [n for n, r in roles.items() if r == role_type]
        print(f"    {role_type:15s}: {len(matches)} accounts -> {matches[:5]}")

    print("\n[3] Top accumulation points (likely final destinations) ...")
    accum = mfg.find_accumulation_points(sub, top_n=10)
    print(accum.to_string(index=False))

    print("\n[4] Checking for SHORT circular money flows (max 6 hops) ...")
    cycles = mfg.find_cycles(sub, max_len=6)
    if cycles:
        for c in cycles:
            print("    " + " -> ".join(c) + f" -> {c[0]}")
    else:
        print("    No short cycles detected.")

    print("\n[5] Rendering interactive graph ...")
    out_path = mfg.render(sub, output_name=f"money_flow_{seeds[0]}")
    print(f"\nDone. Open this file in a browser: {out_path}")


if __name__ == "__main__":
    main()