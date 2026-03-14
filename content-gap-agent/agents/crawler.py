"""
crawler.py - Firecrawl-based web scraper for competitor and own-site content.

Public API (used by main.py):
  scrape_competitor(url)  → list[{title, content, url, date}]
  scrape_own_site(url)    → list[{title, content, url, date}]

Internal batch API (used by run_crawler for full-config crawls):
  crawl_site(...)         → list of enriched page dicts
  run_crawler(config)     → {own_site: [...], competitors: {...}}
"""

import os
import time
import json
import hashlib
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import yaml
from firecrawl import FirecrawlApp

logger = logging.getLogger(__name__)

# Date metadata fields Firecrawl may populate (checked in order)
_DATE_FIELDS = [
    "publishedTime",
    "ogPublishedTime",
    "datePublished",
    "article:published_time",
    "modifiedTime",
    "ogModifiedTime",
]

_DEFAULT_MAX_PAGES = 50
_DEFAULT_MAX_DEPTH = 2
_DEFAULT_TIMEOUT_MS = 30_000  # Firecrawl uses milliseconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_app() -> FirecrawlApp:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise EnvironmentError("FIRECRAWL_API_KEY environment variable is not set.")
    return FirecrawlApp(api_key=api_key)


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.split()).strip()


def _extract_date(metadata: dict) -> str:
    """Extract the best available publish date from page metadata."""
    for field in _DATE_FIELDS:
        val = metadata.get(field, "")
        if val:
            # Normalise to YYYY-MM-DD; handle ISO timestamps
            return str(val)[:10]
    return ""


def _should_skip(url: str, exclude_keywords: list[str]) -> bool:
    path = urlparse(url).path.lower()
    return any(kw.lower() in path for kw in exclude_keywords)


def _pages_from_result(result: Any, base_url: str) -> list[dict]:
    """Extract the raw page list from a Firecrawl crawl response (SDK v0 or v1)."""
    if isinstance(result, dict):
        return result.get("data", [])
    # SDK v1 returns an object with a .data attribute
    data = getattr(result, "data", None)
    if data is not None:
        return list(data)
    return []


def _next_cursor(result: Any) -> str | None:
    """Return the pagination cursor/URL if the crawl has more pages."""
    if isinstance(result, dict):
        return result.get("next") or result.get("nextPage")
    return getattr(result, "next", None) or getattr(result, "nextPage", None)


# ---------------------------------------------------------------------------
# Core crawl with pagination
# ---------------------------------------------------------------------------

def _crawl_with_pagination(
    app: FirecrawlApp,
    url: str,
    max_depth: int,
    max_pages: int,
    timeout_ms: int,
    exclude_keywords: list[str],
) -> list[dict[str, Any]]:
    """
    Crawl a URL and follow pagination cursors until max_pages is reached.

    Firecrawl's synchronous `crawl_url` returns up to `limit` pages per call.
    If the response carries a `next` cursor we follow it to gather more pages.
    """
    all_raw: list[dict[str, Any]] = []
    current_url = url
    first_call = True

    while len(all_raw) < max_pages:
        remaining = max_pages - len(all_raw)

        max_retries = 3
        retry_delay = 30
        for attempt in range(max_retries):
            try:
                if hasattr(app, "v1") and hasattr(app.v1, "crawl_url"):
                    from firecrawl.v1.client import V1ScrapeOptions
                    scrape_opts = V1ScrapeOptions(
                        formats=["markdown"],
                        onlyMainContent=True,
                        timeout=timeout_ms
                    )
                    if first_call:
                        result = app.v1.crawl_url(
                            current_url,
                            exclude_paths=exclude_keywords,
                            max_depth=max_depth,
                            limit=min(remaining, max_pages),
                            scrape_options=scrape_opts
                        )
                    else:
                        result = app.v1.crawl_url(
                            current_url,
                            limit=remaining,
                            scrape_options=scrape_opts
                        )
                elif hasattr(app, "crawl_url"):
                    params = {
                        "crawlerOptions": {
                            "maxDepth": max_depth,
                            "limit": min(remaining, max_pages),
                            "excludes": exclude_keywords,
                        },
                        "pageOptions": {
                            "onlyMainContent": True,
                            "includeHtml": False,
                        },
                        "timeout": timeout_ms,
                    }
                    if first_call:
                        result = app.crawl_url(current_url, params=params)
                    else:
                        result = app.crawl_url(current_url, params={"limit": remaining})
                else:
                    raise AttributeError("FirecrawlApp has no crawl_url method on this library version.")
                
                # If we get here without exception, break the retry loop
                break
                
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "Rate limit exceeded" in error_str:
                    if attempt < max_retries - 1:
                        logger.warning(f"Rate limit exceeded. Waiting {retry_delay}s before retry {attempt + 1}/{max_retries}...")
                        time.sleep(retry_delay)
                        continue
                # For other errors or out of retries, re-raise
                raise
        
        first_call = False

        batch = _pages_from_result(result, current_url)
        all_raw.extend(batch)

        cursor = _next_cursor(result)
        if not cursor or not batch:
            break
        current_url = cursor

    return all_raw[:max_pages]


def _parse_pages(
    raw_pages: list[dict],
    site_name: str,
    base_url: str,
    exclude_keywords: list[str],
) -> list[dict[str, Any]]:
    """Convert raw Firecrawl page records to our standard schema."""
    parsed = []
    for raw_page in raw_pages:
        # Normalize page object to dict
        page = raw_page
        if not isinstance(page, dict):
            if hasattr(page, "model_dump"):
                page = page.model_dump()
            elif hasattr(page, "dict"):
                page = page.dict()
            else:
                page = {"metadata": getattr(page, "metadata", {}), "content": getattr(page, "markdown", getattr(page, "content", ""))}

        meta = page.get("metadata") or {}
        if not isinstance(meta, dict):
            if hasattr(meta, "model_dump"):
                meta = meta.model_dump()
            elif hasattr(meta, "dict"):
                meta = meta.dict()
            else:
                try:
                    meta = vars(meta)
                except Exception:
                    meta = {}

        page_url = meta.get("sourceURL")
        if not page_url:
            page_url = base_url

        if _should_skip(page_url, exclude_keywords):
            logger.debug(f"Skipping excluded URL: {page_url}")
            continue

        raw_content = page.get("markdown") or page.get("content", "")
        content = _clean_text(raw_content)
        title = _clean_text(meta.get("title", ""))
        description = _clean_text(meta.get("description", ""))
        date = _extract_date(meta)

        if not content and not title:
            continue

        parsed.append(
            {
                "site_name": site_name,
                "url": page_url,
                "title": title,
                "description": description,
                "content": content,
                "date": date,
                "word_count": len(content.split()),
            }
        )
    return parsed


# ---------------------------------------------------------------------------
# Public API: single-URL entry points (used by main.py)
# ---------------------------------------------------------------------------

def scrape_competitor(url: str) -> list[dict[str, Any]]:
    """
    Crawl all blog posts and LinkedIn posts from a competitor URL.

    Handles Firecrawl pagination automatically, following `next` cursors
    until `_DEFAULT_MAX_PAGES` is reached or no more pages exist.

    Args:
        url: The competitor's blog/content root URL.

    Returns:
        List of dicts: [{title, content, url, date}, ...]
    """
    app = _get_app()
    site_name = urlparse(url).netloc or url
    logger.info(f"[competitor] Crawling: {url}")

    exclude = ["careers", "jobs", "privacy", "terms", "contact", "login", "signup"]

    try:
        raw = _crawl_with_pagination(
            app=app,
            url=url,
            max_depth=_DEFAULT_MAX_DEPTH,
            max_pages=_DEFAULT_MAX_PAGES,
            timeout_ms=_DEFAULT_TIMEOUT_MS,
            exclude_keywords=exclude,
        )
        pages = _parse_pages(raw, site_name=site_name, base_url=url, exclude_keywords=exclude)
        logger.info(f"[competitor] {site_name}: {len(pages)} pages scraped.")
        return [
            {
                "title": p["title"],
                "content": p["content"],
                "url": p["url"],
                "date": p["date"],
            }
            for p in pages
        ]
    except Exception as e:
        logger.error(f"[competitor] Failed to scrape {url}: {e}")
        return []


def scrape_own_site(url: str) -> list[dict[str, Any]]:
    """
    Crawl all blog/content pages from voicecare.ai (own site).

    Handles Firecrawl pagination automatically.

    Args:
        url: The own site's content root URL (e.g. https://voicecare.ai/blog).

    Returns:
        List of dicts: [{title, content, url, date}, ...]
    """
    app = _get_app()
    site_name = urlparse(url).netloc or url
    logger.info(f"[own_site] Crawling: {url}")

    exclude = ["careers", "jobs", "privacy", "terms", "contact", "login", "signup"]

    try:
        raw = _crawl_with_pagination(
            app=app,
            url=url,
            max_depth=_DEFAULT_MAX_DEPTH,
            max_pages=_DEFAULT_MAX_PAGES,
            timeout_ms=_DEFAULT_TIMEOUT_MS,
            exclude_keywords=exclude,
        )
        pages = _parse_pages(raw, site_name=site_name, base_url=url, exclude_keywords=exclude)
        logger.info(f"[own_site] {site_name}: {len(pages)} pages scraped.")
        return [
            {
                "title": p["title"],
                "content": p["content"],
                "url": p["url"],
                "date": p["date"],
            }
            for p in pages
        ]
    except Exception as e:
        logger.error(f"[own_site] Failed to scrape {url}: {e}")
        return []


# ---------------------------------------------------------------------------
# Batch API: full-config crawl (used by run_crawler)
# ---------------------------------------------------------------------------

def load_sites_config(config_path: str = "config/sites.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def crawl_site(
    app: FirecrawlApp,
    site_name: str,
    urls: list[str],
    crawl_settings: dict,
    exclude_keywords: list[str],
) -> list[dict[str, Any]]:
    """Crawl multiple URLs for one site and aggregate results."""
    pages: list[dict[str, Any]] = []
    max_pages = crawl_settings.get("max_pages_per_site", _DEFAULT_MAX_PAGES)
    max_depth = crawl_settings.get("max_depth", _DEFAULT_MAX_DEPTH)
    timeout_ms = crawl_settings.get("timeout_seconds", 30) * 1000

    from pathlib import Path
    
    # Ensure cache directory is always relative to the project root, not where the script was called from
    project_root = Path(__file__).parent.parent
    cache_dir = project_root / "cache"
    os.makedirs(cache_dir, exist_ok=True)

    for base_url in urls:
        cache_key = hashlib.md5(base_url.encode("utf-8")).hexdigest()
        cache_path = cache_dir / f"{cache_key}.json"
        
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    batch = json.load(f)
                pages.extend(batch)
                logger.info(f"[{site_name}] Loaded {len(batch)} pages from local cache for {base_url}")
                continue
            except Exception as e:
                logger.warning(f"[{site_name}] Failed to read cache for {base_url}, re-crawling. Error: {e}")

        logger.info(f"[{site_name}] Crawling: {base_url}")
        try:
            raw = _crawl_with_pagination(
                app=app,
                url=base_url,
                max_depth=max_depth,
                max_pages=max_pages,
                timeout_ms=timeout_ms,
                exclude_keywords=exclude_keywords,
            )
            batch = _parse_pages(raw, site_name=site_name, base_url=base_url,
                                  exclude_keywords=exclude_keywords)
            pages.extend(batch)
            logger.info(f"[{site_name}] +{len(batch)} pages from {base_url}")
            
            # Save to cache
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(batch, f)
            except Exception as ce:
                logger.warning(f"Failed to write cache for {base_url}: {ce}")
                
        except Exception as e:
            logger.error(f"[{site_name}] Failed to crawl {base_url}: {e}")
        
        # Add a 1-minute sleep to respect Firecrawl rate limits (10 req/min free tier)
        logger.info(f"[{site_name}] Sleeping for 60 seconds to respect rate limits...")
        time.sleep(60)

    return pages


def run_crawler(config_path: str = "config/sites.yaml") -> dict[str, Any]:
    """
    Full-config entry point. Crawls own site and all competitors defined in sites.yaml.

    Returns:
        {
            "own_site": [page_records...],
            "competitors": {"Competitor A": [page_records...], ...}
        }
    """
    app = _get_app()
    config = load_sites_config(config_path)
    crawl_settings = config.get("crawl_settings", {})
    exclude_keywords = config.get("topic_filters", {}).get("exclude_keywords", [])

    own_site_cfg = config["own_site"]
    logger.info(f"Starting crawl for own site: {own_site_cfg['name']}")
    own_pages = crawl_site(app, own_site_cfg["name"], own_site_cfg["urls"],
                           crawl_settings, exclude_keywords)

    competitor_pages: dict[str, list[dict]] = {}
    for competitor in config.get("competitors", []):
        name = competitor["name"]
        logger.info(f"Starting crawl for competitor: {name}")
        competitor_pages[name] = crawl_site(app, name, competitor["urls"],
                                             crawl_settings, exclude_keywords)

    logger.info(
        f"Crawl complete. Own: {len(own_pages)} pages | "
        f"Competitors: {sum(len(v) for v in competitor_pages.values())} pages "
        f"across {len(competitor_pages)} sites."
    )
    return {"own_site": own_pages, "competitors": competitor_pages}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Quick test of the new API
    import json
    pages = scrape_own_site("https://voicecare.ai/blog")
    print(json.dumps(pages[:2], indent=2))
