"""
crawler.py - Firecrawl-based web scraper for competitor and own-site content.

Crawls each URL defined in sites.yaml, extracts page titles, descriptions,
and body text, then returns a structured list of page records per site.
"""

import os
import logging
from typing import Any
from urllib.parse import urlparse

import yaml
from firecrawl import FirecrawlApp

logger = logging.getLogger(__name__)


def load_sites_config(config_path: str = "config/sites.yaml") -> dict:
    """Load the sites configuration from YAML."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _clean_text(text: str | None) -> str:
    """Normalize whitespace and strip None values."""
    if not text:
        return ""
    return " ".join(text.split()).strip()


def _should_skip(url: str, exclude_keywords: list[str]) -> bool:
    """Return True if the URL path contains any excluded keyword."""
    path = urlparse(url).path.lower()
    return any(kw.lower() in path for kw in exclude_keywords)


def crawl_site(
    app: FirecrawlApp,
    site_name: str,
    urls: list[str],
    crawl_settings: dict,
    exclude_keywords: list[str],
) -> list[dict[str, Any]]:
    """
    Crawl a list of URLs for a single site and return extracted page data.

    Returns:
        List of dicts with keys: site_name, url, title, description, content, word_count
    """
    pages: list[dict[str, Any]] = []
    max_pages = crawl_settings.get("max_pages_per_site", 50)
    max_depth = crawl_settings.get("max_depth", 2)
    timeout = crawl_settings.get("timeout_seconds", 30)

    for base_url in urls:
        logger.info(f"[{site_name}] Crawling: {base_url}")
        try:
            result = app.crawl_url(
                base_url,
                params={
                    "crawlerOptions": {
                        "maxDepth": max_depth,
                        "limit": max_pages,
                        "excludes": exclude_keywords,
                    },
                    "pageOptions": {
                        "onlyMainContent": True,
                        "includeHtml": False,
                    },
                    "timeout": timeout * 1000,  # Firecrawl uses milliseconds
                },
            )

            raw_pages = result.get("data", []) if isinstance(result, dict) else []

            for page in raw_pages:
                page_url = page.get("metadata", {}).get("sourceURL", base_url)

                if _should_skip(page_url, exclude_keywords):
                    logger.debug(f"Skipping excluded URL: {page_url}")
                    continue

                content = _clean_text(page.get("content", ""))
                title = _clean_text(page.get("metadata", {}).get("title", ""))
                description = _clean_text(
                    page.get("metadata", {}).get("description", "")
                )

                if not content and not title:
                    continue

                pages.append(
                    {
                        "site_name": site_name,
                        "url": page_url,
                        "title": title,
                        "description": description,
                        "content": content,
                        "word_count": len(content.split()),
                    }
                )

            logger.info(f"[{site_name}] Collected {len(raw_pages)} pages from {base_url}")

        except Exception as e:
            logger.error(f"[{site_name}] Failed to crawl {base_url}: {e}")

    return pages


def run_crawler(config_path: str = "config/sites.yaml") -> dict[str, list[dict]]:
    """
    Main entry point. Crawls own site and all competitors.

    Returns:
        {
            "own_site": [page_records...],
            "competitors": {
                "Competitor A": [page_records...],
                ...
            }
        }
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise EnvironmentError("FIRECRAWL_API_KEY environment variable is not set.")

    config = load_sites_config(config_path)
    crawl_settings = config.get("crawl_settings", {})
    topic_filters = config.get("topic_filters", {})
    exclude_keywords = topic_filters.get("exclude_keywords", [])

    app = FirecrawlApp(api_key=api_key)

    # Crawl own site
    own_site_cfg = config["own_site"]
    logger.info(f"Starting crawl for own site: {own_site_cfg['name']}")
    own_pages = crawl_site(
        app,
        site_name=own_site_cfg["name"],
        urls=own_site_cfg["urls"],
        crawl_settings=crawl_settings,
        exclude_keywords=exclude_keywords,
    )

    # Crawl competitors
    competitor_pages: dict[str, list[dict]] = {}
    for competitor in config.get("competitors", []):
        name = competitor["name"]
        logger.info(f"Starting crawl for competitor: {name}")
        competitor_pages[name] = crawl_site(
            app,
            site_name=name,
            urls=competitor["urls"],
            crawl_settings=crawl_settings,
            exclude_keywords=exclude_keywords,
        )

    total_own = len(own_pages)
    total_comp = sum(len(v) for v in competitor_pages.values())
    logger.info(
        f"Crawl complete. Own site: {total_own} pages. "
        f"Competitors: {total_comp} pages across {len(competitor_pages)} sites."
    )

    return {
        "own_site": own_pages,
        "competitors": competitor_pages,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = run_crawler()
    print(f"Own site pages: {len(data['own_site'])}")
    for name, pages in data["competitors"].items():
        print(f"{name}: {len(pages)} pages")
