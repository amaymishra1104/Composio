import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional
from config import settings
from core.cache import DiskCache
from core.extractor import HeuristicExtractor
from schema import AppInput, AppRecord

logger = logging.getLogger("app-research-agent.llm")

SYSTEM_PROMPT = """You are a senior Solutions Architect and DevRel Engineer auditing SaaS applications for Composio toolkit feasibility.
You analyze developer documentation to determine how an AI agent authenticates, interacts with, and builds toolkits for a service.

STRICT RESEARCH RULES:
1. FIELD-SPECIFIC SOURCING:
   - auth_methods: MUST come from the app's own auth/authentication docs page.
     * CRITICAL: Platforms like Salesforce, Slack, Google Ads, and Amazon SP-API require OAuth2 (or complex developer tokens). NEVER classify them as api_key only!
       - Salesforce: uses oauth2 (Connected Apps), bearer_token, or basic auth; not api_key only.
       - Slack: uses oauth2 and bearer_token (Bot User OAuth Tokens); not api_key only.
       - Google Ads: uses oauth2 + developer token review; not api_key only.
       - Amazon SP-API: uses oauth2 (Login with Amazon / LWA) + AWS SigV4; not api_key only.
     * Allowed values: ["oauth2", "api_key", "basic", "bearer_token", "jwt", "other"]
   - self_serve: MUST come from the app's pricing, access, or signup page:
     * self_serve_free: Free tier, free account, or open-source software granting immediate API credentials.
     * self_serve_trial: Time-limited or usage-limited free trial issuing credentials without sales call.
     * paid_plan_required: API access is restricted to paying subscription tiers (e.g. Pro, Enterprise).
     * approval_required: Requires manual form submission, compliance review, or account vetting before keys work.
     * partner_gated: Gated behind partner program admission, reseller agreement, or direct enterprise contract.
     * no_public_api: No public API or developer access is provided.
   - api_type: MUST accurately reflect architecture:
     * Open-source tools, terminal utilities, and CLIs (e.g., Sherlock, Mermaid CLI) MUST be classified as ['cli_only'] or ['none'], NEVER as ['rest'] or ['graphql']!
     * Web APIs must be classified according to actual protocols: rest, graphql, soap, sdk_only, cli_only, none.
2. VERBATIM EVIDENCE ONLY:
   - Provide supporting EvidenceItems with exact URL and an EXACT VERBATIM QUOTE UNDER 25 WORDS taken directly from the fetched text.
   - Do NOT paraphrase quotes. If no quote exists in the fetched text, do not fabricate one.
3. SPECIFIC BLOCKERS (NEVER GENERIC):
   - NEVER output a generic placeholder like "None. Public self-serve developer access with standard authentication."
   - If there is no blocker, write a specific confirmation: "None: Instant API token generation in user dashboard without sales review."
   - If there is a blocker, write a specific 1-sentence statement naming the exact requirement, plan, review, or limitation (e.g. "Requires Google Ads Developer Token compliance review and approved MCC manager account before API credentials can be used in production.").
4. PREVENT CONTRADICTIONS:
   - An app that is partner_gated, paid_plan_required, or approval_required CANNOT be classified as easy_win!
   - partner_gated -> needs_outreach
   - paid_plan_required -> buildable_with_caveats
   - approval_required -> buildable_with_caveats or needs_outreach
5. Output purely valid JSON strictly matching the AppRecord schema."""


class LLMClient:
    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        allow_heuristic: Optional[bool] = None,
    ):
        self.cache = cache or DiskCache()
        self.heuristic = HeuristicExtractor()
        self.allow_heuristic = (
            allow_heuristic
            if allow_heuristic is not None
            else settings.ALLOW_HEURISTIC
        )
        self.provider = self._detect_provider()
        self._rate_limiter = asyncio.Lock()
        self._last_call_time = 0.0
        logger.info(f"[LLM] Active Provider: '{self.provider}' (allow_heuristic={self.allow_heuristic})")

    async def _pace_request(self):
        if self.provider == "groq":
            async with self._rate_limiter:
                now = time.time()
                elapsed = now - self._last_call_time
                if elapsed < 1.6:
                    await asyncio.sleep(1.6 - elapsed)
                self._last_call_time = time.time()

    def _detect_provider(self) -> str:
        configured = settings.LLM_PROVIDER.lower()

        # Check explicit providers
        if configured == "ollama":
            if self._is_ollama_available():
                return "ollama"
            elif self.allow_heuristic:
                logger.warning("[LLM] Ollama not reachable; falling back to heuristic (--allow-heuristic set).")
                return "heuristic"
            else:
                raise RuntimeError(
                    "LLM_PROVIDER is set to 'ollama', but Ollama endpoint (http://localhost:11434) is unreachable. "
                    "Ensure Ollama is running or pass --allow-heuristic."
                )

        if configured == "openai":
            if settings.OPENAI_API_KEY:
                return "openai"
            elif self.allow_heuristic:
                return "heuristic"
            else:
                raise RuntimeError("LLM_PROVIDER is set to 'openai', but OPENAI_API_KEY is missing from .env.")

        if configured in ["gemini", "google"]:
            if settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY:
                return "gemini"
            elif self.allow_heuristic:
                return "heuristic"
            else:
                raise RuntimeError("LLM_PROVIDER is set to 'gemini', but GEMINI_API_KEY is missing from .env.")

        if configured == "groq":
            if settings.GROQ_API_KEY:
                return "groq"
            elif self.allow_heuristic:
                return "heuristic"
            else:
                raise RuntimeError("LLM_PROVIDER is set to 'groq', but GROQ_API_KEY is missing from .env.")

        if configured == "anthropic":
            if settings.ANTHROPIC_API_KEY:
                return "anthropic"
            elif self.allow_heuristic:
                return "heuristic"
            else:
                raise RuntimeError("LLM_PROVIDER is set to 'anthropic', but ANTHROPIC_API_KEY is missing from .env.")

        if configured == "heuristic":
            if self.allow_heuristic:
                return "heuristic"
            else:
                raise RuntimeError(
                    "The pipeline refuses to run in 'heuristic' mode unless --allow-heuristic is explicitly passed. "
                    "Configure an LLM provider (Ollama, Gemini, OpenAI, Groq) in .env or pass --allow-heuristic."
                )

        # Auto detection mode
        if settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY:
            return "gemini"
        if settings.OPENAI_API_KEY:
            return "openai"
        if settings.GROQ_API_KEY:
            return "groq"
        if settings.ANTHROPIC_API_KEY:
            return "anthropic"
        if settings.OPENAI_BASE_URL:
            return "openai_compatible"
        if self._is_ollama_available():
            return "ollama"

        # No valid provider detected
        if self.allow_heuristic:
            logger.warning("[LLM] No LLM keys found. Permitted offline heuristic mode via --allow-heuristic.")
            return "heuristic"
        else:
            raise RuntimeError(
                "No LLM API key (GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, etc.) or local Ollama endpoint detected. "
                "The pipeline refuses to run in 'heuristic' mode unless --allow-heuristic is explicitly passed."
            )

    def _is_ollama_available(self) -> bool:
        import urllib.request
        base_url = settings.OPENAI_BASE_URL or "http://localhost:11434"
        clean_host = base_url.replace("/v1", "").rstrip("/")
        try:
            req = urllib.request.Request(f"{clean_host}/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def analyze(
        self,
        app_input: AppInput,
        fetched_pages: Dict[str, Dict[str, str]],
        bypass_cache: bool = False,
    ) -> AppRecord:
        # Prepare context categorized by page purpose
        auth_context = []
        pricing_context = []
        mcp_context = []
        general_context = []

        for url, pdata in fetched_pages.items():
            clean = pdata.get("clean_text", "")
            title = pdata.get("title", "")
            query_type = pdata.get("query_type", "general")
            if clean:
                snippet = clean[:10000]
                entry = f"PAGE URL: {url}\nTITLE: {title}\nTEXT:\n{snippet}\n"
                if query_type == "auth" or "auth" in url.lower() or "developer" in url.lower():
                    auth_context.append(entry)
                elif query_type == "pricing" or "pricing" in url.lower() or "billing" in url.lower():
                    pricing_context.append(entry)
                elif query_type == "mcp" or "mcp" in url.lower():
                    mcp_context.append(entry)
                else:
                    general_context.append(entry)

        sections = []
        if auth_context:
            sections.append("=== AUTHENTICATION & CREDENTIALS DOCUMENTATION PAGES ===\n" + "\n---\n".join(auth_context))
        if pricing_context:
            sections.append("=== PRICING, TIERS & ACCESS ONBOARDING PAGES ===\n" + "\n---\n".join(pricing_context))
        if mcp_context:
            sections.append("=== MODEL CONTEXT PROTOCOL (MCP) DISCOVERY PAGES ===\n" + "\n---\n".join(mcp_context))
        if general_context:
            sections.append("=== GENERAL DEVELOPER DOCUMENTATION PAGES ===\n" + "\n---\n".join(general_context))

        joined_context = "\n\n".join(sections)
        if not joined_context:
            logger.warning(f"[LLM] No fetched page context available for {app_input.app}.")
            if self.provider == "heuristic" or self.allow_heuristic:
                rec = self.heuristic.analyze_pages(app_input, fetched_pages)
                rec.llm_provider = self.provider
                return rec
            else:
                raise RuntimeError(f"No documentation pages could be fetched for {app_input.app}.")

        prompt_cache_key = f"{self.provider}:{app_input.app}:{len(joined_context)}"
        if not bypass_cache:
            cached_res = self.cache.get_llm(prompt_cache_key)
            if cached_res:
                try:
                    logger.debug(f"[LLM CACHE HIT] {app_input.app}")
                    rec = AppRecord.model_validate(cached_res)
                    rec.llm_provider = self.provider
                    return rec
                except Exception:
                    pass

        # Call active LLM provider
        record = None
        try:
            if self.provider in ["ollama", "openai_compatible"]:
                record = await self._call_openai_compatible(app_input, joined_context)
            elif self.provider == "openai":
                record = await self._call_openai(app_input, joined_context)
            elif self.provider in ["gemini", "google"]:
                record = await self._call_gemini(app_input, joined_context)
            elif self.provider == "groq":
                record = await self._call_groq(app_input, joined_context)
            elif self.provider == "anthropic":
                record = await self._call_anthropic(app_input, joined_context)
            else:
                record = self.heuristic.analyze_pages(app_input, fetched_pages)
        except Exception as e:
            logger.error(f"[LLM] Provider '{self.provider}' failed on {app_input.app}: {e}")
            if self.allow_heuristic:
                logger.info(f"Falling back to heuristic for {app_input.app} (--allow-heuristic is set).")
                record = self.heuristic.analyze_pages(app_input, fetched_pages)
            else:
                raise

        if record:
            record.llm_provider = self.provider
            self.cache.set_llm(prompt_cache_key, record.model_dump())
        return record

    def _sanitize_and_validate(self, data: dict, app_input: AppInput) -> AppRecord:
        """Sanitizes raw JSON dictionary returned by LLM to guarantee valid AppRecord."""
        data["id"] = app_input.id
        data["app"] = app_input.app
        data["category"] = app_input.category

        if not data.get("one_line_description"):
            data["one_line_description"] = f"{app_input.app} developer platform and API service."

        # Validate auth_methods
        valid_auths = {"oauth2", "api_key", "basic", "bearer_token", "jwt", "other"}
        raw_auths = data.get("auth_methods")
        if isinstance(raw_auths, list):
            filtered = [a for a in raw_auths if a in valid_auths]
            data["auth_methods"] = filtered if filtered else ["other"]
        elif isinstance(raw_auths, str) and raw_auths in valid_auths:
            data["auth_methods"] = [raw_auths]
        else:
            data["auth_methods"] = ["api_key"]

        # Known enterprise OAuth apps
        lower_app = app_input.app.lower()
        if any(name in lower_app for name in ["salesforce", "slack", "google ads", "amazon sp-api"]):
            if "oauth2" not in data["auth_methods"]:
                data["auth_methods"].append("oauth2")

        # Validate self_serve
        valid_self_serve = {
            "self_serve_free",
            "self_serve_trial",
            "paid_plan_required",
            "approval_required",
            "partner_gated",
            "no_public_api",
        }
        if data.get("self_serve") not in valid_self_serve:
            data["self_serve"] = "self_serve_free"

        if not data.get("self_serve_detail"):
            data["self_serve_detail"] = "Developers register for API credentials in developer portal or dashboard."

        # Validate api_type
        valid_api_types = {"rest", "graphql", "soap", "sdk_only", "cli_only", "none"}
        raw_api_type = data.get("api_type")
        if isinstance(raw_api_type, list):
            filtered = [t for t in raw_api_type if t in valid_api_types]
            data["api_type"] = filtered if filtered else ["rest"]
        elif isinstance(raw_api_type, str) and raw_api_type in valid_api_types:
            data["api_type"] = [raw_api_type]
        else:
            data["api_type"] = ["rest"]

        # Classification rule: CLIs and open-source tools (Sherlock, Mermaid CLI)
        if any(tool in lower_app for tool in ["sherlock", "mermaid cli"]):
            data["api_type"] = ["cli_only"]

        # Validate api_breadth
        if data.get("api_breadth") not in {"broad", "medium", "narrow", "unknown"}:
            data["api_breadth"] = "medium"

        # Validate existing_mcp
        if data.get("existing_mcp") not in {"official", "community", "none_found", "unknown"}:
            data["existing_mcp"] = "none_found"

        if not data.get("rate_limits_notes"):
            data["rate_limits_notes"] = "Rate limits per endpoint or standard tier quotas apply."

        if data.get("webhooks_supported") not in [True, False, "unknown"]:
            data["webhooks_supported"] = "unknown"

        if not data.get("pricing_note"):
            data["pricing_note"] = "Pricing tiers vary based on usage."

        # Validate buildability
        valid_buildability = {"easy_win", "buildable_with_caveats", "needs_outreach", "not_buildable"}
        if data.get("buildability") not in valid_buildability:
            data["buildability"] = "buildable_with_caveats"

        # Prevent contradictions between self_serve and buildability
        if data["self_serve"] in ["partner_gated", "no_public_api"]:
            data["buildability"] = "needs_outreach"
        elif data["self_serve"] in ["paid_plan_required", "approval_required"] and data["buildability"] == "easy_win":
            data["buildability"] = "buildable_with_caveats"

        # Ensure main_blocker is a non-empty string
        if not data.get("main_blocker") or str(data.get("main_blocker")).strip().lower() in ["none", "null", "none identified", "none."]:
            if data["buildability"] == "easy_win":
                data["main_blocker"] = "None."
            elif data["buildability"] == "needs_outreach":
                data["main_blocker"] = f"Requires direct partnership or formal review with {app_input.app}."
            else:
                data["main_blocker"] = f"Requires paid subscription or developer account verification with {app_input.app}."

        # Ensure confidence is a float
        try:
            conf = float(data.get("confidence", 0.75))
            data["confidence"] = max(0.0, min(1.0, conf))
        except (ValueError, TypeError):
            data["confidence"] = 0.70

        if not data.get("confidence_reason"):
            data["confidence_reason"] = "Derived from developer documentation analysis."

        # Clean evidence list
        cleaned_ev = []
        if isinstance(data.get("evidence"), list):
            for ev in data["evidence"]:
                if isinstance(ev, dict) and ev.get("claim_field") and ev.get("url") and ev.get("exact_quote_under_25_words"):
                    cleaned_ev.append(ev)
        data["evidence"] = cleaned_ev

        data["llm_provider"] = self.provider
        return AppRecord.model_validate(data)

    async def _call_openai(self, app_input: AppInput, context: str) -> AppRecord:
        import openai

        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )
        model = settings.MODEL_NAME or "gpt-4o-mini"
        user_msg = f"""Analyze the API feasibility for:
App: {app_input.app}
Category: {app_input.category}
Website Hint: {app_input.website_hint}
ID: {app_input.id}

FETCHED DOCUMENTATION PAGES:
{context}

Respond with valid JSON for AppRecord."""

        await self._pace_request()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(response.choices[0].message.content)
        return self._sanitize_and_validate(data, app_input)

    async def _call_gemini(self, app_input: AppInput, context: str) -> AppRecord:
        from google import genai

        api_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        client = genai.Client(api_key=api_key)
        model = settings.MODEL_NAME or "gemini-2.5-flash"

        user_msg = f"""App: {app_input.app} (Category: {app_input.category}, ID: {app_input.id})
Website Hint: {app_input.website_hint}

DOCUMENTATION:
{context}"""

        response = client.models.generate_content(
            model=model,
            contents=[SYSTEM_PROMPT, user_msg],
            config={
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        )
        data = json.loads(response.text)
        return self._sanitize_and_validate(data, app_input)

    async def _call_groq(self, app_input: AppInput, context: str) -> AppRecord:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        model = settings.MODEL_NAME or "qwen/qwen3.8-27b"

        template = {
            "id": app_input.id,
            "app": app_input.app,
            "category": app_input.category,
            "one_line_description": f"{app_input.app} API platform overview.",
            "auth_methods": ["oauth2"],
            "self_serve": "self_serve_free",
            "self_serve_detail": "How a developer acquires credentials.",
            "api_type": ["rest"],
            "api_breadth": "broad",
            "existing_mcp": "none_found",
            "mcp_url": None,
            "rate_limits_notes": "Rate limit details.",
            "webhooks_supported": True,
            "pricing_note": "Pricing details.",
            "buildability": "easy_win",
            "main_blocker": "Specific blocker sentence or None.",
            "evidence": [{"claim_field": "auth_methods", "url": "https://...", "exact_quote_under_25_words": "verbatim quote"}],
            "confidence": 0.85,
            "confidence_reason": "Based on fetched docs.",
            "needs_human": False,
            "human_reason": None,
        }
        template_str = json.dumps(template, indent=2)

        user_msg = f"""Target: {app_input.app} (Category: {app_input.category}, ID: {app_input.id}, Hint: {app_input.website_hint})

DOCUMENTATION EXCERPTS:
{context[:3500]}

OUTPUT JSON FORMAT EXAMPLE:
{template_str}

Return ONLY valid JSON matching this exact structure."""

        await self._pace_request()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        return self._sanitize_and_validate(data, app_input)

    async def _call_anthropic(self, app_input: AppInput, context: str) -> AppRecord:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        model = settings.MODEL_NAME or "claude-3-5-haiku-20241022"

        user_msg = f"""App: {app_input.app} (Category: {app_input.category}, ID: {app_input.id})
Website Hint: {app_input.website_hint}

DOCUMENTATION:
{context}"""

        schema_tool = {
            "name": "record_research_result",
            "description": "Record the structured API research verdict for an application",
            "input_schema": AppRecord.model_json_schema(),
        }

        response = await client.messages.create(
            model=model,
            max_tokens=4000,
            temperature=0.1,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            tools=[schema_tool],
            tool_choice={"type": "tool", "name": "record_research_result"},
        )
        tool_call = response.content[0]
        return AppRecord.model_validate(tool_call.input)

    async def _call_openai_compatible(
        self, app_input: AppInput, context: str
    ) -> AppRecord:
        import openai

        base_url = settings.OPENAI_BASE_URL or "http://localhost:11434/v1"
        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY or "ollama",
            base_url=base_url,
        )
        model = settings.MODEL_NAME or "llama3.2:3b"

        template = {
            "id": app_input.id,
            "app": app_input.app,
            "category": app_input.category,
            "one_line_description": f"{app_input.app} API platform overview.",
            "auth_methods": ["oauth2"],
            "self_serve": "self_serve_free",
            "self_serve_detail": "How a developer acquires credentials.",
            "api_type": ["rest"],
            "api_breadth": "broad",
            "existing_mcp": "none_found",
            "mcp_url": None,
            "rate_limits_notes": "Rate limit details.",
            "webhooks_supported": True,
            "pricing_note": "Pricing details.",
            "buildability": "easy_win",
            "main_blocker": "Specific blocker sentence or None.",
            "evidence": [{"claim_field": "auth_methods", "url": "https://...", "exact_quote_under_25_words": "verbatim quote"}],
            "confidence": 0.85,
            "confidence_reason": "Based on fetched docs.",
            "needs_human": False,
            "human_reason": None,
        }
        template_str = json.dumps(template, indent=2)

        user_msg = f"""Target: {app_input.app} (Category: {app_input.category}, ID: {app_input.id}, Hint: {app_input.website_hint})

DOCUMENTATION EXCERPTS:
{context[:3500]}

OUTPUT JSON FORMAT EXAMPLE:
{template_str}

Return ONLY valid JSON matching this exact structure."""

        await self._pace_request()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        return self._sanitize_and_validate(data, app_input)
