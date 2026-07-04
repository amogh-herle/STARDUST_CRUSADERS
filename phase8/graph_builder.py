"""
Phase 8 — Graph Builder

Builds two NetworkX graphs from the cleaned transaction dataframe:

  TXN_GRAPH   — directed multigraph where every edge is one transaction
                node = account_id
                edge attrs: amount, date, timestamp, utr_ref, narration, channel

  ACCOUNT_GRAPH — simple weighted digraph where edge weight = total flow
                  between two accounts (collapsed from TXN_GRAPH)
                  Used for community detection and risk propagation

Both graphs are returned so downstream modules (round-trip, layering,
fan-in/out, graph analytics) can share the same object rather than
rebuilding it each time.
"""

import re
import pandas as pd
import networkx as nx
from datetime import datetime


def is_merchant(node_name: str) -> bool:
    """Identify if a node is a merchant, payment gateway, utility, or bank name."""
    name_upper = str(node_name).upper().strip()
    if not name_upper or name_upper == "NAN" or name_upper == "NONE":
        return True

    # 1. Known merchants & payment aggregators
    merchant_keywords = {
        "AMAZON", "SWIGGY", "IRCTC", "FLIPKART", "ZOMATO", "UBER", "OLA",
        "ZEPTO", "BLINKIT", "PAYTM", "PHONEPE", "GPAY", "GOOGLE PAY", "BHIM",
        "CRED", "RAZORPAY", "CASHFREE", "PAYU", "BILLDESK", "PAYU@HDFCBANK",
        "BDPG@HDFCBANK", "JIO", "AIRTEL", "NETFLIX", "SPOTIFY", "TATACLIQ"
    }
    for kw in merchant_keywords:
        if kw in name_upper:
            return True

    # 2. Generic utility, fee, and system terms
    utility_keywords = {
        "HEAD OFFICE", "SELF", "CASH", "ATM", "25 NFS", "OPW", "MOB", 
        "NEFT", "IMPS", "UPI", "RTGS", "UNKNOWN", "FEE", "CHARGE", "INTEREST",
        "DECLINE", "MINIMUM BALANCE", "SMS"
    }
    for kw in utility_keywords:
        if kw in name_upper:
            return True

    return False


def build_graphs(df: pd.DataFrame) -> tuple[nx.MultiDiGraph, nx.DiGraph]:
    """
    Input : cleaned_transactions DataFrame (Phase 7 output)
    Output: (txn_graph, account_graph)

    Node attribute  is_internal=True  → account_id from the dataset
                    is_internal=False → external counterparty name (merchant, unknown)
    """
    txn_graph     = nx.MultiDiGraph()
    account_graph = nx.DiGraph()

    # All known internal account IDs
    internal_ids = set(df["account_id"].unique())

    # Add all internal account nodes first
    for acc_id in internal_ids:
        meta = df[df["account_id"] == acc_id].iloc[0]
        txn_graph.add_node(acc_id,
            holder=meta.get("account_holder", ""),
            bank=meta.get("bank_name", ""),
            is_internal=True,
            is_merchant=False,
        )
        account_graph.add_node(acc_id,
            holder=meta.get("account_holder", ""),
            bank=meta.get("bank_name", ""),
            is_internal=True,
            is_merchant=False,
        )

    # Process transfer edges. Prefer counterparty_account for internal linkage;
    # fall back to normalised counterparty_name for external entities.
    transfer_df = df[df["debit"] > 0].copy()

    for row_idx, row in transfer_df.iterrows():
        src = row["account_id"]  # Keep as native type (numpy.int64) to match internal node types
        dst = _counterparty_identity(row)
        if not dst:
            continue

        amt = float(row["debit"])
        ts = _parse_ts(row)
        utr = str(row.get("utr_ref", "")).strip()
        nar = str(row.get("narration", "")).strip()
        chan = str(row.get("channel", "")).strip()
        date = str(row.get("date", "")).strip()

        dst_is_internal = False
        for i_id in internal_ids:
            if str(i_id).strip() == str(dst).strip():
                dst_is_internal = True
                dst = i_id
                break

        dst_is_merchant = not dst_is_internal and is_merchant(dst)

        if dst not in txn_graph:
            txn_graph.add_node(dst, holder=dst, bank="UNKNOWN", is_internal=dst_is_internal, is_merchant=dst_is_merchant)
        if dst not in account_graph:
            account_graph.add_node(dst, holder=dst, bank="UNKNOWN", is_internal=dst_is_internal, is_merchant=dst_is_merchant)

        edge_attrs = dict(
            amount=amt,
            timestamp=ts,
            date=date,
            utr_ref=utr,
            narration=nar,
            channel=chan,
            account_id=src,
            row_idx=int(row_idx),
            dst_is_internal=dst_is_internal,
        )
        txn_graph.add_edge(src, dst, **edge_attrs)

        if account_graph.has_edge(src, dst):
            account_graph[src][dst]["total_amount"] += amt
            account_graph[src][dst]["txn_count"] += 1
            account_graph[src][dst]["row_indices"].append(int(row_idx))
        else:
            account_graph.add_edge(src, dst, total_amount=amt, txn_count=1, row_indices=[int(row_idx)])

    # Process credit transfer edges (inflows to internal accounts from external accounts)
    credit_df = df[df["credit"] > 0].copy()
    for row_idx, row in credit_df.iterrows():
        dst = row["account_id"]  # recipient (internal)
        src = _counterparty_identity(row)  # sender (could be external)
        if not src:
            continue
        # If the sender is internal, we already captured this from the sender's debit row
        src_is_internal = False
        for i_id in internal_ids:
            if str(i_id).strip() == str(src).strip():
                src_is_internal = True
                break
        if src_is_internal:
            continue

        amt = float(row["credit"])
        ts = _parse_ts(row)
        utr = str(row.get("utr_ref", "")).strip()
        nar = str(row.get("narration", "")).strip()
        chan = str(row.get("channel", "")).strip()
        date = str(row.get("date", "")).strip()

        src_is_merchant = is_merchant(src)

        if src not in txn_graph:
            txn_graph.add_node(src, holder=src, bank="UNKNOWN", is_internal=False, is_merchant=src_is_merchant)
        if src not in account_graph:
            account_graph.add_node(src, holder=src, bank="UNKNOWN", is_internal=False, is_merchant=src_is_merchant)

        edge_attrs = dict(
            amount=amt,
            timestamp=ts,
            date=date,
            utr_ref=utr,
            narration=nar,
            channel=chan,
            account_id=src,
            row_idx=int(row_idx),
            dst_is_internal=True,
        )
        txn_graph.add_edge(src, dst, **edge_attrs)

        if account_graph.has_edge(src, dst):
            account_graph[src][dst]["total_amount"] += amt
            account_graph[src][dst]["txn_count"] += 1
            account_graph[src][dst]["row_indices"].append(int(row_idx))
        else:
            account_graph.add_edge(src, dst, total_amount=amt, txn_count=1, row_indices=[int(row_idx)])

    return txn_graph, account_graph


def _counterparty_identity(row) -> str:
    for col in ("counterparty_account", "counterparty_name"):
        val = row.get(col, "")
        if pd.isna(val):
            continue
        s = str(val).strip()
        if s and s.lower() not in {"nan", "none", "null"}:
            return re.sub(r"\s+", " ", s).upper()
    return ""


def _parse_ts(row) -> datetime:
    """Parse a timestamp from date + time columns, fall back gracefully."""
    date_str = str(row.get("date", "")).strip()
    time_str = str(row.get("time", "00:00:00")).strip()
    if not time_str or time_str == "nan":
        time_str = "00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return datetime(2000, 1, 1)


def graph_summary(txn_graph: nx.MultiDiGraph, account_graph: nx.DiGraph) -> dict:
    internal_nodes = [n for n, d in account_graph.nodes(data=True) if d.get("is_internal")]
    external_nodes = account_graph.number_of_nodes() - len(internal_nodes)
    density = nx.density(account_graph) if account_graph.number_of_nodes() > 1 else 0.0
    return {
        "txn_graph_nodes": txn_graph.number_of_nodes(),
        "txn_graph_edges": txn_graph.number_of_edges(),
        "account_graph_nodes": account_graph.number_of_nodes(),
        "account_graph_edges": account_graph.number_of_edges(),
        "internal_account_nodes": len(internal_nodes),
        "external_counterparty_nodes": external_nodes,
        "weakly_connected_comps": nx.number_weakly_connected_components(account_graph) if account_graph.number_of_nodes() else 0,
        "density": round(float(density), 6),
    }
