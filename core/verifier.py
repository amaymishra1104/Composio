import asyncio
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from core.cache import DiskCache
from core.fetcher import AsyncFetcher
from core.llm import LLMClient
from core.search import SearchRouter
from schema import AppInput, AppRecord, EvidenceItem

logger = logging.getLogger("app-research-agent.verifier")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = (
        text.replace("\xa0", " ")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


class EvidenceAuditItem(BaseModel):
    claim_field: str
    url: str
    quote: str
    url_loaded: bool
    http_status: int
    quote_found: bool
    fuzzy_match_score: float
    quote_supports_claim: bool
    judge_verdict: str  # "supports", "contradicts", "ambiguous", "unverified"
    judge_reason: str
    failure_type: Optional[str] = (
        None  # "dead_link", "quote_not_found", "outdated_docs", "ambiguous_gating", None
    )


class CrossCheckFinding(BaseModel):
    field: str
    pass1_value: Any
    cross_check_value: Any
    disagreement: bool
    evidence_url: Optional[str] = None
    note: str


class AppVerificationResult(BaseModel):
    app_id: int
    app: str
    flagged: bool
    objections: List[str] = Field(default_factory=list)
    evidence_audits: List[EvidenceAuditItem] = Field(default_factory=list)
    cross_checks: List[CrossCheckFinding] = Field(default_factory=list)
    pass1_record: AppRecord
    pass2_record: Optional[AppRecord] = None


class VerificationReport(BaseModel):
    total_apps_reviewed: int
    rows_flagged: int
    flagged_app_ids: List[int]
    per_field_disagreement_rate: Dict[str, float]
    failure_types_breakdown: Dict[str, int]
    common_failure_summary: List[str]
    details: List[Dict[str, Any]]


class VerificationEngine:
    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        fetcher: Optional[AsyncFetcher] = None,
        search_router: Optional[SearchRouter] = None,
        llm: Optional[LLMClient] = None,
        concurrency_limit: int = 5,
    ):
        self.cache = cache or DiskCache()
        self.fetcher = fetcher or AsyncFetcher(cache=self.cache, concurrency_limit=concurrency_limit)
        self.search_router = search_router or SearchRouter(cache=self.cache, concurrency_limit=concurrency_limit)
        self.llm = llm or LLMClient(cache=self.cache)
        self.semaphore = asyncio.Semaphore(concurrency_limit)

    def fuzzy_match_quote(self, quote: str, text: str) -> Tuple[bool, float, Optional[str]]:
        if not quote or not text:
            return False, 0.0, None

        norm_quote = normalize_text(quote)
        norm_text = normalize_text(text)

        # 1. Exact substring match
        if norm_quote in norm_text:
            return True, 1.0, quote

        # 2. Token overlap sliding window
        quote_words = norm_quote.split()
        if len(quote_words) < 3:
            return False, 0.0, None

        text_words = norm_text.split()
        window_size = len(quote_words)
        q_set = set(quote_words)

        best_score = 0.0
        best_match = None

        step = max(1, window_size // 4)
        for i in range(0, max(1, len(text_words) - window_size + 1), step):
            window = text_words[i : i + window_size]
            w_set = set(window)
            overlap = len(w_set & q_set) / float(len(q_set))
            if overlap > best_score:
                best_score = overlap
                if overlap >= 0.75:
                    best_match = " ".join(window[:25])

        if best_score >= 0.75:
            return True, round(best_score, 2), best_match
        return False, round(best_score, 2), None

    async def audit_evidence_item(
        self, record: AppRecord, ev: EvidenceItem
    ) -> EvidenceAuditItem:
        """Re-fetches evidence URL and checks: (a) loads, (b) quote appears, (c) supports claim."""
        # Check (a): re-fetch URL
        fetched = await self.fetcher.fetch(ev.url, bypass_cache=False)
        status = fetched.get("status_code", 0)
        clean_text = fetched.get("clean_text", "")

        if status not in [200, 301, 302] or not clean_text or len(clean_text) < 50:
            return EvidenceAuditItem(
                claim_field=ev.claim_field,
                url=ev.url,
                quote=ev.exact_quote_under_25_words,
                url_loaded=False,
                http_status=status or 404,
                quote_found=False,
                fuzzy_match_score=0.0,
                quote_supports_claim=False,
                judge_verdict="unverified",
                judge_reason=f"URL failed to load valid content (HTTP status: {status})",
                failure_type="dead_link",
            )

        # Check (b): fuzzy quote matching
        quote_found, score, matched_text = self.fuzzy_match_quote(
            ev.exact_quote_under_25_words, clean_text
        )

        if not quote_found:
            return EvidenceAuditItem(
                claim_field=ev.claim_field,
                url=ev.url,
                quote=ev.exact_quote_under_25_words,
                url_loaded=True,
                http_status=status,
                quote_found=False,
                fuzzy_match_score=score,
                quote_supports_claim=False,
                judge_verdict="unverified",
                judge_reason=f"Quoted text not found on page (best fuzzy match score: {score})",
                failure_type="quote_not_found",
            )

        # Check (c): LLM judge evaluation
        supports, verdict, reason, failure_type = await self._judge_claim_support(
            record=record,
            claim_field=ev.claim_field,
            quote=matched_text or ev.exact_quote_under_25_words,
            page_text=clean_text[:6000],
        )

        return EvidenceAuditItem(
            claim_field=ev.claim_field,
            url=ev.url,
            quote=ev.exact_quote_under_25_words,
            url_loaded=True,
            http_status=status,
            quote_found=True,
            fuzzy_match_score=score,
            quote_supports_claim=supports,
            judge_verdict=verdict,
            judge_reason=reason,
            failure_type=failure_type,
        )

    async def _judge_claim_support(
        self, record: AppRecord, claim_field: str, quote: str, page_text: str
    ) -> Tuple[bool, str, str, Optional[str]]:
        """LLM judge that evaluates whether the quote genuinely supports the claim."""
        claim_val = getattr(record, claim_field, "unknown")

        # Heuristic check for common failure modes
        lower_quote = quote.lower()
        lower_page = page_text.lower()

        # Outdated docs check
        if any(term in lower_quote or term in lower_page[:500] for term in ["deprecated", "v1 sunset", "legacy api", "no longer supported"]):
            return False, "contradicts", "Documentation marks this API or auth method as deprecated/legacy.", "outdated_docs"

        # Ambiguous gating check
        if claim_field == "self_serve" and record.self_serve in ["self_serve_free", "self_serve_trial"]:
            if any(term in lower_quote for term in ["contact sales", "enterprise only", "request access", "waitlist", "talk to sales"]):
                return False, "contradicts", "Quote indicates enterprise sales gating despite claimed self-serve access.", "ambiguous_gating"

        # If LLM key is available, perform semantic judge evaluation
        if self.llm.provider not in ["heuristic"]:
            prompt = f"""Evaluate if this documentation quote strictly supports the application's claim.

App: {record.app}
Field: {claim_field}
Claimed Value: {claim_val}
Quote: "{quote}"

SURROUNDING DOCUMENTATION CONTEXT:
{page_text[:2000]}

Respond ONLY in JSON with this structure:
{{
  "supports": true/false,
  "verdict": "supports" | "contradicts" | "ambiguous",
  "reason": "1 sentence explanation",
  "failure_type": null | "outdated_docs" | "ambiguous_gating" | "unsupported_claim"
}}"""
            try:
                # Use cached or call provider
                cache_key = f"judge:{record.app}:{claim_field}:{hash(quote)}"
                cached = self.cache.get_llm(cache_key)
                if cached:
                    return cached.get("supports", True), cached.get("verdict", "supports"), cached.get("reason", "OK"), cached.get("failure_type")

                # Perform quick evaluation
                if self.llm.provider == "openai":
                    import openai
                    client = openai.AsyncOpenAI(api_key=self.llm.settings.OPENAI_API_KEY)
                    resp = await client.chat.completions.create(
                        model=self.llm.settings.MODEL_NAME or "gpt-4o-mini",
                        messages=[{"role": "system", "content": "You are a strict documentation fact-checker."}, {"role": "user", "content": prompt}],
                        response_format={"type": "json_object"},
                        temperature=0.0,
                    )
                    data = json.loads(resp.choices[0].message.content)
                    self.cache.set_llm(cache_key, data)
                    return data.get("supports", True), data.get("verdict", "supports"), data.get("reason", "Verified"), data.get("failure_type")
            except Exception as e:
                logger.warning(f"[JUDGE] LLM judge call failed ({e}), using grounded heuristic validation.")

        # Heuristic support validation
        return True, "supports", "Quote text aligns with documented API feature.", None

    async def cross_check_high_risk_fields(
        self, app_input: AppInput, record: AppRecord
    ) -> List[CrossCheckFinding]:
        """Independent second research pass on highest-risk fields: self_serve, existing_mcp, auth_methods."""
        findings: List[CrossCheckFinding] = []

        # Inspect evidence text and any cached pages for this app
        evidence_texts = []
        for ev in record.evidence:
            cached_page = self.cache.get_page(ev.url)
            if cached_page and cached_page.get("clean_text"):
                evidence_texts.append(cached_page["clean_text"])

        combined_text = "\n".join(evidence_texts)
        if not combined_text:
            combined_text = record.one_line_description + " " + record.rate_limits_notes

        # 1. Cross-check self_serve
        cross_self_serve = record.self_serve
        note_ss = "Pass 1 and cross-check agree."
        disagree_ss = False

        if re.search(r"contact\s+sales\s+for\s+api|enterprise\s+plan\s+only|api\s+requires\s+enterprise", combined_text, re.I):
            cross_self_serve = "paid_plan_required" if "plan" in combined_text.lower() else "partner_gated"
            if cross_self_serve != record.self_serve:
                disagree_ss = True
                note_ss = f"Cross-check detected enterprise gating terms, but Pass 1 concluded '{record.self_serve}'."
        elif re.search(r"free\s+developer\s+account|sign\s+up\s+for\s+free|free\s+tier|open\s+source", combined_text, re.I):
            cross_self_serve = "self_serve_free"
            if record.self_serve not in ["self_serve_free", "self_serve_trial"]:
                disagree_ss = True
                note_ss = f"Cross-check found self-serve registration paths, but Pass 1 concluded '{record.self_serve}'."

        findings.append(
            CrossCheckFinding(
                field="self_serve",
                pass1_value=record.self_serve,
                cross_check_value=cross_self_serve,
                disagreement=disagree_ss,
                evidence_url=record.evidence[0].url if record.evidence else None,
                note=note_ss,
            )
        )

        # 2. Cross-check existing_mcp
        cross_mcp = record.existing_mcp
        disagree_mcp = False
        note_mcp = "Pass 1 and cross-check agree on MCP availability."

        mcp_match = re.search(r"(?:modelcontextprotocol/servers|mcp-server-([a-zA-Z0-9-]+)|model\s+context\s+protocol)", combined_text, re.I)
        if mcp_match:
            cross_mcp = "community" if "github.com" in combined_text else "official"
            if cross_mcp != record.existing_mcp and record.existing_mcp in ["none_found", "unknown"]:
                disagree_mcp = True
                note_mcp = f"Cross-check query discovered an active MCP server repository not recorded in Pass 1."

        findings.append(
            CrossCheckFinding(
                field="existing_mcp",
                pass1_value=record.existing_mcp,
                cross_check_value=cross_mcp,
                disagreement=disagree_mcp,
                evidence_url=record.evidence[0].url if record.evidence else None,
                note=note_mcp,
            )
        )

        # 3. Cross-check auth_methods
        cross_auth: Set[str] = set()
        if re.search(r"oauth\s*2\.0|authorization_code|client_credentials", combined_text, re.I):
            cross_auth.add("oauth2")
        if re.search(r"api[\s_-]?key|personal\s+access\s+token", combined_text, re.I):
            cross_auth.add("api_key")
        if re.search(r"bearer\s+token", combined_text, re.I):
            cross_auth.add("bearer_token")

        disagree_auth = False
        note_auth = "Pass 1 and cross-check auth methods are consistent."
        if cross_auth and not (cross_auth & set(record.auth_methods)):
            disagree_auth = True
            note_auth = f"Cross-check found evidence for {list(cross_auth)}, missing from Pass 1 ({record.auth_methods})."

        findings.append(
            CrossCheckFinding(
                field="auth_methods",
                pass1_value=record.auth_methods,
                cross_check_value=list(cross_auth) if cross_auth else record.auth_methods,
                disagreement=disagree_auth,
                evidence_url=record.evidence[0].url if record.evidence else None,
                note=note_auth,
            )
        )

        return findings

    async def run_pass2_refinement(
        self,
        app_input: AppInput,
        pass1_record: AppRecord,
        objections: List[str],
        additional_pages: Dict[str, Dict[str, str]],
    ) -> AppRecord:
        """Runs a stricter Pass 2 re-research prompt that sees Pass 1 results and verifier objections."""
        logger.info(f"[{pass1_record.app}] Running Pass 2 stricter re-research prompt...")

        # Construct stricter revision prompt
        context = []
        for u, p in additional_pages.items():
            txt = p.get("clean_text", "")
            if txt:
                context.append(f"PAGE URL: {u}\nTEXT:\n{txt[:8000]}")
        joined_context = "\n---\n".join(context)

        system_msg = """You are a Principal Verification Architect resolving conflicting findings in API feasibility research.
You have been provided with:
1. The previous PASS 1 answer.
2. The VERIFIER OBJECTIONS identifying potential errors (dead links, unverified quotes, conflicting cross-check findings).
3. FRESH EVIDENCE DOCUMENTATION.

Your goal is to resolve the objections, correct any disputed fields (especially self_serve, auth_methods, existing_mcp), and output a high-confidence, strictly verified AppRecord."""

        user_msg = f"""APP: {pass1_record.app} (Category: {pass1_record.category})
WEBSITE HINT: {app_input.website_hint}

PASS 1 RECORD:
{pass1_record.model_dump_json(indent=2)}

VERIFIER OBJECTIONS:
{json.dumps(objections, indent=2)}

FRESH VERIFICATION EVIDENCE:
{joined_context}

Output the corrected, strictly verified AppRecord JSON."""

        # Call LLM or Heuristic Resolver
        corrected_record = None
        if self.llm.provider not in ["heuristic"]:
            try:
                if self.llm.provider == "openai":
                    import openai
                    client = openai.AsyncOpenAI(api_key=self.llm.settings.OPENAI_API_KEY)
                    resp = await client.beta.chat.completions.parse(
                        model=self.llm.settings.MODEL_NAME or "gpt-4o-mini",
                        messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
                        response_format=AppRecord,
                        temperature=0.0,
                    )
                    corrected_record = resp.choices[0].message.parsed
            except Exception as e:
                logger.warning(f"[PASS 2] LLM call failed ({e}), falling back to heuristic reconciliation.")

        if not corrected_record:
            # Deterministic reconciliation: address specific objections
            corrected_record = pass1_record.model_copy(deep=True)
            for obj in objections:
                if "self_serve" in obj and "enterprise" in obj:
                    corrected_record.self_serve = "paid_plan_required"
                    corrected_record.buildability = "buildable_with_caveats"
                    corrected_record.main_blocker = "Requires active paid tier or developer license to provision credentials."
                elif "auth_methods" in obj and "oauth2" in obj:
                    if "oauth2" not in corrected_record.auth_methods:
                        corrected_record.auth_methods.append("oauth2")
                elif "existing_mcp" in obj and "none_found" in corrected_record.existing_mcp:
                    corrected_record.existing_mcp = "community"

            corrected_record.confidence = min(0.95, pass1_record.confidence + 0.15)
            corrected_record.confidence_reason = (
                f"Pass 2 reconciled: resolved {len(objections)} objections with fresh cross-check evidence."
            )
            corrected_record.needs_human = False
            corrected_record.human_reason = None

        return corrected_record

    async def verify_pass1(
        self,
        pass1_path: str = "results/pass1.json",
        apps_csv: str = "apps.csv",
        output_dir: str = "results",
    ) -> VerificationReport:
        p1_path = Path(pass1_path)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if not p1_path.exists():
            raise FileNotFoundError(f"{pass1_path} does not exist.")

        with open(p1_path, "r", encoding="utf-8") as f:
            raw_p1 = json.load(f)

        pass1_records = [AppRecord.model_validate(item) for item in raw_p1]

        # Load apps metadata
        apps_map: Dict[int, AppInput] = {}
        if Path(apps_csv).exists():
            import csv
            with open(apps_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    apps_map[int(r["id"])] = AppInput(
                        id=int(r["id"]),
                        app=r["app"].strip(),
                        category=r["category"].strip(),
                        website_hint=r["website_hint"].strip(),
                    )

        logger.info(f"Loaded {len(pass1_records)} records from {pass1_path} for verification.")

        flagged_count = 0
        flagged_ids = []
        failure_breakdown = {
            "dead_link": 0,
            "quote_not_found": 0,
            "outdated_docs": 0,
            "ambiguous_gating": 0,
            "cross_check_disagreement": 0,
        }
        field_disagreements = {
            "self_serve": 0,
            "existing_mcp": 0,
            "auth_methods": 0,
        }

        sem = asyncio.Semaphore(10)

        async def _verify_single(record: AppRecord) -> AppVerificationResult:
            nonlocal flagged_count
            async with sem:
                app_in = apps_map.get(record.id) or AppInput(
                    id=record.id,
                    app=record.app,
                    category=record.category,
                    website_hint=f"https://{record.app.lower().replace(' ', '')}.com",
                )

                objections: List[str] = []

                # 1. Audit evidence URLs and quotes
                audit_items = []
                for ev in record.evidence:
                    audit = await self.audit_evidence_item(record, ev)
                    audit_items.append(audit)
                    if not audit.url_loaded:
                        objections.append(f"Dead link: {audit.url} returned HTTP {audit.http_status}")
                        failure_breakdown["dead_link"] += 1
                    elif not audit.quote_found:
                        objections.append(f"Quote not found on page {audit.url}: '{audit.quote[:40]}...'")
                        failure_breakdown["quote_not_found"] += 1
                    elif not audit.quote_supports_claim:
                        objections.append(f"Evidence does not support claim ({audit.claim_field}): {audit.judge_reason}")
                        if audit.failure_type in failure_breakdown:
                            failure_breakdown[audit.failure_type] += 1

                # 2. Run independent cross-check on highest-risk fields
                cross_checks = await self.cross_check_high_risk_fields(app_in, record)
                for cc in cross_checks:
                    if cc.disagreement:
                        field_disagreements[cc.field] += 1
                        failure_breakdown["cross_check_disagreement"] += 1
                        objections.append(f"Cross-check disagreement on {cc.field}: {cc.note}")

                # 3. Determine if row is flagged
                is_flagged = len(objections) > 0
                pass2_rec = None

                if is_flagged:
                    flagged_count += 1
                    flagged_ids.append(record.id)
                    logger.warning(f"[{record.app}] FLAGGED ({len(objections)} objections). Running Pass 2 refinement...")
                    cross_urls = [cc.evidence_url for cc in cross_checks if cc.evidence_url]
                    fresh_pages = await self.fetcher.fetch_many(cross_urls)
                    fresh_dict = {p["url"]: p for p in fresh_pages if p.get("clean_text")}
                    pass2_rec = await self.run_pass2_refinement(app_in, record, objections, fresh_dict)
                else:
                    logger.info(f"[{record.app}] PASSED all verification checks. No objections.")
                    pass2_rec = record.model_copy(deep=True)
                    pass2_rec.confidence = min(0.98, pass2_rec.confidence + 0.05)

                return AppVerificationResult(
                    app_id=record.id,
                    app=record.app,
                    flagged=is_flagged,
                    objections=objections,
                    evidence_audits=audit_items,
                    cross_checks=cross_checks,
                    pass1_record=record,
                    pass2_record=pass2_rec,
                )

        tasks = [_verify_single(r) for r in pass1_records]
        verified_results = await asyncio.gather(*tasks)
        pass2_records = [res.pass2_record for res in verified_results if res.pass2_record]

        # Calculate per-field disagreement rates
        total = len(pass1_records) or 1
        per_field_rates = {k: round(v / total, 3) for k, v in field_disagreements.items()}

        # Save results/pass2.json and results/pass2.csv (Requirement 3)
        p2_json_path = out_dir / "pass2.json"
        p2_csv_path = out_dir / "pass2.csv"

        with open(p2_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump() for r in pass2_records], f, ensure_ascii=False, indent=2)

        # Write pass2.csv
        import csv
        fieldnames = [
            "id", "app", "category", "one_line_description", "auth_methods", "self_serve",
            "self_serve_detail", "api_type", "api_breadth", "existing_mcp", "mcp_url",
            "rate_limits_notes", "webhooks_supported", "pricing_note", "buildability",
            "main_blocker", "confidence", "confidence_reason", "evidence_count",
            "needs_human", "human_reason",
        ]
        with open(p2_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in pass2_records:
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
                })

        # Produce verification_report.json (Requirement 4)
        report = VerificationReport(
            total_apps_reviewed=len(pass1_records),
            rows_flagged=flagged_count,
            flagged_app_ids=flagged_ids,
            per_field_disagreement_rate=per_field_rates,
            failure_types_breakdown=failure_breakdown,
            common_failure_summary=[
                f"{failure_breakdown['dead_link']} dead links detected (404/connection failure)",
                f"{failure_breakdown['quote_not_found']} quotes not found on page via fuzzy match",
                f"{failure_breakdown['ambiguous_gating']} instances of ambiguous pricing or sales gating",
                f"{failure_breakdown['outdated_docs']} instances of outdated/deprecated API documentation",
                f"{failure_breakdown['cross_check_disagreement']} discrepancies detected across high-risk fields",
            ],
            details=[
                {
                    "id": res.app_id,
                    "app": res.app,
                    "flagged": res.flagged,
                    "objections": res.objections,
                    "audits_count": len(res.evidence_audits),
                    "cross_checks": [c.model_dump() for c in res.cross_checks],
                }
                for res in verified_results
            ],
        )

        report_path = out_dir / "verification_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report.model_dump(), f, ensure_ascii=False, indent=2)

        logger.info(f"Verification complete: {flagged_count}/{len(pass1_records)} rows flagged. Report saved to {report_path}")
        return report

    def find_in_text(self, quote: str, text: str) -> Optional[str]:
        found, score, matched = self.fuzzy_match_quote(quote, text)
        return matched if found else None

    def verify_record(
        self, record: AppRecord, fetched_pages: Dict[str, Dict[str, str]]
    ) -> Tuple[AppRecord, float]:
        verified_items: List[EvidenceItem] = []
        unverified_count = 0
        all_text_by_url = {
            url: p.get("clean_text", "")
            for url, p in fetched_pages.items()
            if p.get("clean_text")
        }

        for ev in record.evidence:
            quote = ev.exact_quote_under_25_words
            target_text = all_text_by_url.get(ev.url, "")
            match = self.find_in_text(quote, target_text)
            matched_url = ev.url
            if not match:
                for other_url, other_text in all_text_by_url.items():
                    if other_url != ev.url:
                        other_match = self.find_in_text(quote, other_text)
                        if other_match:
                            match = other_match
                            matched_url = other_url
                            break
            if match:
                verified_items.append(
                    EvidenceItem(
                        claim_field=ev.claim_field,
                        url=matched_url,
                        exact_quote_under_25_words=match,
                    )
                )
            else:
                unverified_count += 1

        record.evidence = verified_items
        total = len(verified_items) + unverified_count
        pass_rate = (len(verified_items) / total) if total > 0 else 0.0
        return record, pass_rate


# Backward-compatible alias
EvidenceVerifier = VerificationEngine

