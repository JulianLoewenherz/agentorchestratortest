#!/usr/bin/env python3
"""
Entry point for the Competitive Intelligence Agent Fleet.

Usage:
    python run.py                          # full run with competitors.yaml
    python run.py --competitors my.yaml   # custom config file
    python run.py --skip-scrape           # re-synthesize from existing shared_state/
    python run.py --skip-scrape --sample  # synthesize from sample data
    python run.py --agent changelog       # run only one agent
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from rich.console import Console

console = Console()


def load_competitors(config_path: Path) -> list[dict]:
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        sys.exit(1)
    cfg = yaml.safe_load(config_path.read_text())
    competitors = cfg.get("competitors", [])
    if not competitors:
        console.print("[red]No competitors found in config.[/red]")
        sys.exit(1)
    return competitors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Competitive Intelligence Agent Fleet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--competitors",
        default="competitors.yaml",
        metavar="FILE",
        help="Path to competitors YAML config (default: competitors.yaml)",
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip scraping agents; synthesize briefing from existing shared_state/ data",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Use sample data from shared_state/sample/ (implies --skip-scrape)",
    )
    parser.add_argument(
        "--agent",
        metavar="NAME",
        help="Run only one agent: changelog | jobs | pricing | social",
    )
    parser.add_argument(
        "--no-briefing",
        action="store_true",
        help="Run scraping agents but skip the Claude synthesis step",
    )

    args = parser.parse_args()

    config_path = Path(args.competitors)
    use_sample = args.sample
    skip_scrape = args.skip_scrape or use_sample

    if not skip_scrape:
        competitors = load_competitors(config_path)

        from agents.planner import run as planner_run

        agent_filter = [args.agent] if args.agent else None
        planner_run(competitors, agent_filter=agent_filter)

    if not args.no_briefing:
        from orchestrator import synthesize
        synthesize(use_sample=use_sample)


if __name__ == "__main__":
    main()
