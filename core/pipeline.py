import asyncio
import csv
import json
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional
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
        self.llm = LLMClient(cache=self.cache)
        self.verifier = EvidenceVerifier()
        self.bypass_cache = bypass_cache
        self.results_dir = Path(settings.RESULTS_DIR)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def extract_urls_from_hint(self, hint: str) -> List[str]:
        """Extracts valid candidate URLs and domains from the website_hint column."""
        if not hint:
            return []

        urls = []
        # 1. Match explicit https?:// URLs
        for m in re.finditer(r"https?://[^\s)\]]+", hint):
            url = m.group(0).rstrip(".,;)")
            urls.append(url)

        # 2. Extract domain or domain/path (ignoring remarks in parentheses)
        clean = re.sub(r"\(.*?\)", "", hint).strip()
        # Find any domain-like string (e.g. shopify.dev, api-docs.ecwid.com, etc.)
        domain_match = re.search(
            r"([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+(?:/[^\s)]*)?)",
            clean,
        )
        if domain_match:
            raw_target = domain_match.group(1).rstrip("/.,;)")
            if not raw_target.startswith("http"):
                urls.append(f"https://{raw_target}")

        return urls

    async def discover_urls_for_app(self, app_input: AppInput) -> List[str]:
        """Discovers developer docs, auth pages, and pricing pages using domain patterns & search (Rule 1)."""
        app = app_input.app
        candidate_urls: List[str] = []

        # 1. Add URLs directly extracted from website_hint
        hint_urls = self.extract_urls_from_hint(app_input.website_hint)
        for u in hint_urls:
            if u not in candidate_urls:
                candidate_urls.append(u)

        # 2. Derive domain from hint for targeted subdomains/paths
        target_domain = None
        if hint_urls:
            parsed = urllib.parse.urlparse(hint_urls[0])
            netloc = parsed.netloc or parsed.path.split("/")[0]
            # Strip port or subpaths
            target_domain = netloc.split(":")[0].lower()

        if target_domain:
            # Common developer documentation subdomains and endpoints
            derived = [
                f"https://developers.{target_domain}",
                f"https://developer.{target_domain}",
                f"https://docs.{target_domain}",
                f"https://{target_domain}/docs",
                f"https://{target_domain}/api",
            ]
            for ep in derived:
                if ep not in candidate_urls:
                    candidate_urls.append(ep)

        # 3. Only run search if fewer than 2 candidate URLs exist
        if len(candidate_urls) < 3:
            queries = [
                f"{app} official developer api documentation",
                f"{app} api authentication oauth2 api key docs",
            ]
            search_tasks = [
                self.search_router.search(q, max_results=2, bypass_cache=self.bypass_cache)
                for q in queries
            ]
            search_results_nested = await asyncio.gather(*search_tasks)

            for result_list in search_results_nested:
                for item in result_list:
                    url = item.get("url", "")
                    if url and url.startswith("http"):
                        if any(sub in url for sub in ["capterra.com", "g2.com", "wikipedia.org"]):
                            continue
                        if url not in candidate_urls:
                            candidate_urls.append(url)

        # Filter out binary assets
        unique = []
        for u in candidate_urls:
            if u not in unique and not any(u.endswith(ext) for ext in [".png", ".jpg", ".zip", ".exe", ".pdf"]):
                unique.append(u)

        return unique[:4]

    async def research_app(self, app_input: AppInput) -> AppRecord:
        logger.info(
            f"=== Researching [{app_input.id}/100]: {app_input.app} ({app_input.category}) ==="
        )

        # Step 1: Discover URLs (Rule 1)
        urls = await self.discover_urls_for_app(app_input)
        logger.info(f"[{app_input.app}] Discovered {len(urls)} target URLs to fetch: {urls}")

        # Step 2: Fetch pages
        fetched_list = await self.fetcher.fetch_many(
            urls, bypass_cache=self.bypass_cache
        )
        fetched_dict = {
            item["url"]: item
            for item in fetched_list
            if item.get("clean_text") and len(item.get("clean_text", "")) > 100
        }
        logger.info(
            f"[{app_input.app}] Successfully fetched {len(fetched_dict)} valid content pages"
        )

        # Step 3: LLM / Heuristic Analysis (Rule 4)
        record = await self.llm.analyze(
            app_input, fetched_dict, bypass_cache=self.bypass_cache
        )

        # Step 4: Verification Loop (Rule 2)
        verified_record, pass_rate = self.verifier.verify_record(record, fetched_dict)
        logger.info(
            f"[{app_input.app}] Verification pass rate: {int(pass_rate*100)}% ({len(verified_record.evidence)} verified quotes)"
        )

        return verified_record

    async def run(
        self, apps: List[AppInput], output_pass: int = 1
    ) -> List[AppRecord]:
        logger.info(
            f"Starting research pipeline across {len(apps)} apps (Pass {output_pass})"
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
            "id",
            "app",
            "category",
            "one_line_description",
            "auth_methods",
            "self_serve",
            "self_serve_detail",
            "api_type",
            "api_breadth",
            "existing_mcp",
            "mcp_url",
            "rate_limits_notes",
            "webhooks_supported",
            "pricing_note",
            "buildability",
            "main_blocker",
            "confidence",
            "confidence_reason",
            "evidence_count",
            "needs_human",
            "human_reason",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in records:
                row = {
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
                }
                writer.writerow(row)
        logger.info(f"Saved CSV results to {csv_path}")

        # 3. Save Human Queue (Rule 6)
        human_records = [r for r in records if r.needs_human]
        human_fields = [
            "id",
            "app",
            "category",
            "confidence",
            "human_reason",
            "self_serve",
            "buildability",
        ]
        with open(human_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=human_fields)
            writer.writeheader()
            for r in human_records:
                writer.writerow(
                    {
                        "id": r.id,
                        "app": r.app,
                        "category": r.category,
                        "confidence": r.confidence,
                        "human_reason": r.human_reason or "Flagged for manual review",
                        "self_serve": r.self_serve,
                        "buildability": r.buildability,
                    }
                )
        logger.info(
            f"Saved {len(human_records)} apps to human queue at {human_path}"
        )
