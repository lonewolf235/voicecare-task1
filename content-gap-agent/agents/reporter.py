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
