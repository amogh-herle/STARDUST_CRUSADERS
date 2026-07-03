import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from reporting import generate_investigator_report


class TestReporting(unittest.TestCase):
    def test_generate_investigator_report_creates_html_and_pdf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            report = {
                "run_timestamp": "2026-07-01T00:00:00",
                "trail_manifest": [],
            }
            risk_df = pd.DataFrame([
                {
                    "account_id": "A",
                    "account_holder": "Test",
                    "bank_name": "Bank",
                    "risk_score": 30.0,
                    "risk_tier": "HIGH",
                    "active_patterns": "FAN_IN | FAN_OUT",
                    "risk_reasoning": "Test reasoning",
                    "isolation_mean_score": 1.0,
                    "isolation_max_score": 1.2,
                    "graph_risk_score": 0.5,
                }
            ])
            community_summaries = [
                {"community_id": 1, "size": 1, "total_flow": 1000.0, "internal_ratio": 1.0}
            ]
            community_risk = [{"community_id": 1, "n_accounts": 1, "avg_risk": 30.0, "max_risk": 30.0}]

            html_path, pdf_path = generate_investigator_report(
                out_dir, report, risk_df, community_summaries, community_risk
            )

            self.assertTrue(html_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertGreater(html_path.stat().st_size, 0)
            self.assertGreater(pdf_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
