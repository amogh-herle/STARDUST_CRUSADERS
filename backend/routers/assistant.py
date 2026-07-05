"""
Assistant router for the AML Investigator Assistant.

Supports two backends:
  - Gemini/Claude (existing single-shot context path) — default
  - Qwen3-8B (tool-calling loop via Ollama) — when QWEN_ENABLED=True

Both services expose the same .ask() signature, so the route handler is
identical regardless of which backend is active.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException

from config import settings
from schemas import AssistantChatRequest, AssistantChatResponse

router = APIRouter(prefix="/assistant", tags=["Assistant"])

project_root = Path(__file__).resolve().parents[2]
analytics_root = project_root / "data" / "analytics_v2"
if not (analytics_root / "risk_scores.csv").exists():
    for d in ["analytics_v2", "analytics_final", "analytics"]:
        path = project_root / "phase8" / d
        if (path / "risk_scores.csv").exists():
            analytics_root = path
            break

from services.qwen_assistant_service import QwenAssistantService

assistant_service = QwenAssistantService(
    analytics_root=str(analytics_root),
    base_url=settings.QWEN_BASE_URL,
    model_name=settings.QWEN_MODEL_NAME,
)


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
