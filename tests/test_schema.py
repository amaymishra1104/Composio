import pytest
from schema import AppRecord, EvidenceItem


def test_evidence_item_word_count_enforcement():
    long_quote = "This is a quote with way more than twenty-five words that needs to be cleanly truncated so that the pydantic model validator does not let excessive tokens slip through."
    item = EvidenceItem(
        claim_field="auth_methods",
        url="https://example.com/docs",
        exact_quote_under_25_words=long_quote,
    )
    words = item.exact_quote_under_25_words.split()
    assert len(words) <= 25


def test_app_record_validations():
    record = AppRecord(
        id=1,
        app="Salesforce",
        category="CRM and Sales",
        one_line_description="CRM platform with REST APIs",
        auth_methods=["oauth2", "jwt"],
        self_serve="self_serve_free",
        self_serve_detail="Free developer edition available.",
        api_type=["rest", "graphql"],
        api_breadth="broad",
        existing_mcp="official",
        mcp_url="https://github.com/salesforce/mcp",
        rate_limits_notes="15,000 calls per 24 hours per org",
        webhooks_supported=True,
        pricing_note="Developer Edition is free.",
        buildability="easy_win",
        main_blocker="None",
        evidence=[
            EvidenceItem(
                claim_field="auth_methods",
                url="https://developer.salesforce.com/docs/auth",
                exact_quote_under_25_words="Salesforce uses OAuth 2.0 authorization framework.",
            )
        ],
        confidence=0.95,
        confidence_reason="Verified against official docs.",
        needs_human=False,
    )
    assert record.id == 1
    assert "oauth2" in record.auth_methods
    assert record.confidence == 0.95
