import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from risk_scorer import _fallback_tier, _score_to_tier


class TestRiskScorer(unittest.TestCase):
    def test_score_to_tier_absolute(self):
        self.assertEqual(_score_to_tier(80.0), "CRITICAL")
        self.assertEqual(_score_to_tier(55.0), "HIGH")
        self.assertEqual(_score_to_tier(30.0), "MEDIUM")
        self.assertEqual(_score_to_tier(10.0), "LOW")

    def test_fallback_tier(self):
        self.assertEqual(_fallback_tier(32.6), "HIGH")
        self.assertEqual(_fallback_tier(45.0), "CRITICAL")
        self.assertEqual(_fallback_tier(24.9), "LOW")

    def test_compute_risk_scores_includes_community(self):
        import pandas as pd
        from risk_scorer import compute_risk_scores
        import networkx as nx

        df = pd.DataFrame([
            {"account_id": "A", "account_holder": "Test", "bank_name": "Bank", "debit": 100.0, "credit": 0.0, "is_velocity_flag": False, "is_high_value_flag": False, "is_balance_breach": False},
        ])
        member_map = {"A": 1}
        graph = nx.DiGraph()
        graph.add_node("A")
        risk_df = compute_risk_scores(df, [], [], [], [], [], [], pd.DataFrame(), graph, member_map)
        self.assertIn("community_id", risk_df.columns)
        self.assertEqual(risk_df.loc[0, "community_id"], 1)


if __name__ == "__main__":
    unittest.main()
