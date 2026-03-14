"""
notion_exporter.py - Formats and exports generated video scripts to a Notion database.
"""

import os
import logging
from typing import Any, Dict

from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)

import re

# Config
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")

def _extract_notion_id(raw_str: str) -> str:
    """Extract a 32-character hex UUID from a raw string or Notion URL."""
    if not raw_str:
        return ""
    # Notion IDs are 32 hex chars, sometimes with hyphens.
    clean_str = raw_str.replace("-", "").lower()
    match = re.search(r"([a-f0-9]{32})", clean_str)
    if match:
        return match.group(1)
    return raw_str

NOTION_DATABASE_ID = _extract_notion_id(os.getenv("NOTION_DATABASE_ID", ""))

# Initialize client
notion = Client(auth=NOTION_API_KEY) if NOTION_API_KEY else None

def export_script_to_notion(script: Dict[str, Any]) -> str:
    """
    Export a generated script dictionary to a structured Notion page.
    
    Args:
        script: Dictionary containing topic, hook, full_script, caption, hashtags, etc.
        
    Returns:
        The URL of the newly created Notion page, or "" if it failed or is disabled.
    """
    if not notion or not NOTION_DATABASE_ID:
        logger.debug("Skipping Notion export (NOTION_API_KEY or NOTION_DATABASE_ID not set).")
        return ""
        
    if script.get("error"):
        logger.warning(f"Skipping Notion export for failed script: {script.get('topic')}")
        return ""
        
    topic = script.get("topic", "Untitled Script")
    rank = script.get("gap_rank", "?")
    hook_data = script.get("hook", {})
    hook_text = hook_data.get("text", "No hook provided")
    full_script = script.get("full_script", "")
    caption = script.get("caption", "")
    hashtags = " ".join(script.get("hashtags", []))
    thumbnail = script.get("thumbnail_text", "")
    
    # Format the content into Notion blocks
    blocks = [
        # Intro Section
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "Video Metadata"}}]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "Topic: ", "link": None}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": topic, "link": None}}
                ]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "Gap Priority Rank: ", "link": None}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": str(rank), "link": None}}
                ]
            }
        },
        {
            "object": "block",
            "type": "divider",
            "divider": {}
        },
        
        # Script Section
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "Full Script"}}]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "🪝 Hook: ", "link": None}, "annotations": {"bold": True, "italic": True}},
                    {"type": "text", "text": {"content": hook_text, "link": None}}
                ]
            }
        }
    ]
    
    # Break long script text into separate paragraphs if needed (Notion limit is 2000 chars per text block)
    # the full script might have newlines, so we split by double newline as paragraphs
    for para in full_script.split('\n\n'):
        clean_para = para.strip()
        if clean_para:
            # We must handle formatting, but we'll keep it simple: plain string array
            # Chunking exactly at 2000 chars just in case any paragraph is massive
            chunk_size = 1999
            chunks = [clean_para[i:i + chunk_size] for i in range(0, len(clean_para), chunk_size)]
            
            for chunk in chunks:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}]
                    }
                })
            
    # Social Details
    blocks.extend([
        {
            "object": "block",
            "type": "divider",
            "divider": {}
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "Social Media Copy"}}]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "Thumbnail text: ", "link": None}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": thumbnail, "link": None}}
                ]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": caption}}]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": hashtags}, "annotations": {"color": "blue"}}]
            }
        }
    ])

    try:
        new_page = notion.pages.create(
            parent={"type": "database_id", "database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {
                    "title": [{"type": "text", "text": {"content": topic}}]
                }
            },
            children=blocks
        )
        url = new_page.get("url", "")
        logger.info(f"Successfully created Notion page for '{topic}': {url}")
        return url
    except APIResponseError as e:
        logger.error(f"Failed to create Notion page for '{topic}'. Notion API error: {e}")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error creating Notion page for '{topic}': {e}")
        return ""
