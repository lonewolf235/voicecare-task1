"""
run.py — CLI entry point for the Content Gap Agent.

Usage:
    python run.py                          # Live mode (needs API keys in .env)
    python run.py --dry-run                # Demo mode (mock data, no API calls)
    python run.py --dry-run --skip-slack   # Demo mode, no Slack
    python run.py --max-scripts 3          # Generate up to 3 scripts

All arguments are forwarded to content-gap-agent/main.py.
"""

import os
import subprocess
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).parent / "content-gap-agent"


def main() -> int:
    if not AGENT_DIR.exists():
        print(f"ERROR: Agent directory not found: {AGENT_DIR}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    return subprocess.run(
        [sys.executable, str(AGENT_DIR / "main.py")] + sys.argv[1:],
        cwd=str(AGENT_DIR),
        env=env,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
