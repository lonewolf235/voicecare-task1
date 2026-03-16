"""
api/index.py — FastAPI backend for the Content Gap Agent.

Provides:
  GET /           → serves the main UI (templates/index.html)
  GET /run        → SSE stream: spawns main.py, pipes stdout line-by-line
  GET /report     → serves the latest generated HTML dashboard
  GET /download/{kind}  → streams latest CSV/JSON report file

Deploy on Railway or Render (no serverless timeout limit).
The frontend (templates/index.html) on Vercel points BACKEND_URL here.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

# api/index.py lives one level below the project root (content-gap-agent/)
PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# On Vercel/Railway the filesystem is ephemeral; /tmp is always writable.
TMP_REPORTS = Path("/tmp/reports")
TMP_OUTPUTS = Path("/tmp/outputs")


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

app = FastAPI(title="Content Gap Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # In production, restrict to your Vercel domain, e.g.:
    # allow_origins=["https://your-app.vercel.app"]
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _latest_file(pattern: str) -> Path | None:
    """Return the most recently created file matching the glob pattern in /tmp/reports."""
    if not TMP_REPORTS.exists():
        return None
    files = sorted(TMP_REPORTS.glob(pattern))
    return files[-1] if files else None


def _detect_level(line: str) -> str:
    if "[ERROR]" in line:
        return "ERROR"
    if "[WARNING]" in line:
        return "WARNING"
    return "INFO"


def _detect_step(line: str) -> int | None:
    for i in range(1, 5):
        if f"STEP {i}/4" in line:
            return i
    return None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main UI page."""
    html_path = TEMPLATES_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="index.html not found")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/run")
async def run_pipeline():
    """
    SSE endpoint: spawns main.py as a subprocess, streams its stdout
    line-by-line as Server-Sent Events in JSON format.

    Event shapes:
      {"type": "log",   "message": str, "level": "INFO|WARNING|ERROR", "step": int|null}
      {"type": "done",  "success": bool}
    """
    async def event_stream():
        # Ensure writable directories exist
        TMP_REPORTS.mkdir(parents=True, exist_ok=True)
        TMP_OUTPUTS.mkdir(parents=True, exist_ok=True)

        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",          # force unbuffered stdout
            "REPORTS_DIR": str(TMP_REPORTS),
            "OUTPUTS_DIR": str(TMP_OUTPUTS),
        }

        proc = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "main.py"), "--skip-slack"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr → stdout so all output streams
            text=True,
            bufsize=1,                  # line-buffered
            cwd=str(PROJECT_ROOT),      # main.py uses relative paths (config/, prompts/)
            env=env,
        )

        loop = asyncio.get_running_loop()
        exit_code = 1
        try:
            while True:
                # readline() is blocking — run it in a thread executor
                line: str = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    break
                payload = {
                    "type": "log",
                    "message": line.rstrip(),
                    "level": _detect_level(line),
                    "step": _detect_step(line),
                }
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            proc.wait()
            exit_code = proc.returncode

        yield f"data: {json.dumps({'type': 'done', 'success': exit_code == 0})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering (Railway/Render)
            "Connection": "keep-alive",
        },
    )


@app.get("/report", response_class=HTMLResponse)
async def report():
    """Serve the latest generated HTML dashboard."""
    f = _latest_file("dashboard_*.html")
    if not f:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:2rem'>"
            "<h2>No report yet</h2><p>Run the pipeline first using the "
            "<a href='/'>main page</a>.</p></body></html>",
            status_code=404,
        )
    return HTMLResponse(f.read_text(encoding="utf-8"))


@app.get("/download/{kind}")
async def download(kind: str):
    """
    Serve the latest report file for download.

    kind: gaps | scripts | json
    """
    patterns: dict[str, str] = {
        "gaps": "gap_report_*.csv",
        "scripts": "scripts_*.csv",
        "json": "full_report_*.json",
    }
    if kind not in patterns:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'. Use: gaps, scripts, json")

    f = _latest_file(patterns[kind])
    if not f:
        raise HTTPException(status_code=404, detail="No report found. Run the pipeline first.")

    return FileResponse(
        path=str(f),
        filename=f.name,
        media_type="application/octet-stream",
    )
