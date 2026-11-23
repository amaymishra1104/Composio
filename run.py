import argparse
import asyncio
import csv
import logging
from pathlib import Path
import sys
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from core.pipeline import ResearchPipeline
from schema import AppInput

console = Console()


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def load_apps(
    csv_path: str = "apps.csv",
    sample_only: bool = False,
    specific_apps: list = None,
    limit: int = None,
) -> list[AppInput]:
    path = Path(csv_path)
    if not path.exists():
        console.print(f"[bold red]Error:[/] {csv_path} not found.")
        sys.exit(1)

    apps = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            apps.append(
                AppInput(
                    id=int(row["id"]),
                    app=row["app"].strip(),
                    category=row["category"].strip(),
                    website_hint=row["website_hint"].strip(),
                )
            )

    if specific_apps:
        apps = [a for a in apps if a.app.lower() in [s.lower() for s in specific_apps]]
    elif sample_only:
        # Default 3 sample apps requested by user
        sample_names = ["salesforce", "telegram", "twenty"]
        apps = [a for a in apps if a.app.lower() in sample_names]

    if limit and limit > 0:
        apps = apps[:limit]

    return apps


def display_results_table(records):
    table = Table(
        title="App Research Feasibility Results",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("App", style="bold white")
    table.add_column("Category", style="dim")
    table.add_column("Auth Methods", style="green")
    table.add_column("Self-Serve", style="yellow")
    table.add_column("Buildability", style="bold")
    table.add_column("Conf.", justify="center")
    table.add_column("Evidence Quotes", justify="center")
    table.add_column("Human Review?", justify="center")

    for r in records:
        build_color = {
            "easy_win": "green",
            "buildable_with_caveats": "yellow",
            "needs_outreach": "orange3",
            "not_buildable": "red",
        }.get(r.buildability, "white")

        table.add_row(
            str(r.id),
            r.app,
            r.category,
            ", ".join(r.auth_methods),
            r.self_serve,
            f"[{build_color}]{r.buildability}[/]",
            f"{int(r.confidence * 100)}%",
            str(len(r.evidence)),
            "[red]YES[/]" if r.needs_human else "[green]NO[/]",
        )

    console.print(table)


async def main():
    parser = argparse.ArgumentParser(
        description="Research agent pipeline for Composio app feasibility"
    )
    parser.add_argument(
        "--pass",
        dest="pass_num",
        type=int,
        default=1,
        help="Pass number (default: 1)",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Run only on the 3 sample apps (Salesforce, Telegram, Twenty)",
    )
    parser.add_argument(
        "--apps",
        type=str,
        default=None,
        help="Comma-separated list of specific app names to run (e.g. 'Salesforce,Telegram,Twenty')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of apps to process",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Async concurrency limit (default: 5)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass disk cache and force re-fetching",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    specific_list = [a.strip() for a in args.apps.split(",")] if args.apps else None
    apps = load_apps(
        csv_path="apps.csv",
        sample_only=args.sample,
        specific_apps=specific_list,
        limit=args.limit,
    )

    if not apps:
        console.print("[bold yellow]No apps matched the criteria to process.[/]")
        return

    console.print(
        f"\n[bold green]=== Launching App Research Pipeline ===[/]\n"
        f"Processing [cyan]{len(apps)}[/] app(s) with concurrency [cyan]{args.concurrency}[/] (Pass {args.pass_num})\n"
    )

    pipeline = ResearchPipeline(
        concurrency_limit=args.concurrency,
        bypass_cache=args.no_cache,
    )

    records = await pipeline.run(apps, output_pass=args.pass_num)

    console.print("\n[bold green]=== Pipeline Execution Finished ===[/]\n")
    display_results_table(records)

    console.print(
        f"\nOutput files generated:\n"
        f"  - [cyan]results/pass{args.pass_num}.json[/]\n"
        f"  - [cyan]results/pass{args.pass_num}.csv[/]\n"
        f"  - [cyan]results/human_queue.csv[/] ([yellow]{sum(1 for r in records if r.needs_human)} flagged[/])\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
