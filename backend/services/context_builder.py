"""
Builds RAG context for the AML Investigator Assistant.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from services.analytics_repository import AnalyticsRepository
from services.prompts import MAX_CONTEXT_WORDS, QUESTION_PROMPT_TEMPLATE


@dataclass
class AssistantContext:
    prompt: str
    sources: List[str]


class AssistantContextBuilder:
    def __init__(self, repository: AnalyticsRepository):
        self.repository = repository

    def build_context(
        self,
        question: str,
        account_id: Optional[str] = None,
        community_id: Optional[str] = None,
    ) -> AssistantContext:
        self.repository.refresh_if_needed()

        sections: List[str] = []
        sources = set(self.repository.get_sources(account_id=account_id, community_id=community_id, include_trails=True))

        analytic_overview = self.repository.get_analytics_summary()
        if analytic_overview:
            sections.append("Phase 8 analytics summary:\n" + analytic_overview.strip())

        report = self.repository.get_analytics_report()
        if report:
            sections.append(self._render_report_overview(report))

        if account_id:
            account = self.repository.get_account(account_id)
            if account:
                sections.append(self._render_account_context(account))
                trail = self.repository.get_money_trail(account_id)
                if trail.get("files"):
                    sections.append(self._render_money_trail_context(account_id, trail))
                    for entry in trail["files"]:
                        sources.add(f"money_trails/{entry['path']}")

        if community_id:
            community = self.repository.get_community(community_id)
            if community:
                sections.append(self._render_community_context(community_id, community))
                members = self.repository.get_community_members(community_id)
                if members:
                    sections.append(self._render_community_members_context(members))

        if not account_id and not community_id:
            sections.append(self._render_top_entities_context())

        prompt = QUESTION_PROMPT_TEMPLATE.format(
            context="\n\n".join(sections),
            question=question.strip(),
        )
        prompt = self._truncate_to_word_limit(prompt)

        return AssistantContext(prompt=prompt, sources=sorted(sources))

    def _render_report_overview(self, report: dict) -> str:
        items = [
            f"Run timestamp: {report.get('run_timestamp', 'N/A')}",
            f"Input file: {report.get('input_file', 'N/A')}",
            f"Accounts analysed: {report.get('accounts', 'N/A')}",
            f"Round trips: {report.get('round_trips', 'N/A')}",
            f"Layering chains: {report.get('layering_chains', 'N/A')}",
            f"Fan-in accounts: {report.get('fan_in', 'N/A')}",
            f"Fan-out accounts: {report.get('fan_out', 'N/A')}",
            f"Smurfing accounts: {report.get('smurfing', 'N/A')}",
            f"Odd-hour accounts: {report.get('odd_hours', 'N/A')}",
        ]
        return "Analytics report overview:\n" + "\n".join(items)

    def _render_account_context(self, account: dict) -> str:
        lines = [
            f"Account: {account.get('account_id')} ({account.get('account_holder', 'N/A')})",
            f"Bank: {account.get('bank_name', 'N/A')}",
            f"Risk score: {account.get('risk_score', 'N/A')}",
            f"Risk tier: {account.get('risk_tier', 'N/A')}",
            f"Community ID: {account.get('community_id', 'N/A')}",
            f"Triggered signals: {account.get('active_patterns', 'N/A')}",
            f"Risk reasoning: {account.get('risk_reasoning', 'N/A')}",
            f"Isolation forest mean score: {account.get('isolation_mean_score', 'N/A')}",
            f"Isolation forest max score: {account.get('isolation_max_score', 'N/A')}",
        ]
        return "Account analytics context:\n" + "\n".join(lines)

    def _render_money_trail_context(self, account_id: str, trail: dict) -> str:
        files = trail.get("files", [])
        lines = [f"Money trails for account {account_id}: {len(files)} trail file(s) found."]
        for file_info in files:
            lines.append(
                f"- {file_info['path']}: direction={file_info['direction']}, rows={file_info['rows']}, columns={len(file_info['columns'])}"
            )
        return "Money trail summary:\n" + "\n".join(lines)

    def _render_community_context(self, community_id: str | int, community: dict) -> str:
        lines = [
            f"Community {community_id} context:",
            f"Members: {community.get('n_accounts', 'N/A')}",
            f"Average risk: {community.get('avg_risk', 'N/A')}",
            f"Max risk: {community.get('max_risk', 'N/A')}",
        ]
        return "\n".join(lines)

    def _render_community_members_context(self, members: list[dict]) -> str:
        top_members = members[:10]
        lines = [f"Top {len(top_members)} community members:"]
        for member in top_members:
            lines.append(
                f"- {member.get('account_id')} score={member.get('risk_score')} tier={member.get('risk_tier')} patterns={member.get('active_patterns', 'N/A')}"
            )
        return "Community members summary:\n" + "\n".join(lines)

    def _render_top_entities_context(self) -> str:
        accounts = self.repository.get_top_risk_accounts(limit=5)
        communities = self.repository.get_top_communities(limit=5)
        lines = ["Top Phase 8 entities:"]
        if accounts:
            lines.append("Top risk accounts:")
            for account in accounts:
                lines.append(
                    f"- {account.get('account_id')} score={account.get('risk_score')} tier={account.get('risk_tier')} patterns={account.get('active_patterns', 'N/A')}"
                )
        if communities:
            lines.append("Top risk communities:")
            for community in communities:
                lines.append(
                    f"- community {community.get('community_id')} avg_risk={community.get('avg_risk')} max_risk={community.get('max_risk')} accounts={community.get('n_accounts')}"
                )
        return "High-level Phase 8 context:\n" + "\n".join(lines)

    def _truncate_to_word_limit(self, prompt: str) -> str:
        words = prompt.split()
        if len(words) <= MAX_CONTEXT_WORDS:
            return prompt
        truncated = " ".join(words[:MAX_CONTEXT_WORDS])
        return truncated + "\n\n[context truncated due to prompt size limits]"
