"""
gap_analyzer.py - Embedding comparison and topic gap ranking.

Uses OpenAI text-embedding-3-small to vectorize page content,
then applies cosine similarity to find topics competitors cover
that our site doesn't — ranked by a priority score via GPT.
"""

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_BATCH_SIZE = 100  # Max texts per API call
GAP_SIMILARITY_THRESHOLD = 0.75  # Below this = our site doesn't cover the topic well
GPT_MODEL = os.getenv("OPENAI_GPT_MODEL", "gpt-4o")


def _load_prompt(prompt_path: str = "prompts/gap_analysis.txt") -> str:
    return Path(prompt_path).read_text()


def _page_to_embedding_text(page: dict) -> str:
    """Combine title + description + first 500 words of content for embedding."""
    parts = [
        page.get("title", ""),
        page.get("description", ""),
        " ".join(page.get("content", "").split()[:500]),
    ]
    return " ".join(p for p in parts if p).strip()


def get_embeddings(client: OpenAI, texts: list[str]) -> np.ndarray:
    """
    Batch-embed a list of texts using the OpenAI Embeddings API.

    Returns:
        numpy array of shape (len(texts), embedding_dim)
    """
    all_embeddings = []
    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)
        logger.debug(f"Embedded batch {i // EMBEDDING_BATCH_SIZE + 1}: {len(batch)} texts")

    return np.array(all_embeddings, dtype=np.float32)


def build_topic_dataframe(pages: list[dict], label: str) -> pd.DataFrame:
    """Convert raw page records into a DataFrame with text for embedding."""
    rows = []
    for page in pages:
        text = _page_to_embedding_text(page)
        if len(text.split()) < 10:  # Skip near-empty pages
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
    """
    For each competitor page, find the maximum similarity to any of our pages.
    Pages with max_similarity < threshold are considered content gaps.

    Returns:
        DataFrame of competitor pages that represent gaps, sorted by max_similarity asc.
    """
    if own_embeddings.shape[0] == 0 or competitor_embeddings.shape[0] == 0:
        logger.warning("Empty embeddings — cannot compute gaps.")
        return pd.DataFrame()

    # similarity matrix: shape (n_competitor_pages, n_own_pages)
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
        f"Found {len(gaps)} potential gap pages out of {len(competitor_df)} competitor pages "
        f"(threshold={threshold})"
    )
    return gaps


def aggregate_gaps_by_site(gaps_df: pd.DataFrame) -> dict[str, list[dict]]:
    """Group gap pages by competitor site for prompt construction."""
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
    """
    Send gap data to GPT for strategic analysis and priority ranking.

    Returns:
        Parsed JSON dict from GPT gap analysis.
    """
    prompt_template = _load_prompt()

    own_topics_text = json.dumps(
        [{"title": p["title"], "url": p["url"]} for p in own_pages_summary[:100]],
        indent=2,
    )
    competitor_topics_text = json.dumps(competitor_gaps, indent=2)

    similarity_summary = (
        gaps_df[["site_name", "title", "max_similarity_to_own"]]
        .head(50)
        .to_dict(orient="records")
    )
    similarity_text = json.dumps(similarity_summary, indent=2)

    prompt = (
        prompt_template
        .replace("{own_topics}", own_topics_text)
        .replace("{competitor_topics}", competitor_topics_text)
        .replace("{similarity_data}", similarity_text)
    )

    logger.info("Sending gap analysis request to GPT...")
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    raw = response.choices[0].message.content
    result = json.loads(raw)
    result.setdefault("analysis_date", str(date.today()))
    return result


def run_gap_analyzer(crawl_data: dict[str, Any]) -> dict[str, Any]:
    """
    Main entry point. Takes crawler output and returns ranked gap analysis.

    Args:
        crawl_data: Output from crawler.run_crawler()
            {
                "own_site": [page_records...],
                "competitors": {"Competitor A": [page_records...], ...}
            }

    Returns:
        GPT gap analysis dict with ranked content gaps.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    client = OpenAI(api_key=api_key)

    own_pages = crawl_data["own_site"]
    competitor_map = crawl_data["competitors"]

    # Build own-site DataFrame + embeddings
    logger.info(f"Building embeddings for {len(own_pages)} own-site pages...")
    own_df = build_topic_dataframe(own_pages, label="own_site")
    own_texts = own_df["text"].tolist()
    own_embeddings = get_embeddings(client, own_texts) if own_texts else np.array([])

    # Build competitor DataFrames + embeddings
    all_competitor_pages = []
    for site_name, pages in competitor_map.items():
        for page in pages:
            page["site_name"] = site_name
        all_competitor_pages.extend(pages)

    logger.info(f"Building embeddings for {len(all_competitor_pages)} competitor pages...")
    comp_df = build_topic_dataframe(all_competitor_pages, label="competitor")
    comp_texts = comp_df["text"].tolist()
    comp_embeddings = get_embeddings(client, comp_texts) if comp_texts else np.array([])

    # Find content gaps
    gaps_df = find_content_gaps(
        own_df=own_df,
        own_embeddings=own_embeddings,
        competitor_df=comp_df,
        competitor_embeddings=comp_embeddings,
    )

    if gaps_df.empty:
        logger.warning("No content gaps found. Check threshold or crawl results.")
        return {"top_gaps": [], "total_gaps_found": 0, "summary": "No gaps detected."}

    # Aggregate for GPT prompt
    competitor_gaps = aggregate_gaps_by_site(gaps_df)
    own_pages_summary = own_df[["title", "url"]].to_dict(orient="records")

    # GPT-powered priority ranking
    analysis = rank_gaps_with_gpt(
        client=client,
        own_pages_summary=own_pages_summary,
        competitor_gaps=competitor_gaps,
        gaps_df=gaps_df,
    )

    # Attach raw gaps DataFrame as metadata for downstream agents
    analysis["_gaps_df"] = gaps_df

    return analysis


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # Quick smoke test with mock data
    mock_data = {
        "own_site": [
            {"title": "Introduction to AI", "url": "https://mysite.com/ai-intro",
             "description": "Basics of AI", "content": "AI is transforming industries...", "word_count": 500}
        ],
        "competitors": {
            "Competitor A": [
                {"title": "Advanced Machine Learning Pipelines", "url": "https://comp.com/ml-pipelines",
                 "description": "ML pipeline guide", "content": "Building production ML pipelines requires...", "word_count": 1200},
                {"title": "LLM Fine-tuning Guide", "url": "https://comp.com/llm-finetuning",
                 "description": "How to fine-tune LLMs", "content": "Fine-tuning large language models...", "word_count": 900},
            ]
        },
    }

    result = run_gap_analyzer(mock_data)
    print(json.dumps({k: v for k, v in result.items() if k != "_gaps_df"}, indent=2))
