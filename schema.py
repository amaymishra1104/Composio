from typing import List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

AuthMethod = Literal["oauth2", "api_key", "basic", "bearer_token", "jwt", "other"]

SelfServeStatus = Literal[
    "self_serve_free",
    "self_serve_trial",
    "paid_plan_required",
    "approval_required",
    "partner_gated",
    "no_public_api",
]

ApiType = Literal["rest", "graphql", "soap", "sdk_only", "cli_only", "none"]

ApiBreadth = Literal["broad", "medium", "narrow", "unknown"]

ExistingMcp = Literal["official", "community", "none_found", "unknown"]

Buildability = Literal[
    "easy_win",
    "buildable_with_caveats",
    "needs_outreach",
    "not_buildable",
]


class EvidenceItem(BaseModel):
    claim_field: str = Field(
        description="The field this claim supports (e.g. auth_methods, self_serve, api_type, rate_limits_notes, webhooks_supported)"
    )
    url: str = Field(description="URL of the documentation page from which the quote was taken")
    exact_quote_under_25_words: str = Field(
        description="Verbatim quote from the page supporting the claim, strictly under 25 words"
    )

    @field_validator("exact_quote_under_25_words")
    @classmethod
    def validate_quote_length(cls, v: str) -> str:
        words = v.strip().split()
        if len(words) > 25:
            # Cleanly truncate to 25 words to enforce schema constraint
            return " ".join(words[:25])
        return v.strip()


class AppRecord(BaseModel):
    id: int
    app: str
    category: str
    one_line_description: str
    auth_methods: List[AuthMethod] = Field(
        default_factory=list,
        description="List of supported authentication methods: oauth2, api_key, basic, bearer_token, jwt, other",
    )
    self_serve: SelfServeStatus = Field(
        description="Developer access path: self_serve_free, self_serve_trial, paid_plan_required, approval_required, partner_gated, no_public_api"
    )
    self_serve_detail: str = Field(
        description="1 sentence explaining exactly how a developer acquires credentials"
    )
    api_type: List[ApiType] = Field(
        default_factory=list,
        description="List of API architectures: rest, graphql, soap, sdk_only, cli_only, none",
    )
    api_breadth: ApiBreadth = Field(
        description="Breadth of API surface: broad, medium, narrow, unknown"
    )
    existing_mcp: ExistingMcp = Field(
        description="Existing Model Context Protocol server: official, community, none_found, unknown"
    )
    mcp_url: Optional[str] = Field(
        default=None, description="URL of official or community MCP repository or package if found"
    )
    rate_limits_notes: str = Field(
        description="Documented rate limits or quota details (e.g. '10,000 req/day per org')"
    )
    webhooks_supported: Union[bool, Literal["unknown"]] = Field(
        description="Whether webhooks / push events are supported: true, false, or 'unknown'"
    )
    pricing_note: str = Field(
        description="Summary of pricing impact on API access (e.g. 'Free tier includes 100 req/min; Pro plan at $29/mo')"
    )
    buildability: Buildability = Field(
        description="Verdict for Composio toolkit feasibility: easy_win, buildable_with_caveats, needs_outreach, not_buildable"
    )
    main_blocker: str = Field(
        description="1 sentence stating the primary obstacle to building an agent toolkit, or 'None' if easy_win"
    )
    evidence: List[EvidenceItem] = Field(
        default_factory=list,
        description="List of supporting evidence items linking claims to fetched URLs and exact quotes",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0 based on evidence verifiability and completeness",
    )
    confidence_reason: str = Field(
        description="Explanation for the confidence score (e.g. 'All claims verified against official Salesforce Developer Docs')"
    )
    needs_human: bool = Field(
        default=False,
        description="Flagged true if confidence < threshold or conflicting documentation requires manual human triage",
    )
    human_reason: Optional[str] = Field(
        default=None,
        description="Reason why human verification is required if needs_human is true",
    )

    @field_validator("auth_methods")
    @classmethod
    def validate_auth_methods(cls, v: List[str]) -> List[str]:
        valid = {"oauth2", "api_key", "basic", "bearer_token", "jwt", "other"}
        return [m.lower() for m in v if m.lower() in valid] or ["other"]

    @field_validator("api_type")
    @classmethod
    def validate_api_type(cls, v: List[str]) -> List[str]:
        valid = {"rest", "graphql", "soap", "sdk_only", "cli_only", "none"}
        return [t.lower() for t in v if t.lower() in valid] or ["rest"]


class AppInput(BaseModel):
    id: int
    app: str
    category: str
    website_hint: str
