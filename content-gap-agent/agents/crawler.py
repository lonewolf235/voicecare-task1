"""
crawler.py - Firecrawl v4 SDK web scraper for competitor and own-site content.

Firecrawl v4 API (firecrawl-py >= 2.0):
  app.crawl(url, ...)            → CrawlJob  (synchronous, polls until done)
  app.get_crawl_status_page(url) → CrawlJob  (pagination follow-up)
  CrawlJob.data                  → List[Document]
  Document.markdown              → str  (page text)
  Document.metadata              → DocumentMetadata (typed object, snake_case attrs)
  DocumentMetadata.source_url    → page URL
  DocumentMetadata.published_time → publish date

Public API (used by main.py):
  scrape_competitor(url)  → list[{title, content, url, date}]
  scrape_own_site(url)    → list[{title, content, url, date}]

Internal batch API (used by run_crawler):
  crawl_site(...)
  run_crawler(config_path)
"""

import os
import logging
from typing import Any
from urllib.parse import urlparse

import yaml
from firecrawl import FirecrawlApp
from firecrawl.v2.types import ScrapeOptions, Document, DocumentMetadata

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PAGES = 50
_DEFAULT_MAX_DEPTH = 2
_DEFAULT_TIMEOUT_S = 120  # Firecrawl v4 timeout is in seconds

# DocumentMetadata date fields checked in priority order (snake_case v4 names)
_DATE_FIELDS = [
    "published_time",
    "modified_time",
    "dc_terms_created",
    "dc_date_created",
    "dc_date",
]


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


def _extract_date(meta: DocumentMetadata | None) -> str:
    """Extract the earliest available publish date from DocumentMetadata."""
    if meta is None:
        return ""
    for field in _DATE_FIELDS:
        val = getattr(meta, field, None)
        if val:
            return str(val)[:10]  # normalise to YYYY-MM-DD
    return ""


def _should_skip(url: str, exclude_keywords: list[str]) -> bool:
    path = urlparse(url).path.lower()
    return any(kw.lower() in path for kw in exclude_keywords)


def _doc_to_page(
    doc: Document,
    site_name: str,
    base_url: str,
    exclude_keywords: list[str],
) -> dict[str, Any] | None:
    """
    Convert a Firecrawl v4 Document object to our standard page dict.
    Returns None if the page should be skipped.
    """
    meta: DocumentMetadata | None = doc.metadata

    page_url = (meta.source_url or meta.url or base_url) if meta else base_url

    if _should_skip(page_url, exclude_keywords):
        logger.debug(f"Skipping excluded URL: {page_url}")
        return None

    content = _clean_text(doc.markdown or "")
    title = _clean_text(meta.title if meta else "")
    description = _clean_text(meta.description if meta else "")
    date = _extract_date(meta)

    if not content and not title:
        return None

    return {
        "site_name": site_name,
        "url": page_url,
        "title": title,
        "description": description,
        "content": content,
        "date": date,
        "word_count": len(content.split()),
    }


# ---------------------------------------------------------------------------
# Core crawl with pagination
# ---------------------------------------------------------------------------

def _crawl_with_pagination(
    app: FirecrawlApp,
    url: str,
    max_depth: int,
    max_pages: int,
    timeout_s: int,
    exclude_keywords: list[str],
) -> list[Document]:
    """
    Crawl a URL using Firecrawl v4 app.crawl() and follow pagination cursors.

    app.crawl() is synchronous — it polls until the job is complete and returns
    a CrawlJob with .data (List[Document]) and an optional .next cursor URL.
    If .next is set, we fetch further pages via app.get_crawl_status_page().
    """
    # Convert exclude keywords to path-fragment patterns Firecrawl understands
    exclude_paths = [f"*{kw}*" for kw in exclude_keywords] if exclude_keywords else None

    scrape_opts = ScrapeOptions(
        formats=["markdown"],
        only_main_content=True,
    )

    # First synchronous crawl
    result = app.crawl(
        url,
        limit=max_pages,
        max_discovery_depth=max_depth,
        exclude_paths=exclude_paths,
        scrape_options=scrape_opts,
        timeout=timeout_s,
    )

    all_docs: list[Document] = list(result.data or [])

    # Follow pagination cursors for large sites
    next_url: str | None = result.next
    while next_url and len(all_docs) < max_pages:
        page_result = app.get_crawl_status_page(next_url)
        page_docs = list(page_result.data or [])
        if not page_docs:
            break
        all_docs.extend(page_docs)
        next_url = page_result.next

    return all_docs[:max_pages]


# ---------------------------------------------------------------------------
# Public API: single-URL entry points (called by main.py)
# ---------------------------------------------------------------------------

def scrape_competitor(url: str) -> list[dict[str, Any]]:
    """
    Crawl all blog/content pages from a competitor URL.

    Uses Firecrawl v4 app.crawl() with markdown extraction and pagination.

    Args:
        url: The competitor's content root URL (e.g. https://syllable.ai/blog).

    Returns:
        List of dicts: [{title, content, url, date}, ...]
    """
    app = _get_app()
    site_name = urlparse(url).netloc or url
    logger.info(f"[competitor] Crawling: {url}")

    exclude = ["careers", "jobs", "privacy", "terms", "contact", "login", "signup"]

    try:
        docs = _crawl_with_pagination(
            app=app,
            url=url,
            max_depth=_DEFAULT_MAX_DEPTH,
            max_pages=_DEFAULT_MAX_PAGES,
            timeout_s=_DEFAULT_TIMEOUT_S,
            exclude_keywords=exclude,
        )
        pages = [
            p for doc in docs
            if (p := _doc_to_page(doc, site_name, url, exclude)) is not None
        ]
        logger.info(f"[competitor] {site_name}: {len(pages)} pages scraped.")
        return [
            {"title": p["title"], "content": p["content"], "url": p["url"], "date": p["date"]}
            for p in pages
        ]
    except Exception as e:
        logger.error(f"[competitor] Failed to scrape {url}: {e}")
        return []


def scrape_own_site(url: str) -> list[dict[str, Any]]:
    """
    Crawl all blog/content pages from Voicecare.ai (own site).

    Uses Firecrawl v4 app.crawl() with markdown extraction and pagination.

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
        docs = _crawl_with_pagination(
            app=app,
            url=url,
            max_depth=_DEFAULT_MAX_DEPTH,
            max_pages=_DEFAULT_MAX_PAGES,
            timeout_s=_DEFAULT_TIMEOUT_S,
            exclude_keywords=exclude,
        )
        pages = [
            p for doc in docs
            if (p := _doc_to_page(doc, site_name, url, exclude)) is not None
        ]
        logger.info(f"[own_site] {site_name}: {len(pages)} pages scraped.")
        return [
            {"title": p["title"], "content": p["content"], "url": p["url"], "date": p["date"]}
            for p in pages
        ]
    except Exception as e:
        logger.error(f"[own_site] Failed to scrape {url}: {e}")
        return []


# ---------------------------------------------------------------------------
# Internal batch API (used by run_crawler for full-config crawls)
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
    timeout_s = crawl_settings.get("timeout_seconds", _DEFAULT_TIMEOUT_S)

    for base_url in urls:
        logger.info(f"[{site_name}] Crawling: {base_url}")
        try:
            docs = _crawl_with_pagination(
                app=app,
                url=base_url,
                max_depth=max_depth,
                max_pages=max_pages,
                timeout_s=timeout_s,
                exclude_keywords=exclude_keywords,
            )
            batch = [
                p for doc in docs
                if (p := _doc_to_page(doc, site_name, base_url, exclude_keywords)) is not None
            ]
            pages.extend(batch)
            logger.info(f"[{site_name}] +{len(batch)} pages from {base_url}")
        except Exception as e:
            logger.error(f"[{site_name}] Failed to crawl {base_url}: {e}")

    return pages


def run_crawler(config_path: str = "config/sites.yaml") -> dict[str, Any]:
    """
    Full-config entry point. Crawls own site and all competitors defined in sites.yaml.

    Returns:
        {"own_site": [page_records...], "competitors": {"Name": [page_records...], ...}}
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
    import json
    pages = scrape_own_site("https://voicecare.ai/blog")
    print(json.dumps(pages[:2], indent=2))
