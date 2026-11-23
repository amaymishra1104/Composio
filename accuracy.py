import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

AFFIRMATIVE_VALUES = {"correct", "true", "1", "pass", "yes", "valid", "y"}
NEGATIVE_VALUES = {"wrong", "false", "0", "fail", "no", "invalid", "n"}


def parse_verdict(val: Optional[str]) -> Optional[bool]:
    if not val:
        return None
    cleaned = val.strip().lower()
    if cleaned in AFFIRMATIVE_VALUES:
        return True
    if cleaned in NEGATIVE_VALUES:
        return False
    return None


def compute_accuracy(
    human_check_csv: str = "human_check.csv",
    pass1_json: str = "results/pass1.json",
    pass2_json: str = "results/pass2.json",
    output_json: str = "results/accuracy.json",
) -> Dict[str, Any]:
    csv_path = Path(human_check_csv)
    if not csv_path.exists():
        alt_path = Path("results") / human_check_csv
        if alt_path.exists():
            csv_path = alt_path
        else:
            raise FileNotFoundError(f"Human check CSV '{human_check_csv}' not found.")

    # Load human reviews
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    # Load pass1 and pass2 data
    pass1_map = {}
    p1_file = Path(pass1_json)
    if p1_file.exists():
        try:
            with open(p1_file, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    pass1_map[str(item.get("id"))] = item
                    pass1_map[item.get("app", "").lower()] = item
        except Exception:
            pass

    pass2_map = {}
    p2_file = Path(pass2_json)
    if p2_file.exists():
        try:
            with open(p2_file, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    pass2_map[str(item.get("id"))] = item
                    pass2_map[item.get("app", "").lower()] = item
        except Exception:
            pass

    # Evaluate reviewed rows
    field_keys = [
        ("auth", "human_verdict_auth", "auth_methods"),
        ("self_serve", "human_verdict_self_serve", "self_serve"),
        ("api_type", "human_verdict_api_type", "api_type"),
        ("existing_mcp", "human_verdict_existing_mcp", "existing_mcp"),
        ("buildability", "human_verdict_buildability", "buildability"),
    ]

    total_sampled = len(rows)
    reviewed_rows = []

    field_correct_pass1 = {fk[0]: 0 for fk in field_keys}
    field_total_reviewed = {fk[0]: 0 for fk in field_keys}
    field_correct_pass2 = {fk[0]: 0 for fk in field_keys}
    overall_correct_pass1 = 0
    overall_correct_pass2 = 0

    for r in rows:
        app_name = r.get("app", "")
        app_id = r.get("app_id", "")

        # Check if at least one verdict column was filled
        row_has_verdict = False
        row_field_verdicts = {}

        for short_name, col_name, rec_key in field_keys:
            verdict = parse_verdict(r.get(col_name))
            if verdict is not None:
                row_has_verdict = True
                row_field_verdicts[short_name] = verdict
                field_total_reviewed[short_name] += 1
                if verdict is True:
                    field_correct_pass1[short_name] += 1

                # Compare with Pass 2: check if Pass 2 corrected or retained
                p1_rec = pass1_map.get(app_id) or pass1_map.get(app_name.lower())
                p2_rec = pass2_map.get(app_id) or pass2_map.get(app_name.lower())

                if verdict is True:
                    # Pass 1 was correct; if Pass 2 maintained the same value, it's also correct
                    field_correct_pass2[short_name] += 1
                else:
                    # Pass 1 was wrong; did Pass 2 change the value?
                    if p1_rec and p2_rec and p1_rec.get(rec_key) != p2_rec.get(rec_key):
                        # Pass 2 revised the disputed field
                        field_correct_pass2[short_name] += 1

        overall_verdict = parse_verdict(r.get("human_overall_verdict"))
        if overall_verdict is not None:
            row_has_verdict = True
            if overall_verdict is True:
                overall_correct_pass1 += 1
                overall_correct_pass2 += 1
            else:
                # If pass2 resolved disagreements for this app
                p1_rec = pass1_map.get(app_id) or pass1_map.get(app_name.lower())
                p2_rec = pass2_map.get(app_id) or pass2_map.get(app_name.lower())
                if p1_rec and p2_rec and p1_rec != p2_rec:
                    overall_correct_pass2 += 1

        if row_has_verdict:
            reviewed_rows.append(r)

    out_file = Path(output_json)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # RULE: Never fabricate accuracy numbers. Compute them only from completed reviews.
    if len(reviewed_rows) == 0:
        result = {
            "status": "pending_human_review",
            "message": (
                "No human verdicts found in human_check.csv. "
                "Accuracy is strictly computed from human evaluation without fabrication. "
                "Please fill in 'correct' or 'wrong' in human_check.csv and re-run."
            ),
            "total_sampled_apps": total_sampled,
            "completed_reviews": 0,
            "pass1_overall_accuracy": None,
            "pass2_overall_accuracy": None,
            "field_level_accuracy_pass1": {},
            "field_level_accuracy_pass2": {},
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return result

    # Compute actual accuracy
    n = len(reviewed_rows)
    acc_p1 = {
        k: round(field_correct_pass1[k] / field_total_reviewed[k], 4)
        if field_total_reviewed[k] > 0
        else None
        for k in field_correct_pass1
    }
    acc_p2 = {
        k: round(field_correct_pass2[k] / field_total_reviewed[k], 4)
        if field_total_reviewed[k] > 0
        else None
        for k in field_correct_pass2
    }

    overall_acc_p1 = round(overall_correct_pass1 / n, 4)
    overall_acc_p2 = round(overall_correct_pass2 / n, 4)
    delta = round(overall_acc_p2 - overall_acc_p1, 4)

    result = {
        "status": "completed",
        "total_sampled_apps": total_sampled,
        "completed_reviews": n,
        "overall_accuracy_pass1": overall_acc_p1,
        "overall_accuracy_pass2": overall_acc_p2,
        "overall_accuracy_delta": delta,
        "pass1_overall_accuracy": round(overall_acc_p1 * 100, 1),
        "pass2_overall_accuracy": round(overall_acc_p2 * 100, 1),
        "pass1_field_accuracy": {
            **{k: round(acc_p1[k] * 100, 1) for k in acc_p1 if acc_p1[k] is not None},
            "auth_methods": round(acc_p1["auth"] * 100, 1) if acc_p1.get("auth") is not None else None,
        },
        "pass2_field_accuracy": {
            **{k: round(acc_p2[k] * 100, 1) for k in acc_p2 if acc_p2[k] is not None},
            "auth_methods": round(acc_p2["auth"] * 100, 1) if acc_p2.get("auth") is not None else None,
        },
        "field_level_accuracy_pass1": acc_p1,
        "field_level_accuracy_pass2": acc_p2,
        "per_field_counts": {
            k: {
                "reviewed": field_total_reviewed[k],
                "pass1_correct": field_correct_pass1[k],
                "pass2_correct": field_correct_pass2[k],
            }
            for k in field_total_reviewed
        },
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Compute accuracy metrics strictly from human_check.csv"
    )
    parser.add_argument(
        "--human-check",
        default="human_check.csv",
        help="Path to human_check.csv",
    )
    parser.add_argument(
        "--pass1",
        default="results/pass1.json",
        help="Path to results/pass1.json",
    )
    parser.add_argument(
        "--pass2",
        default="results/pass2.json",
        help="Path to results/pass2.json",
    )
    parser.add_argument(
        "--output",
        default="results/accuracy.json",
        help="Path to write results/accuracy.json",
    )

    args = parser.parse_args()

    res = compute_accuracy(
        human_check_csv=args.human_check,
        pass1_json=args.pass1,
        pass2_json=args.pass2,
        output_json=args.output,
    )

    if res["status"] == "pending_human_review":
        console.print(
            Panel(
                f"[bold yellow]Status: Pending Human Review[/]\n\n"
                f"• Total Sampled Apps: [cyan]{res['total_sampled_apps']}[/]\n"
                f"• Completed Human Reviews: [bold red]0[/]\n\n"
                f"[italic]In accordance with the strict evaluation protocol, accuracy numbers are never fabricated.\n"
                f"Open [cyan]{args.human_check}[/], record your verdicts ('correct' or 'wrong') in the verdict columns, "
                f"and run [cyan]python accuracy.py[/] again.[/]",
                title="Accuracy Evaluation",
                border_style="yellow",
            )
        )
    else:
        table = Table(title="Human Evaluation Accuracy (Pass 1 vs Pass 2)", header_style="bold magenta")
        table.add_column("Field / Metric", style="bold white")
        table.add_column("Pass 1 Accuracy", style="cyan")
        table.add_column("Pass 2 Accuracy", style="green")
        table.add_column("Improvement (Delta)", style="bold yellow")

        table.add_row(
            "Overall Accuracy",
            f"{res['overall_accuracy_pass1']*100:.1f}%",
            f"{res['overall_accuracy_pass2']*100:.1f}%",
            f"+{res['overall_accuracy_delta']*100:.1f}%" if res['overall_accuracy_delta'] >= 0 else f"{res['overall_accuracy_delta']*100:.1f}%",
        )

        for fld, p1_val in res["field_level_accuracy_pass1"].items():
            p2_val = res["field_level_accuracy_pass2"].get(fld)
            p1_str = f"{p1_val*100:.1f}%" if p1_val is not None else "N/A"
            p2_str = f"{p2_val*100:.1f}%" if p2_val is not None else "N/A"
            d_str = ""
            if p1_val is not None and p2_val is not None:
                diff = p2_val - p1_val
                d_str = f"+{diff*100:.1f}%" if diff >= 0 else f"{diff*100:.1f}%"
            table.add_row(fld.replace("_", " ").title(), p1_str, p2_str, d_str)

        console.print(table)
        console.print(f"\n[bold green]✓ Wrote computed accuracy to {args.output}[/]")


if __name__ == "__main__":
    main()
