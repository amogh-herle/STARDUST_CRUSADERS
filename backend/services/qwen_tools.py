"""
Tool dispatcher for the Qwen3-8B AML Investigation Assistant.

Each method is a thin wrapper around an existing AnalyticsRepository call.
The TOOL_REGISTRY dict at the bottom maps OpenAI-style function names to
callables so the tool-calling loop can dispatch by name.

Design notes:
- analytics_repository.py is UNTOUCHED — all data access goes through it.
- Tool results are kept small on purpose (truncated previews, stripped HTML)
  because an 8B model works better with clean, compact context than with
  large raw dumps.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from io import StringIO
from typing import Any, Dict, Optional

from services.analytics_repository import AnalyticsRepository


# ---------------------------------------------------------------------------
# HTML → plain-text helper (for the case report)
# ---------------------------------------------------------------------------
class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper — no external dependency needed."""

    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _truncate_words(text: str, max_words: int = 2000) -> str:
    """Cap text length so it doesn't blow the model's useful context window."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n\n[truncated for brevity]"


# ---------------------------------------------------------------------------
# AssistantTools — one method per tool the model can call
# ---------------------------------------------------------------------------
class AssistantTools:
    """Wraps AnalyticsRepository for the Qwen3 tool-calling loop."""

    def __init__(self, repository: AnalyticsRepository):
        self.repo = repository

    # --- 1. Overview ----------------------------------------------------------
    def get_analytics_overview(self) -> Dict[str, Any]:
        self.repo.refresh_if_needed()
        summary_text = self.repo.get_analytics_summary()
        report_json = self.repo.get_analytics_report()
        return {
            "summary": _truncate_words(summary_text, max_words=800),
            "report": report_json,
        }

    # --- 2. Account profile ---------------------------------------------------
    def get_account_profile(self, account_id: str) -> Dict[str, Any]:
        account = self.repo.get_account(account_id)
        if not account:
            return {"error": f"No analytics found for account {account_id}."}
        # Enrich with graph metrics if available
        graph = self.repo.get_graph_metrics(account_id)
        if graph:
            account["graph_metrics"] = graph
        return account

    # --- 3. Money trail -------------------------------------------------------
    def get_money_trail(
        self, account_id: str, direction: Optional[str] = None
    ) -> Dict[str, Any]:
        trail = self.repo.get_money_trail(account_id, direction)
        files = trail.get("files", [])
        if not files:
            return {"error": f"No money-trail data found for account {account_id}."}
        # Cap preview rows per file to keep context compact
        for f in files:
            if "preview" in f:
                f["preview"] = f["preview"][:10]
        return {"files": files}

    # --- 4. Community profile -------------------------------------------------
    def get_community_profile(
        self, community_id: str, include_members: bool = False
    ) -> Dict[str, Any]:
        community = self.repo.get_community(community_id)
        if not community:
            return {"error": f"No community/ring {community_id} found."}
        result: Dict[str, Any] = {"community": community}
        if include_members:
            members = self.repo.get_community_members(community_id)
            # Cap to 15 members to keep the context manageable
            result["members"] = members[:15]
            if len(members) > 15:
                result["members_note"] = (
                    f"Showing top 15 of {len(members)} members."
                )
        return result

    # --- 5. Top risk entities -------------------------------------------------
    def get_top_risk_entities(
        self, entity_type: str, limit: int = 5
    ) -> Dict[str, Any]:
        if entity_type == "accounts":
            return {"accounts": self.repo.get_top_risk_accounts(limit=limit)}
        elif entity_type == "communities":
            return {"communities": self.repo.get_top_communities(limit=limit)}
        return {"error": f"Unknown entity_type '{entity_type}'. Use 'accounts' or 'communities'."}

    # --- 6. Full case report --------------------------------------------------
    def get_full_case_report(self) -> Dict[str, Any]:
        html = self.repo.get_case_report_html()
        if not html:
            return {"error": "No case report has been generated yet."}
        # Strip HTML and truncate — the model handles plain text much better
        plain = _strip_html(html)
        return {"report_text": _truncate_words(plain, max_words=2000)}


# ---------------------------------------------------------------------------
# TOOL_REGISTRY — maps function names → callables for the dispatch loop
# ---------------------------------------------------------------------------
TOOL_REGISTRY: Dict[str, Any] = {
    "get_analytics_overview": lambda t, args: t.get_analytics_overview(),
    "get_account_profile":    lambda t, args: t.get_account_profile(**args),
    "get_money_trail":        lambda t, args: t.get_money_trail(**args),
    "get_community_profile":  lambda t, args: t.get_community_profile(**args),
    "get_top_risk_entities":  lambda t, args: t.get_top_risk_entities(**args),
    "get_full_case_report":   lambda t, args: t.get_full_case_report(),
}
