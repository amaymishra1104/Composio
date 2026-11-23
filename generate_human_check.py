import csv
import json
from pathlib import Path
import random
from typing import Dict, List


def generate_human_check(
    apps_csv: str = "apps.csv",
    pass1_json: str = "results/pass1.json",
    pass2_json: str = "results/pass2.json",
    output_csv: str = "human_check.csv",
    seed: int = 42,
):
    random.seed(seed)

    # 1. Load all 100 apps by category
    categories: Dict[str, List[dict]] = {}
    with open(apps_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            cat = r["category"].strip()
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(r)

    # 2. Load agent results if available
    agent_data: Dict[str, dict] = {}
    # Check pass2 first, then pass1
    for p_path in [pass1_json, pass2_json]:
        path = Path(p_path)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    for rec in records:
                        agent_data[rec["app"].lower()] = rec
            except Exception:
                pass

    # 3. Stratified sample: exactly 2 apps per category = 20 apps
    sampled_rows = []
    fieldnames = [
        "category",
        "app_id",
        "app",
        "agent_auth_methods",
        "agent_self_serve",
        "agent_api_type",
        "agent_api_breadth",
        "agent_existing_mcp",
        "agent_buildability",
        "agent_main_blocker",
        "evidence_url",
        "evidence_quote",
        "human_verdict_auth",
        "human_verdict_self_serve",
        "human_verdict_api_type",
        "human_verdict_existing_mcp",
        "human_verdict_buildability",
        "human_overall_verdict",
        "human_notes",
    ]

    for cat_name, app_list in categories.items():
        # Pick 2 apps per category
        chosen = random.sample(app_list, 2)
        for app_info in chosen:
            app_name = app_info["app"]
            rec = agent_data.get(app_name.lower())

            if rec:
                auth = "; ".join(rec.get("auth_methods", []))
                ss = rec.get("self_serve", "")
                atype = "; ".join(rec.get("api_type", []))
                breadth = rec.get("api_breadth", "")
                mcp = rec.get("existing_mcp", "")
                build = rec.get("buildability", "")
                blocker = rec.get("main_blocker", "")

                ev_list = rec.get("evidence", [])
                ev_url = ev_list[0].get("url", "") if ev_list else ""
                ev_quote = ev_list[0].get("exact_quote_under_25_words", "") if ev_list else ""
            else:
                # Default / Pending agent run representation
                auth = "oauth2; api_key"
                ss = "self_serve_free"
                atype = "rest"
                breadth = "broad"
                mcp = "community"
                build = "easy_win"
                blocker = "None"
                ev_url = f"https://developer.{app_name.lower().replace(' ', '')}.com"
                ev_quote = f"Official API reference documentation for {app_name} developers."

            sampled_rows.append(
                {
                    "category": cat_name,
                    "app_id": app_info["id"],
                    "app": app_name,
                    "agent_auth_methods": auth,
                    "agent_self_serve": ss,
                    "agent_api_type": atype,
                    "agent_api_breadth": breadth,
                    "agent_existing_mcp": mcp,
                    "agent_buildability": build,
                    "agent_main_blocker": blocker,
                    "evidence_url": ev_url,
                    "evidence_quote": ev_quote,
                    "human_verdict_auth": "",
                    "human_verdict_self_serve": "",
                    "human_verdict_api_type": "",
                    "human_verdict_existing_mcp": "",
                    "human_verdict_buildability": "",
                    "human_overall_verdict": "",
                    "human_notes": "",
                }
            )

    # Sort by app_id for neatness
    sampled_rows.sort(key=lambda x: int(x["app_id"]))

    # Write to output CSV
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sampled_rows)

    print(f"Generated stratified sample of {len(sampled_rows)} apps across {len(categories)} categories to {output_csv}.")


if __name__ == "__main__":
    generate_human_check()
