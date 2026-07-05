"""
Router: /api/v1/graph

Produces Cytoscape.js-ready node/edge payloads for:
  GET /fund-trace/{account_id}   Ego graph N hops from a seed account
  GET /ring/{ring_id}            Full ring subgraph
  GET /full                      Entire transaction graph (paginated,
                                 for overview visualization)

Node = Account.  Edge = internal Transaction (counterparty_account_id != null).

Edge weight = total amount transferred between the pair.
Risk score encoded as node colour gradient in the frontend.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from collections import defaultdict

from dependencies import get_db
from models import Account, Transaction, FraudRing, FraudRingMember
from schemas import GraphData, GraphNode, GraphEdge

router = APIRouter(prefix="/graph", tags=["Graph"])


async def _build_graph_from_account_ids(
    account_ids: list[str],
    db: AsyncSession,
    seed_account_id: Optional[str] = None,
) -> GraphData:
    """
    Core graph builder. Given a set of account IDs:
    1. Load account metadata (nodes)
    2. Load all internal transactions between those accounts (edges)
    3. Return Cytoscape.js-compatible payload
    """
    if not account_ids:
        return GraphData(nodes=[], edges=[], total_nodes=0,
                         total_edges=0, seed_account_id=seed_account_id)

    # --- Nodes ---
    acct_q = select(Account).where(Account.account_id.in_(account_ids))
    accounts = (await db.execute(acct_q)).scalars().all()

    # Get per-account transaction stats
    stats_q = (
        select(
            Transaction.account_id,
            func.count().label("txn_count"),
            func.sum(Transaction.debit).label("total_debit"),
            func.sum(Transaction.credit).label("total_credit"),
        )
        .where(Transaction.account_id.in_(account_ids))
        .group_by(Transaction.account_id)
    )
    stats_rows = (await db.execute(stats_q)).all()
    stats = {r.account_id: r for r in stats_rows}

    nodes = []
    for acct in accounts:
        s = stats.get(acct.account_id)
        nodes.append(GraphNode(
            id=acct.account_id,
            label=acct.holder_name,
            bank=acct.bank_name,
            risk_score=acct.risk_score,
            is_suspect=acct.is_suspect,
            fraud_role=acct.fraud_role,
            total_debit=float(s.total_debit or 0) if s else 0.0,
            total_credit=float(s.total_credit or 0) if s else 0.0,
            txn_count=int(s.txn_count or 0) if s else 0,
        ))

    # --- Edges: aggregate transfers between account pairs ---
    edge_q = (
        select(
            Transaction.account_id,
            Transaction.counterparty_account_id,
            Transaction.channel,
            Transaction.date,
            Transaction.utr_ref,
            func.sum(Transaction.debit + Transaction.credit).label("total_amount"),
            func.count().label("txn_count"),
            func.max(func.cast(Transaction.is_high_value_flag, func.Integer())).label("any_flag"),
        )
        .where(
            Transaction.account_id.in_(account_ids),
            Transaction.counterparty_account_id.in_(account_ids),
            Transaction.counterparty_account_id.isnot(None),
        )
        .group_by(
            Transaction.account_id,
            Transaction.counterparty_account_id,
            Transaction.channel,
            Transaction.date,
            Transaction.utr_ref,
        )
    )
    edge_rows = (await db.execute(edge_q)).all()

    # Deduplicate bidirectional pairs — keep one edge per pair
    seen_pairs = set()
    edges = []
    for i, row in enumerate(edge_rows):
        pair = tuple(sorted([row.account_id, row.counterparty_account_id]))
        edge_key = (pair, row.channel, row.date or "")
        if edge_key in seen_pairs:
            continue
        seen_pairs.add(edge_key)

        edges.append(GraphEdge(
            id=row.utr_ref or f"edge_{i}",
            source=row.account_id,
            target=row.counterparty_account_id,
            amount=float(row.total_amount or 0),
            channel=row.channel or "OTHER",
            date=row.date,
            is_fraud_flagged=bool(row.any_flag),
        ))

    return GraphData(
        nodes=nodes,
        edges=edges,
        total_nodes=len(nodes),
        total_edges=len(edges),
        seed_account_id=seed_account_id,
    )


# ---------------------------------------------------------------------------
# Fund trace — N-hop ego graph from a seed account
# ---------------------------------------------------------------------------
@router.get("/fund-trace/{account_id}", response_model=GraphData)
async def fund_trace(
    account_id: str,
    hops: int = Query(default=2, ge=1, le=4),
    db: AsyncSession = Depends(get_db),
):
    """
    Starting from seed account, expand N hops via internal counterparty
    links. This is the primary investigator tool: "starting from this
    victim/mule account, show me the full money trail."

    hops=1 → direct counterparties only
    hops=2 → counterparties of counterparties (typical for layering)
    hops=3 → full ring exposure (fan-in/fan-out)
    """
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # BFS expansion
    visited = {account_id}
    frontier = {account_id}

    for _ in range(hops):
        if not frontier:
            break
        cp_q = (
            select(Transaction.counterparty_account_id)
            .where(
                Transaction.account_id.in_(frontier),
                Transaction.counterparty_account_id.isnot(None),
            )
            .distinct()
        )
        next_ids = set((await db.execute(cp_q)).scalars().all()) - visited
        visited |= next_ids
        frontier = next_ids

    return await _build_graph_from_account_ids(
        list(visited), db, seed_account_id=account_id
    )


# ---------------------------------------------------------------------------
# Ring subgraph
# ---------------------------------------------------------------------------
@router.get("/ring/{ring_id}", response_model=GraphData)
async def ring_graph(ring_id: str, db: AsyncSession = Depends(get_db)):
    """
    Full graph for a specific fraud ring.
    Used in the investigation report and ring detail panel.
    """
    ring = await db.get(FraudRing, ring_id)
    if not ring:
        raise HTTPException(status_code=404, detail="Fraud ring not found")

    member_q = select(FraudRingMember.account_id).where(
        FraudRingMember.ring_id == ring_id
    )
    account_ids = (await db.execute(member_q)).scalars().all()

    return await _build_graph_from_account_ids(
        list(account_ids), db, seed_account_id=None
    )


# ---------------------------------------------------------------------------
# Full graph (paginated by account count)
# ---------------------------------------------------------------------------
@router.get("/full", response_model=GraphData)
async def full_graph(
    limit_accounts: int = Query(default=100, ge=10, le=500),
    suspect_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Overview graph — all accounts and their internal connections.
    limit_accounts caps node count for frontend performance.
    suspect_only=True shows only high-risk accounts and their neighbours.
    """
    q = select(Account.account_id).order_by(Account.risk_score.desc())
    if suspect_only:
        q = q.where(Account.is_suspect == True)
    q = q.limit(limit_accounts)

    account_ids = (await db.execute(q)).scalars().all()
    return await _build_graph_from_account_ids(list(account_ids), db)


# ---------------------------------------------------------------------------
# Cytoscape Overview — returns Cytoscape-compatible nested-data format
# used by the frontend incremental graph overview (top N accounts)
# ---------------------------------------------------------------------------
@router.get("/cytoscape-overview")
async def cytoscape_overview(
    limit_accounts: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a Cytoscape.js-ready payload ({nodes: [{data:{...}}], edges: [{data:{...}}]})
    for the top `limit_accounts` accounts by risk score.
    This format matches the frontend CytoscapeGraph interface exactly.
    """
    # Fetch top accounts by risk score
    q = select(Account).order_by(Account.risk_score.desc()).limit(limit_accounts)
    accounts = (await db.execute(q)).scalars().all()

    if not accounts:
        return {"nodes": [], "edges": []}

    account_ids = [a.account_id for a in accounts]

    # Per-account transaction stats
    stats_q = (
        select(
            Transaction.account_id,
            func.count().label("txn_count"),
        )
        .where(Transaction.account_id.in_(account_ids))
        .group_by(Transaction.account_id)
    )
    stats_rows = (await db.execute(stats_q)).all()
    txn_counts = {r.account_id: int(r.txn_count or 0) for r in stats_rows}

    def risk_tier(score: float) -> str:
        if score >= 75: return "CRITICAL"
        if score >= 50: return "HIGH"
        if score >= 25: return "MEDIUM"
        return "LOW"

    # Build Cytoscape nodes
    nodes = []
    for acct in accounts:
        score = float(acct.risk_score or 0)
        nodes.append({
            "data": {
                "id": acct.account_id,
                "label": acct.holder_name or acct.account_id,
                "bank": acct.bank_name or "Unknown Bank",
                "risk_score": score,
                "risk_tier": risk_tier(score),
                "role": acct.fraud_role or "unknown",
                "is_seed": False,
                "is_internal": True,
                "txn_count": txn_counts.get(acct.account_id, 0),
            }
        })

    # Build Cytoscape edges — internal transfers between overview accounts only
    edge_q = (
        select(
            Transaction.account_id,
            Transaction.counterparty_account_id,
            Transaction.utr_ref,
            Transaction.date,
            Transaction.is_high_value_flag,
            func.sum(Transaction.debit + Transaction.credit).label("total_amount"),
        )
        .where(
            Transaction.account_id.in_(account_ids),
            Transaction.counterparty_account_id.in_(account_ids),
            Transaction.counterparty_account_id.isnot(None),
        )
        .group_by(
            Transaction.account_id,
            Transaction.counterparty_account_id,
            Transaction.utr_ref,
            Transaction.date,
            Transaction.is_high_value_flag,
        )
    )
    edge_rows = (await db.execute(edge_q)).all()

    seen_pairs: set = set()
    edges = []
    for i, row in enumerate(edge_rows):
        pair = tuple(sorted([row.account_id, row.counterparty_account_id]))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        risk_flag = "SUSPICIOUS" if row.is_high_value_flag else "NORMAL"
        edges.append({
            "data": {
                "id": row.utr_ref or f"ov_edge_{i}",
                "source": row.account_id,
                "target": row.counterparty_account_id,
                "amount": float(row.total_amount or 0),
                "dates": [str(row.date)] if row.date else [],
                "risk_flag": risk_flag,
            }
        })

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Ledger trace — Load pre-generated Cytoscape JSON or fallback parse ledger CSV
# ---------------------------------------------------------------------------
@router.get("/ledger-trace/{account_id}")
async def get_ledger_trace(account_id: str, db: AsyncSession = Depends(get_db)):
    """
    Load pre-generated Cytoscape JSON for an account ledger.
    If not found, parses the CSV and returns dynamically generated Cytoscape format.
    If CSV is also not found, falls back to querying the database dynamically.
    """
    from pathlib import Path
    import json
    import os
    import pandas as pd

    # 1. Try to find ledger_{account_id}_graph.json
    project_root = Path(__file__).resolve().parents[2]
    dirs_to_try = [
        project_root / "data" / "analytics_v2",
        project_root / "phase8" / "analytics_v2",
        project_root / "phase8" / "analytics_final",
        project_root / "phase8" / "analytics",
    ]
    
    json_path = None
    csv_path = None
    
    for d in dirs_to_try:
        j_p = d / f"ledger_{account_id}_graph.json"
        c_p = d / f"ledger_{account_id}.csv"
        if j_p.exists():
            json_path = j_p
            break
        elif c_p.exists():
            csv_path = c_p
    
    if json_path:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass # fallback to CSV
            
    # 2. Dynamic CSV parsing fallback
    if not csv_path:
        for d in dirs_to_try:
            c_p = d / f"ledger_{account_id}.csv"
            if c_p.exists():
                csv_path = c_p
                break
                
    if csv_path:
        try:
            df = pd.read_csv(csv_path, dtype=str)
            
            # Read risk scores from risk_scores.csv if possible to color nodes
            risk_map = {}
            for d in dirs_to_try:
                r_csv = d / "risk_scores.csv"
                if r_csv.exists():
                    try:
                        r_df = pd.read_csv(r_csv, dtype=str)
                        for _, row in r_df.iterrows():
                            risk_map[row["account_id"]] = {
                                "risk_score": float(row.get("risk_score", 0.0)),
                                "risk_tier": row.get("risk_tier", "LOW")
                            }
                        break
                    except Exception:
                        pass
            
            nodes = []
            edges = []
            added = set()
            
            def make_node(nid):
                risk_info = risk_map.get(nid, {})
                risk_score = risk_info.get("risk_score", 0.0)
                risk_tier = risk_info.get("risk_tier", "LOW")
                if not risk_score and nid == account_id:
                    risk_score = 80.0
                    risk_tier = "CRITICAL"
                
                return {
                    "data": {
                        "id": nid,
                        "label": nid,
                        "bank": "UNKNOWN Bank",
                        "risk_score": risk_score,
                        "risk_tier": risk_tier,
                        "role": "mule" if nid == account_id else "unknown",
                        "is_seed": nid == account_id,
                        "is_internal": nid.isdigit() and len(nid) >= 10
                    }
                }
                
            nodes.append(make_node(account_id))
            added.add(account_id)
            
            edge_map = {}
            for _, row in df.iterrows():
                src = str(row.get("credit_source", "UNKNOWN")).strip()
                dst = str(row.get("debit_destination", "UNKNOWN")).strip()
                amt = float(row.get("allocation_amount", 0.0))
                date = str(row.get("debit_date", ""))
                risk = str(row.get("risk_flag", "NORMAL"))
                
                if src not in added:
                    nodes.append(make_node(src))
                    added.add(src)
                if dst not in added:
                    nodes.append(make_node(dst))
                    added.add(dst)
                    
                if src != account_id:
                    k1 = (src, account_id)
                    if k1 not in edge_map:
                        edge_map[k1] = {"amount": 0.0, "dates": set(), "risks": set()}
                    edge_map[k1]["amount"] += amt
                    edge_map[k1]["dates"].add(date)
                    edge_map[k1]["risks"].add(risk)
                    
                if dst != account_id:
                    k2 = (account_id, dst)
                    if k2 not in edge_map:
                        edge_map[k2] = {"amount": 0.0, "dates": set(), "risks": set()}
                    edge_map[k2]["amount"] += amt
                    edge_map[k2]["dates"].add(date)
                    edge_map[k2]["risks"].add(risk)
                    
            for idx, ((s, t), info) in enumerate(edge_map.items()):
                edges.append({
                    "data": {
                        "id": f"e_{account_id}_{idx}",
                        "source": s,
                        "target": t,
                        "amount": info["amount"],
                        "dates": sorted(list(info["dates"])),
                        "risk_flag": "SUSPICIOUS" if "SUSPICIOUS" in info["risks"] else "NORMAL"
                    }
                })
                
            return {"nodes": nodes, "edges": edges}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed parsing ledger: {str(e)}")

    # 3. Dynamic Database parsing fallback (when files do not exist for this neighbor/account)
    try:
        from models import Transaction, Account
        
        # Query transactions of the account
        seed_acct = await db.get(Account, account_id)
        if not seed_acct:
            txn_stmt = select(Transaction).where(
                or_(
                    Transaction.counterparty_account_id == account_id,
                    Transaction.counterparty_name == account_id
                )
            ).order_by(Transaction.date, Transaction.time)
            cp_txns = (await db.execute(txn_stmt)).scalars().all()
            if cp_txns:
                internal_ids = list({t.account_id for t in cp_txns})
                risk_map = {}
                if internal_ids:
                    acct_stmt = select(Account).where(Account.account_id.in_(internal_ids))
                    db_accts = (await db.execute(acct_stmt)).scalars().all()
                    for a in db_accts:
                        risk_map[a.account_id] = {
                            "risk_score": float(a.risk_score),
                            "risk_tier": "CRITICAL" if a.risk_score >= 75 else "HIGH" if a.risk_score >= 50 else "MEDIUM" if a.risk_score >= 25 else "LOW"
                        }
                
                from routers.accounts import _is_merchant
                is_merch = _is_merchant(account_id)
                seed_score = 15.0 if is_merch else 40.0
                seed_risk_tier = "LOW" if is_merch else "MEDIUM"
                
                nodes = []
                edges = []
                added = set()
                
                def make_node(nid, is_seed_node=False):
                    if is_seed_node:
                        return {
                            "data": {
                                "id": nid,
                                "label": nid,
                                "bank": "Merchant/Gateway" if is_merch else "External Entity",
                                "risk_score": seed_score,
                                "risk_tier": seed_risk_tier,
                                "role": "merchant" if is_merch else "counterparty",
                                "is_seed": True,
                                "is_internal": False
                            }
                        }
                    risk_info = risk_map.get(nid, {})
                    risk_score = risk_info.get("risk_score", 0.0)
                    risk_tier = risk_info.get("risk_tier", "LOW")
                    return {
                        "data": {
                            "id": nid,
                            "label": nid,
                            "bank": "UNKNOWN Bank",
                            "risk_score": risk_score,
                            "risk_tier": risk_tier,
                            "role": "unknown",
                            "is_seed": False,
                            "is_internal": True
                        }
                    }
                
                nodes.append(make_node(account_id, is_seed_node=True))
                added.add(account_id)
                
                edge_map = {}
                for row in cp_txns:
                    src = row.account_id
                    dst = account_id
                    debit_amt = float(row.debit or 0.0)
                    credit_amt = float(row.credit or 0.0)
                    
                    if debit_amt > 0:
                        edge_src = src
                        edge_dst = dst
                        amt = debit_amt
                    else:
                        edge_src = dst
                        edge_dst = src
                        amt = credit_amt
                        
                    date = str(row.date or "")
                    has_flags = row.is_high_value_flag or row.is_balance_breach or (row.final_risk_score and row.final_risk_score >= 0.7)
                    risk_flag = 'SUSPICIOUS' if has_flags else 'NORMAL'
                    
                    k = (edge_src, edge_dst)
                    if k not in edge_map:
                        edge_map[k] = {"amount": 0.0, "dates": set(), "risks": set()}
                    edge_map[k]["amount"] += amt
                    edge_map[k]["dates"].add(date)
                    edge_map[k]["risks"].add(risk_flag)
                    
                    if src not in added:
                        nodes.append(make_node(src))
                        added.add(src)
                        
                for idx, ((s, t), info) in enumerate(edge_map.items()):
                    edges.append({
                        "data": {
                            "id": f"e_{account_id}_{idx}",
                            "source": s,
                            "target": t,
                            "amount": info["amount"],
                            "dates": sorted(list(info["dates"])),
                            "risk_flag": "SUSPICIOUS" if "SUSPICIOUS" in info["risks"] else "NORMAL"
                        }
                    })
                    
                return {"nodes": nodes, "edges": edges}

        txn_stmt = select(Transaction).where(Transaction.account_id == account_id).order_by(Transaction.date, Transaction.time)
        db_txns = (await db.execute(txn_stmt)).scalars().all()
        
        if db_txns:
            credit_queue = []
            ledger_rows = []
            
            # Fetch risk score mapping for counterparties
            counterparty_ids = set()
            for t in db_txns:
                if t.counterparty_account_id:
                    counterparty_ids.add(t.counterparty_account_id)
            
            risk_map = {}
            if counterparty_ids:
                acct_stmt = select(Account).where(Account.account_id.in_(list(counterparty_ids)))
                db_accts = (await db.execute(acct_stmt)).scalars().all()
                for a in db_accts:
                    risk_map[a.account_id] = {
                        "risk_score": float(a.risk_score),
                        "risk_tier": "CRITICAL" if a.risk_score >= 75 else "HIGH" if a.risk_score >= 50 else "MEDIUM" if a.risk_score >= 25 else "LOW"
                    }
            
            # Seed account risk info
            seed_acct = await db.get(Account, account_id)
            seed_score = float(seed_acct.risk_score) if seed_acct else 80.0
            seed_risk_tier = "CRITICAL" if seed_score >= 75 else "HIGH" if seed_score >= 50 else "MEDIUM" if seed_score >= 25 else "LOW"

            # Run FIFO allocation logic
            for row in db_txns:
                debit_amt = float(row.debit or 0.0)
                credit_amt = float(row.credit or 0.0)
                date_str = str(row.date or "")
                
                if credit_amt > 0:
                    source = row.counterparty_account_id or row.counterparty_name or "UNKNOWN"
                    credit_queue.append({
                        'date': date_str,
                        'amount': credit_amt,
                        'source': source,
                        'remaining': credit_amt
                    })
                    
                if debit_amt > 0:
                    destination = row.counterparty_account_id or row.counterparty_name or "UNKNOWN"
                    
                    # Determine risk flag
                    dest_risk = "LOW"
                    if destination in risk_map:
                        dest_risk = risk_map[destination].get("risk_tier", "LOW")
                    
                    has_flags = row.is_high_value_flag or row.is_balance_breach or (row.final_risk_score and row.final_risk_score >= 0.7)
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
                        
            # Build Cytoscape elements
            nodes = []
            edges = []
            added = set()
            
            def make_node(nid):
                risk_info = risk_map.get(nid, {})
                risk_score = risk_info.get("risk_score", 0.0)
                risk_tier = risk_info.get("risk_tier", "LOW")
                if not risk_score and nid == account_id:
                    risk_score = seed_score
                    risk_tier = seed_risk_tier
                
                return {
                    "data": {
                        "id": nid,
                        "label": nid,
                        "bank": "UNKNOWN Bank" if nid != account_id else (seed_acct.bank_name if seed_acct else "UNKNOWN Bank"),
                        "risk_score": risk_score,
                        "risk_tier": risk_tier,
                        "role": "mule" if nid == account_id else "unknown",
                        "is_seed": nid == account_id,
                        "is_internal": nid.isdigit() and len(nid) >= 10
                    }
                }
                
            nodes.append(make_node(account_id))
            added.add(account_id)
            
            edge_map = {}
            for row in ledger_rows:
                src = str(row.get("credit_source", "UNKNOWN")).strip()
                dst = str(row.get("debit_destination", "UNKNOWN")).strip()
                amt = float(row.get("allocation_amount", 0.0))
                date = str(row.get("debit_date", ""))
                risk = str(row.get("risk_flag", "NORMAL"))
                
                if src not in added:
                    nodes.append(make_node(src))
                    added.add(src)
                if dst not in added:
                    nodes.append(make_node(dst))
                    added.add(dst)
                    
                if src != account_id:
                    k1 = (src, account_id)
                    if k1 not in edge_map:
                        edge_map[k1] = {"amount": 0.0, "dates": set(), "risks": set()}
                    edge_map[k1]["amount"] += amt
                    edge_map[k1]["dates"].add(date)
                    edge_map[k1]["risks"].add(risk)
                    
                if dst != account_id:
                    k2 = (account_id, dst)
                    if k2 not in edge_map:
                        edge_map[k2] = {"amount": 0.0, "dates": set(), "risks": set()}
                    edge_map[k2]["amount"] += amt
                    edge_map[k2]["dates"].add(date)
                    edge_map[k2]["risks"].add(risk)
                    
            for idx, ((s, t), info) in enumerate(edge_map.items()):
                edges.append({
                    "data": {
                        "id": f"e_{account_id}_{idx}",
                        "source": s,
                        "target": t,
                        "amount": info["amount"],
                        "dates": sorted(list(info["dates"])),
                        "risk_flag": "SUSPICIOUS" if "SUSPICIOUS" in info["risks"] else "NORMAL"
                    }
                })
                
            return {"nodes": nodes, "edges": edges}
    except Exception as e:
        print(f"Failed dynamic database trace fallback: {str(e)}")

    raise HTTPException(status_code=404, detail=f"Ledger trace for account {account_id} not found")

