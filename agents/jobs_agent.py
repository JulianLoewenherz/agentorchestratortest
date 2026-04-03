"""
Jobs Agent - scrapes competitor job board pages.

Extracts job titles, departments, locations and infers hiring signals.
Writes results to shared_state/job_postings.json.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

SHARED_STATE = Path(__file__).parent.parent / "shared_state"
OUTPUT_FILE = SHARED_STATE / "job_postings.json"

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

# Keyword → department mapping for inference
DEPT_KEYWORDS: dict[str, list[str]] = {
    "Engineering": ["engineer", "developer", "backend", "frontend", "fullstack", "devops", "platform", "infrastructure", "sre", "architect"],
    "AI/ML": ["machine learning", "ml ", " ml", "ai ", " ai", "data scientist", "llm", "nlp", "computer vision", "deep learning"],
    "Design": ["designer", "ux", "ui ", "product design", "visual design", "brand"],
    "Sales": ["account executive", "sales", "ae ", "account manager", "revenue", "business development", "bdr", "sdr"],
    "Marketing": ["marketing", "growth", "content", "seo", "demand gen", "brand"],
    "Product": ["product manager", "pm ", " pm", "product lead"],
    "Customer Success": ["customer success", "customer support", "implementation", "solutions engineer"],
    "Operations": ["operations", "finance", "legal", "recruiting", "hr ", "people ops", "office manager"],
}

SIGNAL_PATTERNS: list[tuple[str, str]] = [
    ("AI/ML", "Heavy AI/ML investment: {count} AI/ML roles signals deep model or feature work"),
    ("Sales", "Sales expansion: {count} sales roles suggests scaling GTM or enterprise push"),
    ("Engineering", "Engineering-heavy hiring ({count} roles): infrastructure or product build-out"),
    ("Customer Success", "Customer Success hiring ({count} roles): focus on retention and expansion"),
]


def _fetch_html(url: str) -> str | None:
    try:
        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        print(f"  [jobs] WARNING: could not fetch {url}: {exc}")
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _classify_department(title: str) -> str:
    title_lower = title.lower()
    for dept, keywords in DEPT_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return dept
    return "Other"


def _extract_jobs(html: str) -> list[dict[str, str]]:
    """Extract job listings from arbitrary jobs page HTML."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["nav", "footer", "script", "style", "head"]):
        tag.decompose()

    jobs: list[dict[str, str]] = []
    seen: set[str] = set()

    # Strategy 1: Greenhouse / Lever / Ashby / Workable-style structured markup
    for item in soup.find_all(["li", "div", "article"], limit=200):
        classes = " ".join(item.get("class", []))
        # Common job board class patterns
        if not any(kw in classes.lower() for kw in ["job", "opening", "position", "role", "posting", "listing", "vacancy"]):
            continue
        text = _clean_text(item.get_text()[:300])
        if text and text not in seen and len(text) > 8:
            seen.add(text)
            jobs.append({"title": text, "department": _classify_department(text)})

    # Strategy 2: Look for <a> tags that look like job links
    if len(jobs) < 3:
        for a in soup.find_all("a", href=True, limit=200):
            href = a.get("href", "")
            text = _clean_text(a.get_text())
            if (
                any(kw in href.lower() for kw in ["job", "career", "opening", "position", "role"])
                and text
                and len(text) > 8
                and len(text) < 120
                and text not in seen
            ):
                seen.add(text)
                jobs.append({"title": text, "department": _classify_department(text)})

    # Strategy 3: Headings that look like role names
    if len(jobs) < 3:
        for h in soup.find_all(["h2", "h3", "h4"], limit=60):
            text = _clean_text(h.get_text())
            if (
                text
                and len(text) > 8
                and len(text) < 120
                and text not in seen
                and any(kw in text.lower() for kw in ["engineer", "designer", "manager", "director", "analyst", "lead", "head of", "vp ", "specialist"])
            ):
                seen.add(text)
                jobs.append({"title": text, "department": _classify_department(text)})

    return jobs[:50]


def _build_hiring_signals(dept_counts: dict[str, int]) -> list[str]:
    signals: list[str] = []
    total = sum(dept_counts.values())
    for dept, template in SIGNAL_PATTERNS:
        count = dept_counts.get(dept, 0)
        if count >= 2:
            signals.append(template.format(count=count))
    if total > 20:
        signals.append(f"High hiring volume ({total} open roles): rapid scaling phase")
    elif total == 0:
        signals.append("No open roles detected - possible hiring freeze or scraping issue")
    return signals


def scrape_competitor(competitor: dict[str, Any]) -> dict[str, Any]:
    url = competitor.get("jobs_url", "")
    result: dict[str, Any] = {
        "total_openings": 0,
        "departments": {},
        "notable_roles": [],
        "locations": [],
        "hiring_signals": [],
        "error": None,
    }

    if not url:
        result["error"] = "No jobs URL configured"
        return result

    html = _fetch_html(url)
    if html is None:
        result["error"] = f"Failed to fetch {url}"
        return result

    jobs = _extract_jobs(html)
    dept_counter: Counter[str] = Counter(j["department"] for j in jobs)

    result["total_openings"] = len(jobs)
    result["departments"] = dict(dept_counter.most_common())
    result["notable_roles"] = [j["title"] for j in jobs[:8]]
    result["hiring_signals"] = _build_hiring_signals(dict(dept_counter))

    return result


def run(competitors: list[dict[str, Any]]) -> None:
    SHARED_STATE.mkdir(exist_ok=True)

    output: dict[str, Any] = {
        "agent": "jobs",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "competitors": {},
    }

    for comp in competitors:
        name = comp["name"]
        print(f"  [jobs] Scraping {name}...")
        output["competitors"][name] = scrape_competitor(comp)

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"  [jobs] Done. Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    import yaml

    cfg_path = Path(__file__).parent.parent / "competitors.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    run(cfg["competitors"])
