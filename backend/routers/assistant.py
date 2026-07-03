"""
Assistant router for the AML Investigator Assistant.
"""
from pathlib import Path
from fastapi import APIRouter, HTTPException

from schemas import AssistantChatRequest, AssistantChatResponse
from services.assistant_service import AssistantService

router = APIRouter(prefix="/assistant", tags=["Assistant"])

analytics_root = Path(__file__).resolve().parents[2] / "phase8" / "analytics"
assistant_service = AssistantService(str(analytics_root))


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(payload: AssistantChatRequest):
    if payload.account_id is not None and not assistant_service.repository.get_account(payload.account_id):
        raise HTTPException(status_code=404, detail=f"Account {payload.account_id} not found in Phase 8 analytics outputs")
    if payload.community_id is not None and not assistant_service.repository.get_community(payload.community_id):
        raise HTTPException(status_code=404, detail=f"Community {payload.community_id} not found in Phase 8 analytics outputs")

    answer, sources = assistant_service.ask(
        question=payload.question,
        account_id=payload.account_id,
        community_id=payload.community_id,
    )
    return AssistantChatResponse(answer=answer, sources=sources)
