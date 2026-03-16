"""
reporter.py - Sends formatted output to Slack and saves results to CSV.

Takes gap analysis + generated scripts and:
1. Saves a timestamped CSV report of all gaps + scripts
2. Posts a formatted Slack message with the top findings
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from agents.notion_exporter import export_script_to_notion

logger = logging.getLogger(__name__)

REPORTS_DIR = os.getenv("REPORTS_DIR", "reports")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#content-strategy")
COMPANY_NAME = os.getenv("COMPANY_NAME", "My Company")


# ---------------------------------------------------------------------------
# CSV / File Reporting
# ---------------------------------------------------------------------------

def _ensure_reports_dir() -> Path:
    path = Path(REPORTS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_gaps_to_csv(
    gap_analysis: dict[str, Any],
    run_timestamp: str,
) -> Path:
    """
    Save the full gap analysis (all top gaps) to a CSV file.

    Returns:
        Path to the written CSV file.
    """
    reports_dir = _ensure_reports_dir()
    filename = f"gap_report_{run_timestamp}.csv"
    filepath = reports_dir / filename

    top_gaps = gap_analysis.get("top_gaps", [])
    rows = []
    for gap in top_gaps:
        scores = gap.get("scores", {})
        rows.append(
            {
                "rank": gap.get("rank", ""),
                "topic": gap.get("topic", ""),
                "gap_description": gap.get("gap_description", ""),
                "covered_by_competitors": ", ".join(gap.get("covered_by_competitors", [])),
                "our_coverage": gap.get("our_coverage", ""),
                "search_demand": scores.get("search_demand", ""),
                "competitive_pressure": scores.get("competitive_pressure", ""),
                "strategic_fit": scores.get("strategic_fit", ""),
                "priority_score": scores.get("priority_score", ""),
                "recommended_angle": gap.get("recommended_angle", ""),
                "suggested_format": gap.get("suggested_format", ""),
                "estimated_word_count": gap.get("estimated_word_count", ""),
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(filepath, index=False)
    logger.info(f"Gap report saved: {filepath}")
    return filepath


def save_scripts_to_csv(
    scripts: list[dict[str, Any]],
    run_timestamp: str,
) -> Path:
    """
    Save generated video scripts to a CSV file.

    Returns:
        Path to the written CSV file.
    """
    reports_dir = _ensure_reports_dir()
    filename = f"scripts_{run_timestamp}.csv"
    filepath = reports_dir / filename

    rows = []
    for script in scripts:
        if script.get("error"):
            rows.append(
                {
                    "rank": script.get("gap_rank", ""),
                    "topic": script.get("topic", ""),
                    "error": script.get("error", ""),
                    "hook": "",
                    "full_script": "",
                    "caption": "",
                    "hashtags": "",
                    "thumbnail_text": "",
                    "total_duration_seconds": "",
                }
            )
        else:
            hook_data = script.get("hook", {})
            rows.append(
                {
                    "rank": script.get("gap_rank", ""),
                    "topic": script.get("topic", ""),
                    "error": "",
                    "hook": hook_data.get("text", ""),
                    "full_script": script.get("full_script", ""),
                    "caption": script.get("caption", ""),
                    "hashtags": " ".join(script.get("hashtags", [])),
                    "thumbnail_text": script.get("thumbnail_text", ""),
                    "total_duration_seconds": script.get("total_duration_seconds", 60),
                }
            )

    df = pd.DataFrame(rows)
    df.to_csv(filepath, index=False)
    logger.info(f"Scripts report saved: {filepath}")
    return filepath


def save_json_report(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    run_timestamp: str,
) -> Path:
    """Save the full combined report as a JSON file for archiving."""
    reports_dir = _ensure_reports_dir()
    filename = f"full_report_{run_timestamp}.json"
    filepath = reports_dir / filename

    report = {
        "run_timestamp": run_timestamp,
        "gap_analysis": {k: v for k, v in gap_analysis.items() if k != "_gaps_df"},
        "scripts": [
            {k: v for k, v in s.items() if k not in ("_raw",)}
            for s in scripts
        ],
    }

    with open(filepath, "w") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info(f"JSON report saved: {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Slack Reporting
# ---------------------------------------------------------------------------

def _build_slack_blocks(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    gaps_csv_path: Path,
    scripts_csv_path: Path,
    run_timestamp: str,
) -> list[dict]:
    """Build Slack Block Kit message blocks for the weekly report."""
    top_gaps = gap_analysis.get("top_gaps", [])[:5]  # Show top 5 in Slack
    summary = gap_analysis.get("summary", "No summary available.")
    total_gaps = gap_analysis.get("total_gaps_found", len(top_gaps))
    analysis_date = gap_analysis.get("analysis_date", run_timestamp[:10])

    blocks: list[dict] = [
        # Header
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Weekly Content Gap Report — {analysis_date}",
            },
        },
        {"type": "divider"},
        # Summary
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Executive Summary*\n{summary}\n\n"
                        f"*Total gaps identified:* {total_gaps}  |  "
                        f"*Scripts generated:* {len([s for s in scripts if not s.get('error')])}",
            },
        },
        {"type": "divider"},
        # Top gaps header
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🎯 Top Content Gaps This Week*"},
        },
    ]

    # Individual gap blocks
    for gap in top_gaps:
        scores = gap.get("scores", {})
        priority = scores.get("priority_score", "N/A")
        competitors = ", ".join(gap.get("covered_by_competitors", []))
        recommended_angle = gap.get("recommended_angle", "")

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*#{gap.get('rank', '?')}. {gap.get('topic', 'N/A')}*\n"
                        f"Priority Score: `{priority}`  |  Covered by: _{competitors}_\n"
                        f"_{gap.get('gap_description', '')}_\n"
                        f"💡 Angle: {recommended_angle}"
                    ),
                },
            }
        )

    blocks.append({"type": "divider"})

    # Script previews
    if scripts:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*🎬 Generated Video Scripts (Hooks)*"},
            }
        )
        for script in scripts[:3]:  # Show first 3 hooks in Slack
            if script.get("error"):
                continue
            hook_data = script.get("hook", {})
            hook_text = hook_data.get("text", "N/A")
            caption = script.get("caption", "")
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{script.get('topic', 'N/A')}*\n"
                            f"🪝 Hook: _{hook_text}_\n"
                            f"📱 Caption: {caption}\n"
                            f"🔗 < {script.get('notion_url')} | View in Notion >" if script.get('notion_url') else "🔗 Notion Export Disabled"
                        ),
                    },
                }
            )

    blocks.append({"type": "divider"})

    # Footer with file paths
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"📁 Reports saved to `{gaps_csv_path}` and `{scripts_csv_path}`\n"
                        f"Run completed at: {run_timestamp}"
                    ),
                }
            ],
        }
    )

    return blocks


def send_slack_report(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    gaps_csv_path: Path,
    scripts_csv_path: Path,
    run_timestamp: str,
) -> bool:
    """
    Post the weekly content gap report to Slack via webhook.

    Returns:
        True if successful, False if Slack webhook is not configured or fails.
    """
    webhook_url = SLACK_WEBHOOK_URL
    if not webhook_url:
        logger.warning(
            "SLACK_WEBHOOK_URL not set — skipping Slack notification. "
            "Set this in your .env file to enable Slack reporting."
        )
        return False

    blocks = _build_slack_blocks(
        gap_analysis=gap_analysis,
        scripts=scripts,
        gaps_csv_path=gaps_csv_path,
        scripts_csv_path=scripts_csv_path,
        run_timestamp=run_timestamp,
    )

    payload = {
        "channel": SLACK_CHANNEL,
        "username": f"{COMPANY_NAME} Content Bot",
        "icon_emoji": ":bar_chart:",
        "blocks": blocks,
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if response.status_code == 200 and response.text == "ok":
            logger.info("Slack report sent successfully.")
            return True
        else:
            logger.error(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )
            return False
    except requests.RequestException as e:
        logger.error(f"Failed to send Slack report: {e}")
        return False


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )


def generate_html_dashboard(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    run_timestamp: str,
) -> str:
    """Return a self-contained HTML dashboard string with all 3 script posts."""
    top_gaps = gap_analysis.get("top_gaps", [])
    total_gaps = gap_analysis.get("total_gaps_found", 0)
    summary = _html_escape(gap_analysis.get("summary", ""))
    analysis_date = gap_analysis.get("analysis_date", run_timestamp[:10])
    script_count = sum(1 for s in scripts if not s.get("error"))

    # Build script cards
    cards_html = ""
    gradient_pairs = [
        ("#00d4aa", "#007a62"),
        ("#6366f1", "#4338ca"),
        ("#f59e0b", "#b45309"),
    ]
    for idx, script in enumerate(scripts[:3]):
        if script.get("error"):
            continue
        gap = top_gaps[idx] if idx < len(top_gaps) else {}
        scores = gap.get("scores", {})
        priority = scores.get("priority_score", 0)
        priority_pct = min(int(float(priority) * 10), 100)
        topic = _html_escape(script.get("topic") or gap.get("topic", f"Topic {idx + 1}"))
        hook = _html_escape(script.get("hook", {}).get("text", ""))
        full_script = _html_escape(script.get("full_script", ""))
        caption = _html_escape(script.get("caption", ""))
        hashtags = script.get("hashtags", [])
        thumbnail = _html_escape(script.get("thumbnail_text", ""))
        competitors = ", ".join(gap.get("covered_by_competitors", []))
        angle = _html_escape(gap.get("recommended_angle", ""))
        g1, g2 = gradient_pairs[idx % len(gradient_pairs)]
        pills = "".join(
            f'<span style="background:rgba(255,255,255,0.12);border-radius:999px;'
            f'padding:3px 12px;font-size:12px;margin:3px 3px 0 0;display:inline-block;">'
            f'{_html_escape(h)}</span>'
            for h in hashtags
        )
        card_id = f"script{idx}"
        cards_html += f"""
        <div class="card">
          <div class="card-header" style="background:linear-gradient(135deg,{g1} 0%,{g2} 100%);">
            <div class="rank">#{idx + 1}</div>
            <div class="card-meta">
              <div class="card-topic">{topic}</div>
              {f'<div class="card-competitor">Covered by: {_html_escape(competitors)}</div>' if competitors else ''}
            </div>
            <div class="score-badge">{float(priority):.1f}/10</div>
          </div>
          <div class="card-body">
            <div class="score-row">
              <span>Priority Score</span>
              <div class="score-bar">
                <div class="score-fill" style="width:{priority_pct}%;background:linear-gradient(90deg,{g1},{g2});"></div>
              </div>
              <span>{float(priority):.1f}</span>
            </div>
            {f'<div class="angle"><strong>💡 Recommended Angle:</strong> {angle}</div>' if angle else ''}
            <div class="section-title">🪝 Hook</div>
            <blockquote class="hook-text">{hook}</blockquote>
            <div class="section-title">📜 Full Script</div>
            <button class="toggle-btn" onclick="toggleScript('{card_id}')">▾ Show Full Script</button>
            <div class="script-content" id="{card_id}" style="display:none">
              <pre>{full_script}</pre>
            </div>
            {f'<div class="section-title">📱 Social Caption</div><p class="caption">{caption}</p>' if caption else ''}
            {f'<div class="section-title">🏷 Hashtags</div><div class="hashtags">{pills}</div>' if pills else ''}
            {f'<div class="thumbnail">🖼 Thumbnail Text: <strong>{thumbnail}</strong></div>' if thumbnail else ''}
          </div>
        </div>"""

    if not cards_html:
        cards_html = """<div style="text-align:center;color:#6b7a99;padding:40px;">
          <div style="font-size:40px;margin-bottom:12px;">📝</div>
          <div>No scripts generated. Run in live mode with real API keys to generate scripts.</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Content Gap Report | VoiceCare.ai</title>
<style>
  :root {{
    --bg: #070b14; --surface: #0e1524; --surface2: #162035;
    --primary: #00d4aa; --text: #e8f1ff; --muted: #6b7a99;
    --border: #1e2d4d;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  header {{
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 18px 40px; display: flex; align-items: center; justify-content: space-between;
  }}
  .logo {{ font-size: 22px; font-weight: 800; }}
  .logo-dot {{ color: var(--primary); }}
  .logo-tag {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
  .date-badge {{
    background: rgba(0,212,170,0.1); border: 1px solid rgba(0,212,170,0.3);
    color: var(--primary); border-radius: 999px; padding: 4px 16px; font-size: 13px;
  }}
  main {{ max-width: 860px; margin: 0 auto; padding: 40px 24px 60px; }}
  .summary-card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px; padding: 28px; margin-bottom: 36px;
  }}
  .summary-title {{ font-size: 20px; font-weight: 700; margin-bottom: 14px; color: var(--primary); }}
  .meta-row {{
    display: flex; gap: 24px; margin-bottom: 14px; flex-wrap: wrap;
  }}
  .meta-item {{
    display: flex; flex-direction: column; gap: 2px;
  }}
  .meta-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }}
  .meta-val {{ font-size: 20px; font-weight: 700; color: var(--text); }}
  .summary-text {{ color: var(--muted); line-height: 1.6; }}
  .section-header {{
    font-size: 22px; font-weight: 700; margin-bottom: 22px;
    background: linear-gradient(90deg, var(--primary), #6366f1);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 16px; overflow: hidden; margin-bottom: 28px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
    transition: transform 0.2s;
  }}
  .card:hover {{ transform: translateY(-2px); }}
  .card-header {{
    padding: 22px 28px; display: flex; align-items: center; gap: 16px;
    color: #fff;
  }}
  .rank {{
    font-size: 32px; font-weight: 900; opacity: 0.9; flex-shrink: 0;
    background: rgba(0,0,0,0.2); border-radius: 10px;
    width: 56px; height: 56px; display: flex; align-items: center; justify-content: center;
  }}
  .card-meta {{ flex: 1; }}
  .card-topic {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; }}
  .card-competitor {{ font-size: 12px; opacity: 0.8; }}
  .score-badge {{
    background: rgba(0,0,0,0.25); border-radius: 10px;
    padding: 8px 14px; font-size: 18px; font-weight: 700;
    white-space: nowrap;
  }}
  .card-body {{ padding: 24px 28px; display: flex; flex-direction: column; gap: 16px; }}
  .score-row {{ display: flex; align-items: center; gap: 10px; }}
  .score-row > span {{ font-size: 12px; color: var(--muted); white-space: nowrap; }}
  .score-bar {{ flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }}
  .score-fill {{ height: 100%; border-radius: 3px; }}
  .angle {{ font-size: 13px; color: var(--muted); padding: 10px 14px; background: var(--surface2); border-radius: 8px; border-left: 3px solid var(--primary); }}
  .section-title {{ font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }}
  .hook-text {{
    border-left: 3px solid var(--primary); padding: 12px 18px;
    background: rgba(0,212,170,0.06); border-radius: 0 8px 8px 0;
    font-size: 16px; font-style: italic; line-height: 1.6; color: var(--text);
  }}
  .toggle-btn {{
    background: var(--surface2); border: 1px solid var(--border);
    color: var(--primary); padding: 8px 18px; border-radius: 8px;
    font-size: 13px; cursor: pointer; transition: all 0.2s; width: fit-content;
  }}
  .toggle-btn:hover {{ background: rgba(0,212,170,0.1); }}
  .script-content {{
    background: #000308; border: 1px solid #1a2a1a; border-radius: 10px;
    padding: 16px;
  }}
  .script-content pre {{
    color: #00ff41; font-family: 'Courier New', monospace; font-size: 12px;
    line-height: 1.7; white-space: pre-wrap; word-break: break-word;
  }}
  .caption {{
    background: var(--surface2); border-radius: 8px; padding: 12px 16px;
    font-size: 14px; line-height: 1.6; color: var(--text);
  }}
  .hashtags {{ display: flex; flex-wrap: wrap; gap: 0; }}
  .thumbnail {{
    font-size: 13px; color: var(--muted);
    padding: 10px 14px; background: var(--surface2); border-radius: 8px;
  }}
  .download-section {{
    text-align: center; padding: 32px 0 8px;
  }}
  .dl-btn {{
    display: inline-block;
    background: linear-gradient(135deg, var(--primary) 0%, #007a62 100%);
    color: #000; font-weight: 700; font-size: 16px;
    padding: 14px 40px; border-radius: 999px; text-decoration: none;
    transition: all 0.2s; box-shadow: 0 4px 18px rgba(0,212,170,0.3);
  }}
  .dl-btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 28px rgba(0,212,170,0.4); }}
  footer {{
    text-align: center; padding: 20px; border-top: 1px solid var(--border);
    color: var(--muted); font-size: 13px; margin-top: 20px;
  }}
  footer strong {{ color: var(--text); }}
  @media (max-width: 600px) {{
    header {{ padding: 14px 20px; flex-direction: column; gap: 10px; text-align: center; }}
    .card-header {{ flex-direction: column; text-align: center; }}
    main {{ padding: 24px 16px; }}
  }}
</style>
</head>
<body>
<header>
  <div>
    <div class="logo">Voice<span class="logo-dot">Care.ai</span></div>
    <div class="logo-tag">The Content Gap Agent</div>
  </div>
  <div class="date-badge">Report: {analysis_date}</div>
</header>
<main>
  <div class="summary-card">
    <div class="summary-title">📊 Executive Summary</div>
    <div class="meta-row">
      <div class="meta-item"><span class="meta-label">Gaps Found</span><span class="meta-val">{total_gaps}</span></div>
      <div class="meta-item"><span class="meta-label">Scripts Generated</span><span class="meta-val">{script_count}</span></div>
      <div class="meta-item"><span class="meta-label">Analysis Date</span><span class="meta-val" style="font-size:15px;">{analysis_date}</span></div>
    </div>
    <p class="summary-text">{summary}</p>
  </div>
  <div class="section-header">🎬 Generated Content Posts</div>
  {cards_html}
  <div class="download-section">
    <a href="/download" class="dl-btn">⬇ &nbsp;Download Full Report (ZIP)</a>
  </div>
</main>
<footer>Built by <strong>Shubham Anand</strong> &nbsp;&bull;&nbsp; VoiceCare.ai</footer>
<script>
function toggleScript(id) {{
  const el = document.getElementById(id);
  const btn = el.previousElementSibling;
  if (el.style.display === 'none') {{
    el.style.display = 'block';
    btn.textContent = '▴ Hide Script';
  }} else {{
    el.style.display = 'none';
    btn.textContent = '▾ Show Full Script';
  }}
}}
</script>
</body>
</html>"""


def save_html_dashboard(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    run_timestamp: str,
) -> Path:
    """Generate and save the HTML dashboard. Returns the saved file path."""
    reports_dir = _ensure_reports_dir()
    filepath = reports_dir / f"dashboard_{run_timestamp}.html"
    html = generate_html_dashboard(gap_analysis, scripts, run_timestamp)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML dashboard saved: {filepath}")
    return filepath


def run_reporter(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Main entry point. Saves reports and sends Slack notification.

    Args:
        gap_analysis: Output from gap_analyzer.run_gap_analyzer()
        scripts: Output from script_writer.run_script_writer()

    Returns:
        Dict with paths to saved files and Slack delivery status.
    """
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info("Saving gap analysis CSV...")
    gaps_csv = save_gaps_to_csv(gap_analysis, run_timestamp)

    logger.info("Saving scripts CSV...")
    scripts_csv = save_scripts_to_csv(scripts, run_timestamp)

    logger.info("Exporting scripts to Notion...")
    notion_success_count = 0
    for script in scripts:
        if not script.get("error"):
            url = export_script_to_notion(script)
            if url:
                script["notion_url"] = url
                notion_success_count += 1

    logger.info("Saving full JSON report...")
    json_report = save_json_report(gap_analysis, scripts, run_timestamp)

    logger.info("Generating HTML dashboard...")
    html_dashboard = save_html_dashboard(gap_analysis, scripts, run_timestamp)

    logger.info("Sending Slack report...")
    slack_sent = send_slack_report(
        gap_analysis=gap_analysis,
        scripts=scripts,
        gaps_csv_path=gaps_csv,
        scripts_csv_path=scripts_csv,
        run_timestamp=run_timestamp,
    )

    result = {
        "run_timestamp": run_timestamp,
        "gaps_csv": str(gaps_csv),
        "scripts_csv": str(scripts_csv),
        "json_report": str(json_report),
        "html_dashboard": str(html_dashboard),
        "notion_exports": notion_success_count,
        "slack_sent": slack_sent,
        "gaps_found": gap_analysis.get("total_gaps_found", 0),
        "scripts_generated": len([s for s in scripts if not s.get("error")]),
    }

    # Print human-readable summary to stdout/logs
    logger.info(
        f"\n{'=' * 50}\n"
        f"CONTENT GAP AGENT — RUN COMPLETE\n"
        f"{'=' * 50}\n"
        f"Timestamp:         {run_timestamp}\n"
        f"Gaps found:        {result['gaps_found']}\n"
        f"Scripts generated: {result['scripts_generated']}\n"
        f"Gaps CSV:          {result['gaps_csv']}\n"
        f"Scripts CSV:       {result['scripts_csv']}\n"
        f"JSON Report:       {result['json_report']}\n"
        f"HTML Dashboard:    {result['html_dashboard']}\n"
        f"Notion Exports:    {notion_success_count}\n"
        f"Slack sent:        {result['slack_sent']}\n"
        f"{'=' * 50}"
    )

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Smoke test with minimal mock data
    mock_gaps = {
        "total_gaps_found": 2,
        "analysis_date": "2025-01-01",
        "summary": "Competitors have strong coverage of LLM fine-tuning and AI agents.",
        "top_gaps": [
            {
                "rank": 1,
                "topic": "LLM Fine-tuning Guide",
                "gap_description": "No comprehensive guide on our site.",
                "covered_by_competitors": ["Competitor A"],
                "our_coverage": "none",
                "scores": {
                    "search_demand": 9, "competitive_pressure": 8,
                    "strategic_fit": 7, "priority_score": 8.25
                },
                "recommended_angle": "Weekend fine-tuning project for non-ML teams",
                "suggested_format": "video",
                "estimated_word_count": 1500,
            }
        ],
    }
    mock_scripts = [
        {
            "topic": "LLM Fine-tuning Guide",
            "gap_rank": 1,
            "priority_score": 8.25,
            "covered_by_competitors": ["Competitor A"],
            "hook": {"text": "Most companies don't realize they can fine-tune an LLM in a weekend..."},
            "full_script": "[HOOK] Most companies don't realize...\n[CTA] Follow for more AI tips.",
            "caption": "Fine-tune your first LLM this weekend — here's exactly how.",
            "hashtags": ["#AI", "#MachineLearning", "#LLM", "#TechTips"],
            "thumbnail_text": "Fine-Tune LLMs Fast",
            "total_duration_seconds": 60,
        }
    ]

    result = run_reporter(mock_gaps, mock_scripts)
    print(json.dumps(result, indent=2))
