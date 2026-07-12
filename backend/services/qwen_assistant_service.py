"""
Qwen3-8B AML Investigation Assistant — tool-calling service.

Replaces the single-shot context-dump approach in AssistantService with a
multi-turn tool-calling loop:

    User question
        → Qwen picks a tool (or answers directly)
        → tool result fed back
        → Qwen picks another tool or answers
        (max 3 hops)

Uses Ollama's OpenAI-compatible /v1/chat/completions endpoint via the openai Python SDK.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from services.analytics_repository import AnalyticsRepository
from services.prompts import QWEN_SYSTEM_PROMPT
from services.qwen_tool_schema import QWEN_TOOL_SCHEMA
from services.qwen_tools import AssistantTools, TOOL_REGISTRY

logger = logging.getLogger(__name__)

MAX_TOOL_HOPS = 3
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 1024
REQUEST_TIMEOUT = 120.0  # seconds — local models can be slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip_thinking(text: str) -> str:
    """Remove Qwen3's <think>…</think> blocks from the final answer."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_sources_from_tool_names(tool_names: List[str]) -> List[str]:
    """
    Map tool names back to the analytics artifact filenames an investigator
    would recognise. Multiple calls to the same tool are de-duped.
    """
    mapping = {
        "get_analytics_overview": ["analytics_summary.txt", "analytics_report.json"],
        "get_account_profile": ["risk_scores.csv"],
        "get_money_trail": ["money_trails/"],
        "get_community_profile": ["community_risk.csv", "communities.csv"],
        "get_top_risk_entities": ["risk_scores.csv", "community_risk.csv"],
        "get_full_case_report": ["investigator_case_report.html"],
    }
    sources: list[str] = []
    seen: set[str] = set()
    for name in tool_names:
        for source in mapping.get(name, [name]):
            if source not in seen:
                sources.append(source)
                seen.add(source)
    return sources


# ---------------------------------------------------------------------------
# QwenAssistantService
# ---------------------------------------------------------------------------
class QwenAssistantService:
    """
    Drop-in replacement for AssistantService.ask().

    Same signature:  ask(question, account_id?, community_id?) → (answer, sources)
    """

    def __init__(
        self,
        analytics_root: str,
        base_url: str = "http://localhost:11434",
        model_name: str = "qwen3:8b",
    ):
        self.repository = AnalyticsRepository(Path(analytics_root))
        self.tools = AssistantTools(self.repository)
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        
        # Use OpenAI SDK pointing at the local server (e.g. Ollama or vLLM)
        self._client = OpenAI(base_url=f"{self.base_url}/v1", api_key="ollama", timeout=REQUEST_TIMEOUT)

    # ------------------------------------------------------------------
    # Public API — matches AssistantService.ask()
    # ------------------------------------------------------------------
    def ask(
        self,
        question: str,
        account_id: Optional[str] = None,
        community_id: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """
        Send a question through the Qwen3 tool-calling loop.

        If account_id or community_id is provided by the frontend, we
        inject a hint into the user message so the model doesn't have to
        parse it out of free text.
        """
        user_content = question.strip()
        if account_id:
            user_content += f"\n\n[Context: the investigator is viewing account {account_id}]"
        if community_id:
            user_content += f"\n\n[Context: the investigator is viewing community/ring {community_id}]"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": QWEN_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        tool_names_called: List[str] = []

        for hop in range(MAX_TOOL_HOPS):
            response = self._call_qwen(messages)

            if not getattr(response, "tool_calls", None):
                content = getattr(response, "content", "") or ""
                answer = _strip_thinking(content)
                sources = _extract_sources_from_tool_names(tool_names_called)
                return answer, sources

            # --- Model wants to call tool(s) ---
            # Append the assistant message with tool_calls to the history
            
            tool_calls_raw = getattr(response, "tool_calls")
            
            # OpenAI SDK representation of the message to append back
            messages.append(response)

            for call in tool_calls_raw:
                func_name = call.function.name
                raw_args = call.function.arguments or "{}"

                # Parse arguments — handle both string and dict forms
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = raw_args

                # Dispatch the tool
                handler = TOOL_REGISTRY.get(func_name)
                if handler:
                    try:
                        result = handler(self.tools, args)
                    except Exception as exc:
                        logger.warning("Tool %s raised: %s", func_name, exc)
                        result = {"error": f"Tool error: {exc}"}
                    tool_names_called.append(func_name)
                else:
                    result = {"error": f"Unknown tool: {func_name}"}

                # Feed the tool result back into the conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(call, "id", f"call_{hop}_{func_name}"),
                    "content": json.dumps(result, default=str),
                })

        # Fell through MAX_TOOL_HOPS without a final text answer — ask
        # the model one last time with no tools to force a text response.
        messages.append({
            "role": "user",
            "content": (
                "Please summarise what you've found so far and answer the "
                "original question based on the tool results above."
            ),
        })
        response = self._call_qwen(messages, include_tools=False)
        content = getattr(response, "content", "") or ""
        answer = _strip_thinking(content)
        if not answer:
            answer = (
                "I found some relevant data but need a bit more to fully "
                "answer that — could you ask me again, maybe a little more "
                "specifically?"
            )
        sources = _extract_sources_from_tool_names(tool_names_called)
        return answer, sources

    # ------------------------------------------------------------------
    # Ollama OpenAI-compatible client
    # ------------------------------------------------------------------
    def _call_qwen(
        self,
        messages: List[Dict[str, Any]],
        include_tools: bool = True,
    ) -> Any:
        """
        Calls the local Qwen model using the OpenAI SDK.
        Returns the first choice's message object.
        """
        logger.debug("Qwen request → %s tools=%s", self.base_url, include_tools)

        try:
            kwargs = {
                "model": self.model_name,
                "messages": messages,
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            }
            if include_tools:
                kwargs["tools"] = QWEN_TOOL_SCHEMA
                kwargs["tool_choice"] = "auto"
                
            resp = self._client.chat.completions.create(**kwargs)
            return resp.choices[0].message
            
        except Exception as exc:
            logger.error("Qwen API error: %s", exc)
            # Return a dummy object matching the structure for fallback
            class DummyMessage:
                content = (
                    "I'm sorry, I can't reach the local AI model right now. "
                    "Please make sure it is running and try again."
                )
                tool_calls = None
            return DummyMessage()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying client."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
