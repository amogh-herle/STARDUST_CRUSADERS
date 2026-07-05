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

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, String
from sqlalchemy.ext.asyncio import AsyncSession
from collections import defaultdict

from dependencies import get_db
from models import Account, Transaction, FraudRing, FraudRingMember
from schemas import (
    GraphData, GraphNode, GraphEdge,
    MoneyTrailResponse, SeedCredit, MoneyTrailHop, MoneyTrailNode, SourceCreditAllocation,
    CreditTrailInfo
)

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
        project_root / "phase8" / "analytics",
        project_root / "phase8" / "analytics_final",
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

    # Fallback: Return a single-node graph if no files or transactions exist
    try:
        from models import Account
        seed_acct = await db.get(Account, account_id)
        seed_score = float(seed_acct.risk_score) if seed_acct else 80.0
        seed_risk_tier = "CRITICAL" if seed_score >= 75 else "HIGH" if seed_score >= 50 else "MEDIUM" if seed_score >= 25 else "LOW"
        node = {
            "data": {
                "id": account_id,
                "label": account_id,
                "bank": seed_acct.bank_name if (seed_acct and seed_acct.bank_name) else "UNKNOWN Bank",
                "risk_score": seed_score,
                "risk_tier": seed_risk_tier,
                "role": "mule",
                "is_seed": True,
                "is_internal": True
            }
        }
        return {"nodes": [node], "edges": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate fallback ledger trace: {str(e)}")


# ---------------------------------------------------------------------------
# FIFO Money Trail flow tracing helper functions and endpoint
# ---------------------------------------------------------------------------

async def find_matching_credit_txn_db(
    db: AsyncSession,
    sender_acc: str,
    receiver_acc: str,
    debit_amount: float,
    debit_date: str,
    debit_time: Optional[str],
    debit_utr: Optional[str]
) -> Optional[Transaction]:
    if not receiver_acc or receiver_acc.upper() in {"UNKNOWN", "NAN", "NONE", ""}:
        return None
    
    debit_time_clean = debit_time or "00:00:00"
    try:
        debit_dt = datetime.strptime(f"{debit_date} {debit_time_clean}", "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            debit_dt = datetime.strptime(debit_date, "%Y-%m-%d")
        except Exception:
            debit_dt = None
            
    # 1. Match by UTR if available
    if debit_utr and str(debit_utr).strip() and str(debit_utr).strip().lower() not in {"nan", "none", ""}:
        utr_clean = str(debit_utr).strip()
        q = select(Transaction).where(
            Transaction.account_id == receiver_acc,
            Transaction.credit > 0,
            Transaction.utr_ref == utr_clean
        )
        res = (await db.execute(q)).scalars().all()
        if res:
            if len(res) == 1:
                return res[0]
            if debit_dt:
                def time_diff(t):
                    try:
                        t_time = t.time or "00:00:00"
                        t_dt = datetime.strptime(f"{t.date} {t_time}", "%Y-%m-%d %H:%M:%S")
                        return abs((t_dt - debit_dt).total_seconds())
                    except Exception:
                        return float('inf')
                return min(res, key=time_diff)
            return res[0]
            
    # 2. Match by amount and time window (24 hours)
    q = select(Transaction).where(
        Transaction.account_id == receiver_acc,
        Transaction.credit > 0
    )
    credits = (await db.execute(q)).scalars().all()
    
    if not credits:
        return None
        
    candidates = []
    for cred in credits:
        cred_amt = float(cred.credit)
        if debit_amount <= 0:
            continue
        if abs(cred_amt - debit_amount) / debit_amount > 0.05:
            continue
            
        if debit_dt:
            try:
                cred_time = cred.time or "00:00:00"
                cred_dt = datetime.strptime(f"{cred.date} {cred_time}", "%Y-%m-%d %H:%M:%S")
                time_diff = abs((cred_dt - debit_dt).total_seconds())
                if time_diff > 24 * 3600:
                    continue
            except Exception:
                if cred.date != debit_date:
                    continue
                time_diff = 12 * 3600
        else:
            if cred.date != debit_date:
                continue
            time_diff = 0
            
        cp_acc = str(cred.counterparty_account_id or "").strip()
        cp_name = str(cred.counterparty_name or "").strip()
        is_sender_match = (
            sender_acc == cp_acc or
            sender_acc in cp_name.upper() or
            cp_name.upper() in sender_acc
        )
        
        penalty = time_diff
        if not is_sender_match:
            penalty += 100000.0
            
        candidates.append((cred, penalty))
        
    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
        
    return None


def clean_and_sort_txns(txns: list[Transaction]) -> list[Transaction]:
    def txn_key(t):
        d = t.date or "2000-01-01"
        tm = t.time or "00:00:00"
        t_id = t.transaction_id or str(t.id) or ""
        return (d, tm, t_id)
        
    sorted_txns = sorted(txns, key=txn_key)
    
    seen_ids = set()
    seen_utrs = set()
    unique_txns = []
    for t in sorted_txns:
        t_id = t.transaction_id
        utr = t.utr_ref
        is_dup = False
        if t_id and t_id in seen_ids:
            is_dup = True
        if utr and utr in seen_utrs:
            is_dup = True
            
        if not is_dup:
            if t_id:
                seen_ids.add(t_id)
            if utr:
                seen_utrs.add(utr)
            unique_txns.append(t)
    return unique_txns


async def get_account_name_map(account_ids: list[str], db: AsyncSession) -> dict[str, str]:
    if not account_ids:
        return {}
    name_map = {}
    
    # 1. Query Accounts
    acc_q = select(Account).where(Account.account_id.in_(account_ids))
    db_accs = (await db.execute(acc_q)).scalars().all()
    for acc in db_accs:
        if acc.holder_name and acc.holder_name.strip() not in {"", "Unknown", "Unknown Holder", "nan", "NaN"}:
            name_map[acc.account_id] = acc.holder_name.strip()
            
    # 2. Query Transactions where these accounts are counterparties
    tx_q = select(
        Transaction.counterparty_account_id,
        Transaction.counterparty_name
    ).where(
        Transaction.counterparty_account_id.in_(account_ids),
        Transaction.counterparty_name.isnot(None),
        Transaction.counterparty_name != "",
        Transaction.counterparty_name != "Unknown",
        Transaction.counterparty_name != "Unknown Holder",
        Transaction.counterparty_name != "nan",
        Transaction.counterparty_name != "NaN"
    ).distinct()
    tx_results = (await db.execute(tx_q)).all()
    for row in tx_results:
        # Prefer counterparty_name from transactions if it's set
        val = row.counterparty_name.strip()
        name_map[row.counterparty_account_id] = val
        
    return name_map


def compute_fifo_allocations_for_account(txns: list[Transaction]) -> tuple[dict, dict, dict]:
    def txn_key(t):
        d = t.date or "2000-01-01"
        tm = t.time or "00:00:00"
        t_id = t.transaction_id or str(t.id) or ""
        return (d, tm, t_id)
    
    sorted_txns = sorted(txns, key=txn_key)
    
    credit_queue = []
    allocations = {}
    debit_funding = {}
    debit_untracked = {}
    
    for t in sorted_txns:
        debit_amt = float(t.debit or 0.0)
        credit_amt = float(t.credit or 0.0)
        t_id = str(t.id)
        
        if credit_amt > 0:
            credit_queue.append({
                "id": t_id,
                "amount": credit_amt,
                "remaining": credit_amt
            })
            allocations[t_id] = []
            
        if debit_amt > 0:
            remaining_debit = debit_amt
            sources = []
            while remaining_debit > 0 and credit_queue:
                oldest = credit_queue[0]
                allocated = min(remaining_debit, oldest["remaining"])
                
                oldest["remaining"] -= allocated
                remaining_debit -= allocated
                
                allocations[oldest["id"]].append({
                    "debit_txn_id": t_id,
                    "allocated_amount": allocated
                })
                sources.append(oldest["id"])
                
                if oldest["remaining"] <= 0:
                    credit_queue.pop(0)
                    
            debit_funding[t_id] = sources
            debit_untracked[t_id] = (remaining_debit > 0)
            
    return allocations, debit_funding, debit_untracked


def get_tracked_allocations(allocations_for_credit, tracked_amount):
    res = []
    rem = tracked_amount
    for alloc in allocations_for_credit:
        if rem <= 0:
            break
        allocated = min(rem, alloc["allocated_amount"])
        res.append({
            "debit_txn_id": alloc["debit_txn_id"],
            "amount": allocated
        })
        rem -= allocated
    return res


async def trace_money_trail_mode(
    account_id: str,
    seed_txn: Transaction,
    db: AsyncSession
) -> tuple[list[MoneyTrailHop], list[MoneyTrailNode]]:
    def get_txn_ident(t):
        return t.transaction_id or str(t.id)

    all_hops = []
    
    # BFS queue state
    # Each item: { "account_id": str, "visited_accounts": list[str], "tracked_credits": { txn_id: amount } }
    queue = [
        {
            "account_id": account_id,
            "visited_accounts": [account_id],
            "tracked_credits": { get_txn_ident(seed_txn): float(seed_txn.credit) }
        }
    ]

    while queue:
        item = queue.pop(0)
        curr_acc = item["account_id"]
        visited = item["visited_accounts"]
        tracked = item["tracked_credits"]

        if len(visited) > 8:  # Hop/depth limit to prevent infinite loops
            continue

        # Load all transactions for this account
        txns_q = select(Transaction).where(Transaction.account_id == curr_acc)
        txns = (await db.execute(txns_q)).scalars().all()
        if not txns:
            continue

        # Clean and sort transactions deterministically
        txns = clean_and_sort_txns(txns)

        # Build mapping from transaction ID / transaction_id to the Transaction object
        txn_map = {}
        for t in txns:
            txn_map[str(t.id)] = t
            if t.transaction_id:
                txn_map[t.transaction_id] = t

        allocations, debit_funding, debit_untracked = compute_fifo_allocations_for_account(txns)

        # Track debits funded by our tracked credits
        debits_to_trace = {}
        for C_id, tracked_amt in tracked.items():
            if C_id in allocations:
                allocs = get_tracked_allocations(allocations[C_id], tracked_amt)
                for alloc in allocs:
                    d_id = alloc["debit_txn_id"]
                    amt = alloc["amount"]
                    if d_id not in debits_to_trace:
                        debits_to_trace[d_id] = {
                            "debit_txn": txn_map[d_id],
                            "tracked_amount": 0.0,
                            "source_credits": set(),
                            "source_credits_detail": []
                        }
                    debits_to_trace[d_id]["tracked_amount"] += amt
                    debits_to_trace[d_id]["source_credits"].add(C_id)
                    
                    existing = next((x for x in debits_to_trace[d_id]["source_credits_detail"] if x["credit_txn_id"] == C_id), None)
                    if existing:
                        existing["amount"] += amt
                    else:
                        debits_to_trace[d_id]["source_credits_detail"].append({
                            "credit_txn_id": C_id,
                            "amount": amt
                        })

        for d_id, d_info in debits_to_trace.items():
            D = d_info["debit_txn"]
            tracked_amt = d_info["tracked_amount"]
            to_acc = D.counterparty_account_id or D.counterparty_name or "UNKNOWN"
            debit_ident = get_txn_ident(D)

            funding_sources = debit_funding.get(str(D.id), [])
            source_credit_txn_idents = []
            for fs in funding_sources:
                fs_txn = txn_map.get(fs)
                if fs_txn:
                    source_credit_txn_idents.append(get_txn_ident(fs_txn))
                else:
                    source_credit_txn_idents.append(fs)

            is_commingled = len(source_credit_txn_idents) > 1
            is_untracked_remainder = debit_untracked.get(str(D.id), False)
            is_cycle = to_acc in visited

            d_time_str = D.time or "00:00:00"
            
            source_credits_detail = []
            for item_sc in d_info["source_credits_detail"]:
                source_credits_detail.append(
                    SourceCreditAllocation(
                        credit_txn_id=item_sc["credit_txn_id"],
                        amount=round(item_sc["amount"], 2)
                    )
                )
                
            to_acc_name_val = None
            if D.counterparty_name and D.counterparty_name.strip() not in {"", "Unknown", "Unknown Holder", "nan", "NaN"}:
                to_acc_name_val = D.counterparty_name.strip()

            hop = MoneyTrailHop(
                hop_number=0,  # Will be assigned later after sorting
                from_account=curr_acc,
                from_account_name=None,
                to_account=to_acc,
                to_account_name=to_acc_name_val,
                debit_txn_id=debit_ident,
                amount=round(tracked_amt, 2),
                timestamp=f"{D.date}T{d_time_str}",
                source_credit_txn_ids=source_credit_txn_idents,
                source_credits=source_credits_detail,
                is_commingled=is_commingled,
                is_untracked_remainder=is_untracked_remainder,
                is_cycle=is_cycle
            )
            all_hops.append(hop)

            if not is_cycle and to_acc not in {"UNKNOWN", "NAN", "NONE", ""}:
                # Check if receiver is an internal account
                acc_exists_q = select(Account).where(Account.account_id == to_acc)
                acc_exists = (await db.execute(acc_exists_q)).scalar() is not None

                if acc_exists:
                    # Find corresponding credit txn in target account
                    match_credit = await find_matching_credit_txn_db(
                        db,
                        sender_acc=curr_acc,
                        receiver_acc=to_acc,
                        debit_amount=float(D.debit),
                        debit_date=D.date,
                        debit_time=D.time,
                        debit_utr=D.utr_ref
                    )
                    if match_credit:
                        match_credit_ident = get_txn_ident(match_credit)
                        queue.append({
                            "account_id": to_acc,
                            "visited_accounts": visited + [to_acc],
                            "tracked_credits": { match_credit_ident: tracked_amt }
                        })

    # Sort all hops chronologically by timestamp
    all_hops.sort(key=lambda h: h.timestamp)
    for idx, h in enumerate(all_hops):
        h.hop_number = idx + 1

    # Determine node roles
    all_accounts = set([account_id])
    from_accounts = set()
    for h in all_hops:
        all_accounts.add(h.from_account)
        all_accounts.add(h.to_account)
        from_accounts.add(h.from_account)

    nodes_list = []
    for acc in all_accounts:
        if acc == account_id:
            role = "seed"
        elif acc in from_accounts:
            role = "intermediate"
        else:
            role = "exit"
        nodes_list.append(MoneyTrailNode(account_id=acc, role=role))

    return all_hops, nodes_list


@router.get("/money-trail/{account_id}", response_model=MoneyTrailResponse)
async def get_money_trail_flow(
    account_id: str,
    credit_txn_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Traces the multi-hop money flow from a starting account for all incoming credit transactions
    using FIFO logic.
    """
    def get_txn_ident(t):
        return t.transaction_id or str(t.id)

    # 1. Fetch all credit transactions for this account
    if credit_txn_id:
        import uuid as py_uuid
        try:
            uuid_val = py_uuid.UUID(credit_txn_id)
            is_uuid = True
        except ValueError:
            is_uuid = False

        if is_uuid:
            q = select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.id == uuid_val,
                Transaction.credit > 0
            )
        else:
            q = select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.transaction_id == credit_txn_id,
                Transaction.credit > 0
            )
        credit_txns = (await db.execute(q)).scalars().all()
    else:
        q = select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.credit > 0
        ).order_by(Transaction.credit.desc())
        credit_txns = (await db.execute(q)).scalars().all()

    # Deduplicate credit transactions stably
    unique_credits = []
    seen_credit_ids = set()
    seen_credit_utrs = set()
    
    # Sort them deterministically: credit desc, date, time, transaction_id/id
    def credit_sort_key(t):
        c = float(t.credit or 0.0)
        d = t.date or "2000-01-01"
        tm = t.time or "00:00:00"
        t_id = t.transaction_id or str(t.id) or ""
        return (-c, d, tm, t_id)
        
    sorted_credits = sorted(credit_txns, key=credit_sort_key)
    for t in sorted_credits:
        t_id = t.transaction_id
        utr = t.utr_ref
        is_dup = False
        if t_id and t_id in seen_credit_ids:
            is_dup = True
        if utr and utr in seen_credit_utrs:
            is_dup = True
            
        if not is_dup:
            if t_id:
                seen_credit_ids.add(t_id)
            if utr:
                seen_credit_utrs.add(utr)
            unique_credits.append(t)
            
    credit_txns = unique_credits

    if not credit_txns:
        return MoneyTrailResponse(credits=[])

    credits_list = []
    all_account_ids = set([account_id])
    raw_traces = []

    for seed_txn in credit_txns:
        fifo_hops, fifo_nodes = await trace_money_trail_mode(account_id, seed_txn, db)
        
        # Collect accounts
        if seed_txn.counterparty_account_id:
            all_account_ids.add(seed_txn.counterparty_account_id)
        for h in fifo_hops:
            all_account_ids.add(h.from_account)
            all_account_ids.add(h.to_account)
            
        raw_traces.append((seed_txn, fifo_hops))

    # Fetch name map for all collected accounts
    name_map = await get_account_name_map(list(all_account_ids), db)

    # Fetch risk details for all destination accounts
    all_dst_accounts = set()
    for _, hops in raw_traces:
        for h in hops:
            all_dst_accounts.add(h.to_account)

    account_risk_map = {}
    if all_dst_accounts:
        q_acc = select(Account).where(Account.account_id.in_(all_dst_accounts))
        db_accounts = (await db.execute(q_acc)).scalars().all()
        for acc in db_accounts:
            score = float(acc.risk_score) if acc.risk_score else 0.0
            tier = "CRITICAL" if score >= 75 else "HIGH" if score >= 50 else "MEDIUM" if score >= 25 else "LOW"
            account_risk_map[acc.account_id] = {
                "risk_tier": tier,
                "fraud_role": acc.fraud_role or "UNKNOWN"
            }

    # Populate and build final CreditTrailInfo list
    for seed_txn, fifo_hops in raw_traces:
        for h in fifo_hops:
            h.from_account_name = name_map.get(h.from_account) or "Unknown"
            if not h.to_account_name or h.to_account_name == "Unknown":
                h.to_account_name = name_map.get(h.to_account) or "Unknown"
                
            acc_info = account_risk_map.get(h.to_account)
            if acc_info:
                h.to_account_risk_tier = acc_info["risk_tier"]
                h.to_account_role = acc_info["fraud_role"]
            else:
                h.to_account_risk_tier = "UNKNOWN"
                h.to_account_role = "UNKNOWN"

        source_account = seed_txn.counterparty_account_id or "Unknown"
        source_account_name = None
        if seed_txn.counterparty_name and seed_txn.counterparty_name.strip() not in {"", "Unknown", "Unknown Holder", "nan", "NaN"}:
            source_account_name = seed_txn.counterparty_name.strip()
        else:
            source_account_name = name_map.get(source_account) or "Unknown"

        time_str = seed_txn.time or "00:00:00"
        credits_list.append(CreditTrailInfo(
            credit_txn_id=get_txn_ident(seed_txn),
            amount=float(seed_txn.credit),
            timestamp=f"{seed_txn.date}T{time_str}",
            source_account=source_account,
            source_account_name=source_account_name,
            hops=fifo_hops
        ))

    return MoneyTrailResponse(credits=credits_list)

