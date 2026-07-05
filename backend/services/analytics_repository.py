"""
Analytics repository that caches Phase 8 artifacts in memory.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from services.analytics_loader import AnalyticsLoader


class AnalyticsRepository:
    def __init__(self, analytics_root: Path):
        self.analytics_root = Path(analytics_root)
        self.loader = AnalyticsLoader(self.analytics_root)
        self._cache: Dict[str, Any] = {}
        self._mtimes: Dict[str, float] = {}
        self.refresh()

    def refresh(self) -> None:
        self._cache["risk_scores"] = self.loader.load_csv("risk_scores.csv", dtype=str)
        self._cache["community_risk"] = self.loader.load_csv("community_risk.csv", dtype=str)
        self._cache["community_summaries"] = self.loader.load_csv("community_summaries.csv", dtype=str)
        self._cache["communities"] = self.loader.load_csv("communities.csv", dtype=str)
        self._cache["analytics_report"] = self.loader.load_json("analytics_report.json")
        self._cache["analytics_summary"] = self.loader.load_text("analytics_summary.txt")
        self._cache["case_report_html"] = self.loader.load_text("investigator_case_report.html")
        self._cache["graph_metrics"] = self.loader.load_csv("graph_metrics.csv", dtype=str)
        self._cache["money_trails"] = self.loader.load_all_money_trails()
        self._mtimes = self._compute_mtimes()

    def _compute_mtimes(self) -> Dict[str, float]:
        mtimes: Dict[str, float] = {}
        tracked = [
            "risk_scores.csv",
            "community_risk.csv",
            "community_summaries.csv",
            "communities.csv",
            "analytics_report.json",
            "analytics_summary.txt",
            "investigator_case_report.html",
            "graph_metrics.csv",
        ]
        for filename in tracked:
            path = self.analytics_root / filename
            if path.exists():
                mtimes[filename] = path.stat().st_mtime
        trails_dir = self.analytics_root / "money_trails"
        if trails_dir.exists():
            for trail_file in trails_dir.glob("trail_*.csv"):
                mtimes[f"money_trails/{trail_file.name}"] = trail_file.stat().st_mtime
        return mtimes

    def refresh_if_needed(self) -> None:
        current_mtimes = self._compute_mtimes()
        if current_mtimes != self._mtimes:
            self.refresh()

    def _frame(self, key: str) -> pd.DataFrame:
        self.refresh_if_needed()
        return self._cache.get(key, pd.DataFrame())

    def _text(self, key: str) -> str:
        self.refresh_if_needed()
        return self._cache.get(key, "")

    def _json(self, key: str) -> Any:
        self.refresh_if_needed()
        return self._cache.get(key, {})

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        df = self._frame("risk_scores")
        if df.empty:
            return None
        row = df[df["account_id"].astype(str) == str(account_id)]
        if row.empty:
            return None
        return row.iloc[0].to_dict()

    def get_community(self, community_id: str | int) -> Optional[Dict[str, Any]]:
        df = self._frame("community_risk")
        if df.empty:
            return None
        row = df[df["community_id"].astype(str) == str(community_id)]
        if row.empty:
            return None
        return row.iloc[0].to_dict()

    def get_community_members(self, community_id: str | int) -> list[Dict[str, Any]]:
        communities = self._frame("communities")
        if communities.empty:
            return []
        member_ids = communities[communities["community_id"].astype(str) == str(community_id)]["account_id"].astype(str).tolist()
        if not member_ids:
            return []
        risk_scores = self._frame("risk_scores")
        return risk_scores[risk_scores["account_id"].astype(str).isin(member_ids)].to_dict(orient="records")

    def get_current_account_ids(self) -> set[str]:
        """
        Account IDs that belong to the *current* analytics run (i.e. the most
        recently uploaded/processed set of statements), as loaded from
        risk_scores.csv. This is the scoping key that should be used anywhere
        the app shows "the graph" or "the accounts" for the active case —
        NOT a raw, unscoped query against the accounts table, which
        accumulates every account ever ingested (including demo/seed data
        and accounts from earlier, unrelated uploads).
        """
        df = self._frame("risk_scores")
        if df.empty or "account_id" not in df.columns:
            return set()
        return set(df["account_id"].astype(str).tolist())

    def get_top_risk_accounts(self, limit: int = 10) -> list[Dict[str, Any]]:
        df = self._frame("risk_scores")
        if df.empty:
            return []
        df["risk_score"] = pd.to_numeric(df["risk_score"], errors="coerce").fillna(0.0)
        if "graph_risk_score" in df.columns:
            df["graph_risk_score"] = pd.to_numeric(df["graph_risk_score"], errors="coerce").fillna(0.0)
            rows = df.sort_values(["risk_score", "graph_risk_score"], ascending=False).head(limit)
        else:
            rows = df.sort_values("risk_score", ascending=False).head(limit)
        return rows.to_dict(orient="records")

    def get_top_communities(self, limit: int = 10) -> list[Dict[str, Any]]:
        df = self._frame("community_risk")
        if df.empty:
            return []
        df["avg_risk"] = pd.to_numeric(df["avg_risk"], errors="coerce").fillna(0.0)
        rows = df.sort_values("avg_risk", ascending=False).head(limit)
        return rows.to_dict(orient="records")

    def get_money_trail(self, account_id: str, direction: str | None = None) -> Dict[str, Any]:
        return self.loader.load_money_trail(str(account_id), direction)

    def get_analytics_summary(self) -> str:
        return self._text("analytics_summary")

    def get_analytics_report(self) -> Dict[str, Any]:
        return self._json("analytics_report")

    def get_case_report_html(self) -> str:
        return self._text("case_report_html")

    def get_graph_metrics(self, account_id: str | None = None) -> Optional[Dict[str, Any]]:
        df = self._frame("graph_metrics")
        if df.empty:
            return None
        if account_id is None:
            return None
        row = df[df["account_id"].astype(str) == str(account_id)]
        return row.iloc[0].to_dict() if not row.empty else None

    def get_sources(self, account_id: str | None = None, community_id: str | None = None, include_trails: bool = False) -> list[str]:
        all_possible = [
            "risk_scores.csv",
            "community_risk.csv",
            "communities.csv",
            "community_summaries.csv",
            "analytics_report.json",
            "analytics_summary.txt",
            "investigator_case_report.html",
        ]
        sources = set()
        for filename in all_possible:
            if (self.analytics_root / filename).exists():
                sources.add(filename)
        if include_trails and account_id:
            trail_files = self.get_money_trail(account_id).get("files", [])
            for trail in trail_files:
                if (self.analytics_root / "money_trails" / trail['path']).exists():
                    sources.add(f"money_trails/{trail['path']}")
        return sorted(sources)
