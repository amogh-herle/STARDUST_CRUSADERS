import unittest
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.assistant_service import AssistantService
from services.analytics_repository import AnalyticsRepository
import pandas as pd
import tempfile


class TestAssistantService(unittest.TestCase):
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

    def tearDown(self):
        self.tempdir.cleanup()

    @patch("services.llm_provider.ProviderFactory.create_provider")
    def test_ask_uses_provider_and_returns_response(self, mock_create_provider):
        provider = Mock()
        provider.generate.return_value = "OK"
        mock_create_provider.return_value = provider

        service = AssistantService(str(self.root))
        answer, sources = service.ask("What is the risk?", account_id="A1")

        provider.generate.assert_called_once()
        self.assertEqual(answer, "OK")
        self.assertIn("risk_scores.csv", sources)
