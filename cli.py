"""Interactive Command Line Interface for Etix Checker with Rich styling."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich import box

from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.config.settings import CONFIG
from src.domain.enums import ShowStatus
from src.domain.models import CheckResult
from src.etix.checker import EtixCheckEngine
from src.storage.checkpoint import RunContext
from src.storage.reporter import Reporter
from src.utils.logger import LOGGER

console = Console()


def render_header(group_name: str, active_count: int, reserve_count: int) -> None:
    """Render top application banner."""
    console.print(
        Panel.fit(
            f"[bold cyan]ETIX CHECKER 2026[/bold cyan] [bold green]• AdsPower CDP Edition[/bold green]\n"
            f"[dim]Group:[/dim] [yellow]{group_name}[/yellow] | "
            f"[dim]Active Workers:[/dim] [green]{active_count}[/green] | "
            f"[dim]Reserve:[/dim] [cyan]{reserve_count}[/cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )


def create_results_table(results: list[CheckResult], total_shows: int) -> Table:
    """Build formatted Rich table of check results."""
    table = Table(
        title=f"Progress: {len(results)}/{total_shows} shows checked",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Show Name", style="white", min_width=25)
    table.add_column("Status", style="bold", justify="center", width=14)
    table.add_column("Reserved", justify="center", width=12)
    table.add_column("Details", style="dim")

    status_colors = {
        ShowStatus.OK: "[green]OK[/green]",
        ShowStatus.SOLD_OUT: "[red]SOLD OUT[/red]",
        ShowStatus.ENDED: "[yellow]ENDED[/yellow]",
        ShowStatus.PARTIAL: "[yellow]PARTIAL[/yellow]",
        ShowStatus.INSUFFICIENT: "[red]INSUFFICIENT[/red]",
        ShowStatus.BLOCKED: "[bold red]BLOCKED[/bold red]",
        ShowStatus.FAILED: "[bold red]FAILED[/bold red]",
    }

    for idx, r in enumerate(results, 1):
        status_str = status_colors.get(r.status, str(r.status.value))
        reserved_str = f"{r.reserved}/{r.target}"
        table.add_row(str(idx), r.name[:35], status_str, reserved_str, r.details[:50])

    return table


async def main_cli() -> None:
    """Main CLI entrypoint."""
    console.clear()
    console.print("[cyan]Initializing Etix Checker...[/cyan]")

    client = AdsPowerClient(base_url=CONFIG.adspower_api_url)
    profile_manager = AdsPowerProfileManager(client=client)
    engine = EtixCheckEngine(config=CONFIG, client=client, profile_manager=profile_manager)

    # 1. Verify AdsPower connection
    is_alive = await client.check_status()
    if not is_alive:
        console.print(
            Panel(
                f"[bold red]Error: Could not connect to AdsPower Local API at {client.base_url}[/bold red]\n"
                f"Please make sure AdsPower application is open and Local API is enabled in settings.",
                title="[red]AdsPower Offline[/red]",
                border_style="red",
            )
        )
        sys.exit(1)

    # 2. Fetch and assign profiles
    profiles = await profile_manager.load_and_organize_profiles(
        group_name=CONFIG.adspower_group_name,
        active_count=CONFIG.active_profiles_count,
    )
    if not profiles:
        console.print(
            f"[bold red]Error: No profiles found in AdsPower group '{CONFIG.adspower_group_name}'![/bold red]"
        )
        sys.exit(1)

    active_p = profile_manager.get_active_profiles()
    reserve_p = profile_manager.get_reserve_profiles()
    render_header(CONFIG.adspower_group_name, len(active_p), len(reserve_p))

    # 3. Check shows.csv
    shows = engine.load_shows(CONFIG.shows_csv)
    if not shows:
        console.print(f"[bold red]Error: No valid shows found in {CONFIG.shows_csv}![/bold red]")
        sys.exit(1)

    # 4. Check for active checkpoint
    checkpoint_file = RunContext.find_last_active_checkpoint(CONFIG.runs_dir)
    resume = True
    if checkpoint_file:
        console.print(
            f"[yellow]Found unfinished run checkpoint:[/yellow] {checkpoint_file.parent.name}"
        )
        choice = console.input("[bold cyan]Resume previous run? (Y/n): [/bold cyan]").strip().lower()
        if choice in ("n", "no"):
            resume = False
            checkpoint_file.unlink(missing_ok=True)
            console.print("[dim]Starting fresh run...[/dim]")

    # 5. Live Execution
    results_list: list[CheckResult] = []

    with Live(create_results_table(results_list, len(shows)), console=console, refresh_per_second=4) as live:
        def on_done(res: CheckResult, current: int, total: int):
            results_list.append(res)
            live.update(create_results_table(results_list, total))

        final_results = await engine.run(
            shows_csv=CONFIG.shows_csv,
            resume=resume,
            on_show_done=on_done,
        )

    console.print(
        Panel.fit(
            f"[bold green]Check completed![/bold green] Results saved to [cyan]report.csv[/cyan]",
            box=box.ROUNDED,
            border_style="green",
        )
    )


if __name__ == "__main__":
    asyncio.run(main_cli())
