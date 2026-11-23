import argparse
import asyncio
import logging
import sys
from pathlib import Path
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from core.verifier import VerificationEngine

console = Console()


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


async def main():
    parser = argparse.ArgumentParser(
        description="Verification stage for app-research-agent (Pass 1 -> Verification -> Pass 2)"
    )
    parser.add_argument(
        "--pass1-path",
        default="results/pass1.json",
        help="Path to results/pass1.json",
    )
    parser.add_argument(
        "--apps-csv",
        default="apps.csv",
        help="Path to apps.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="results",
        help="Directory to save pass2.json, pass2.csv, and verification_report.json",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Concurrency limit for verification",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    pass1_file = Path(args.pass1_path)
    if not pass1_file.exists():
        console.print(
            f"[bold red]Error:[/] Pass 1 results file '{args.pass1_path}' not found.\n"
            f"Run `python run.py --pass 1` or `python run.py --sample` first to generate Pass 1."
        )
        sys.exit(1)

    console.print(
        Panel.fit(
            "[bold cyan]Composio Toolkit Research: Automated Verification Stage[/]\n"
            f"• Auditing Evidence URLs & Quotes in [green]{args.pass1_path}[/]\n"
            f"• Running Independent Orthogonal Cross-Checks on High-Risk Fields\n"
            f"• Generating Stricter Pass 2 Re-Research for Flagged Apps",
            title="Verification Engine",
            border_style="cyan",
        )
    )

    engine = VerificationEngine(concurrency_limit=args.concurrency)
    report = await engine.verify_pass1(
        pass1_path=args.pass1_path,
        apps_csv=args.apps_csv,
        output_dir=args.output_dir,
    )

    # Print Summary Report Table
    table = Table(title="Verification Summary Report", header_style="bold magenta")
    table.add_column("Metric", style="bold white")
    table.add_column("Value", style="cyan")

    table.add_row("Total Apps Reviewed", str(report.total_apps_reviewed))
    table.add_row(
        "Rows Flagged for Revision",
        f"[bold red]{report.rows_flagged}[/]"
        if report.rows_flagged > 0
        else "[bold green]0 (100% Verified)[/]",
    )
    table.add_row("Flagged App IDs", str(report.flagged_app_ids))
    table.add_row(
        "Disagreement Rate (self_serve)",
        f"{report.per_field_disagreement_rate.get('self_serve', 0.0) * 100:.1f}%",
    )
    table.add_row(
        "Disagreement Rate (existing_mcp)",
        f"{report.per_field_disagreement_rate.get('existing_mcp', 0.0) * 100:.1f}%",
    )
    table.add_row(
        "Disagreement Rate (auth_methods)",
        f"{report.per_field_disagreement_rate.get('auth_methods', 0.0) * 100:.1f}%",
    )

    console.print("\n")
    console.print(table)

    # Print Breakdown Table
    breakdown_table = Table(
        title="Failure Types Breakdown", header_style="bold yellow"
    )
    breakdown_table.add_column("Failure Type", style="white")
    breakdown_table.add_column("Count", justify="right", style="bold red")

    for k, v in report.failure_types_breakdown.items():
        breakdown_table.add_row(k.replace("_", " ").title(), str(v))

    console.print("\n")
    console.print(breakdown_table)

    console.print(
        f"\n[bold green]✓ Artifacts generated successfully:[/]\n"
        f"  - [cyan]{args.output_dir}/pass2.json[/]\n"
        f"  - [cyan]{args.output_dir}/pass2.csv[/]\n"
        f"  - [cyan]{args.output_dir}/verification_report.json[/]\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
