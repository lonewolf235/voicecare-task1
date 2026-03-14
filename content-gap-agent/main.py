"""
main.py - Orchestrator for the Content Gap Agent.

Runs all agents in sequence:
  1. Crawler     — scrapes own site + competitors via Firecrawl
  2. Gap Analyzer — embeds content, finds gaps, ranks via GPT
  3. Script Writer — generates 60s video scripts for top gaps
  4. Reporter     — saves CSVs and sends Slack notification

Usage:
    python main.py
    python main.py --config config/sites.yaml --max-scripts 3
    python main.py --dry-run   # Skip Slack + use cached crawl data
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing agents (so env vars are available)
load_dotenv()

from agents.crawler import run_crawler
from agents.gap_analyzer import run_gap_analyzer
from agents.reporter import run_reporter
from agents.script_writer import run_script_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("content_gap_agent.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Content Gap Agent — weekly competitor analysis pipeline"
    )
    parser.add_argument(
        "--config",
        default="config/sites.yaml",
        help="Path to sites.yaml configuration file (default: config/sites.yaml)",
    )
    parser.add_argument(
        "--max-scripts",
        type=int,
        default=5,
        help="Number of video scripts to generate (default: 5)",
    )
    parser.add_argument(
        "--gap-threshold",
        type=float,
        default=0.75,
        help="Cosine similarity threshold below which a topic is a gap (default: 0.75)",
    )
    parser.add_argument(
        "--target-audience",
        type=str,
        default=None,
        help="Override the target audience description for script generation",
    )
    parser.add_argument(
        "--crawl-cache",
        type=str,
        default=None,
        help="Path to a cached crawl JSON file (skip re-crawling)",
    )
    parser.add_argument(
        "--save-crawl",
        action="store_true",
        help="Save crawl results to JSON cache for reuse",
    )
    parser.add_argument(
        "--skip-slack",
        action="store_true",
        help="Skip sending the Slack notification",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use mock data instead of live APIs (for testing)",
    )
    return parser.parse_args()


def validate_env() -> list[str]:
    """Check required environment variables and return a list of missing ones."""
    required = ["OPENAI_API_KEY", "FIRECRAWL_API_KEY"]
    missing = [var for var in required if not os.getenv(var)]
    return missing


def load_crawl_cache(cache_path: str) -> dict:
    """Load a previously saved crawl result from disk."""
    logger.info(f"Loading crawl cache from: {cache_path}")
    with open(cache_path, "r") as f:
        return json.load(f)


def save_crawl_cache(crawl_data: dict, config_path: str) -> Path:
    """Save crawl results to a JSON file for reuse."""
    from datetime import datetime
    cache_dir = Path("reports")
    cache_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cache_path = cache_dir / f"crawl_cache_{ts}.json"
    with open(cache_path, "w") as f:
        json.dump(crawl_data, f, indent=2, default=str)
    logger.info(f"Crawl cache saved to: {cache_path}")
    return cache_path


def get_mock_crawl_data() -> dict:
    """Return minimal mock crawl data for dry-run testing."""
    return {
        "own_site": [
            {
                "site_name": "My Company",
                "url": "https://mycompany.com/blog/intro-to-ai",
                "title": "Introduction to Artificial Intelligence",
                "description": "A beginner's guide to AI concepts",
                "content": "Artificial intelligence is transforming how businesses operate. "
                           "From automation to predictive analytics, AI offers enormous potential.",
                "word_count": 800,
            },
            {
                "site_name": "My Company",
                "url": "https://mycompany.com/blog/data-science-basics",
                "title": "Data Science Fundamentals",
                "description": "Core concepts every data practitioner should know",
                "content": "Data science combines statistics, programming, and domain knowledge. "
                           "Python and SQL are the foundational tools for most data scientists.",
                "word_count": 600,
            },
        ],
        "competitors": {
            "Competitor A": [
                {
                    "site_name": "Competitor A",
                    "url": "https://competitora.com/blog/llm-fine-tuning",
                    "title": "Complete Guide to LLM Fine-tuning for Business",
                    "description": "How to fine-tune large language models for your specific use case",
                    "content": "Fine-tuning large language models allows businesses to create specialized AI "
                               "that understands your domain, terminology, and use cases. This guide covers "
                               "dataset preparation, training strategies, and evaluation metrics.",
                    "word_count": 2500,
                },
                {
                    "site_name": "Competitor A",
                    "url": "https://competitora.com/blog/ai-agents-workflow",
                    "title": "Building AI Agents for Workflow Automation",
                    "description": "Practical guide to autonomous AI agents",
                    "content": "AI agents can autonomously complete complex multi-step tasks. "
                               "Learn how to build agents that can browse the web, write code, "
                               "and integrate with your existing business tools.",
                    "word_count": 1800,
                },
            ],
            "Competitor B": [
                {
                    "site_name": "Competitor B",
                    "url": "https://competitorb.com/articles/rag-architecture",
                    "title": "RAG Architecture: Production Deployment Guide",
                    "description": "Retrieval-Augmented Generation in production",
                    "content": "Retrieval-Augmented Generation (RAG) combines the power of LLMs with "
                               "your private knowledge base. This guide covers vector databases, "
                               "chunking strategies, and evaluation frameworks for production RAG systems.",
                    "word_count": 3000,
                },
            ],
        },
    }


def main() -> int:
    """
    Main pipeline orchestrator.

    Returns:
        Exit code (0 = success, 1 = error)
    """
    args = parse_args()

    logger.info("=" * 60)
    logger.info("CONTENT GAP AGENT — STARTING")
    logger.info("=" * 60)

    # Environment validation (skip for dry-run)
    if not args.dry_run:
        missing_vars = validate_env()
        if missing_vars:
            logger.error(
                f"Missing required environment variables: {', '.join(missing_vars)}\n"
                f"Copy .env.example to .env and fill in your API keys."
            )
            return 1

    # -----------------------------------------------------------------------
    # Step 1: Crawl
    # -----------------------------------------------------------------------
    logger.info("STEP 1/4 — Crawling sites...")

    if args.dry_run:
        logger.info("[DRY RUN] Using mock crawl data.")
        crawl_data = get_mock_crawl_data()
    elif args.crawl_cache:
        crawl_data = load_crawl_cache(args.crawl_cache)
    else:
        try:
            crawl_data = run_crawler(config_path=args.config)
        except Exception as e:
            logger.error(f"Crawler failed: {e}")
            return 1

        if args.save_crawl:
            save_crawl_cache(crawl_data, args.config)

    own_count = len(crawl_data.get("own_site", []))
    comp_counts = {k: len(v) for k, v in crawl_data.get("competitors", {}).items()}
    logger.info(f"Crawl complete — Own: {own_count} pages | Competitors: {comp_counts}")

    # -----------------------------------------------------------------------
    # Step 2: Gap Analysis
    # -----------------------------------------------------------------------
    logger.info("STEP 2/4 — Analyzing content gaps...")

    try:
        gap_analysis = run_gap_analyzer(crawl_data)
    except Exception as e:
        logger.error(f"Gap analyzer failed: {e}")
        return 1

    total_gaps = gap_analysis.get("total_gaps_found", 0)
    logger.info(f"Gap analysis complete — {total_gaps} gaps found.")

    if args.dry_run:
        logger.info("[DRY RUN] Gap analysis summary:")
        logger.info(json.dumps(
            {k: v for k, v in gap_analysis.items() if k != "_gaps_df"},
            indent=2, default=str
        ))

    # -----------------------------------------------------------------------
    # Step 3: Script Generation
    # -----------------------------------------------------------------------
    logger.info(f"STEP 3/4 — Generating {args.max_scripts} video scripts...")

    try:
        scripts = run_script_writer(
            gap_analysis=gap_analysis,
            max_scripts=args.max_scripts,
            target_audience=args.target_audience,
        )
    except Exception as e:
        logger.error(f"Script writer failed: {e}")
        return 1

    successful_scripts = len([s for s in scripts if not s.get("error")])
    logger.info(f"Script generation complete — {successful_scripts}/{len(scripts)} scripts generated.")

    # -----------------------------------------------------------------------
    # Step 4: Report
    # -----------------------------------------------------------------------
    logger.info("STEP 4/4 — Generating reports and sending notifications...")

    # Override Slack if skip_slack flag or dry_run
    original_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if args.skip_slack or args.dry_run:
        os.environ["SLACK_WEBHOOK_URL"] = ""
        logger.info("[DRY RUN / SKIP-SLACK] Slack notification disabled.")

    try:
        report = run_reporter(gap_analysis=gap_analysis, scripts=scripts)
    except Exception as e:
        logger.error(f"Reporter failed: {e}")
        return 1
    finally:
        # Restore original webhook env var
        if args.skip_slack or args.dry_run:
            os.environ["SLACK_WEBHOOK_URL"] = original_webhook

    logger.info("=" * 60)
    logger.info("CONTENT GAP AGENT — COMPLETE")
    logger.info(f"  Gaps found:        {report['gaps_found']}")
    logger.info(f"  Scripts generated: {report['scripts_generated']}")
    logger.info(f"  Gaps CSV:          {report['gaps_csv']}")
    logger.info(f"  Scripts CSV:       {report['scripts_csv']}")
    logger.info(f"  JSON Report:       {report['json_report']}")
    logger.info(f"  Slack sent:        {report['slack_sent']}")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
