"""
Router: /api/v1/investigations

Manages investigator-created case files that link accounts,
transactions, fraud rings, and generated reports together.

POST /                 Create a new investigation
GET  /                 List investigations
GET  /{id}             Get investigation detail
PATCH /{id}            Update status / narrative
DELETE /{id}           Close/delete investigation
POST  /{id}/evidence   Add evidence item
GET   /{id}/evidence   List evidence items
"""

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from dependencies import get_db
from models import Investigation, EvidenceItem, Account
from schemas import (
    InvestigationCreate, InvestigationUpdate,
    InvestigationOut, PaginatedResponse,
)

router = APIRouter(prefix="/investigations", tags=["Investigations"])


@router.post("/", response_model=InvestigationOut, status_code=201)
async def create_investigation(
    payload: InvestigationCreate,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(None),
):
    """
    Create a new investigation case. Typically triggered when an
    investigator identifies a suspicious account and wants to build
    a case file around it.
    """
    if payload.seed_account_id:
        account = await db.get(Account, payload.seed_account_id)
        if not account:
            raise HTTPException(
                status_code=404,
                detail=f"Seed account {payload.seed_account_id} not found"
            )

    # Resolve names
    name = payload.name or payload.case_name or "Unnamed Case"
    case_name = payload.case_name or name

    # Resolve case number
    case_number = payload.case_number
    if not case_number:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        suffix = uuid.uuid4().hex[:4].upper()
        case_number = f"CASE-{today}-{suffix}"

    user_uuid = None
    if x_user_id:
        try:
            user_uuid = uuid.UUID(x_user_id)
        except ValueError:
            pass

    investigation = Investigation(
        name=name,
        case_name=case_name,
        case_number=case_number,
        description=payload.description,
        seed_account_id=payload.seed_account_id,
        status=payload.status or "open",
        priority=payload.priority or "medium",
        created_by=user_uuid,
    )
    db.add(investigation)
    await db.flush()
    await db.commit()
    await db.refresh(investigation)
    
    return InvestigationOut.model_validate(investigation)


@router.get("/", response_model=PaginatedResponse)
async def list_investigations(
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(None),
):
    q = select(Investigation)
    if status:
        q = q.where(Investigation.status == status)

    if x_user_id:
        try:
            user_uuid = uuid.UUID(x_user_id)
            q = q.where(Investigation.created_by == user_uuid)
        except ValueError:
            pass

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    q = (
        q.order_by(Investigation.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    investigations = (await db.execute(q)).scalars().all()

    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[InvestigationOut.model_validate(i) for i in investigations],
    )


@router.get("/{investigation_id}", response_model=InvestigationOut)
async def get_investigation(
    investigation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    inv = await db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return InvestigationOut.model_validate(inv)


@router.patch("/{investigation_id}", response_model=InvestigationOut)
async def update_investigation(
    investigation_id: uuid.UUID,
    payload: InvestigationUpdate,
    db: AsyncSession = Depends(get_db),
):
    inv = await db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "case_name" in update_data and "name" not in update_data:
        update_data["name"] = update_data["case_name"]
    elif "name" in update_data and "case_name" not in update_data:
        update_data["case_name"] = update_data["name"]

    for field, value in update_data.items():
        setattr(inv, field, value)

    await db.flush()
    await db.commit()
    await db.refresh(inv)
    return InvestigationOut.model_validate(inv)


@router.delete("/{investigation_id}", status_code=204)
async def delete_investigation(
    investigation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    inv = await db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    await db.delete(inv)
    await db.commit()


@router.post("/{investigation_id}/evidence", status_code=201)
async def add_evidence(
    investigation_id: uuid.UUID,
    evidence_type: str,
    reference_id: Optional[str] = None,
    description: Optional[str] = None,
    amount_involved: Optional[float] = None,
    db: AsyncSession = Depends(get_db),
):
    inv = await db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    item = EvidenceItem(
        investigation_id=investigation_id,
        evidence_type=evidence_type,
        reference_id=reference_id,
        description=description,
        amount_involved=amount_involved,
    )
    db.add(item)
    await db.flush()
    return {"id": str(item.id), "status": "added"}


@router.get("/{investigation_id}/evidence")
async def list_evidence(
    investigation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    inv = await db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    q = select(EvidenceItem).where(
        EvidenceItem.investigation_id == investigation_id
    ).order_by(EvidenceItem.added_at)

    items = (await db.execute(q)).scalars().all()
    return [
        {
            "id": str(e.id),
            "evidence_type": e.evidence_type,
            "reference_id": e.reference_id,
            "description": e.description,
            "amount_involved": e.amount_involved,
            "added_at": e.added_at.isoformat(),
        }
        for e in items
    ]
