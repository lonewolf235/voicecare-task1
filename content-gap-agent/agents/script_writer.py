"""
script_writer.py - Generates Hook + 60-second video scripts via GPT.

Takes the ranked content gap list from gap_analyzer and generates
a complete short-form video script for each top-priority topic.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

GPT_MODEL = os.getenv("OPENAI_GPT_MODEL", "gpt-4o")
DEFAULT_TARGET_AUDIENCE = os.getenv(
    "TARGET_AUDIENCE", "B2B decision-makers and practitioners interested in technology"
)


def _load_prompt(prompt_path: str = "prompts/script_gen.txt") -> str:
    return Path(prompt_path).read_text()


def _build_script_prompt(
    template: str,
    gap: dict[str, Any],
    target_audience: str,
) -> str:
    """Inject gap data into the script generation prompt template."""
    return (
        template
        .replace("{topic}", gap.get("topic", ""))
        .replace("{gap_description}", gap.get("gap_description", ""))
        .replace("{recommended_angle}", gap.get("recommended_angle", ""))
        .replace("{target_audience}", target_audience)
    )


def generate_script_for_gap(
    client: OpenAI,
    gap: dict[str, Any],
    prompt_template: str,
    target_audience: str,
) -> dict[str, Any]:
    """
    Generate a 60-second video script for a single content gap topic.

    Args:
        client: OpenAI client
        gap: A single gap dict from the gap analysis (with keys: topic, gap_description, etc.)
        prompt_template: The raw prompt template string
        target_audience: Who the video is targeting

    Returns:
        Script dict with hook, sections, full_script, hashtags, caption, etc.
    """
    prompt = _build_script_prompt(prompt_template, gap, target_audience)

    logger.info(f"Generating script for topic: {gap.get('topic', 'Unknown')}")
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.7,  # Higher creativity for scripts
        max_tokens=1500,
    )

    raw = response.choices[0].message.content
    script = json.loads(raw)

    # Enrich with metadata from the gap analysis
    script["gap_rank"] = gap.get("rank", 0)
    script["priority_score"] = gap.get("scores", {}).get("priority_score", 0)
    script["covered_by_competitors"] = gap.get("covered_by_competitors", [])
    script["suggested_format"] = gap.get("suggested_format", "video")

    return script


def run_script_writer(
    gap_analysis: dict[str, Any],
    max_scripts: int = 5,
    target_audience: str | None = None,
    prompt_path: str = "prompts/script_gen.txt",
) -> list[dict[str, Any]]:
    """
    Main entry point. Generates video scripts for the top N content gaps.

    Args:
        gap_analysis: Output from gap_analyzer.run_gap_analyzer()
        max_scripts: How many scripts to generate (default: top 5 gaps)
        target_audience: Description of the target viewer (overrides env var)
        prompt_path: Path to the script generation prompt template

    Returns:
        List of script dicts, one per gap topic.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    client = OpenAI(api_key=api_key)
    prompt_template = _load_prompt(prompt_path)
    audience = target_audience or DEFAULT_TARGET_AUDIENCE

    top_gaps = gap_analysis.get("top_gaps", [])
    if not top_gaps:
        logger.warning("No gaps found in analysis — no scripts to generate.")
        return []

    # Only generate scripts for the top N gaps
    gaps_to_script = top_gaps[:max_scripts]
    logger.info(
        f"Generating {len(gaps_to_script)} video scripts "
        f"(top {max_scripts} of {len(top_gaps)} gaps)..."
    )

    scripts: list[dict[str, Any]] = []
    for gap in gaps_to_script:
        try:
            script = generate_script_for_gap(
                client=client,
                gap=gap,
                prompt_template=prompt_template,
                target_audience=audience,
            )
            scripts.append(script)
            logger.info(f"Script generated: {gap.get('topic', 'Unknown')}")
        except Exception as e:
            logger.error(f"Failed to generate script for '{gap.get('topic')}': {e}")
            # Append a stub so downstream agents know this gap exists
            scripts.append(
                {
                    "topic": gap.get("topic", "Unknown"),
                    "gap_rank": gap.get("rank", 0),
                    "error": str(e),
                    "full_script": None,
                }
            )

    logger.info(f"Script generation complete. {len(scripts)} scripts produced.")
    return scripts


def format_script_for_display(script: dict[str, Any]) -> str:
    """Return a human-readable multi-line string of the script."""
    if script.get("error"):
        return f"[ERROR] Could not generate script for: {script.get('topic')}\n{script['error']}"

    lines = [
        f"{'=' * 60}",
        f"TOPIC: {script.get('topic', 'N/A')}",
        f"Priority Rank: #{script.get('gap_rank', '?')} | Score: {script.get('priority_score', '?')}",
        f"Competitors covering this: {', '.join(script.get('covered_by_competitors', []))}",
        f"{'=' * 60}",
        "",
        "--- THUMBNAIL ---",
        script.get("thumbnail_text", ""),
        "",
        "--- CAPTION ---",
        script.get("caption", ""),
        "",
        "--- HASHTAGS ---",
        " ".join(script.get("hashtags", [])),
        "",
        "--- FULL SCRIPT ---",
        script.get("full_script", "[No script generated]"),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Smoke test with mock gap analysis
    mock_analysis = {
        "top_gaps": [
            {
                "rank": 1,
                "topic": "LLM Fine-tuning for Business Use Cases",
                "gap_description": "Competitors have detailed guides on fine-tuning LLMs for specific industries, while our site only covers general AI concepts.",
                "recommended_angle": "Show how a non-ML team fine-tuned a model in a weekend with 3 specific business outcomes",
                "covered_by_competitors": ["Competitor A", "Competitor B"],
                "scores": {"priority_score": 8.7},
                "suggested_format": "video",
            }
        ]
    }

    scripts = run_script_writer(mock_analysis, max_scripts=1)
    for script in scripts:
        print(format_script_for_display(script))
