import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import main
from routers import assistant as assistant_router


class TestAssistantRouter(unittest.TestCase):
    def test_assistant_inference_route(self):
        with patch.object(assistant_router.assistant_service, "ask", return_value=("Answer", ["risk_scores.csv"])), \
             patch.object(assistant_router.assistant_service.repository, "get_account", return_value={"account_id": "A1"}):
            client = TestClient(main.app)
            response = client.post("/api/v1/assistant/chat", json={"question": "What is the risk for A1?", "account_id": "A1"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], "Answer")
        self.assertEqual(payload["sources"], ["risk_scores.csv"])
