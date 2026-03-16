"""
scheduler.py - APScheduler-based cron wrapper for the Content Gap Agent.

Triggers main.py every Monday at 8:00 AM local time using APScheduler's
BlockingScheduler with a CronTrigger. The pipeline runs as a subprocess
to ensure a clean environment and isolated log context per run.

Usage:
    # Start the persistent scheduler (blocks until Ctrl+C):
    python scheduler.py

    # Run the pipeline once immediately (then exit):
    python scheduler.py --run-now

    # Custom day/time:
    python scheduler.py --day friday --time 09:30

    # Pass extra flags through to main.py:
    python scheduler.py --dry-run
    python scheduler.py --skip-slack --max-scripts 5
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

# Ensure logs dir exists before setting up file handler
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scheduler.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Day-of-week name → APScheduler cron day_of_week value
_DAY_MAP = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}

MAIN_SCRIPT = Path(__file__).parent / "main.py"

# Default schedule: every Monday at 08:00 local time
DEFAULT_DAY = os.getenv("SCHEDULE_DAY", "monday")
DEFAULT_TIME = os.getenv("SCHEDULE_TIME", "08:00")


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(extra_args: list[str] | None = None) -> bool:
    """
    Execute main.py as a subprocess.

    A subprocess is preferred over a direct function call because it:
    - Gives each run a completely fresh Python state (no memory leaks)
    - Allows stdout/stderr to flow through to the scheduler log
    - Makes it trivial to kill a runaway job without touching the scheduler

    Returns:
        True if main.py exited with code 0.
    """
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'=' * 60}")
    logger.info(f"SCHEDULED PIPELINE RUN — {run_time}")
    logger.info(f"{'=' * 60}")

    cmd = [sys.executable, str(MAIN_SCRIPT)] + (extra_args or [])
    logger.info(f"Executing: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=False,           # Don't raise on non-zero; we handle it below
            text=True,
            cwd=str(MAIN_SCRIPT.parent),
        )
        if result.returncode == 0:
            logger.info("Pipeline finished successfully (exit code 0).")
            return True
        else:
            logger.error(
                f"Pipeline exited with code {result.returncode}. "
                f"See logs/run_log.txt for details."
            )
            return False
    except Exception as e:
        logger.error(f"Failed to launch pipeline subprocess: {e}")
        return False


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute) integers."""
    try:
        hour, minute = time_str.strip().split(":")
        return int(hour), int(minute)
    except ValueError:
        raise ValueError(
            f"Invalid time format '{time_str}'. Expected HH:MM (e.g. '08:00')."
        )


def build_scheduler(
    day: str,
    time_str: str,
    extra_args: list[str] | None = None,
) -> BlockingScheduler:
    """
    Create and configure a BlockingScheduler with a weekly cron trigger.

    Args:
        day:       Day of week (e.g. "monday", "friday")
        time_str:  Time in HH:MM format (local time)
        extra_args: Arguments passed through to main.py on each run

    Returns:
        Configured BlockingScheduler (not yet started).
    """
    day_lower = day.lower()
    if day_lower not in _DAY_MAP:
        raise ValueError(
            f"Invalid day '{day}'. Must be one of: {list(_DAY_MAP.keys())}"
        )
    cron_day = _DAY_MAP[day_lower]
    hour, minute = _parse_time(time_str)

    scheduler = BlockingScheduler(timezone="local")
    trigger = CronTrigger(day_of_week=cron_day, hour=hour, minute=minute)

    scheduler.add_job(
        func=run_pipeline,
        trigger=trigger,
        kwargs={"extra_args": extra_args or []},
        id="content_gap_weekly",
        name=f"Content Gap Agent — every {day_lower.capitalize()} at {time_str}",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow up to 1 hour late start (e.g. machine was asleep)
        coalesce=True,            # If multiple misfires queued, run only once
    )

    return scheduler


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Content Gap Agent Scheduler — APScheduler cron wrapper "
            "that triggers main.py every Monday at 8 AM local time."
        )
    )
    parser.add_argument("--run-now", action="store_true",
                        help="Run the pipeline immediately and exit (ignores schedule)")
    parser.add_argument("--day", default=DEFAULT_DAY,
                        help=f"Day of week to run on (default: {DEFAULT_DAY})")
    parser.add_argument("--time", default=DEFAULT_TIME,
                        help=f"Time in HH:MM local time (default: {DEFAULT_TIME})")
    parser.add_argument("--config", default="config/sites.yaml",
                        help="Passed to main.py --config")
    parser.add_argument("--max-scripts", type=int, default=3,
                        help="Passed to main.py --max-scripts")
    parser.add_argument("--skip-slack", action="store_true",
                        help="Passed to main.py --skip-slack")
    parser.add_argument("--dry-run", action="store_true",
                        help="Passed to main.py --dry-run (mock data, no real API calls)")
    parser.add_argument("--save-crawl", action="store_true",
                        help="Passed to main.py --save-crawl")
    return parser.parse_args()


def _build_main_args(args: argparse.Namespace) -> list[str]:
    main_args = [
        "--config", args.config,
        "--max-scripts", str(args.max_scripts),
    ]
    if args.skip_slack:
        main_args.append("--skip-slack")
    if args.dry_run:
        main_args.append("--dry-run")
    if args.save_crawl:
        main_args.append("--save-crawl")
    return main_args


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    main_args = _build_main_args(args)

    if args.run_now:
        logger.info("--run-now: executing pipeline immediately.")
        success = run_pipeline(extra_args=main_args)
        return 0 if success else 1

    # Build and start the recurring APScheduler
    try:
        scheduler = build_scheduler(
            day=args.day,
            time_str=args.time,
            extra_args=main_args,
        )
    except ValueError as e:
        logger.error(str(e))
        return 1

    job = scheduler.get_jobs()[0]
    next_run = job.next_run_time

    logger.info(
        f"\n{'=' * 60}\n"
        f"  APScheduler started\n"
        f"  Schedule : every {args.day.capitalize()} at {args.time} (local time)\n"
        f"  Next run : {next_run.strftime('%Y-%m-%d %H:%M:%S %Z') if next_run else 'unknown'}\n"
        f"  Press Ctrl+C to stop.\n"
        f"{'=' * 60}\n"
    )

    try:
        scheduler.start()  # Blocks here until KeyboardInterrupt or shutdown()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
