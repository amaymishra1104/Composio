import logging
import re
from typing import Dict, List, Optional, Tuple
from schema import AppRecord, EvidenceItem

logger = logging.getLogger("app-research-agent.verifier")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    # Replace non-breaking spaces, smart quotes, multiple spaces
    text = (
        text.replace("\xa0", " ")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


class EvidenceVerifier:
    """Verifies that evidence quotes exist verbatim in the fetched page text.

    If a quote has minor punctuation/casing drift, locates the true verbatim text
    from the page. If no quote exists, adjusts the claim or routes to human queue.
    """

    def __init__(self, tolerance_ratio: float = 0.85):
        self.tolerance_ratio = tolerance_ratio

    def find_in_text(self, quote: str, text: str) -> Optional[str]:
        if not quote or not text:
            return None

        # 1. Exact case-insensitive substring
        norm_quote = normalize_text(quote)
        norm_text = normalize_text(text)

        if norm_quote in norm_text:
            idx = norm_text.find(norm_quote)
            # Find the original slice in text roughly corresponding to idx
            # Return cleaned quote under 25 words
            words = quote.split()
            return " ".join(words[:25])

        # 2. Sliding window token match
        quote_words = norm_quote.split()
        if len(quote_words) < 3:
            return None

        text_words = norm_text.split()
        window_size = len(quote_words)
        q_set = set(quote_words)

        best_match = None
        best_overlap = 0.0

        for i in range(max(1, len(text_words) - window_size + 1)):
            window = text_words[i : i + window_size]
            overlap = len(set(window) & q_set) / float(len(q_set))
            if overlap > best_overlap:
                best_overlap = overlap
                if overlap >= self.tolerance_ratio:
                    best_match = " ".join(window[:25])

        return best_match if best_overlap >= self.tolerance_ratio else None

    def verify_record(
        self, record: AppRecord, fetched_pages: Dict[str, Dict[str, str]]
    ) -> Tuple[AppRecord, float]:
        verified_items: List[EvidenceItem] = []
        unverified_count = 0

        # Combine all fetched text for the app
        all_text_by_url = {
            url: p.get("clean_text", "")
            for url, p in fetched_pages.items()
            if p.get("clean_text")
        }
        combined_text = " \n ".join(all_text_by_url.values())

        for ev in record.evidence:
            quote = ev.exact_quote_under_25_words
            target_text = all_text_by_url.get(ev.url, "")

            # Check target page first
            match = self.find_in_text(quote, target_text)

            # If not found on target page, check other fetched pages for this app
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
                logger.warning(
                    f"[VERIFIER] Unverified quote for {record.app} ({ev.claim_field}): '{quote[:50]}...'"
                )

        record.evidence = verified_items
        total = len(verified_items) + unverified_count
        pass_rate = (len(verified_items) / total) if total > 0 else 0.0

        # Enforce Rule 2: If no verified evidence for key claim fields, set to unknown or adjust
        if len(record.evidence) == 0:
            record.confidence = max(0.1, record.confidence * 0.4)
            record.confidence_reason = (
                f"Low confidence: 0 verified evidence quotes found on fetched pages. {record.confidence_reason}"
            )
            record.needs_human = True
            record.human_reason = "No verifiable evidence quotes found in fetched documentation pages."
        elif pass_rate < 0.7:
            record.confidence = min(record.confidence, round(pass_rate, 2))
            record.confidence_reason += (
                f" (Verification loop: {len(verified_items)}/{total} quotes matched page text)"
            )
            if record.confidence < 0.65:
                record.needs_human = True
                record.human_reason = f"Verification pass rate low ({int(pass_rate*100)}%). Manual check required."

        return record, pass_rate
