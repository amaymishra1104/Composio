import csv
import json
import tempfile
from pathlib import Path
import pytest
from accuracy import compute_accuracy


def test_accuracy_empty_human_check():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_csv = Path(tmpdir) / "human_check.csv"
        tmp_out = Path(tmpdir) / "accuracy.json"

        # Create human_check with empty verdicts
        with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "category": "CRM and Sales",
                    "app_id": "1",
                    "app": "Salesforce",
                    "agent_auth_methods": "api_key",
                    "agent_self_serve": "self_serve_free",
                    "agent_api_type": "rest",
                    "agent_api_breadth": "broad",
                    "agent_existing_mcp": "official",
                    "agent_buildability": "easy_win",
                    "agent_main_blocker": "None",
                    "evidence_url": "https://developer.salesforce.com",
                    "evidence_quote": "REST API docs",
                    "human_verdict_auth": "",
                    "human_verdict_self_serve": "",
                    "human_verdict_api_type": "",
                    "human_verdict_existing_mcp": "",
                    "human_verdict_buildability": "",
                    "human_overall_verdict": "",
                    "human_notes": "",
                }
            )

        res = compute_accuracy(
            human_check_csv=str(tmp_csv),
            pass1_json="results/pass1.json",
            pass2_json="results/pass2.json",
            output_json=str(tmp_out),
        )

        assert res["status"] == "pending_human_review"
        assert res["completed_reviews"] == 0
        assert res["pass1_overall_accuracy"] is None


def test_accuracy_filled_human_check():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_csv = Path(tmpdir) / "human_check.csv"
        tmp_out = Path(tmpdir) / "accuracy.json"

        # Create human_check with filled verdicts
        with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "category": "CRM and Sales",
                    "app_id": "1",
                    "app": "Salesforce",
                    "agent_auth_methods": "api_key",
                    "agent_self_serve": "self_serve_free",
                    "agent_api_type": "rest",
                    "agent_api_breadth": "broad",
                    "agent_existing_mcp": "official",
                    "agent_buildability": "easy_win",
                    "agent_main_blocker": "None",
                    "evidence_url": "https://developer.salesforce.com",
                    "evidence_quote": "REST API docs",
                    "human_verdict_auth": "correct",
                    "human_verdict_self_serve": "correct",
                    "human_verdict_api_type": "correct",
                    "human_verdict_existing_mcp": "correct",
                    "human_verdict_buildability": "correct",
                    "human_overall_verdict": "correct",
                    "human_notes": "All verified against official docs",
                }
            )

        res = compute_accuracy(
            human_check_csv=str(tmp_csv),
            pass1_json="results/pass1.json",
            pass2_json="results/pass2.json",
            output_json=str(tmp_out),
        )

        assert res["status"] == "completed"
        assert res["completed_reviews"] == 1
        assert res["overall_accuracy_pass1"] == 1.0
        assert res["field_level_accuracy_pass1"]["auth"] == 1.0
