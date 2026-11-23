import asyncio
import csv
import json
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple
import urllib.parse
from config import settings
from core.cache import DiskCache
from core.fetcher import AsyncFetcher
from core.llm import LLMClient
from core.search import SearchRouter
from core.verifier import EvidenceVerifier
from schema import AppInput, AppRecord

logger = logging.getLogger("app-research-agent.pipeline")


class ResearchPipeline:
    def __init__(
        self,
        cache_dir: Optional[str] = None,
        concurrency_limit: int = 5,
        bypass_cache: bool = False,
        allow_heuristic: Optional[bool] = None,
    ):
        self.cache = DiskCache(cache_dir or settings.CACHE_DIR)
        self.fetcher = AsyncFetcher(
            cache=self.cache,
            concurrency_limit=concurrency_limit,
            timeout=settings.REQUEST_TIMEOUT,
            max_retries=settings.MAX_RETRIES,
        )
        self.search_router = SearchRouter(
            cache=self.cache,
            composio_api_key=settings.COMPOSIO_API_KEY,
            tavily_api_key=settings.TAVILY_API_KEY,
            brave_api_key=settings.BRAVE_API_KEY,
            concurrency_limit=concurrency_limit,
        )
        self.allow_heuristic = (
            allow_heuristic
            if allow_heuristic is not None
            else settings.ALLOW_HEURISTIC
        )
        self.llm = LLMClient(cache=self.cache, allow_heuristic=self.allow_heuristic)
        self.verifier = EvidenceVerifier()
        self.bypass_cache = bypass_cache
        self.results_dir = Path(settings.RESULTS_DIR)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def extract_urls_from_hint(self, hint: str) -> List[str]:
        """Extracts valid candidate URLs and domains from the website_hint column."""
        if not hint:
            return []

        urls = []
        for m in re.finditer(r"https?://[^\s)\]]+", hint):
            url = m.group(0).rstrip(".,;)")
            urls.append(url)

        clean = re.sub(r"\(.*?\)", "", hint).strip()
        domain_match = re.search(
            r"([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+(?:/[^\s)]*)?)",
            clean,
        )
        if domain_match:
            raw_target = domain_match.group(1).rstrip("/.,;)")
            if not raw_target.startswith("http"):
                urls.append(f"https://{raw_target}")

        return urls

    async def discover_urls_for_app(self, app_input: AppInput) -> Dict[str, str]:
        """Discovers developer docs, auth pages, and pricing pages using domain patterns & field-separated queries.
        Returns a dict mapping url -> query_type ('auth', 'pricing', 'mcp', 'general').
        """
        app = app_input.app
        url_types: Dict[str, str] = {}

        # 1. Add direct hint URLs (tagged as auth/developer)
        hint_urls = self.extract_urls_from_hint(app_input.website_hint)
        for u in hint_urls:
            url_types[u] = "auth"

        # 2. Derive domain endpoints
        target_domain = None
        if hint_urls:
            parsed = urllib.parse.urlparse(hint_urls[0])
            netloc = parsed.netloc or parsed.path.split("/")[0]
            target_domain = netloc.split(":")[0].lower()

        if target_domain:
            parts = target_domain.split(".")
            base_domain = ".".join(parts[-2:]) if len(parts) >= 2 else target_domain
            derived_auth = [
                f"https://developer.{base_domain}",
                f"https://developers.{base_domain}",
                f"https://docs.{base_domain}",
            ]
            if target_domain != base_domain:
                derived_auth.append(f"https://{target_domain}")
            for ep in derived_auth:
                if ep not in url_types:
                    url_types[ep] = "auth"

        # 3. Requirement 2: Separate search queries per field:
        #    "<app> API authentication", "<app> API pricing access", "<app> MCP server"
        queries = [
            (f"{app} API authentication", "auth"),
            (f"{app} API pricing access", "pricing"),
            (f"{app} MCP server", "mcp"),
        ]

        search_tasks = [
            self.search_router.search(q, max_results=2, bypass_cache=self.bypass_cache)
            for q, _ in queries
        ]
        search_results = await asyncio.gather(*search_tasks)

        for (q, qtype), res_list in zip(queries, search_results):
            for item in res_list:
                url = item.get("url", "")
                if url and url.startswith("http"):
                    if any(sub in url for sub in ["capterra.com", "g2.com", "wikipedia.org"]):
                        continue
                    if url not in url_types:
                        url_types[url] = qtype

        # Filter out binary assets and limit to top 6 distinct URLs
        valid_urls: Dict[str, str] = {}
        for u, qt in url_types.items():
            if not any(u.endswith(ext) for ext in [".png", ".jpg", ".zip", ".exe", ".pdf"]):
                valid_urls[u] = qt
                if len(valid_urls) >= 6:
                    break

        return valid_urls

    async def research_app(self, app_input: AppInput) -> AppRecord:
        logger.info(
            f"=== Researching [{app_input.id}/100]: {app_input.app} ({app_input.category}) ==="
        )

        # Step 1: Discover URLs with separate queries for auth, pricing, and MCP
        url_types = await self.discover_urls_for_app(app_input)
        urls = list(url_types.keys())
        logger.info(f"[{app_input.app}] Discovered {len(urls)} target URLs across auth/pricing/mcp queries")

        # Step 2: Fetch pages
        fetched_list = await self.fetcher.fetch_many(urls, bypass_cache=self.bypass_cache)
        fetched_dict = {}
        for item in fetched_list:
            u = item.get("url")
            if u and item.get("clean_text") and len(item.get("clean_text", "")) > 80:
                item["query_type"] = url_types.get(u, "general")
                fetched_dict[u] = item

        logger.info(f"[{app_input.app}] Successfully fetched {len(fetched_dict)} valid content pages")

        # Step 3: LLM Analysis with real provider (Rule 1 & 2)
        record = await self.llm.analyze(
            app_input, fetched_dict, bypass_cache=self.bypass_cache
        )
        record.llm_provider = self.llm.provider

        # Step 4: Verification Loop (Rule 2)
        verified_record, pass_rate = self.verifier.verify_record(record, fetched_dict)
        verified_record.llm_provider = self.llm.provider
        logger.info(
            f"[{app_input.app}] Provider: '{verified_record.llm_provider}' | Verification pass rate: {int(pass_rate*100)}% ({len(verified_record.evidence)} quotes)"
        )

        return verified_record

    async def run(
        self, apps: List[AppInput], output_pass: int = 1
    ) -> List[AppRecord]:
        logger.info(
            f"Starting research pipeline across {len(apps)} apps (Pass {output_pass}) | Provider: {self.llm.provider}"
        )

        sem = asyncio.Semaphore(settings.CONCURRENCY_LIMIT)

        async def _bounded_research(app_in: AppInput):
            async with sem:
                return await self.research_app(app_in)

        tasks = [_bounded_research(app) for app in apps]
        records = await asyncio.gather(*tasks)

        # Step 5: Save outputs (Rule 7)
        self.save_results(records, output_pass=output_pass)
        return records

    def save_results(self, records: List[AppRecord], output_pass: int = 1):
        pass_name = f"pass{output_pass}"
        json_path = self.results_dir / f"{pass_name}.json"
        csv_path = self.results_dir / f"{pass_name}.csv"
        human_path = self.results_dir / "human_queue.csv"

        # 1. Save JSON
        data = [r.model_dump() for r in records]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved JSON results to {json_path}")

        # 2. Save CSV
        fieldnames = [
            "id", "app", "category", "one_line_description", "auth_methods", "self_serve",
            "self_serve_detail", "api_type", "api_breadth", "existing_mcp", "mcp_url",
            "rate_limits_notes", "webhooks_supported", "pricing_note", "buildability",
            "main_blocker", "confidence", "confidence_reason", "evidence_count",
            "needs_human", "human_reason", "llm_provider",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in records:
                writer.writerow({
                    "id": r.id,
                    "app": r.app,
                    "category": r.category,
                    "one_line_description": r.one_line_description,
                    "auth_methods": "; ".join(r.auth_methods),
                    "self_serve": r.self_serve,
                    "self_serve_detail": r.self_serve_detail,
                    "api_type": "; ".join(r.api_type),
                    "api_breadth": r.api_breadth,
                    "existing_mcp": r.existing_mcp,
                    "mcp_url": r.mcp_url or "",
                    "rate_limits_notes": r.rate_limits_notes,
                    "webhooks_supported": str(r.webhooks_supported),
                    "pricing_note": r.pricing_note,
                    "buildability": r.buildability,
                    "main_blocker": r.main_blocker,
                    "confidence": r.confidence,
                    "confidence_reason": r.confidence_reason,
                    "evidence_count": len(r.evidence),
                    "needs_human": r.needs_human,
                    "human_reason": r.human_reason or "",
                    "llm_provider": r.llm_provider or "unknown",
                })
        logger.info(f"Saved CSV results to {csv_path}")

        # 3. Save human_queue.csv
        flagged = [r for r in records if r.needs_human]
        with open(human_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "app", "category", "confidence", "human_reason", "llm_provider"])
            writer.writeheader()
            for r in flagged:
                writer.writerow({
                    "id": r.id,
                    "app": r.app,
                    "category": r.category,
                    "confidence": r.confidence,
                    "human_reason": r.human_reason or "Low confidence / unverified claims",
                    "llm_provider": r.llm_provider or "unknown",
                })
        logger.info(f"Logged {len(flagged)} records requiring human triage to {human_path}")
