"""
Pricing Agent - scrapes competitor pricing pages.

Extracts plan names, prices, and feature lists.
Detects price changes by comparing with the existing JSON state.
Writes results to shared_state/pricing.json.
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
OUTPUT_FILE = SHARED_STATE / "pricing.json"

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

PRICE_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")
PLAN_KEYWORDS = ["free", "starter", "basic", "plus", "pro", "business", "team", "enterprise", "growth", "scale"]


def _fetch_html(url: str) -> str | None:
    try:
        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        print(f"  [pricing] WARNING: could not fetch {url}: {exc}")
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_prices_from_text(text: str) -> list[float]:
    return [float(m) for m in PRICE_RE.findall(text)]


def _parse_pricing(html: str) -> list[dict[str, Any]]:
    """Extract pricing tiers from a pricing page."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["nav", "footer", "script", "style", "head"]):
        tag.decompose()

    plans: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Strategy 1: Look for pricing card containers
    card_selectors = [
        ("div", ["pricing", "plan", "tier", "package", "card"]),
        ("section", ["pricing", "plan", "tier"]),
        ("article", ["pricing", "plan"]),
    ]

    for tag_name, class_keywords in card_selectors:
        for el in soup.find_all(tag_name, limit=50):
            classes = " ".join(el.get("class", [])).lower()
            if not any(kw in classes for kw in class_keywords):
                continue

            text = _clean_text(el.get_text())
            if len(text) < 10:
                continue

            prices = _extract_prices_from_text(text)
            name = _infer_plan_name(text)

            if name and name not in seen_names:
                seen_names.add(name)
                features = _extract_features(el)
                plans.append({
                    "name": name,
                    "price_monthly": prices[0] if len(prices) >= 1 else None,
                    "price_annual": prices[1] if len(prices) >= 2 else None,
                    "features": features[:8],
                })

    # Strategy 2: Look for headings that name plans
    if len(plans) < 2:
        for heading in soup.find_all(["h2", "h3"], limit=30):
            text = _clean_text(heading.get_text()).lower()
            if any(kw in text for kw in PLAN_KEYWORDS) and text not in seen_names:
                seen_names.add(text)
                # Grab surrounding text for price
                parent = heading.parent or heading
                parent_text = _clean_text(parent.get_text()[:400])
                prices = _extract_prices_from_text(parent_text)
                features = _extract_features(parent)
                plans.append({
                    "name": heading.get_text().strip(),
                    "price_monthly": prices[0] if len(prices) >= 1 else None,
                    "price_annual": prices[1] if len(prices) >= 2 else None,
                    "features": features[:8],
                })

    return plans[:6]


def _infer_plan_name(text: str) -> str | None:
    text_lower = text.lower()
    for kw in PLAN_KEYWORDS:
        if kw in text_lower:
            # Capitalize first match
            idx = text_lower.index(kw)
            return text[idx: idx + len(kw)].title()
    return None


def _extract_features(el: Any) -> list[str]:
    features: list[str] = []
    for li in el.find_all("li", limit=15):
        text = _clean_text(li.get_text())
        if text and len(text) > 4 and len(text) < 120:
            features.append(text)
    return features


def _detect_price_changes(
    current_plans: list[dict[str, Any]],
    previous_competitor: dict[str, Any] | None,
    now: str,
) -> list[dict[str, Any]]:
    """Compare current plans against stored state and emit change records."""
    if not previous_competitor:
        return []

    prev_plans = {p["name"].lower(): p for p in previous_competitor.get("plans", [])}
    changes: list[dict[str, Any]] = []

    for plan in current_plans:
        name_key = plan["name"].lower()
        prev = prev_plans.get(name_key)
        if not prev:
            continue
        for field in ("price_monthly", "price_annual"):
            old_val = prev.get(field)
            new_val = plan.get(field)
            if old_val is not None and new_val is not None and old_val != new_val:
                changes.append({
                    "plan": plan["name"],
                    "field": field,
                    "old_value": old_val,
                    "new_value": new_val,
                    "detected_at": now,
                    "note": f"{field.replace('_', ' ').title()} changed from ${old_val} to ${new_val}/user/month",
                })

    return changes


def scrape_competitor(
    competitor: dict[str, Any],
    previous_state: dict[str, Any] | None,
) -> dict[str, Any]:
    url = competitor.get("pricing_url", "")
    now = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "plans": [],
        "price_changes": [],
        "currency": "USD",
        "notes": None,
        "error": None,
    }

    if not url:
        result["error"] = "No pricing URL configured"
        return result

    html = _fetch_html(url)
    if html is None:
        result["error"] = f"Failed to fetch {url}"
        return result

    plans = _parse_pricing(html)
    result["plans"] = plans

    prev_comp = previous_state.get("competitors", {}).get(competitor["name"]) if previous_state else None
    result["price_changes"] = _detect_price_changes(plans, prev_comp, now)

    if result["price_changes"]:
        result["notes"] = f"Detected {len(result['price_changes'])} price change(s) since last run."
    elif plans:
        result["notes"] = f"Extracted {len(plans)} pricing tiers. No changes detected."
    else:
        result["notes"] = "No pricing data extracted - page may require JavaScript."

    return result


def run(competitors: list[dict[str, Any]]) -> None:
    SHARED_STATE.mkdir(exist_ok=True)

    # Load previous state for change detection
    previous_state: dict[str, Any] | None = None
    if OUTPUT_FILE.exists():
        try:
            previous_state = json.loads(OUTPUT_FILE.read_text())
        except Exception:
            pass

    output: dict[str, Any] = {
        "agent": "pricing",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "competitors": {},
    }

    for comp in competitors:
        name = comp["name"]
        print(f"  [pricing] Scraping {name}...")
        output["competitors"][name] = scrape_competitor(comp, previous_state)

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"  [pricing] Done. Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    import yaml

    cfg_path = Path(__file__).parent.parent / "competitors.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    run(cfg["competitors"])
