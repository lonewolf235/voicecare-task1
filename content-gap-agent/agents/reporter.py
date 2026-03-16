"""
reporter.py - Sends formatted output to Slack and saves results to CSV/JSON/HTML.

Takes gap analysis + generated scripts and:
1. Saves a timestamped CSV report of all gaps + scripts
2. Saves a full JSON archive
3. Generates a self-contained HTML dashboard (opened in the browser)
4. Posts a formatted Slack message with the top findings
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
# HTML Dashboard
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _score_row_color(priority_score: float) -> str:
    """Return a background colour for a gap table row based on priority score."""
    if priority_score >= 8:
        return "#dcfce7"   # green-100
    if priority_score >= 5:
        return "#fef9c3"   # yellow-100
    return "#fee2e2"       # red-100


def _gap_row_html(gap: dict) -> str:
    scores    = gap.get("scores", {})
    priority  = scores.get("priority_score", 0)
    try:
        priority = float(priority)
    except (TypeError, ValueError):
        priority = 0.0
    competitors = ", ".join(gap.get("covered_by_competitors", [])) or "—"
    bg = _score_row_color(priority)
    return (
        f'<tr style="background:{bg};">'
        f'<td class="px-4 py-3 text-center font-bold text-gray-700">{_esc(gap.get("rank",""))}</td>'
        f'<td class="px-4 py-3 font-semibold text-gray-800">{_esc(gap.get("topic",""))}</td>'
        f'<td class="px-4 py-3 text-center">{_esc(scores.get("search_demand","—"))}</td>'
        f'<td class="px-4 py-3 text-center">{_esc(scores.get("novelty","—"))}</td>'
        f'<td class="px-4 py-3 text-center">{_esc(scores.get("viral","—"))}</td>'
        f'<td class="px-4 py-3 text-center font-bold" style="color:#0891b2">{priority:.1f}</td>'
        f'<td class="px-4 py-3 text-sm text-gray-600">{_esc(competitors)}</td>'
        f'<td class="px-4 py-3 text-sm text-gray-600">{_esc(gap.get("recommended_angle","—"))}</td>'
        f'</tr>\n'
    )


def _script_card_html(script: dict, idx: int) -> str:
    """Return HTML for a single script accordion card."""
    topic          = _esc(script.get("topic", f"Script {idx+1}"))
    thumbnail_text = _esc(script.get("thumbnail_text", ""))
    hook_text      = _esc((script.get("hook") or {}).get("text", ""))
    caption        = _esc(script.get("caption", ""))
    hashtags       = " ".join(
        f'<span class="hashtag-pill">{_esc(h)}</span>'
        for h in script.get("hashtags", [])
    )
    duration = script.get("total_duration_seconds", "")

    # Parse scenes from full_script text (looks for [SCENE_n] markers)
    full_script = script.get("full_script", "")
    scene_rows = ""
    if full_script:
        import re
        scenes = re.split(r'\[SCENE_\d+\]', full_script)
        scene_titles_found = re.findall(r'\[SCENE_(\d+)\]', full_script)
        for i, (scene_n, scene_text) in enumerate(zip(scene_titles_found, scenes[1:])):
            scene_rows += (
                f'<details class="scene-details">'
                f'<summary class="scene-summary">Scene {_esc(scene_n)}</summary>'
                f'<p class="scene-body">{_esc(scene_text.strip())}</p>'
                f'</details>\n'
            )

    cta_match = ""
    import re
    cta_found = re.search(r'\[CTA\](.*?)(?:\[|$)', full_script, re.DOTALL)
    if cta_found:
        cta_match = f'<div class="cta-box"><strong>CTA:</strong> {_esc(cta_found.group(1).strip())}</div>'

    return f"""
<div class="script-card">
  <div class="script-card-header">
    <div>
      <span class="thumbnail-badge">{thumbnail_text}</span>
      <h3 class="script-title">{topic}</h3>
    </div>
    {f'<span class="duration-badge">⏱ {duration}s</span>' if duration else ''}
  </div>

  <div class="hook-box">
    <div class="hook-label">🪝 Hook</div>
    <div class="hook-text">"{hook_text}"</div>
  </div>

  {scene_rows}
  {cta_match}

  {f'<div class="caption-box"><strong>Caption:</strong> {caption}</div>' if caption else ''}
  {f'<div class="hashtags-row">{hashtags}</div>' if hashtags else ''}
</div>
"""


def generate_html_report(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    run_timestamp: str,
) -> str:
    """
    Generate a self-contained HTML dashboard string.

    Returns:
        Complete HTML as a string (no external file dependencies).
    """
    gaps           = gap_analysis.get("top_gaps", [])
    total_gaps     = gap_analysis.get("total_gaps_found", len(gaps))
    analysis_date  = gap_analysis.get("analysis_date", run_timestamp[:10])
    summary        = _esc(gap_analysis.get("summary", ""))
    ok_scripts     = [s for s in scripts if not s.get("error")]
    top_score      = ""
    if gaps:
        raw = gaps[0].get("scores", {}).get("priority_score", "")
        try:
            top_score = f"{float(raw):.1f}"
        except (TypeError, ValueError):
            top_score = str(raw)

    gap_rows_html   = "".join(_gap_row_html(g) for g in gaps)
    script_cards    = "".join(_script_card_html(s, i) for i, s in enumerate(ok_scripts))
    chart_labels    = json.dumps([g.get("topic", "")[:30] for g in gaps])
    chart_scores    = json.dumps([
        round(float(g.get("scores", {}).get("priority_score", 0)), 2)
        for g in gaps
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Content Gap Dashboard — Voicecare.ai</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet"/>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --vc-primary: #0891b2;
      --vc-accent:  #06b6d4;
      --vc-dark:    #0c4a6e;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', sans-serif; background: #f0f9ff; color: #1e293b; }}
    a {{ color: var(--vc-primary); }}

    /* ── Header ── */
    header {{
      background: var(--vc-dark);
      color: #fff;
      padding: 1rem 2rem;
      display: flex;
      align-items: center;
      gap: 1rem;
    }}
    .logo-icon {{
      width: 2rem; height: 2rem; border-radius: 0.5rem;
      background: var(--vc-accent);
      display: flex; align-items: center; justify-content: center;
    }}
    .logo-text {{ font-size: 1.2rem; font-weight: 800; letter-spacing: -0.02em; }}
    .header-right {{ margin-left: auto; font-size: 0.8rem; color: #a5f3fc; }}

    /* ── Layout ── */
    main {{ max-width: 1100px; margin: 0 auto; padding: 2.5rem 1.5rem; }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{ font-size: 1.25rem; font-weight: 700; color: #0c4a6e; margin-bottom: 1rem;
          border-left: 4px solid var(--vc-accent); padding-left: 0.75rem; }}

    /* ── Summary cards ── */
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
    .card {{
      background: #fff; border-radius: 1rem; padding: 1.5rem;
      box-shadow: 0 1px 6px rgba(0,0,0,0.07); border: 1px solid #e0f2fe;
    }}
    .card-value {{ font-size: 2.4rem; font-weight: 800; color: var(--vc-primary); line-height: 1; }}
    .card-label {{ font-size: 0.8rem; color: #64748b; margin-top: 0.4rem; font-weight: 500; }}

    /* ── Summary box ── */
    .summary-box {{
      background: #fff; border-radius: 1rem; padding: 1.25rem 1.5rem;
      border: 1px solid #e0f2fe; color: #334155; font-size: 0.95rem; line-height: 1.7;
    }}

    /* ── Chart ── */
    .chart-wrap {{
      background: #fff; border-radius: 1rem; padding: 1.5rem;
      box-shadow: 0 1px 6px rgba(0,0,0,0.07); border: 1px solid #e0f2fe;
    }}

    /* ── Gap table ── */
    .table-wrap {{
      overflow-x: auto; border-radius: 1rem;
      box-shadow: 0 1px 6px rgba(0,0,0,0.07); border: 1px solid #e0f2fe;
    }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    thead {{ background: var(--vc-dark); color: #fff; }}
    thead th {{ padding: 0.75rem 1rem; text-align: left; font-size: 0.78rem;
                font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; white-space: nowrap; }}
    tbody tr {{ border-bottom: 1px solid #f1f5f9; }}
    tbody td {{ font-size: 0.85rem; }}
    tbody tr:last-child {{ border-bottom: none; }}

    /* ── Script cards ── */
    .script-card {{
      background: #fff; border-radius: 1rem; padding: 1.5rem;
      margin-bottom: 1.25rem;
      box-shadow: 0 1px 6px rgba(0,0,0,0.07); border: 1px solid #e0f2fe;
    }}
    .script-card-header {{
      display: flex; justify-content: space-between; align-items: flex-start;
      margin-bottom: 1rem;
    }}
    .thumbnail-badge {{
      display: inline-block; background: var(--vc-dark); color: #a5f3fc;
      font-size: 0.7rem; font-weight: 700; padding: 0.25rem 0.6rem;
      border-radius: 0.35rem; margin-bottom: 0.4rem; letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .script-title {{ font-size: 1.1rem; font-weight: 700; color: #0c4a6e; margin-top: 0.25rem; }}
    .duration-badge {{
      font-size: 0.75rem; background: #f0f9ff; color: var(--vc-primary);
      border: 1px solid #bae6fd; border-radius: 1rem; padding: 0.25rem 0.6rem; white-space: nowrap;
    }}
    .hook-box {{
      background: #f0f9ff; border-left: 4px solid var(--vc-accent);
      border-radius: 0.5rem; padding: 0.9rem 1rem; margin-bottom: 1rem;
    }}
    .hook-label {{ font-size: 0.72rem; font-weight: 700; color: var(--vc-primary);
                   text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.3rem; }}
    .hook-text {{ font-size: 0.95rem; color: #1e293b; font-style: italic; line-height: 1.5; }}
    .scene-details {{
      border: 1px solid #e0f2fe; border-radius: 0.5rem; margin-bottom: 0.5rem; overflow: hidden;
    }}
    .scene-summary {{
      cursor: pointer; padding: 0.6rem 1rem; font-size: 0.82rem; font-weight: 600;
      color: var(--vc-primary); background: #f0f9ff; list-style: none; user-select: none;
    }}
    .scene-summary::-webkit-details-marker {{ display: none; }}
    .scene-summary::before {{ content: '▶  '; font-size: 0.65rem; }}
    details[open] .scene-summary::before {{ content: '▼  '; }}
    .scene-body {{ padding: 0.75rem 1rem; font-size: 0.85rem; color: #334155; line-height: 1.65; }}
    .cta-box {{
      background: #ecfdf5; border-left: 4px solid #22c55e;
      border-radius: 0.5rem; padding: 0.75rem 1rem; margin: 0.75rem 0;
      font-size: 0.88rem; color: #166534;
    }}
    .caption-box {{
      font-size: 0.85rem; color: #475569; margin-top: 0.75rem; line-height: 1.55;
    }}
    .hashtags-row {{ margin-top: 0.75rem; display: flex; flex-wrap: wrap; gap: 0.4rem; }}
    .hashtag-pill {{
      background: #f0f9ff; color: var(--vc-primary); font-size: 0.75rem; font-weight: 500;
      border: 1px solid #bae6fd; border-radius: 1rem; padding: 0.2rem 0.55rem;
    }}

    /* ── Downloads ── */
    .dl-row {{ display: flex; flex-wrap: wrap; gap: 0.75rem; }}
    .dl-btn {{
      display: inline-flex; align-items: center; gap: 0.4rem;
      padding: 0.55rem 1.1rem; border-radius: 0.6rem; font-size: 0.85rem; font-weight: 500;
      text-decoration: none; border: 1.5px solid var(--vc-primary);
      color: #fff; background: var(--vc-primary); transition: opacity 0.2s;
    }}
    .dl-btn-outline {{ background: #fff; color: var(--vc-primary); }}
    .dl-btn:hover {{ opacity: 0.85; }}

    /* ── Footer ── */
    footer {{
      text-align: center; padding: 1.5rem; font-size: 0.82rem; color: #94a3b8;
      border-top: 1px solid #e0f2fe; background: #fff; margin-top: 2rem;
    }}
    footer strong {{ color: #475569; }}
  </style>
</head>
<body>

<header>
  <div class="logo-icon">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff"
         stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
      <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
      <line x1="12" y1="19" x2="12" y2="23"/>
      <line x1="8"  y1="23" x2="16" y2="23"/>
    </svg>
  </div>
  <div>
    <div class="logo-text">voicecare.ai</div>
  </div>
  <div class="header-right">Content Gap Dashboard &nbsp;|&nbsp; {_esc(analysis_date)}</div>
</header>

<main>

  <!-- Summary Cards -->
  <section>
    <div class="cards">
      <div class="card">
        <div class="card-value">{total_gaps}</div>
        <div class="card-label">Gaps Identified</div>
      </div>
      <div class="card">
        <div class="card-value">{len(ok_scripts)}</div>
        <div class="card-label">Scripts Generated</div>
      </div>
      <div class="card">
        <div class="card-value">{top_score or "—"}</div>
        <div class="card-label">Top Gap Score</div>
      </div>
      <div class="card">
        <div class="card-value">{_esc(analysis_date)}</div>
        <div class="card-label">Analysis Date</div>
      </div>
    </div>
  </section>

  <!-- Executive Summary -->
  {'<section><h2>Executive Summary</h2><div class="summary-box">' + summary + '</div></section>' if summary else ''}

  <!-- Priority Score Chart -->
  {'<section><h2>Gap Priority Scores</h2><div class="chart-wrap"><canvas id="gapChart" height="120"></canvas></div></section>' if gaps else ''}

  <!-- Gap Analysis Table -->
  <section>
    <h2>Content Gap Analysis</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Topic</th>
            <th>Search Vol</th>
            <th>Novelty</th>
            <th>Viral</th>
            <th>Priority ⬆</th>
            <th>Competitors</th>
            <th>Recommended Angle</th>
          </tr>
        </thead>
        <tbody>
          {gap_rows_html or '<tr><td colspan="8" style="text-align:center;padding:2rem;color:#94a3b8">No gaps found.</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  <!-- Script Cards -->
  {'<section><h2>Generated Video Scripts</h2>' + script_cards + '</section>' if ok_scripts else ''}

  <!-- Downloads -->
  <section>
    <h2>Download Reports</h2>
    <div class="dl-row">
      <a href="/download/gaps"    class="dl-btn" download>⬇ Gaps CSV</a>
      <a href="/download/scripts" class="dl-btn dl-btn-outline" download>⬇ Scripts CSV</a>
      <a href="/download/json"    class="dl-btn dl-btn-outline" download>⬇ Full JSON</a>
    </div>
  </section>

</main>

<footer>
  Built by <strong>Shubham Anand</strong> &ensp;|&ensp;
  <span style="color:var(--vc-primary);font-weight:600;">Voicecare.ai</span> &nbsp;© 2025
</footer>

<script>
  {'(function(){var ctx=document.getElementById("gapChart").getContext("2d");new Chart(ctx,{type:"bar",data:{labels:' + chart_labels + ',datasets:[{label:"Priority Score",data:' + chart_scores + ',backgroundColor:"rgba(6,182,212,0.7)",borderColor:"rgba(8,145,178,1)",borderWidth:1.5,borderRadius:6}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,max:10,grid:{color:"#e0f2fe"}},x:{grid:{display:false}}}}});})();' if gaps else ''}
</script>

</body>
</html>"""


def save_html_report(
    gap_analysis: dict[str, Any],
    scripts: list[dict[str, Any]],
    run_timestamp: str,
) -> Path:
    """Save the HTML dashboard to the reports directory and return its path."""
    reports_dir = _ensure_reports_dir()
    filepath = reports_dir / f"dashboard_{run_timestamp}.html"
    filepath.write_text(
        generate_html_report(gap_analysis, scripts, run_timestamp),
        encoding="utf-8",
    )
    logger.info(f"HTML dashboard saved: {filepath}")
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

    logger.info("Generating HTML dashboard...")
    html_report = save_html_report(gap_analysis, scripts, run_timestamp)

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
        "html_report": str(html_report),
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
        f"HTML Dashboard:    {result['html_report']}\n"
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
