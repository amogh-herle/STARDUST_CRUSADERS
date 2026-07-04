import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from services.analytics_repository import AnalyticsRepository


class TestAnalyticsRepository(unittest.TestCase):
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
        with open(self.root / "analytics_report.json", "w", encoding="utf-8") as f:
            json.dump({"accounts": 1, "round_trips": 0}, f)
        (self.root / "analytics_summary.txt").write_text("Summary file", encoding="utf-8")
        (self.root / "investigator_case_report.html").write_text("<html>Report</html>", encoding="utf-8")
        pd.DataFrame([
            {"account_id": "A1", "pagerank": 0.1, "betweenness": 0.0, "in_degree": 1, "out_degree": 2,
             "degree_centrality": 0.1, "graph_risk_score": 0.1}
        ]).to_csv(self.root / "graph_metrics.csv", index=False)
        pd.DataFrame([
            {"root_account": "A1", "direction": "forward", "seed_amount": 100.0}
        ]).to_csv(self.root / "money_trails" / "trail_A1_forward.csv", index=False)

        self.repo = AnalyticsRepository(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_get_account_returns_account_data(self):
        account = self.repo.get_account("A1")
        self.assertIsNotNone(account)
        self.assertEqual(account["account_id"], "A1")
        self.assertEqual(account["risk_tier"], "MEDIUM")

    def test_get_community_returns_community_data(self):
        community = self.repo.get_community(1)
        self.assertIsNotNone(community)
        self.assertEqual(community["avg_risk"], "45.0")

    def test_get_money_trail_returns_files(self):
        trail = self.repo.get_money_trail("A1")
        self.assertTrue(trail.get("files"))
        self.assertEqual(trail["files"][0]["direction"], "forward")

    def test_get_top_risk_accounts(self):
        top_accounts = self.repo.get_top_risk_accounts(limit=1)
        self.assertEqual(len(top_accounts), 1)
        self.assertEqual(top_accounts[0]["account_id"], "A1")
