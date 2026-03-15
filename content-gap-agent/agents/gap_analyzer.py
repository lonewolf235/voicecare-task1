"""
gap_analyzer.py - Embedding comparison, content gap detection, and topic ranking.

Public API (used by main.py):
  embed_topics(titles_list)                  → list[list[float]]
  find_gaps(competitor_titles, own_titles)   → list[str]
  rank_gaps(gaps)                            → list[dict]  ← top 3 as structured JSON

Internal batch API (used by run_gap_analyzer):
  get_embeddings(client, texts)
  build_topic_dataframe(pages, label)
  find_content_gaps(...)
  rank_gaps_with_gpt(...)
  run_gap_analyzer(crawl_data)
"""

import json
import logging
import os
import re
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_BATCH_SIZE = 100
GAP_SIMILARITY_THRESHOLD = 0.75
GPT_MODEL = os.getenv("OPENAI_GPT_MODEL", "gpt-5.4")

# Semrush Keywords Overview endpoint
SEMRUSH_API_URL = "https://api.semrush.com/"
SEMRUSH_DATABASE = os.getenv("SEMRUSH_DATABASE", "us")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)


def _load_prompt(prompt_path: str = "prompts/gap_analysis.txt") -> str:
    return Path(prompt_path).read_text()


def _page_to_embedding_text(page: dict) -> str:
    parts = [
        page.get("title", ""),
        page.get("description", ""),
        " ".join(page.get("content", "").split()[:500]),
    ]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Public API: embed_topics
# ---------------------------------------------------------------------------

def embed_topics(titles_list: list[str]) -> list[list[float]]:
    """
    Embed a list of topic title strings using OpenAI text-embedding-3-small.

    Each title is embedded individually (not batched into a single embedding)
    so callers get a 1-to-1 mapping of title → embedding vector.

    Args:
        titles_list: List of topic/title strings to embed.

    Returns:
        List of embedding vectors (each is list[float] of length 1536).
    """
    if not titles_list:
        return []

    client = _get_openai_client()
    all_embeddings: list[list[float]] = []

    for i in range(0, len(titles_list), EMBEDDING_BATCH_SIZE):
        batch = titles_list[i : i + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([item.embedding for item in response.data])
        logger.debug(f"embed_topics: batch {i // EMBEDDING_BATCH_SIZE + 1} done ({len(batch)} items)")

    logger.info(f"embed_topics: embedded {len(titles_list)} titles.")
    return all_embeddings


# ---------------------------------------------------------------------------
# Public API: find_gaps
# ---------------------------------------------------------------------------

def find_gaps(
    competitor_titles: list[str],
    own_titles: list[str],
    threshold: float = GAP_SIMILARITY_THRESHOLD,
) -> list[str]:
    """
    Compute a cosine similarity matrix between competitor and own-site titles.
    Any competitor topic whose maximum similarity to any own topic is below
    `threshold` is flagged as a content gap.

    Args:
        competitor_titles: Titles scraped from competitor sites.
        own_titles: Titles scraped from own site.
        threshold: Similarity cutoff (default 0.75). Lower = stricter gap definition.

    Returns:
        List of competitor title strings that are gaps (not covered by own site),
        sorted by ascending max-similarity (most novel gaps first).
    """
    if not competitor_titles or not own_titles:
        logger.warning("find_gaps: empty title list(s) — returning empty gap list.")
        return []

    logger.info(
        f"find_gaps: embedding {len(competitor_titles)} competitor titles "
        f"and {len(own_titles)} own-site titles..."
    )

    comp_embeddings = np.array(embed_topics(competitor_titles), dtype=np.float32)
    own_embeddings = np.array(embed_topics(own_titles), dtype=np.float32)

    # shape: (n_competitor, n_own)
    sim_matrix = cosine_similarity(comp_embeddings, own_embeddings)
    max_sims = sim_matrix.max(axis=1)  # highest similarity to any own-site topic

    gap_pairs = [
        (title, float(sim))
        for title, sim in zip(competitor_titles, max_sims)
        if sim < threshold
    ]
    # Sort: lowest similarity first (most novel gap at index 0)
    gap_pairs.sort(key=lambda x: x[1])

    gaps = [title for title, _ in gap_pairs]
    logger.info(
        f"find_gaps: {len(gaps)} gaps found out of {len(competitor_titles)} competitor topics "
        f"(threshold={threshold})."
    )
    return gaps


# ---------------------------------------------------------------------------
# Semrush search volume helper
# ---------------------------------------------------------------------------

def _fetch_semrush_volume(keyword: str, api_key: str) -> int:
    """
    Call Semrush Keywords Overview API to get monthly search volume.

    Returns 0 on any error (missing key, API limit, network issue, etc.)
    so the pipeline never hard-fails on Semrush.
    """
    if not api_key:
        logger.debug("SEMRUSH_API_KEY not set — search volume defaults to 0.")
        return 0

    try:
        params = {
            "type": "phrase_this",
            "key": api_key,
            "phrase": keyword,
            "database": SEMRUSH_DATABASE,
            "export_columns": "Ph,Nq",
        }
        response = requests.get(SEMRUSH_API_URL, params=params, timeout=10)
        response.raise_for_status()

        # Semrush returns semicolon-delimited CSV:
        #   Keyword;Search Volume\r\nmy phrase;12100\r\n
        lines = [ln.strip() for ln in response.text.strip().splitlines() if ln.strip()]
        if len(lines) >= 2:
            parts = lines[1].split(";")
            if len(parts) >= 2:
                return int(parts[1].replace(",", "").strip())
    except Exception as e:
        logger.warning(f"Semrush lookup failed for '{keyword}': {e}")

    return 0


# ---------------------------------------------------------------------------
# Gap scoring helpers
# ---------------------------------------------------------------------------

_VIRAL_KEYWORDS = [
    "how to", "guide", "step by step", "tutorial", "tips", "mistakes",
    "secret", "hack", "vs", "comparison", "best", "top", "free",
    "checklist", "template", "cheat sheet", "why", "what is",
]

_NOVELTY_HIGH_SIGNALS = [
    "ai agent", "llm", "fine-tun", "rag", "vector", "embedding",
    "multimodal", "autonomous", "voice ai", "real-time ai", "workflow automation",
]


def _compute_novelty_score(topic: str) -> float:
    """
    Heuristic novelty score (1–10) based on topic text.
    Higher = more cutting-edge / forward-looking content.
    """
    t = topic.lower()
    score = 5.0
    for signal in _NOVELTY_HIGH_SIGNALS:
        if signal in t:
            score += 1.0
    return min(round(score, 1), 10.0)


def _compute_viral_score(topic: str) -> float:
    """
    Heuristic viral potential score (1–10) based on common high-engagement patterns.
    """
    t = topic.lower()
    score = 4.0
    for kw in _VIRAL_KEYWORDS:
        if kw in t:
            score += 0.8
    # Bonus for numbers in title (e.g. "5 ways to…")
    if re.search(r"\b\d+\b", t):
        score += 1.0
    return min(round(score, 1), 10.0)


def _normalize_volume(volumes: list[int]) -> list[float]:
    """Scale raw search volumes to a 1–10 range relative to the batch maximum."""
    if not volumes:
        return []
    max_vol = max(volumes) or 1
    return [round(max(1.0, (v / max_vol) * 10), 2) for v in volumes]


# ---------------------------------------------------------------------------
# Public API: rank_gaps
# ---------------------------------------------------------------------------

def rank_gaps(gaps: list[str]) -> list[dict[str, Any]]:
    """
    Score and rank content gaps, then return the top 3 as structured JSON.

    Scoring formula:
        final_score = (search_vol_score * 0.5) + (novelty_score * 0.3) + (viral_score * 0.2)

    Search volume is fetched from the Semrush API (requires SEMRUSH_API_KEY).
    If the key is absent, search_vol defaults to 0 and the other two signals drive ranking.

    Args:
        gaps: List of competitor topic strings identified by find_gaps().

    Returns:
        Top 3 gap dicts, each containing:
            {topic, search_volume, search_vol_score, novelty_score, viral_score, final_score}
    """
    if not gaps:
        return []

    semrush_key = os.getenv("SEMRUSH_API_KEY", "")
    logger.info(f"rank_gaps: scoring {len(gaps)} gaps (Semrush={'enabled' if semrush_key else 'disabled'})...")

    # Fetch raw search volumes
    raw_volumes = []
    for topic in gaps:
        vol = _fetch_semrush_volume(topic, semrush_key)
        raw_volumes.append(vol)
        logger.debug(f"  '{topic[:60]}' → {vol:,} searches/mo")

    # Normalise volumes to 1–10
    vol_scores = _normalize_volume(raw_volumes)

    scored: list[dict[str, Any]] = []
    for topic, raw_vol, vol_score in zip(gaps, raw_volumes, vol_scores):
        novelty = _compute_novelty_score(topic)
        viral = _compute_viral_score(topic)
        final = round((vol_score * 0.5) + (novelty * 0.3) + (viral * 0.2), 3)

        scored.append(
            {
                "topic": topic,
                "search_volume": raw_vol,
                "search_vol_score": vol_score,
                "novelty_score": novelty,
                "viral_score": viral,
                "final_score": final,
            }
        )

    scored.sort(key=lambda x: x["final_score"], reverse=True)
    top3 = scored[:3]

    logger.info(
        "rank_gaps: top 3 gaps:\n"
        + "\n".join(f"  #{i+1} [{g['final_score']}] {g['topic'][:70]}" for i, g in enumerate(top3))
    )
    return top3


# ---------------------------------------------------------------------------
# Internal batch API (used by run_gap_analyzer)
# ---------------------------------------------------------------------------

def get_embeddings(client: OpenAI, texts: list[str]) -> np.ndarray:
    """Batch-embed a list of texts. Returns shape (len(texts), 1536)."""
    all_embeddings = []
    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([item.embedding for item in response.data])
        logger.debug(f"get_embeddings: batch {i // EMBEDDING_BATCH_SIZE + 1} ({len(batch)} items)")
    return np.array(all_embeddings, dtype=np.float32)


def build_topic_dataframe(pages: list[dict], label: str) -> pd.DataFrame:
    rows = []
    for page in pages:
        text = _page_to_embedding_text(page)
        if len(text.split()) < 10:
            continue
        rows.append(
            {
                "label": label,
                "site_name": page.get("site_name", label),
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "description": page.get("description", ""),
                "text": text,
                "word_count": page.get("word_count", 0),
            }
        )
    return pd.DataFrame(rows)


def find_content_gaps(
    own_df: pd.DataFrame,
    own_embeddings: np.ndarray,
    competitor_df: pd.DataFrame,
    competitor_embeddings: np.ndarray,
    threshold: float = GAP_SIMILARITY_THRESHOLD,
) -> pd.DataFrame:
    if own_embeddings.shape[0] == 0 or competitor_embeddings.shape[0] == 0:
        logger.warning("Empty embeddings — cannot compute gaps.")
        return pd.DataFrame()

    sim_matrix = cosine_similarity(competitor_embeddings, own_embeddings)
    max_similarities = sim_matrix.max(axis=1)
    best_match_idx = sim_matrix.argmax(axis=1)

    gap_df = competitor_df.copy()
    gap_df["max_similarity_to_own"] = max_similarities
    gap_df["closest_own_title"] = [
        own_df.iloc[idx]["title"] if idx < len(own_df) else ""
        for idx in best_match_idx
    ]

    gaps = gap_df[gap_df["max_similarity_to_own"] < threshold].copy()
    gaps = gaps.sort_values("max_similarity_to_own", ascending=True)
    logger.info(
        f"find_content_gaps: {len(gaps)} gaps from {len(competitor_df)} competitor pages "
        f"(threshold={threshold})."
    )
    return gaps


def aggregate_gaps_by_site(gaps_df: pd.DataFrame) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for _, row in gaps_df.iterrows():
        site = row["site_name"]
        grouped.setdefault(site, []).append(
            {
                "title": row["title"],
                "url": row["url"],
                "description": row["description"],
                "similarity_score": round(float(row["max_similarity_to_own"]), 3),
            }
        )
    return grouped


def rank_gaps_with_gpt(
    client: OpenAI,
    own_pages_summary: list[dict],
    competitor_gaps: dict[str, list[dict]],
    gaps_df: pd.DataFrame,
) -> dict[str, Any]:
    prompt_template = _load_prompt()

    own_topics_text = json.dumps(
        [{"title": p["title"], "url": p["url"]} for p in own_pages_summary[:100]], indent=2
    )
    competitor_topics_text = json.dumps(competitor_gaps, indent=2)
    similarity_text = json.dumps(
        gaps_df[["site_name", "title", "max_similarity_to_own"]].head(50).to_dict(orient="records"),
        indent=2,
    )

    prompt = (
        prompt_template
        .replace("{own_topics}", own_topics_text)
        .replace("{competitor_topics}", competitor_topics_text)
        .replace("{similarity_data}", similarity_text)
    )

    logger.info("rank_gaps_with_gpt: sending gap analysis to GPT...")
    response = client.responses.create(
        model=GPT_MODEL,
        input=prompt,
    )

    raw_json = response.output_text
    if raw_json.strip().startswith("```json"):
        raw_json = raw_json.strip().strip("`").removeprefix("json").strip()
    elif raw_json.strip().startswith("```"):
        raw_json = raw_json.strip().strip("`").strip()

    result = json.loads(raw_json)
    result.setdefault("analysis_date", str(date.today()))
    return result


def run_gap_analyzer(crawl_data: dict[str, Any]) -> dict[str, Any]:
    """
    Full-config entry point (uses run_crawler output).

    Returns GPT-ranked gap analysis dict; also exposes `_gaps_df` key
    for downstream use.
    """
    client = _get_openai_client()

    own_pages = crawl_data["own_site"]
    competitor_map = crawl_data["competitors"]

    logger.info(f"run_gap_analyzer: embedding {len(own_pages)} own-site pages...")
    own_df = build_topic_dataframe(own_pages, label="own_site")
    own_texts = own_df["text"].tolist()
    own_embeddings = get_embeddings(client, own_texts) if own_texts else np.array([]).reshape(0, 1)

    all_competitor_pages = []
    for site_name, pages in competitor_map.items():
        for page in pages:
            page["site_name"] = site_name
        all_competitor_pages.extend(pages)

    logger.info(f"run_gap_analyzer: embedding {len(all_competitor_pages)} competitor pages...")
    comp_df = build_topic_dataframe(all_competitor_pages, label="competitor")
    comp_texts = comp_df["text"].tolist()
    comp_embeddings = (
        get_embeddings(client, comp_texts) if comp_texts else np.array([]).reshape(0, 1)
    )

    gaps_df = find_content_gaps(own_df, own_embeddings, comp_df, comp_embeddings)

    if gaps_df.empty:
        logger.warning("run_gap_analyzer: no gaps found.")
        return {"top_gaps": [], "total_gaps_found": 0, "summary": "No gaps detected."}

    competitor_gaps = aggregate_gaps_by_site(gaps_df)
    own_pages_summary = own_df[["title", "url"]].to_dict(orient="records")

    analysis = rank_gaps_with_gpt(client, own_pages_summary, competitor_gaps, gaps_df)
    analysis["_gaps_df"] = gaps_df
    return analysis


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # Smoke test the new public API with mock data
    comp_titles = [
        "LLM Fine-tuning Guide for Business",
        "Building AI Agents for Workflow Automation",
        "RAG Architecture: Production Deployment",
        "Voice AI in Customer Support",
    ]
    own_titles = [
        "Introduction to Artificial Intelligence",
        "Data Science Fundamentals",
    ]

    gaps = find_gaps(comp_titles, own_titles, threshold=0.75)
    print(f"\nGaps found: {gaps}")

    top3 = rank_gaps(gaps)
    print(f"\nTop 3 ranked gaps:")
    print(json.dumps(top3, indent=2))
