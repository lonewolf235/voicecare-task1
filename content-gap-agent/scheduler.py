"""
scheduler.py - Cron job wrapper for the Content Gap Agent.

Runs the full pipeline every Monday at 8:00 AM (local time).
Can also be triggered manually for ad-hoc runs.

Usage:
    # Start the scheduler (runs until interrupted):
    python scheduler.py

    # Run once immediately (ignores schedule):
    python scheduler.py --run-now

    # Custom schedule (cron expression):
    python scheduler.py --cron "0 8 * * MON"

    # Systemd / Docker: just run and keep alive
    python scheduler.py
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import schedule
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scheduler.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

# Default schedule: every Monday at 08:00 local time
DEFAULT_DAY = "monday"
DEFAULT_TIME = "08:00"

# Path to main.py relative to this file
MAIN_SCRIPT = Path(__file__).parent / "main.py"


def run_pipeline(extra_args: list[str] | None = None) -> bool:
    """
    Execute main.py as a subprocess.

    Running as a subprocess ensures a fresh Python environment for each run,
    prevents memory leaks, and makes it easy to capture logs per run.

    Returns:
        True if the pipeline completed successfully (exit code 0).
    """
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'=' * 55}")
    logger.info(f"SCHEDULED RUN STARTING — {run_time}")
    logger.info(f"{'=' * 55}")

    cmd = [sys.executable, str(MAIN_SCRIPT)] + (extra_args or [])
    logger.info(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            cwd=str(MAIN_SCRIPT.parent),
        )
        if result.returncode == 0:
            logger.info(f"Pipeline completed successfully (exit code 0).")
            return True
        else:
            logger.error(
                f"Pipeline exited with code {result.returncode}. "
                f"Check content_gap_agent.log for details."
            )
            return False
    except Exception as e:
        logger.error(f"Failed to launch pipeline: {e}")
        return False


def setup_schedule(day: str, run_time: str, extra_args: list[str] | None = None) -> None:
    """
    Register the pipeline with the schedule library.

    Args:
        day: Day of week (e.g. "monday", "friday")
        run_time: Time in HH:MM format (e.g. "08:00")
        extra_args: Extra CLI arguments to pass to main.py
    """
    valid_days = [
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    ]
    day_lower = day.lower()
    if day_lower not in valid_days:
        raise ValueError(f"Invalid day '{day}'. Must be one of: {valid_days}")

    scheduled_job = getattr(schedule.every(), day_lower).at(run_time)
    scheduled_job.do(run_pipeline, extra_args=extra_args)

    logger.info(
        f"Scheduler configured: every {day_lower.capitalize()} at {run_time}\n"
        f"Next run: {schedule.next_run()}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Content Gap Agent Scheduler — runs the pipeline on a weekly cron schedule"
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the pipeline immediately and exit (ignores schedule)",
    )
    parser.add_argument(
        "--day",
        default=os.getenv("SCHEDULE_DAY", DEFAULT_DAY),
        help=f"Day of week to run (default: {DEFAULT_DAY})",
    )
    parser.add_argument(
        "--time",
        default=os.getenv("SCHEDULE_TIME", DEFAULT_TIME),
        help=f"Time to run in HH:MM format (default: {DEFAULT_TIME})",
    )
    parser.add_argument(
        "--config",
        default="config/sites.yaml",
        help="Path to sites.yaml passed to main.py",
    )
    parser.add_argument(
        "--max-scripts",
        type=int,
        default=5,
        help="Number of scripts to generate (passed to main.py)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to main.py (uses mock data)",
    )
    return parser.parse_args()


def build_main_args(args: argparse.Namespace) -> list[str]:
    """Build the argument list to pass to main.py."""
    main_args = [
        "--config", args.config,
        "--max-scripts", str(args.max_scripts),
    ]
    if args.dry_run:
        main_args.append("--dry-run")
    return main_args


def main() -> int:
    args = parse_args()
    main_args = build_main_args(args)

    if args.run_now:
        logger.info("--run-now specified. Running pipeline immediately.")
        success = run_pipeline(extra_args=main_args)
        return 0 if success else 1

    # Setup recurring schedule
    try:
        setup_schedule(day=args.day, run_time=args.time, extra_args=main_args)
    except ValueError as e:
        logger.error(str(e))
        return 1

    logger.info(
        f"Scheduler running. Press Ctrl+C to stop.\n"
        f"Next run: {schedule.next_run()}"
    )

    # Main loop
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
