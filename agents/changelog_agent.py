"""
Changelog Agent - scrapes competitor changelog / release pages.

Writes results to shared_state/changelogs.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

SHARED_STATE = Path(__file__).parent.parent / "shared_state"
OUTPUT_FILE = SHARED_STATE / "changelogs.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def _fetch_html(url: str) -> str | None:
    try:
        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        print(f"  [changelog] WARNING: could not fetch {url}: {exc}")
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_entries(html: str, url: str) -> list[str]:
    """Best-effort extraction of changelog entries from arbitrary HTML."""
    soup = BeautifulSoup(html, "lxml")

    # Remove nav / footer noise
    for tag in soup(["nav", "footer", "script", "style", "head"]):
        tag.decompose()

    entries: list[str] = []

    # Strategy 1: look for <h2> / <h3> as entry headings (common pattern)
    for heading in soup.find_all(["h2", "h3"], limit=20):
        text = _clean_text(heading.get_text())
        if text and len(text) > 4:
            entries.append(text)

    # Strategy 2: look for <article> or <section> with date-like siblings
    if len(entries) < 3:
        for article in soup.find_all(["article", "section"], limit=20):
            text = _clean_text(article.get_text()[:200])
            if text:
                entries.append(text)

    # Strategy 3: list items that look like changelog bullets
    if len(entries) < 3:
        for li in soup.find_all("li", limit=30):
            text = _clean_text(li.get_text()[:150])
            if text and len(text) > 10:
                entries.append(text)

    return entries[:10]  # cap at 10 entries


def _extract_date(html: str) -> str | None:
    """Try to find the most recent date in the page."""
    date_patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def scrape_competitor(competitor: dict[str, Any]) -> dict[str, Any]:
    name = competitor["name"]
    url = competitor.get("changelog_url", "")

    result: dict[str, Any] = {
        "latest_entry": None,
        "date": None,
        "summary": None,
        "raw_entries": [],
        "error": None,
    }

    if not url:
        result["error"] = "No changelog URL configured"
        return result

    html = _fetch_html(url)
    if html is None:
        result["error"] = f"Failed to fetch {url}"
        return result

    entries = _parse_entries(html, url)
    result["raw_entries"] = entries
    result["latest_entry"] = entries[0] if entries else "No entries found"
    result["date"] = _extract_date(html)
    result["summary"] = (
        f"Found {len(entries)} changelog entries. "
        f"Most recent: {entries[0]!r}" if entries else "No changelog entries extracted."
    )

    return result


def run(competitors: list[dict[str, Any]]) -> None:
    SHARED_STATE.mkdir(exist_ok=True)

    output: dict[str, Any] = {
        "agent": "changelog",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "competitors": {},
    }

    for comp in competitors:
        name = comp["name"]
        print(f"  [changelog] Scraping {name}...")
        output["competitors"][name] = scrape_competitor(comp)

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"  [changelog] Done. Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    import yaml

    cfg_path = Path(__file__).parent.parent / "competitors.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    run(cfg["competitors"])
