import json
import logging
import os
import re
from typing import Dict, List, Optional
from config import settings
from core.cache import DiskCache
from core.extractor import HeuristicExtractor
from schema import AppInput, AppRecord

logger = logging.getLogger("app-research-agent.llm")

SYSTEM_PROMPT = """You are a senior Developer Relations and Solutions Architect researching APIs for Composio toolkit feasibility.
You analyze developer documentation to determine how an AI agent can authenticate, interact with, and call tools against a service.

CRITICAL RULES:
1. GROUNDED IN EVIDENCE ONLY: Fetch the facts directly from the provided fetched page texts. DO NOT guess, hallucinate, or recall from pre-training memory if not confirmed in the text.
2. EVIDENCE REQUIREMENT: For claims (auth_methods, self_serve, api_type, rate_limits_notes, webhooks_supported, existing_mcp), provide an EvidenceItem with the exact page URL and an EXACT VERBATIM QUOTE under 25 words found in that page text.
3. UNKNOWN OVER GUESSING: If the fetched documentation does not state a detail, use "unknown" or state "Not documented in fetched pages".
4. SELF-SERVE CLASSIFICATION:
   - self_serve_free: Free account/tier or open-source gives immediate API credentials.
   - self_serve_trial: Free trial provides API credentials without talking to sales.
   - paid_plan_required: API requires an upgraded/paid subscription plan.
   - approval_required: Form or manual review required before credentials issued.
   - partner_gated: Must be an approved partner or contact enterprise sales.
   - no_public_api: No public API exists.
5. BUILDABILITY VERDICT:
   - easy_win: Self-serve free/trial + public REST/GraphQL + clear auth.
   - buildable_with_caveats: Requires paid plan, complex signature/OAuth app review, or tricky rate limits.
   - needs_outreach: Partner-gated, contact sales, or enterprise approval required.
   - not_buildable: No public API or prohibited by ToS.
6. Return purely valid JSON strictly matching the requested schema.
"""


class LLMClient:
    def __init__(self, cache: Optional[DiskCache] = None):
        self.cache = cache or DiskCache()
        self.heuristic = HeuristicExtractor()
        self.provider = self._detect_provider()

    def _detect_provider(self) -> str:
        configured = settings.LLM_PROVIDER.lower()
        if configured != "auto":
            return configured

        if settings.OPENAI_API_KEY:
            return "openai"
        if settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY:
            return "gemini"
        if settings.GROQ_API_KEY:
            return "groq"
        if settings.ANTHROPIC_API_KEY:
            return "anthropic"
        if settings.OPENAI_BASE_URL:
            return "openai_compatible"

        logger.info("[LLM] No API key detected. Using Heuristic/Verbatim extractor engine.")
        return "heuristic"

    async def analyze(
        self,
        app_input: AppInput,
        fetched_pages: Dict[str, Dict[str, str]],
        bypass_cache: bool = False,
    ) -> AppRecord:
        # Prepare context from fetched pages
        pages_context = []
        for url, pdata in fetched_pages.items():
            clean = pdata.get("clean_text", "")
            title = pdata.get("title", "")
            if clean:
                snippet = clean[:12000]  # Cap each page context
                pages_context.append(f"PAGE URL: {url}\nTITLE: {title}\nTEXT:\n{snippet}\n")

        joined_context = "\n===\n".join(pages_context)
        if not joined_context:
            logger.warning(
                f"[LLM] No fetched page context available for {app_input.app}."
            )
            return self.heuristic.analyze_pages(app_input, fetched_pages)

        prompt_cache_key = f"{self.provider}:{app_input.app}:{len(joined_context)}"
        if not bypass_cache:
            cached_res = self.cache.get_llm(prompt_cache_key)
            if cached_res:
                try:
                    logger.debug(f"[LLM CACHE HIT] {app_input.app}")
                    return AppRecord.model_validate(cached_res)
                except Exception:
                    pass

        # Call appropriate provider
        record = None
        try:
            if self.provider == "openai":
                record = await self._call_openai(app_input, joined_context)
            elif self.provider in ["gemini", "google"]:
                record = await self._call_gemini(app_input, joined_context)
            elif self.provider == "groq":
                record = await self._call_groq(app_input, joined_context)
            elif self.provider == "anthropic":
                record = await self._call_anthropic(app_input, joined_context)
            elif self.provider == "openai_compatible":
                record = await self._call_openai_compatible(app_input, joined_context)
            else:
                record = self.heuristic.analyze_pages(app_input, fetched_pages)
        except Exception as e:
            logger.error(
                f"[LLM] Error calling provider '{self.provider}' for {app_input.app}: {e}. Falling back to heuristic extractor."
            )
            record = self.heuristic.analyze_pages(app_input, fetched_pages)

        if record:
            self.cache.set_llm(prompt_cache_key, record.model_dump())
        return record

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

        response = await client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format=AppRecord,
            temperature=0.1,
        )
        return response.choices[0].message.parsed

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
                "response_schema": AppRecord,
                "temperature": 0.1,
            },
        )
        data = json.loads(response.text)
        return AppRecord.model_validate(data)

    async def _call_groq(self, app_input: AppInput, context: str) -> AppRecord:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        model = settings.MODEL_NAME or "llama-3.3-70b-versatile"

        user_msg = f"""App: {app_input.app} (Category: {app_input.category}, ID: {app_input.id})
Website Hint: {app_input.website_hint}

DOCUMENTATION:
{context}

Output JSON schema matching AppRecord."""

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
        return AppRecord.model_validate(data)

    async def _call_anthropic(self, app_input: AppInput, context: str) -> AppRecord:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        model = settings.MODEL_NAME or "claude-3-5-haiku-20241022"

        user_msg = f"""App: {app_input.app} (Category: {app_input.category}, ID: {app_input.id})
Website Hint: {app_input.website_hint}

DOCUMENTATION:
{context}"""

        # Using Claude tool-use for guaranteed JSON structured output
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

        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY or "dummy",
            base_url=settings.OPENAI_BASE_URL,
        )
        model = settings.MODEL_NAME or "default"
        user_msg = f"""App: {app_input.app} (Category: {app_input.category}, ID: {app_input.id})
Website Hint: {app_input.website_hint}

DOCUMENTATION:
{context}

Respond ONLY with valid JSON matching AppRecord."""

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
        return AppRecord.model_validate(data)
