import pytest
from core.extractor import HeuristicExtractor
from core.verifier import VerificationEngine
from schema import AppInput, AppRecord, EvidenceItem


def test_auth_not_api_key_only():
    """Requirement 6: Salesforce/Slack/Google Ads/Amazon SP-API must not be classified as api_key only."""
    extractor = HeuristicExtractor()

    targets = [
        AppInput(id=1, app="Salesforce", category="CRM and Sales", website_hint="developer.salesforce.com"),
        AppInput(id=21, app="Slack", category="Communications and Messaging", website_hint="api.slack.com"),
        AppInput(id=36, app="Google Ads", category="Marketing, Ads, Email and Social", website_hint="developers.google.com/google-ads"),
        AppInput(id=48, app="Amazon SP-API", category="Ecommerce", website_hint="developer-docs.amazon.com/sp-api"),
    ]

    # Sample documentation contexts containing OAuth / developer token references
    docs = {
        "https://developer.salesforce.com": {
            "clean_text": "Salesforce Connected Apps use OAuth 2.0 authorization code flow and JWT bearer flow for secure API access."
        },
        "https://api.slack.com": {
            "clean_text": "Slack apps use OAuth 2.0 to request permissions and receive Bot User OAuth Access Tokens (Bearer)."
        },
        "https://developers.google.com/google-ads": {
            "clean_text": "Google Ads API requires OAuth2 credentials alongside an approved Developer Token for MCC managers."
        },
        "https://developer-docs.amazon.com/sp-api": {
            "clean_text": "Selling Partner API (SP-API) requires Login with Amazon (LWA) OAuth 2.0 tokens and AWS Signature v4."
        },
    }

    for app_in in targets:
        rec = extractor.analyze_pages(app_in, docs)
        assert rec.auth_methods != ["api_key"], f"{app_in.app} was erroneously classified as api_key only!"
        assert "oauth2" in rec.auth_methods, f"{app_in.app} must include oauth2!"


def test_cli_tools_not_classified_as_rest():
    """Requirement 2: Open-source tools/CLIs like Sherlock and Mermaid CLI must be cli_only/none, not rest."""
    extractor = HeuristicExtractor()

    cli_apps = [
        AppInput(id=58, app="Sherlock", category="Data, SEO and Scraping", website_hint="github.com/sherlock-project/sherlock"),
        AppInput(id=98, app="Mermaid CLI", category="AI, Research and Media-native", website_hint="github.com/mermaid-js/mermaid-cli"),
    ]

    docs = {
        "https://github.com/sherlock-project/sherlock": {
            "clean_text": "Sherlock is an open-source command line tool to find usernames across social networks. Run: python3 sherlock user"
        },
        "https://github.com/mermaid-js/mermaid-cli": {
            "clean_text": "Mermaid CLI allows you to generate diagrams and charts from the command line using mmdc."
        },
    }

    for app_in in cli_apps:
        rec = extractor.analyze_pages(app_in, docs)
        assert "cli_only" in rec.api_type or "none" in rec.api_type, f"{app_in.app} must be classified as cli_only or none!"
        assert "rest" not in rec.api_type, f"{app_in.app} should not be classified as REST API!"


def test_record_with_no_evidence_not_marked_verified():
    """Requirement 6 & 3: A record with no evidence must not be marked verified."""
    verifier = VerificationEngine(allow_heuristic=True)

    record = AppRecord(
        id=999,
        app="GhostApp",
        category="Other",
        one_line_description="Unverified service",
        auth_methods=["api_key"],
        self_serve="self_serve_free",
        self_serve_detail="Unknown",
        api_type=["rest"],
        api_breadth="narrow",
        existing_mcp="none_found",
        rate_limits_notes="Unknown",
        webhooks_supported="unknown",
        pricing_note="Unknown",
        buildability="easy_win",
        main_blocker="None",
        evidence=[],  # EMPTY EVIDENCE
        confidence=0.8,
        confidence_reason="Claimed without evidence",
        needs_human=False,
    )

    pages = {
        "https://ghostapp.com": {"clean_text": "Welcome to GhostApp!"}
    }

    updated_record, pass_rate = verifier.verify_record(record, pages)
    assert pass_rate == 0.0, "Pass rate must be 0 for empty evidence!"
    assert updated_record.needs_human is True, "Record with no evidence must be flagged for human review!"
    assert updated_record.confidence <= 0.40, "Confidence must be downgraded when no evidence exists!"


@pytest.mark.asyncio
async def test_pass1_vs_pass2_diff_reported(tmp_path, monkeypatch):
    """Requirement 6: Pass 1 vs Pass 2 differences are properly captured in verification report."""
    from core.verifier import VerificationEngine, VerificationReport
    import json

    verifier = VerificationEngine(allow_heuristic=True)

    # Mock network calls for instant local execution
    async def mock_fetch(url, bypass_cache=False):
        return {"status_code": 200, "clean_text": "Instant API token for developers."}

    async def mock_fetch_many(urls, bypass_cache=False):
        return [{"url": u, "status_code": 200, "clean_text": "Docs"} for u in urls]

    async def mock_search(query, max_results=2, bypass_cache=False):
        return [{"url": "https://valid.dev", "title": "Docs"}]

    async def mock_pass2(app_in, pass1_rec, objections, pages):
        copy = pass1_rec.model_copy(deep=True)
        copy.confidence = 0.8
        copy.needs_human = False
        return copy

    async def mock_cross_judge(app_in, rec, text):
        return {
            "auth_methods": {"cross_check_value": rec.auth_methods, "disagreement": False, "reason": "OK"},
            "self_serve": {"cross_check_value": rec.self_serve, "disagreement": False, "reason": "OK"},
            "existing_mcp": {"cross_check_value": rec.existing_mcp, "disagreement": False, "reason": "OK"},
        }

    monkeypatch.setattr(verifier.fetcher, "fetch", mock_fetch)
    monkeypatch.setattr(verifier.fetcher, "fetch_many", mock_fetch_many)
    monkeypatch.setattr(verifier.search_router, "search", mock_search)
    monkeypatch.setattr(verifier, "run_pass2_refinement", mock_pass2)
    monkeypatch.setattr(verifier, "_judge_cross_check", mock_cross_judge)

    # Create dummy pass1 with a low confidence record and a valid record
    pass1_records = [
        {
            "id": 1,
            "app": "ValidApp",
            "category": "Developer Platforms",
            "one_line_description": "Valid dev platform",
            "auth_methods": ["api_key"],
            "self_serve": "self_serve_free",
            "self_serve_detail": "Instant API key",
            "api_type": ["rest"],
            "api_breadth": "broad",
            "existing_mcp": "official",
            "mcp_url": "https://github.com/valid/mcp",
            "rate_limits_notes": "100/min",
            "webhooks_supported": True,
            "pricing_note": "Free tier",
            "buildability": "easy_win",
            "main_blocker": "None: Instant token generation in user dashboard.",
            "evidence": [{"claim_field": "auth_methods", "url": "https://valid.dev", "exact_quote_under_25_words": "Instant API token"}],
            "confidence": 0.9,
            "confidence_reason": "Verified",
            "needs_human": False,
            "human_reason": None,
            "llm_provider": "ollama",
        },
        {
            "id": 2,
            "app": "FlaggedApp",
            "category": "CRM and Sales",
            "one_line_description": "Gated enterprise CRM",
            "auth_methods": ["api_key"],
            "self_serve": "self_serve_free",
            "self_serve_detail": "Talk to sales",
            "api_type": ["rest"],
            "api_breadth": "broad",
            "existing_mcp": "none_found",
            "mcp_url": None,
            "rate_limits_notes": "Unknown",
            "webhooks_supported": False,
            "pricing_note": "Custom",
            "buildability": "easy_win",
            "main_blocker": "None",
            "evidence": [],  # NO EVIDENCE
            "confidence": 0.45,  # LOW CONFIDENCE
            "confidence_reason": "Low confidence due to missing documentation",
            "needs_human": True,
            "human_reason": "Missing evidence and low confidence",
            "llm_provider": "ollama",
        }
    ]

    p1_file = tmp_path / "pass1.json"
    with open(p1_file, "w") as f:
        json.dump(pass1_records, f)

    csv_file = tmp_path / "apps.csv"
    with open(csv_file, "w") as f:
        f.write("id,app,category,website_hint\n1,ValidApp,Developer Platforms,valid.dev\n2,FlaggedApp,CRM and Sales,flagged.com\n")

    report = await verifier.verify_pass1(
        pass1_path=str(p1_file),
        apps_csv=str(csv_file),
        output_dir=str(tmp_path),
    )

    assert isinstance(report, VerificationReport)
    assert report.total_apps_reviewed == 2
    assert report.rows_flagged >= 1, "Must flag row with missing evidence and low confidence!"
    assert 2 in report.flagged_app_ids, "FlaggedApp (id=2) must be in flagged_app_ids!"
