"""
Social Agent - scrapes public Twitter/X profile pages via nitter.net mirrors.

Extracts recent post summaries and engagement signals.
Writes results to shared_state/social_signals.json.
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
OUTPUT_FILE = SHARED_STATE / "social_signals.json"

# Nitter public instances (try in order)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

TIMEOUT = httpx.Timeout(12.0, connect=8.0)

# Theme keyword sets for inference
THEME_KEYWORDS: dict[str, list[str]] = {
    "AI integration": ["ai", "gpt", "copilot", "llm", "ml ", "machine learning", "intelligent", "smart", "automate"],
    "enterprise expansion": ["enterprise", "soc 2", "hipaa", "compliance", "security", "sso", "saml"],
    "global growth": ["office", "region", "country", "europe", "emea", "apac", "asia", "global"],
    "product velocity": ["shipped", "launched", "new feature", "now available", "announcing", "release", "update"],
    "developer focus": ["api", "sdk", "webhook", "integration", "developer", "open source"],
    "community & brand": ["community", "blog", "case study", "customer story", "hiring", "team"],
}


def _fetch_html(url: str) -> str | None:
    try:
        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        print(f"  [social] WARNING: could not fetch {url}: {exc}")
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fetch_nitter_profile(handle: str) -> str | None:
    """Try each nitter instance until one works."""
    for base in NITTER_INSTANCES:
        url = f"{base}/{handle}"
        html = _fetch_html(url)
        if html and len(html) > 500:
            return html
    return None


def _parse_nitter_posts(html: str) -> list[dict[str, Any]]:
    """Extract tweets from a nitter profile page."""
    soup = BeautifulSoup(html, "lxml")
    posts: list[dict[str, Any]] = []

    for tweet_div in soup.find_all("div", class_="timeline-item", limit=20):
        # Skip retweets
        if tweet_div.find("div", class_="retweet-header"):
            continue

        content_el = tweet_div.find("div", class_="tweet-content")
        if not content_el:
            continue

        text = _clean_text(content_el.get_text())
        if not text or len(text) < 10:
            continue

        date_el = tweet_div.find("span", class_="tweet-date")
        date_str = None
        if date_el:
            a_el = date_el.find("a")
            if a_el:
                title = a_el.get("title", "")
                # nitter title format: "Jan 1, 2026 · 10:00 AM UTC"
                date_str = title.split("·")[0].strip() if "·" in title else title

        # Rough engagement classification based on stats
        stats_el = tweet_div.find("div", class_="tweet-stats")
        stats_text = stats_el.get_text() if stats_el else ""
        nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", stats_text) if n]
        max_stat = max(nums) if nums else 0
        if max_stat > 500:
            engagement = "very_high"
        elif max_stat > 100:
            engagement = "high"
        elif max_stat > 20:
            engagement = "medium"
        else:
            engagement = "low"

        posts.append({
            "text": text[:280],
            "engagement": engagement,
            "date": date_str,
        })

    return posts[:10]


def _parse_twitter_fallback(html: str) -> list[dict[str, Any]]:
    """
    Fallback: parse Twitter/X direct page (often JS-rendered, so minimal data).
    Look for og:description or any visible text chunks.
    """
    soup = BeautifulSoup(html, "lxml")
    posts: list[dict[str, Any]] = []

    # og:description sometimes contains latest tweet
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        posts.append({
            "text": _clean_text(og["content"])[:280],
            "engagement": "unknown",
            "date": None,
        })

    # Grab any visible <p> or <span> with tweet-like text
    for el in soup.find_all(["p", "span", "div"], limit=100):
        text = _clean_text(el.get_text())
        if (
            len(text) > 30
            and len(text) < 290
            and text not in {p["text"] for p in posts}
        ):
            posts.append({"text": text, "engagement": "unknown", "date": None})
        if len(posts) >= 5:
            break

    return posts


def _infer_themes(posts: list[dict[str, Any]]) -> list[str]:
    all_text = " ".join(p["text"].lower() for p in posts)
    matched = [
        theme
        for theme, keywords in THEME_KEYWORDS.items()
        if sum(1 for kw in keywords if kw in all_text) >= 2
    ]
    return matched[:4]


def _infer_sentiment(posts: list[dict[str, Any]]) -> str:
    if not posts:
        return "unknown"
    # Simple heuristic: high engagement posts → positive signal
    high_count = sum(1 for p in posts if p["engagement"] in ("high", "very_high"))
    if high_count >= 2:
        return "positive"
    if high_count == 1:
        return "neutral"
    return "neutral"


def scrape_competitor(competitor: dict[str, Any]) -> dict[str, Any]:
    handle = competitor.get("twitter_handle", "")
    result: dict[str, Any] = {
        "twitter_handle": handle,
        "profile_url": f"https://twitter.com/{handle}" if handle else None,
        "recent_posts": [],
        "sentiment": "unknown",
        "themes": [],
        "follower_signal": None,
        "error": None,
    }

    if not handle:
        result["error"] = "No Twitter handle configured"
        return result

    # Try nitter first
    html = _fetch_nitter_profile(handle)
    if html:
        posts = _parse_nitter_posts(html)
        source = "nitter"
    else:
        # Fallback: try twitter.com directly (usually JS-rendered, low data)
        print(f"  [social] Nitter unavailable for {handle}, trying twitter.com fallback...")
        tw_html = _fetch_html(f"https://twitter.com/{handle}")
        posts = _parse_twitter_fallback(tw_html) if tw_html else []
        source = "twitter_fallback"

    result["recent_posts"] = posts
    result["sentiment"] = _infer_sentiment(posts)
    result["themes"] = _infer_themes(posts)

    if not posts:
        result["error"] = f"No posts extracted (source: {source})"
        result["follower_signal"] = "Could not retrieve social data - scraping blocked or profile private"
    else:
        themes_str = ", ".join(result["themes"]) if result["themes"] else "general"
        result["follower_signal"] = (
            f"Retrieved {len(posts)} recent posts via {source}. "
            f"Dominant themes: {themes_str}."
        )

    return result


def run(competitors: list[dict[str, Any]]) -> None:
    SHARED_STATE.mkdir(exist_ok=True)

    output: dict[str, Any] = {
        "agent": "social",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "competitors": {},
    }

    for comp in competitors:
        name = comp["name"]
        print(f"  [social] Scraping {name} (@{comp.get('twitter_handle', 'N/A')})...")
        output["competitors"][name] = scrape_competitor(comp)

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"  [social] Done. Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    import yaml

    cfg_path = Path(__file__).parent.parent / "competitors.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    run(cfg["competitors"])
