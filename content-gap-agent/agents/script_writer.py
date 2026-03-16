"""
script_writer.py - Generates Hook + 60-second video scripts via GPT-4o.

Public API (used by main.py):
  generate_video_script(top_gap_topic, company_context)
      → {hook, scene_1, scene_2, scene_3, scene_4, scene_5, cta}
      → also saves raw output to /outputs/week_YYYY-MM-DD.json

Internal batch API (used by run_script_writer / reporter pipeline):
  run_script_writer(gap_analysis, max_scripts, ...)
  format_script_for_display(script)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

GPT_MODEL = os.getenv("OPENAI_GPT_MODEL", "gpt-5.4")
OUTPUTS_DIR = os.getenv("OUTPUTS_DIR", "outputs")
DEFAULT_TARGET_AUDIENCE = os.getenv(
    "TARGET_AUDIENCE",
    "B2B decision-makers and practitioners interested in AI-powered voice technology",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)


def _load_prompt(prompt_path: str = "prompts/script_gen.txt") -> str:
    return Path(prompt_path).read_text()


def _ensure_outputs_dir() -> Path:
    path = Path(OUTPUTS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_output(data: dict, week_str: str) -> Path:
    """Persist raw GPT output to outputs/week_YYYY-MM-DD.json."""
    out_dir = _ensure_outputs_dir()
    filepath = out_dir / f"week_{week_str}.json"

    # Merge into existing file if it exists (accumulate multiple scripts per week)
    existing: list[dict] = []
    if filepath.exists():
        try:
            existing = json.loads(filepath.read_text())
            if not isinstance(existing, list):
                existing = [existing]
        except json.JSONDecodeError:
            existing = []

    existing.append(data)
    filepath.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    logger.info(f"Script saved to: {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Public API: generate_video_script
# ---------------------------------------------------------------------------

def generate_video_script(
    top_gap_topic: str,
    company_context: dict[str, Any],
    prompt_path: str = "prompts/script_gen.txt",
) -> dict[str, Any]:
    """
    Generate a structured 60-second video script for a content gap topic.

    Uses GPT-4o at temperature=0.8 for creative output.
    Injects the gap topic and Voicecare.ai company facts into the master prompt.
    Saves the raw output to outputs/week_YYYY-MM-DD.json.

    Args:
        top_gap_topic:   The content gap topic string (e.g. "Voice AI in Customer Support").
        company_context: Dict of company facts injected into the prompt.
                         Expected keys: name, tagline, key_features, target_customer, url
        prompt_path:     Path to the script_gen.txt master prompt.

    Returns:
        Structured script dict:
        {
            "topic": str,
            "hook": {"text": str, "duration_seconds": int, "visual_suggestion": str},
            "scene_1": {"text": str, "duration_seconds": int, "visual_suggestion": str},
            "scene_2": {...},
            "scene_3": {...},
            "scene_4": {...},
            "scene_5": {...},
            "cta": {"text": str, "duration_seconds": int, "visual_suggestion": str},
            "full_script": str,
            "total_duration_seconds": 60,
            "hashtags": list[str],
            "caption": str,
            "thumbnail_text": str,
        }
    """
    client = _get_client()
    template = _load_prompt(prompt_path)

    # Inject variables into prompt template
    prompt = (
        template
        .replace("{topic}", top_gap_topic)
        .replace("{company_context}", json.dumps(company_context, indent=2))
        # Legacy placeholders kept for compatibility with batch mode
        .replace("{gap_description}", "")
        .replace("{recommended_angle}", "")
        .replace("{target_audience}", company_context.get("target_customer", DEFAULT_TARGET_AUDIENCE))
    )

    logger.info(f"generate_video_script: generating script for '{top_gap_topic}'...")

    response = client.responses.create(
        model=GPT_MODEL,
        input=prompt,
    )

    raw_json = response.output_text
    if raw_json.strip().startswith("```json"):
        raw_json = raw_json.strip().strip("`").removeprefix("json").strip()
    elif raw_json.strip().startswith("```"):
        raw_json = raw_json.strip().strip("`").strip()

    script: dict[str, Any] = json.loads(raw_json)

    # Guarantee the 5-scene keys exist (GPT may use different names)
    script = _normalise_scenes(script, top_gap_topic)

    # Attach generation metadata
    script["topic"] = top_gap_topic
    script["generated_at"] = datetime.now().isoformat()
    script["company"] = company_context.get("name", "")

    # Persist to disk
    week_str = datetime.now().strftime("%Y-%m-%d")
    script["output_file"] = str(_save_output(script, week_str))

    return script


def _normalise_scenes(script: dict[str, Any], topic: str) -> dict[str, Any]:
    """
    Ensure the response always contains hook + scene_1…scene_5 + cta keys,
    regardless of how GPT named the sections internally.
    """
    scene_aliases = {
        "problem": "scene_1",
        "setup": "scene_1",
        "context": "scene_1",
        "insight": "scene_2",
        "solution": "scene_2",
        "point_1": "scene_2",
        "detail": "scene_3",
        "point_2": "scene_3",
        "example": "scene_4",
        "proof": "scene_4",
        "point_3": "scene_4",
        "summary": "scene_5",
        "takeaway": "scene_5",
        "recap": "scene_5",
    }

    for alias, canonical in scene_aliases.items():
        if alias in script and canonical not in script:
            script[canonical] = script.pop(alias)

    # Provide stubs for any still-missing scenes
    for scene_key in ("hook", "scene_1", "scene_2", "scene_3", "scene_4", "scene_5", "cta"):
        if scene_key not in script:
            script[scene_key] = {
                "text": f"[{scene_key} for: {topic}]",
                "duration_seconds": 8,
                "visual_suggestion": "Speaker on camera",
            }

    return script


# ---------------------------------------------------------------------------
# Internal batch API (used by run_script_writer → reporter pipeline)
# ---------------------------------------------------------------------------

def _build_batch_prompt(
    template: str,
    gap: dict[str, Any],
    target_audience: str,
) -> str:
    return (
        template
        .replace("{topic}", gap.get("topic", ""))
        .replace("{gap_description}", gap.get("gap_description", ""))
        .replace("{recommended_angle}", gap.get("recommended_angle", ""))
        .replace("{target_audience}", target_audience)
        .replace("{company_context}", "{}")
    )


def generate_script_for_gap(
    client: OpenAI,
    gap: dict[str, Any],
    prompt_template: str,
    target_audience: str,
) -> dict[str, Any]:
    """Generate a script for a single gap dict (batch-mode helper)."""
    prompt = _build_batch_prompt(prompt_template, gap, target_audience)

    logger.info(f"generate_script_for_gap: '{gap.get('topic', 'Unknown')}'")
    response = client.responses.create(
        model=GPT_MODEL,
        input=prompt,
    )

    raw_json = response.output_text
    if raw_json.strip().startswith("```json"):
        raw_json = raw_json.strip().strip("`").removeprefix("json").strip()
    elif raw_json.strip().startswith("```"):
        raw_json = raw_json.strip().strip("`").strip()

    script = json.loads(raw_json)
    script = _normalise_scenes(script, gap.get("topic", ""))
    script["gap_rank"] = gap.get("rank", 0)
    script["priority_score"] = gap.get("scores", {}).get("priority_score", 0)
    script["covered_by_competitors"] = gap.get("covered_by_competitors", [])
    script["suggested_format"] = gap.get("suggested_format", "video")

    # Persist each batch script
    week_str = datetime.now().strftime("%Y-%m-%d")
    _save_output(script, week_str)
    return script


def run_script_writer(
    gap_analysis: dict[str, Any],
    max_scripts: int = 5,
    target_audience: str | None = None,
    prompt_path: str = "prompts/script_gen.txt",
) -> list[dict[str, Any]]:
    """
    Batch entry point. Generates video scripts for the top N content gaps.

    Returns:
        List of script dicts, one per gap topic.
    """
    client = _get_client()
    prompt_template = _load_prompt(prompt_path)
    audience = target_audience or DEFAULT_TARGET_AUDIENCE

    top_gaps = gap_analysis.get("top_gaps", [])
    if not top_gaps:
        logger.warning("run_script_writer: no gaps to script.")
        return []

    gaps_to_script = top_gaps[:max_scripts]
    logger.info(f"run_script_writer: generating {len(gaps_to_script)} scripts...")

    scripts: list[dict[str, Any]] = []
    for gap in gaps_to_script:
        try:
            script = generate_script_for_gap(client, gap, prompt_template, audience)
            scripts.append(script)
            logger.info(f"Script done: {gap.get('topic', 'Unknown')}")
        except Exception as e:
            logger.error(f"Script failed for '{gap.get('topic')}': {e}")
            scripts.append(
                {
                    "topic": gap.get("topic", "Unknown"),
                    "gap_rank": gap.get("rank", 0),
                    "error": str(e),
                    "full_script": None,
                }
            )

    logger.info(f"run_script_writer: {len(scripts)} scripts produced.")
    return scripts


def format_script_for_display(script: dict[str, Any]) -> str:
    """Return a human-readable multi-line string of the script."""
    if script.get("error"):
        return f"[ERROR] {script.get('topic')}: {script['error']}"

    scenes = "\n".join(
        f"  [{k.upper()}] {v.get('text', '') if isinstance(v, dict) else v}"
        for k in ("hook", "scene_1", "scene_2", "scene_3", "scene_4", "scene_5", "cta")
        if k in script
    )

    return (
        f"{'=' * 60}\n"
        f"TOPIC: {script.get('topic', 'N/A')}\n"
        f"Rank: #{script.get('gap_rank', '?')} | Score: {script.get('priority_score', '?')}\n"
        f"{'=' * 60}\n"
        f"THUMBNAIL: {script.get('thumbnail_text', '')}\n"
        f"CAPTION:   {script.get('caption', '')}\n"
        f"HASHTAGS:  {' '.join(script.get('hashtags', []))}\n\n"
        f"SCENES:\n{scenes}\n\n"
        f"FULL SCRIPT:\n{script.get('full_script', '[none]')}\n"
        f"Output saved: {script.get('output_file', 'n/a')}\n"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Smoke test generate_video_script
    mock_context = {
        "name": "Voicecare.ai",
        "tagline": "AI-powered voice agents for healthcare",
        "key_features": ["24/7 voice triage", "EHR integration", "HIPAA compliant"],
        "target_customer": "Healthcare providers, clinic managers, and digital health teams",
        "url": "https://voicecare.ai",
    }

    script = generate_video_script(
        top_gap_topic="Voice AI for Patient Triage Automation",
        company_context=mock_context,
    )
    print(format_script_for_display(script))
