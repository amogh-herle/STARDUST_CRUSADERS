import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from services.analytics_repository import AnalyticsRepository
from services.context_builder import AssistantContextBuilder


class TestContextBuilder(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "money_trails").mkdir(parents=True)

        pd.DataFrame([
            {"account_id": "A1", "account_holder": "Alice", "bank_name": "Bank", "community_id": 1,
             "risk_score": 45.0, "risk_tier": "MEDIUM", "active_patterns": "FAN_IN", "risk_reasoning": "Test"}
        ]).to_csv(self.root / "risk_scores.csv", index=False)
        pd.DataFrame([
            {"community_id": 1, "n_accounts": 1, "avg_risk": 45.0, "max_risk": 45.0}
        ]).to_csv(self.root / "community_risk.csv", index=False)
        pd.DataFrame([
            {"community_id": 1, "size": 1, "total_flow": 1000.0, "internal_ratio": 1.0, "top_accounts": "['A1']"}
        ]).to_csv(self.root / "community_summaries.csv", index=False)
        pd.DataFrame([
            {"account_id": "A1", "community_id": 1}
        ]).to_csv(self.root / "communities.csv", index=False)
        self.repo = AnalyticsRepository(self.root)
        self.builder = AssistantContextBuilder(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_build_context_for_account(self):
        result = self.builder.build_context("What is the risk for A1?", account_id="A1")
        self.assertIn("risk_scores.csv", result.sources)
        self.assertIn("community_summaries.csv", result.sources)
        self.assertIn("Answer using only the analytics artifacts cited above.", result.prompt)
        self.assertGreater(len(result.prompt), 100)
