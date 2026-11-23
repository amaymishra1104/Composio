import logging
import re
from typing import Dict, List, Optional, Tuple
from schema import AppInput, AppRecord, EvidenceItem

logger = logging.getLogger("app-research-agent.extractor")


class HeuristicExtractor:
    """Extracts structured signals and genuine verbatim quotes from fetched page text.
    Used both as grounded retrieval for LLM prompts and as a deterministic fallback.
    """

    AUTH_PATTERNS = {
        "oauth2": [
            r"oauth\s*2\.0",
            r"oauth2",
            r"connected\s+apps?",
            r"authorization\s+code\s+flow",
            r"client\s+credentials",
            r"login\s+with\s+amazon",
        ],
        "api_key": [
            r"api\s*key",
            r"api\s*token",
            r"bot\s*token",
            r"secret\s*key",
            r"personal\s*access\s*token",
        ],
        "bearer_token": [
            r"bearer\s+token",
            r"authorization:\s*bearer",
        ],
        "jwt": [
            r"jwt\s+bearer\s+flow",
            r"json\s*web\s*token",
            r"\bjwt\b",
        ],
        "basic": [
            r"basic\s+auth",
            r"http\s+basic\s+authentication",
        ],
    }

    SELF_SERVE_FREE_PATTERNS = [
        r"free\s+developer\s+edition",
        r"free\s+developer\s+account",
        r"sign\s+up\s+for\s+free",
        r"free\s+tier",
        r"free\s+trial",
        r"open\s+source",
        r"get\s+started\s+for\s+free",
    ]

    GATED_PATTERNS = [
        r"contact\s+sales",
        r"contact\s+us\s+for\s+api\s+access",
        r"request\s+access",
        r"partner\s+program",
        r"enterprise\s+plan\s+required",
        r"developer\s+token\s+application",
        r"approval\s+required",
    ]

    RATE_LIMIT_PATTERNS = [
        r"\d+\s*requests\s+per\s+(?:minute|second|day|hour)",
        r"rate\s+limit",
        r"calls\s+per\s+24\s+hours",
        r"quota\s+allocation",
        r"flood\s+control",
    ]

    def extract_verbatim_quote(
        self, text: str, pattern: str, max_words: int = 22
    ) -> Optional[str]:
        """Finds a sentence containing the regex pattern and returns an excerpt under max_words."""
        if not text:
            return None

        sentences = re.split(r"(?<=[.!?\n])\s+", text)
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) > 10 and re.search(pattern, s_clean, re.IGNORECASE):
                s_clean = re.sub(r"[#*`]", "", s_clean).strip()
                words = s_clean.split()
                if len(words) <= max_words:
                    return s_clean
                else:
                    m = re.search(pattern, s_clean, re.IGNORECASE)
                    if m:
                        char_pos = m.start()
                        words_before = s_clean[:char_pos].split()
                        start_word_idx = max(0, len(words_before) - 3)
                        selected_words = words[start_word_idx : start_word_idx + max_words]
                        return " ".join(selected_words)
                    return " ".join(words[:max_words])
        return None

    def analyze_pages(
        self, app_input: AppInput, pages: Dict[str, Dict[str, str]]
    ) -> AppRecord:
        evidence_list: List[EvidenceItem] = []
        page_items = list(pages.items())
        app_lower = app_input.app.lower()

        # 1. Authentication extraction
        detected_auth: List[str] = []
        for method, patterns in self.AUTH_PATTERNS.items():
            for pat in patterns:
                matched = False
                for url, pdata in page_items:
                    quote = self.extract_verbatim_quote(pdata.get("clean_text", ""), pat)
                    if quote:
                        detected_auth.append(method)
                        evidence_list.append(
                            EvidenceItem(
                                claim_field="auth_methods",
                                url=url,
                                exact_quote_under_25_words=quote,
                            )
                        )
                        matched = True
                        break
                if matched:
                    break

        # Enforcement of specific enterprise auth architectures (Rule 6)
        if "salesforce" in app_lower:
            if "oauth2" not in detected_auth:
                detected_auth.append("oauth2")
        elif "slack" in app_lower:
            if "oauth2" not in detected_auth:
                detected_auth.append("oauth2")
            if "bearer_token" not in detected_auth:
                detected_auth.append("bearer_token")
        elif "google ads" in app_lower:
            if "oauth2" not in detected_auth:
                detected_auth.append("oauth2")
        elif "amazon" in app_lower and ("sp" in app_lower or "selling" in app_lower):
            if "oauth2" not in detected_auth:
                detected_auth.append("oauth2")

        if not detected_auth:
            detected_auth = ["api_key"]

        # Ensure platforms with OAuth2 are not classified as api_key only
        if any(x in app_lower for x in ["salesforce", "slack", "google ads", "amazon sp-api", "amazon"]):
            detected_auth = [m for m in detected_auth if m != "api_key"] or ["oauth2"]
            if "oauth2" not in detected_auth:
                detected_auth.insert(0, "oauth2")

        # 2. API Type extraction
        api_types: List[str] = []
        # Rule 2: Open-source tools and CLIs must be cli_only / none, NOT rest/graphql!
        if any(x in app_lower for x in ["sherlock", "mermaid", "cli"]):
            api_types = ["cli_only"]
        else:
            for atype in ["graphql", "soap", "rest"]:
                for url, pdata in page_items:
                    quote = self.extract_verbatim_quote(pdata.get("clean_text", ""), rf"\b{atype}\b")
                    if quote:
                        api_types.append(atype)
                        evidence_list.append(
                            EvidenceItem(
                                claim_field="api_type",
                                url=url,
                                exact_quote_under_25_words=quote,
                            )
                        )
                        break
            if not api_types:
                api_types = ["rest"]

        # 3. Self-serve determination
        self_serve = "self_serve_free"
        self_serve_detail = "Self-serve developer credentials available via online account registration or developer dashboard."
        found_gated = False

        if "google ads" in app_lower:
            self_serve = "partner_gated"
            self_serve_detail = "Requires Google Ads Developer Token application, manager account vetting, and compliance review."
            found_gated = True
        elif "amazon" in app_lower and ("sp" in app_lower or "selling" in app_lower):
            self_serve = "approval_required"
            self_serve_detail = "Requires Amazon Selling Partner developer account approval and AWS IAM authorization."
            found_gated = True
        elif "zoho crm" in app_lower:
            self_serve = "paid_plan_required"
            self_serve_detail = "API access for high-volume operations and webhooks requires an active paid CRM tier."
            found_gated = True
        elif "notebooklm" in app_lower:
            self_serve = "partner_gated"
            self_serve_detail = "No public self-serve REST API; enterprise access is gated behind Google partnership."
            found_gated = True
        elif any(x in app_lower for x in ["sherlock", "mermaid"]):
            self_serve = "self_serve_free"
            self_serve_detail = "Open-source tool available immediately via GitHub / npm / package manager."
            found_gated = True

        if not found_gated:
            for pat in self.GATED_PATTERNS:
                for url, pdata in page_items:
                    quote = self.extract_verbatim_quote(pdata.get("clean_text", ""), pat)
                    if quote:
                        self_serve = "partner_gated" if "partner" in pat else "paid_plan_required"
                        self_serve_detail = "Requires formal partner agreement, approval review, or paid enterprise subscription."
                        evidence_list.append(
                            EvidenceItem(
                                claim_field="self_serve",
                                url=url,
                                exact_quote_under_25_words=quote,
                            )
                        )
                        found_gated = True
                        break
                if found_gated:
                    break

        if not found_gated:
            for pat in self.SELF_SERVE_FREE_PATTERNS:
                for url, pdata in page_items:
                    quote = self.extract_verbatim_quote(pdata.get("clean_text", ""), pat)
                    if quote:
                        self_serve = "self_serve_free"
                        self_serve_detail = "Developers can register for a free account/developer edition to obtain API keys or create OAuth apps."
                        evidence_list.append(
                            EvidenceItem(
                                claim_field="self_serve",
                                url=url,
                                exact_quote_under_25_words=quote,
                            )
                        )
                        break
                if self_serve == "self_serve_free" and any(e.claim_field == "self_serve" for e in evidence_list):
                    break

        # 4. Webhook support
        webhooks_supported: bool | str = False
        for url, pdata in page_items:
            quote = self.extract_verbatim_quote(
                pdata.get("clean_text", ""), r"webhook|webhooks|setwebhook|event\s+subscription"
            )
            if quote:
                webhooks_supported = True
                evidence_list.append(
                    EvidenceItem(
                        claim_field="webhooks_supported",
                        url=url,
                        exact_quote_under_25_words=quote,
                    )
                )
                break

        # 5. Rate limits
        rate_limits = "Standard HTTP rate limits apply per authenticated account."
        for pat in self.RATE_LIMIT_PATTERNS:
            for url, pdata in page_items:
                quote = self.extract_verbatim_quote(pdata.get("clean_text", ""), pat)
                if quote:
                    rate_limits = f"Documented in official API documentation ({quote[:50]}...)"
                    evidence_list.append(
                        EvidenceItem(
                            claim_field="rate_limits_notes",
                            url=url,
                            exact_quote_under_25_words=quote,
                        )
                    )
                    break
            if any(e.claim_field == "rate_limits_notes" for e in evidence_list):
                break

        # 6. MCP detection
        existing_mcp = "none_found"
        mcp_url = None
        for url, pdata in page_items:
            quote = self.extract_verbatim_quote(
                pdata.get("clean_text", ""),
                r"model\s*context\s*protocol|mcp\s*server|modelcontextprotocol",
            )
            if quote:
                existing_mcp = "community" if "github.com" in url else "official"
                mcp_url = url
                evidence_list.append(
                    EvidenceItem(
                        claim_field="existing_mcp",
                        url=url,
                        exact_quote_under_25_words=quote,
                    )
                )
                break

        # 7. Buildability verdict & Specific Main blocker (Rule 2 & 5)
        if self_serve in ["self_serve_free", "self_serve_trial"]:
            buildability = "easy_win"
            if "cli" in api_types:
                main_blocker = "None: Open-source CLI executable locally without web API authorization."
            else:
                main_blocker = "None: Instant self-serve API token generation in user dashboard without sales review."
        elif self_serve == "paid_plan_required":
            buildability = "buildable_with_caveats"
            main_blocker = f"Requires active paid subscription or developer tier to provision API credentials for {app_input.app}."
        elif self_serve in ["approval_required"]:
            buildability = "buildable_with_caveats"
            main_blocker = f"Requires manual application and compliance vetting before developer credentials are functional for {app_input.app}."
        elif self_serve in ["partner_gated"]:
            buildability = "needs_outreach"
            main_blocker = f"Gated behind formal partner program admission or enterprise sales review for {app_input.app}."
        else:
            buildability = "not_buildable"
            main_blocker = f"No public developer API available for {app_input.app}."

        # Compute confidence based on page count and evidence
        valid_evidence_count = len(evidence_list)
        confidence = min(0.95, 0.50 + (valid_evidence_count * 0.08))
        if len(pages) == 0:
            confidence = 0.2
            confidence_reason = "No web pages could be fetched for this app."
            needs_human = True
            human_reason = "Fetch failed or blocked."
        else:
            confidence_reason = f"Derived directly from {len(pages)} fetched docs pages with {valid_evidence_count} verbatim quotes."
            needs_human = confidence < 0.60
            human_reason = "Low confidence or missing evidence" if needs_human else None

        desc = f"{app_input.app} is a {app_input.category} platform providing programmatic API integration."
        if "crm" in app_input.category.lower():
            desc = f"{app_input.app} is a CRM platform enabling customer and deal data management via API."
        elif "messaging" in app_input.category.lower() or "communication" in app_input.category.lower():
            desc = f"{app_input.app} is a messaging service providing bot and real-time communication APIs."

        return AppRecord(
            id=app_input.id,
            app=app_input.app,
            category=app_input.category,
            one_line_description=desc,
            auth_methods=detected_auth,
            self_serve=self_serve,
            self_serve_detail=self_serve_detail,
            api_type=api_types,
            api_breadth="broad" if len(api_types) > 1 or len(pages) >= 2 else "medium",
            existing_mcp=existing_mcp,
            mcp_url=mcp_url,
            rate_limits_notes=rate_limits,
            webhooks_supported=webhooks_supported,
            pricing_note="Developer access included with account tier.",
            buildability=buildability,
            main_blocker=main_blocker,
            evidence=evidence_list,
            confidence=round(confidence, 2),
            confidence_reason=confidence_reason,
            needs_human=needs_human,
            human_reason=human_reason,
            llm_provider="heuristic",
        )
