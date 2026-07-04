"""
Phase 8 — Investigator Case Report Generation

Generates human-readable investigator output from the Phase 8 analytics results.
This module produces both HTML and a basic PDF representation using pure Python,
so it can work without additional PDF libraries in the runtime environment.
"""

import html
from pathlib import Path
from typing import Any

import pandas as pd

from analytics_config import (
    RISK_TIERS,
    RISK_TIER_FALLBACK_ENABLED,
    RISK_TIER_FALLBACK_HIGH,
    RISK_TIER_FALLBACK_CRITICAL,
)


def generate_investigator_report(
    out_dir: str,
    report: dict[str, Any],
    risk_df: pd.DataFrame,
    community_summaries: list[dict[str, Any]] | pd.DataFrame,
    community_risk: list[dict[str, Any]] | pd.DataFrame,
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    html_path = out_dir / "investigator_case_report.html"
    pdf_path = out_dir / "investigator_case_report.pdf"

    html_content = _build_html(report, risk_df, community_summaries, community_risk)
    html_path.write_text(html_content, encoding="utf-8")
    _write_pdf_report(pdf_path, report, risk_df, community_summaries, community_risk)

    return html_path, pdf_path


def _build_html(
    report: dict[str, Any],
    risk_df: pd.DataFrame,
    community_summaries: list[dict[str, Any]] | pd.DataFrame,
    community_risk: list[dict[str, Any]] | pd.DataFrame,
) -> str:
    community_summaries = _ensure_dataframe(community_summaries)
    community_risk = _ensure_dataframe(community_risk)

    top_accounts = risk_df.head(10)
    tier_counts = risk_df["risk_tier"].value_counts().reindex(["CRITICAL", "HIGH", "MEDIUM", "LOW"]).fillna(0).astype(int)

    rows = [
        "<!DOCTYPE html>",
        "<html><head><meta charset=\"utf-8\"><title>Phase 8 Investigator Case Report</title>",
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#111;} h1,h2,h3{color:#1b3a4b;} table{border-collapse:collapse;width:100%;margin-bottom:24px;} th,td{border:1px solid #ddd;padding:8px;vertical-align:top;} th{background:#f4f7fa;} .bar{display:inline-block;height:12px;background:#2d7cd6;border-radius:6px;} .small{text-transform:uppercase;font-size:0.85em;color:#555;} .mono{font-family:monospace;background:#f4f4f4;padding:2px 4px;border-radius:3px;}</style>",
        "</head><body>",
        "<h1>Phase 8 Investigator Case Report</h1>",
        f"<p><strong>Date:</strong> {html.escape(report.get('run_timestamp', 'N/A'))}</p>",
    ]

    rows.extend([
        "<h2>Risk Calibration Overview</h2>",
        "<p>Phase 8 thresholds are defined as:</p>",
        "<ul>",
        f"<li><strong>CRITICAL</strong>: score ≥ {RISK_TIERS['CRITICAL']}</li>",
        f"<li><strong>HIGH</strong>: score ≥ {RISK_TIERS['HIGH']}</li>",
        f"<li><strong>MEDIUM</strong>: score ≥ {RISK_TIERS['MEDIUM']}</li>",
        f"<li><strong>LOW</strong>: score ≥ {RISK_TIERS['LOW']}</li>",
        "</ul>",
    ])
    if RISK_TIER_FALLBACK_ENABLED:
        rows.extend([
            "<p><em>Fallback calibration is enabled for low-volume datasets. "
            "If no accounts reach HIGH or CRITICAL under absolute thresholds, "
            "the system will promote top performers using fallback thresholds.</em></p>",
            "<p>Fallback thresholds:</p>",
            "<ul>",
            f"<li><strong>HIGH</strong> fallback: score ≥ {RISK_TIER_FALLBACK_HIGH}</li>",
            f"<li><strong>CRITICAL</strong> fallback: score ≥ {RISK_TIER_FALLBACK_CRITICAL}</li>",
            "</ul>",
        ])

    rows.extend([
        "<h2>Risk Distribution</h2>",
        "<table><thead><tr><th>Tier</th><th>Accounts</th></tr></thead><tbody>",
    ])
    for tier in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        rows.append(f"<tr><td>{tier}</td><td>{tier_counts[tier]}</td></tr>")
    rows.extend(["</tbody></table>"])

    rows.append("<h2>Top 10 Suspicious Accounts</h2>")
    for _, row in top_accounts.iterrows():
        rows.append(f"<h3>{html.escape(str(row['account_id']))} — {html.escape(str(row['risk_tier']))} ({row['risk_score']})</h3>")
        rows.append("<table>")
        rows.append(f"<tr><th>Holder</th><td>{html.escape(str(row.get('account_holder', '')) or 'N/A')}</td></tr>")
        rows.append(f"<tr><th>Bank</th><td>{html.escape(str(row.get('bank_name', '')) or 'N/A')}</td></tr>")
        rows.append(f"<tr><th>Active patterns</th><td>{html.escape(str(row.get('active_patterns', '')))}</td></tr>")
        rows.append(f"<tr><th>Risk reasoning</th><td>{html.escape(str(row.get('risk_reasoning', '')))}</td></tr>")
        rows.append(f"<tr><th>Isolation</th><td>{html.escape(str(row.get('isolation_mean_score', '')))} / {html.escape(str(row.get('isolation_max_score', '')))}</td></tr>")
        rows.append(f"<tr><th>Graph risk</th><td>{html.escape(str(row.get('graph_risk_score', '')))}</td></tr>")
        community_value = row.get('community_id', 'N/A')
        if community_value is None or str(community_value).lower() == 'nan':
            community_value = 'N/A'
        rows.append(f"<tr><th>Community</th><td>{html.escape(str(community_value))}</td></tr>")
        rows.append(f"<tr><th>Money trails</th><td>Forward: {html.escape(str(_trail_status(report, row['account_id'], 'forward')))}; Backward: {html.escape(str(_trail_status(report, row['account_id'], 'backward')))}</td></tr>")
        rows.append("</table>")

    if not community_risk.empty:
        rows.extend(["<h2>Community Risk Summary</h2>", "<table><thead><tr><th>Community ID</th><th>Accounts</th><th>Avg Risk</th><th>Max Risk</th></tr></thead><tbody>"])
        for _, row in community_risk.head(5).iterrows():
            rows.append(f"<tr><td>{html.escape(str(row['community_id']))}</td><td>{html.escape(str(row['n_accounts']))}</td><td>{html.escape(str(row['avg_risk']))}</td><td>{html.escape(str(row['max_risk']))}</td></tr>")
        rows.extend(["</tbody></table>"])

    if not community_summaries.empty:
        rows.extend(["<h2>Top Communities by Flow</h2>", "<table><thead><tr><th>Community ID</th><th>Members</th><th>Total Flow</th><th>Internal Ratio</th></tr></thead><tbody>"])
        for _, row in community_summaries.head(5).iterrows():
            rows.append(f"<tr><td>{html.escape(str(row['community_id']))}</td><td>{html.escape(str(row['size']))}</td><td>{html.escape(str(row['total_flow']))}</td><td>{html.escape(str(row['internal_ratio']))}</td></tr>")
        rows.extend(["</tbody></table>"])

    rows.append("</body></html>")
    return '\n'.join(rows)


def _ensure_dataframe(value: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value
    return pd.DataFrame(value)


def _trail_status(report: dict[str, Any], account_id: str, direction: str) -> str:
    for row in report.get('trail_manifest', []):
        if row.get('account') == account_id and row.get('direction') == direction:
            return f"{row.get('status')} ({row.get('hops')} hops, {row.get('trails')} trails)"
    return 'none'


def _write_pdf_report(
    pdf_path: Path,
    report: dict[str, Any],
    risk_df: pd.DataFrame,
    community_summaries: list[dict[str, Any]] | pd.DataFrame,
    community_risk: list[dict[str, Any]] | pd.DataFrame,
) -> None:
    lines = [
        'Phase 8 Investigator Case Report',
        f"Date: {report.get('run_timestamp', 'N/A')}",
        '',
        'Risk tiers: CRITICAL>=75, HIGH>=50, MEDIUM>=25, LOW>=0',
    ]
    tier_counts = risk_df['risk_tier'].value_counts().reindex(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']).fillna(0).astype(int)
    lines.extend([
        f"Counts: CRITICAL {tier_counts['CRITICAL']}, HIGH {tier_counts['HIGH']}, MEDIUM {tier_counts['MEDIUM']}, LOW {tier_counts['LOW']}",
        f"Score range: {risk_df['risk_score'].min():.1f} - {risk_df['risk_score'].max():.1f}",
        '',
        'Top 10 accounts:'
    ])

    for _, row in risk_df.head(10).iterrows():
        lines.append(f"{row['account_id']} | {row['risk_tier']} | {row['risk_score']} | {row['active_patterns']}")
        lines.append(f"  Reason: {row['risk_reasoning']}")
        lines.append(f"  Isolation: {row['isolation_mean_score']}/{row['isolation_max_score']}")
        lines.append('')

    _write_plain_pdf(pdf_path, lines)


def _write_plain_pdf(path: Path, lines: list[str]) -> None:
    def esc(text: str) -> str:
        return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')[:110]

    stream_lines = []
    y = 770
    for line in lines:
        if y < 50:
            break
        stream_lines.append(f"BT /F1 10 Tf 50 {y} Td ({esc(line)}) Tj ET")
        y -= 14

    stream = '\n'.join(stream_lines).encode('latin1')
    objs = []
    objs.append(b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n')
    objs.append(b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n')
    objs.append(b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n')
    objs.append(b'4 0 obj\n<< /Length ' + str(len(stream)).encode('latin1') + b' >>\nstream\n' + stream + b'\nendstream\nendobj\n')
    objs.append(b'5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n')
    offsets = []
    pos = 0
    for obj in objs:
        offsets.append(pos)
        pos += len(obj)
    pdf = b'%PDF-1.4\n'
    for obj in objs:
        pdf += obj
    xref_start = len(pdf)
    pdf += b'xref\n0 6\n0000000000 65535 f \n'
    for off in offsets:
        pdf += f'{off:010d} 00000 n \n'.encode('latin1')
    pdf += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'
    pdf += str(xref_start).encode('latin1') + b'\n%%EOF\n'
    path.write_bytes(pdf)
