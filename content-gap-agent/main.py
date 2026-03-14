"""
main.py - Orchestrator for the Content Gap Agent.

Pipeline (executed in sequence):
  1. Crawler      — scrape_own_site() + scrape_competitor() for each URL in sites.yaml
  2. Gap Analyzer — embed_topics() → find_gaps() → rank_gaps() → top 3 gaps
  3. Script Writer — generate_video_script() for the #1 ranked gap
  4. Reporter     — save CSVs + send Slack notification

All errors are caught per-step, logged to /logs/run_log.txt, and result in a
graceful exit with a clear terminal summary.

Usage:
    python main.py
    python main.py --config config/sites.yaml --dry-run
    python main.py --skip-slack --save-crawl
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ensure /logs dir exists before configuring the file handler
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/run_log.txt", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

from agents.crawler import scrape_competitor, scrape_own_site, load_sites_config, run_crawler
from agents.gap_analyzer import find_gaps, rank_gaps, run_gap_analyzer
from agents.reporter import run_reporter
from agents.script_writer import generate_video_script, run_script_writer


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Content Gap Agent — weekly competitor analysis pipeline"
    )
    parser.add_argument("--config", default="config/sites.yaml",
                        help="Path to sites.yaml (default: config/sites.yaml)")
    parser.add_argument("--max-scripts", type=int, default=3,
                        help="Number of batch video scripts to generate (default: 3)")
    parser.add_argument("--gap-threshold", type=float, default=0.75,
                        help="Cosine similarity gap threshold (default: 0.75)")
    parser.add_argument("--crawl-cache", type=str, default=None,
                        help="Path to a cached crawl JSON — skips live crawling")
    parser.add_argument("--save-crawl", action="store_true",
                        help="Save crawl results to JSON cache")
    parser.add_argument("--skip-slack", action="store_true",
                        help="Disable Slack notification")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use built-in mock data instead of live APIs")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Env validation
# ---------------------------------------------------------------------------

def validate_env() -> list[str]:
    return [v for v in ["OPENAI_API_KEY", "FIRECRAWL_API_KEY"] if not os.getenv(v)]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_crawl_cache(cache_path: str) -> dict:
    logger.info(f"Loading crawl cache: {cache_path}")
    with open(cache_path, "r") as f:
        return json.load(f)


def save_crawl_cache(crawl_data: dict) -> Path:
    Path("reports").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path("reports") / f"crawl_cache_{ts}.json"
    with open(path, "w") as f:
        json.dump(crawl_data, f, indent=2, default=str)
    logger.info(f"Crawl cache saved: {path}")
    return path


# ---------------------------------------------------------------------------
# Mock data (dry-run)
# ---------------------------------------------------------------------------

def _mock_crawl_data() -> dict:
    return {
        "own_site": [
            {
                "site_name": "Voicecare.ai",
                "url": "https://voicecare.ai/blog/intro-to-voice-ai",
                "title": "Introduction to Voice AI in Healthcare",
                "description": "Basics of voice AI for healthcare providers",
                "content": "Voice AI is changing how patients interact with healthcare systems. "
                           "Automated triage and appointment booking save staff hours daily.",
                "date": "2024-11-01",
                "word_count": 600,
            },
        ],
        "competitors": {
            "Competitor A": [
                {
                    "site_name": "Competitor A",
                    "url": "https://competitora.com/blog/llm-patient-triage",
                    "title": "LLM-Powered Patient Triage: Complete Guide",
                    "description": "How large language models are transforming patient triage",
                    "content": "Large language models can assess patient urgency through conversational AI, "
                               "routing calls to the right care pathway automatically.",
                    "date": "2024-12-10",
                    "word_count": 1800,
                },
                {
                    "site_name": "Competitor A",
                    "url": "https://competitora.com/blog/voice-ai-no-shows",
                    "title": "Reducing No-Shows with Voice AI Reminders",
                    "description": "Automated voice reminders cut appointment no-shows by 40%",
                    "content": "Automated voice reminder systems contact patients 48 hours and 2 hours "
                               "before appointments. Studies show 38–42% reduction in no-shows.",
                    "date": "2025-01-05",
                    "word_count": 1200,
                },
            ],
            "Competitor B": [
                {
                    "site_name": "Competitor B",
                    "url": "https://competitorb.com/articles/hipaa-voice-ai",
                    "title": "HIPAA-Compliant Voice AI: What Clinics Need to Know",
                    "description": "Compliance guide for deploying voice AI in clinical settings",
                    "content": "Deploying voice AI in healthcare requires HIPAA Business Associate Agreements, "
                               "end-to-end encryption, and audit logging. This guide covers all requirements.",
                    "date": "2025-01-20",
                    "word_count": 2200,
                },
            ],
        },
    }


def _mock_gap_analysis() -> dict:
    return {
        "top_gaps": [
            {
                "rank": 1,
                "topic": "HIPAA-Compliant Voice AI for Clinics",
                "gap_description": "No compliance guide on our site. Competitors cover this in depth.",
                "covered_by_competitors": ["Competitor B"],
                "our_coverage": "none",
                "recommended_angle": "Step-by-step HIPAA checklist for deploying voice AI",
                "scores": {
                    "search_demand": 8, "competitive_pressure": 7,
                    "strategic_fit": 9, "priority_score": 8.1,
                },
                "suggested_format": "video",
            },
            {
                "rank": 2,
                "topic": "Reducing No-Shows with AI Reminders",
                "gap_description": "Competitors show 40% no-show reduction stats. We have no content on this.",
                "covered_by_competitors": ["Competitor A"],
                "our_coverage": "none",
                "recommended_angle": "The 3 types of reminder touchpoints that actually work",
                "scores": {
                    "search_demand": 7, "competitive_pressure": 8,
                    "strategic_fit": 8, "priority_score": 7.7,
                },
                "suggested_format": "video",
            },
        ],
        "total_gaps_found": 2,
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "summary": "Competitors lead on compliance and outcomes content. Two high-priority gaps found.",
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main() -> int:
    run_start = datetime.now()
    args = parse_args()
    step_results: dict[str, Any] = {}

    logger.info("=" * 65)
    logger.info(f"CONTENT GAP AGENT — RUN STARTED at {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 65)

    # ── Env check ─────────────────────────────────────────────────────────
    if not args.dry_run:
        missing = validate_env()
        if missing:
            logger.error(f"Missing env vars: {', '.join(missing)}. Copy .env.example → .env")
            _print_summary(step_results, run_start, success=False)
            return 1

    # Load config
    try:
        config = load_sites_config(args.config)
    except Exception as e:
        logger.error(f"Cannot load config '{args.config}': {e}")
        return 1

    company_context: dict = config.get("company_context", {})
    if not company_context:
        logger.warning("No company_context in sites.yaml — script generation will use defaults.")

    # ── STEP 1: Crawl ────────────────────────────────────────────────────
    logger.info("STEP 1/4 — Crawling sites...")
    crawl_data: dict = {}

    if args.dry_run:
        logger.info("[DRY RUN] Using mock crawl data.")
        crawl_data = _mock_crawl_data()
        step_results["crawl"] = "mock"
    elif args.crawl_cache:
        try:
            crawl_data = load_crawl_cache(args.crawl_cache)
            step_results["crawl"] = f"cache:{args.crawl_cache}"
        except Exception as e:
            logger.error(f"Crawl cache load failed: {e}")
            _print_summary(step_results, run_start, success=False)
            return 1
    else:
        try:
            own_site_cfg = config["own_site"]
            competitor_cfgs = config.get("competitors", [])

            # Use the new per-URL public API
            own_pages: list[dict] = []
            for url in own_site_cfg["urls"]:
                own_pages.extend(scrape_own_site(url))

            competitor_pages: dict[str, list[dict]] = {}
            for comp in competitor_cfgs:
                pages: list[dict] = []
                for url in comp["urls"]:
                    pages.extend(scrape_competitor(url))
                competitor_pages[comp["name"]] = pages

            crawl_data = {"own_site": own_pages, "competitors": competitor_pages}

            if args.save_crawl:
                save_crawl_cache(crawl_data)

            step_results["crawl"] = (
                f"own={len(own_pages)} pages | "
                + " | ".join(f"{k}={len(v)}" for k, v in competitor_pages.items())
            )
        except Exception as e:
            logger.error(f"Crawler failed: {e}", exc_info=True)
            _print_summary(step_results, run_start, success=False)
            return 1

    logger.info(f"Crawl complete — {step_results.get('crawl', '')}")

    # ── STEP 2: Gap Analysis ─────────────────────────────────────────────
    logger.info("STEP 2/4 — Analyzing content gaps...")

    if args.dry_run:
        logger.info("[DRY RUN] Using mock gap analysis.")
        gap_analysis = _mock_gap_analysis()
        top3 = gap_analysis["top_gaps"][:3]
        step_results["gap_analysis"] = f"{len(top3)} gaps (mock)"
    else:
        try:
            # Use the new lean public API (title-based, Semrush-scored)
            own_titles = [p["title"] for p in crawl_data["own_site"] if p.get("title")]
            comp_titles = [
                p["title"]
                for pages in crawl_data["competitors"].values()
                for p in pages
                if p.get("title")
            ]

            gaps = find_gaps(comp_titles, own_titles, threshold=args.gap_threshold)
            top3_raw = rank_gaps(gaps)  # returns top 3 dicts

            # Shape to match the batch gap_analysis format expected by reporter + script_writer
            gap_analysis = {
                "total_gaps_found": len(gaps),
                "analysis_date": datetime.now().strftime("%Y-%m-%d"),
                "summary": (
                    f"Found {len(gaps)} content gaps. "
                    f"Top topic: '{top3_raw[0]['topic'] if top3_raw else 'none'}'"
                ),
                "top_gaps": [
                    {
                        "rank": i + 1,
                        "topic": g["topic"],
                        "gap_description": (
                            f"Competitor covers this; our site similarity score "
                            f"{g.get('search_vol_score', 0):.2f}"
                        ),
                        "covered_by_competitors": [],
                        "our_coverage": "none",
                        "recommended_angle": "",
                        "scores": {
                            "search_demand": g.get("search_vol_score", 0),
                            "novelty": g.get("novelty_score", 0),
                            "viral": g.get("viral_score", 0),
                            "priority_score": g.get("final_score", 0),
                        },
                        "suggested_format": "video",
                        # Carry Semrush data through for the reporter
                        "_semrush": {
                            "search_volume": g.get("search_volume", 0),
                            "novelty_score": g.get("novelty_score", 0),
                            "viral_score": g.get("viral_score", 0),
                            "final_score": g.get("final_score", 0),
                        },
                    }
                    for i, g in enumerate(top3_raw)
                ],
            }
            top3 = gap_analysis["top_gaps"]
            step_results["gap_analysis"] = f"{len(gaps)} gaps found, top 3 ranked"
        except Exception as e:
            logger.error(f"Gap analyzer failed: {e}", exc_info=True)
            _print_summary(step_results, run_start, success=False)
            return 1

    logger.info(f"Gap analysis complete — {step_results.get('gap_analysis', '')}")

    # ── STEP 3: Script Generation ─────────────────────────────────────────
    logger.info("STEP 3/4 — Generating video scripts...")

    scripts: list[dict] = []
    if args.dry_run:
        logger.info("[DRY RUN] Skipping GPT script generation.")
        scripts = []
        step_results["scripts"] = "skipped (dry-run)"
    else:
        try:
            # Generate the #1 gap script using the new public API
            if top3:
                top_topic = top3[0]["topic"]
                logger.info(f"Generating script for top gap: '{top_topic}'")
                primary_script = generate_video_script(
                    top_gap_topic=top_topic,
                    company_context=company_context,
                )
                scripts.append(primary_script)

            # Also generate batch scripts for gaps 2 and 3 via run_script_writer
            remaining_gaps = {"top_gaps": top3[1:args.max_scripts]}
            if remaining_gaps["top_gaps"]:
                batch = run_script_writer(remaining_gaps, max_scripts=args.max_scripts - 1)
                scripts.extend(batch)

            ok = sum(1 for s in scripts if not s.get("error"))
            step_results["scripts"] = f"{ok}/{len(scripts)} scripts generated"
        except Exception as e:
            logger.error(f"Script writer failed: {e}", exc_info=True)
            step_results["scripts"] = f"FAILED: {e}"
            # Non-fatal — continue to reporter with whatever we have

    logger.info(f"Script generation — {step_results.get('scripts', 'n/a')}")

    # ── STEP 4: Report ────────────────────────────────────────────────────
    logger.info("STEP 4/4 — Generating reports and notifications...")

    original_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if args.skip_slack or args.dry_run:
        os.environ["SLACK_WEBHOOK_URL"] = ""

    try:
        report = run_reporter(gap_analysis=gap_analysis, scripts=scripts)
        step_results["report"] = (
            f"gaps_csv={report['gaps_csv']} | scripts_csv={report['scripts_csv']} | "
            f"slack={'sent' if report['slack_sent'] else 'skipped'}"
        )
    except Exception as e:
        logger.error(f"Reporter failed: {e}", exc_info=True)
        step_results["report"] = f"FAILED: {e}"
    finally:
        os.environ["SLACK_WEBHOOK_URL"] = original_webhook

    _print_summary(step_results, run_start, success=True)
    return 0


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def _print_summary(step_results: dict, run_start: datetime, success: bool) -> None:
    elapsed = (datetime.now() - run_start).total_seconds()
    status = "COMPLETE" if success else "FAILED"
    border = "=" * 65

    lines = [
        "",
        border,
        f"  CONTENT GAP AGENT — {status}",
        f"  Run started : {run_start.strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Elapsed     : {elapsed:.1f}s",
        border,
    ]
    for step, result in step_results.items():
        lines.append(f"  {step:<18} {result}")
    lines += [border, ""]

    summary = "\n".join(lines)
    print(summary)
    logger.info(summary)


# ---------------------------------------------------------------------------
# Type hint import (Python 3.9 compat)
# ---------------------------------------------------------------------------
from typing import Any  # noqa: E402  (imported at bottom to keep code readable above)

if __name__ == "__main__":
    sys.exit(main())
