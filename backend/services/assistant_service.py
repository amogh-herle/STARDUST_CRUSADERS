"""
Assistant service orchestrates context building and LLM generation.
"""
from __future__ import annotations

from typing import Optional

from config import settings
from services.analytics_repository import AnalyticsRepository
from services.context_builder import AssistantContextBuilder
from services.llm_provider import ProviderFactory
from services.prompts import AML_INVESTIGATOR_SYSTEM_PROMPT


class AssistantService:
    def __init__(self, analytics_root: str):
        self.repository = AnalyticsRepository(analytics_root)
        self.context_builder = AssistantContextBuilder(self.repository)
        self.provider = ProviderFactory.create_provider()

    def _choose_model(self, question: str) -> str:
        lowered = question.lower()
        if any(keyword in lowered for keyword in ["sar", "suspicious activity report", "case summary", "executive summary", "investigation guidance", "recommend"]):
            return settings.REPORT_MODEL
        return settings.CHAT_MODEL

    def ask(
        self,
        question: str,
        account_id: Optional[str] = None,
        community_id: Optional[str] = None,
    ) -> tuple[str, list[str]]:
        context = self.context_builder.build_context(question, account_id, community_id)
        model = self._choose_model(question)
        messages = [
            {"role": "system", "content": AML_INVESTIGATOR_SYSTEM_PROMPT},
            {"role": "user", "content": context.prompt},
        ]
        answer = self.provider.generate(
            messages=messages,
            model=model,
            temperature=0.2,
            max_output_tokens=1024,
        )
        return answer, context.sources
