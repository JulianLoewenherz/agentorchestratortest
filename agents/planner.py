"""
Planner - reads competitors config, runs all 4 agents in parallel via ThreadPoolExecutor,
prints a live status table, and writes shared_state/run_manifest.json.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from rich.console import Console
from rich.table import Table
from rich import box

SHARED_STATE = Path(__file__).parent.parent / "shared_state"
MANIFEST_FILE = SHARED_STATE / "run_manifest.json"

console = Console()

# Agent registry: name → (module_path, run_function)
AGENTS: dict[str, str] = {
    "changelog": "agents.changelog_agent",
    "jobs": "agents.jobs_agent",
    "pricing": "agents.pricing_agent",
    "social": "agents.social_agent",
}


def _load_agent(module_name: str) -> Callable[[list[dict[str, Any]]], None]:
    import importlib
    mod = importlib.import_module(module_name)
    return mod.run


def run_agent(
    agent_name: str,
    competitors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run a single agent and return a manifest entry."""
    start = datetime.now(timezone.utc)
    start_ts = start.isoformat()
    error: str | None = None

    try:
        run_fn = _load_agent(AGENTS[agent_name])
        run_fn(competitors)
    except Exception as exc:
        error = str(exc)
        console.print(f"  [bold red][{agent_name}] ERROR:[/] {exc}")

    end = datetime.now(timezone.utc)
    return {
        "agent": agent_name,
        "start_time": start_ts,
        "end_time": end.isoformat(),
        "duration_seconds": round((end - start).total_seconds(), 2),
        "status": "error" if error else "success",
        "error": error,
    }


def run(
    competitors: list[dict[str, Any]],
    agent_filter: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Launch all (or filtered) agents in parallel.

    Returns the manifest dict.
    """
    SHARED_STATE.mkdir(exist_ok=True)

    agents_to_run = agent_filter if agent_filter else list(AGENTS.keys())
    manifest_entries: list[dict[str, Any]] = []

    # Live status table
    status: dict[str, str] = {a: "[yellow]pending[/yellow]" for a in agents_to_run}

    def _print_table() -> None:
        table = Table(
            title="[bold cyan]Competitive Intelligence Agent Fleet[/bold cyan]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Agent", style="bold", width=14)
        table.add_column("Status", width=16)
        table.add_column("Output File", style="dim", width=36)

        output_map = {
            "changelog": "shared_state/changelogs.json",
            "jobs": "shared_state/job_postings.json",
            "pricing": "shared_state/pricing.json",
            "social": "shared_state/social_signals.json",
        }
        for agent in agents_to_run:
            table.add_row(agent, status[agent], output_map.get(agent, "-"))
        console.print(table)

    console.rule("[bold cyan]Starting agent fleet[/bold cyan]")
    console.print(
        f"  Competitors: {', '.join(c['name'] for c in competitors)}\n"
        f"  Agents:      {', '.join(agents_to_run)}\n"
    )

    plan_start = datetime.now(timezone.utc)

    with ThreadPoolExecutor(max_workers=len(agents_to_run)) as executor:
        future_to_agent: dict[Future[dict[str, Any]], str] = {
            executor.submit(run_agent, agent_name, competitors): agent_name
            for agent_name in agents_to_run
        }

        # Mark running
        for agent_name in agents_to_run:
            status[agent_name] = "[blue]running...[/blue]"
        _print_table()

        for future in as_completed(future_to_agent):
            agent_name = future_to_agent[future]
            try:
                entry = future.result()
            except Exception as exc:
                entry = {
                    "agent": agent_name,
                    "status": "error",
                    "error": str(exc),
                }
            manifest_entries.append(entry)
            if entry.get("status") == "success":
                dur = entry.get("duration_seconds", "?")
                status[agent_name] = f"[green]done ({dur}s)[/green]"
            else:
                status[agent_name] = "[red]error[/red]"
            console.clear()
            _print_table()

    plan_end = datetime.now(timezone.utc)
    total_seconds = round((plan_end - plan_start).total_seconds(), 2)

    manifest: dict[str, Any] = {
        "plan_start": plan_start.isoformat(),
        "plan_end": plan_end.isoformat(),
        "total_duration_seconds": total_seconds,
        "competitors": [c["name"] for c in competitors],
        "agents": manifest_entries,
    }

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2))

    successes = sum(1 for e in manifest_entries if e.get("status") == "success")
    failures = len(manifest_entries) - successes

    console.print()
    console.rule("[bold cyan]Fleet complete[/bold cyan]")
    console.print(
        f"  [green]{successes} agent(s) succeeded[/green], "
        f"[red]{failures} failed[/red] "
        f"in [bold]{total_seconds}s[/bold] total"
    )
    console.print(f"  Manifest: {MANIFEST_FILE}\n")

    return manifest
