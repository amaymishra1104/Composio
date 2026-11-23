import pytest
from core.verifier import EvidenceVerifier
from schema import AppRecord, EvidenceItem


def test_verifier_exact_match():
    verifier = EvidenceVerifier()
    text = "To access the API, developers must sign up for a free developer account at our portal."
    quote = "sign up for a free developer account"
    match = verifier.find_in_text(quote, text)
    assert match is not None
    assert "developer account" in match


def test_verifier_record_pass():
    verifier = EvidenceVerifier()
    record = AppRecord(
        id=27,
        app="Telegram",
        category="Communications and Messaging",
        one_line_description="Messaging platform with bot API",
        auth_methods=["api_key"],
        self_serve="self_serve_free",
        self_serve_detail="Create a bot with @BotFather to get your token.",
        api_type=["rest"],
        api_breadth="broad",
        existing_mcp="community",
        rate_limits_notes="30 messages per second",
        webhooks_supported=True,
        pricing_note="Free bot API",
        buildability="easy_win",
        main_blocker="None",
        evidence=[
            EvidenceItem(
                claim_field="auth_methods",
                url="https://core.telegram.org/bots/api",
                exact_quote_under_25_words="The Bot API allows you to easily build programs.",
            )
        ],
        confidence=0.9,
        confidence_reason="Official docs",
        needs_human=False,
    )
    pages = {
        "https://core.telegram.org/bots/api": {
            "clean_text": "Welcome to Telegram! The Bot API allows you to easily build programs that use Telegram messages."
        }
    }
    updated_record, pass_rate = verifier.verify_record(record, pages)
    assert pass_rate == 1.0
    assert len(updated_record.evidence) == 1
    assert not updated_record.needs_human
