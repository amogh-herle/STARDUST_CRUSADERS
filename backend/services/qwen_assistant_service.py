"""
Qwen3-8B AML Investigation Assistant — tool-calling service.

Replaces the single-shot context-dump approach in AssistantService with a
multi-turn tool-calling loop:

    User question
        → Qwen picks a tool (or answers directly)
        → tool result fed back
        → Qwen picks another tool or answers
        (max 3 hops)

Uses Ollama's OpenAI-compatible /v1/chat/completions endpoint via httpx.
No new SDK dependency — httpx ships with FastAPI/Starlette.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

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
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT)

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

            # --- Model decided to answer directly (no tool calls) ---
            if not response.get("tool_calls"):
                content = response.get("content", "")
                answer = _strip_thinking(content)
                sources = _extract_sources_from_tool_names(tool_names_called)
                return answer, sources

            # --- Model wants to call tool(s) ---
            # Append the assistant message with tool_calls to the history
            messages.append({
                "role": "assistant",
                "content": response.get("content", ""),
                "tool_calls": response["tool_calls"],
            })

            for call in response["tool_calls"]:
                func_name = call["function"]["name"]
                raw_args = call["function"].get("arguments", "{}")

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
                    "tool_call_id": call.get("id", f"call_{hop}_{func_name}"),
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
        answer = _strip_thinking(response.get("content", ""))
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
    ) -> Dict[str, Any]:
        """
        POST to Ollama's /v1/chat/completions (OpenAI-compatible).

        Returns the first choice's message dict, which may contain:
        - "content": str  (the text answer)
        - "tool_calls": list[dict]  (tool invocations)
        """
        url = f"{self.base_url}/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if include_tools:
            payload["tools"] = QWEN_TOOL_SCHEMA

        logger.debug("Qwen request → %s tools=%s", url, include_tools)

        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.ConnectError:
            logger.error(
                "Cannot reach Qwen at %s — is Ollama running?", self.base_url
            )
            return {
                "content": (
                    "I'm sorry, I can't reach the local AI model right now. "
                    "Please make sure Ollama is running and try again."
                )
            }
        except httpx.HTTPStatusError as exc:
            logger.error("Qwen HTTP error: %s", exc)
            return {
                "content": (
                    "The AI model returned an error. Please try again in a "
                    "moment, or contact your system administrator."
                )
            }

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"content": "No response from the model."}

        message = choices[0].get("message", {})
        return message

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
