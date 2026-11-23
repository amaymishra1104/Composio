import asyncio
import logging
import re
import time
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
import httpx
from core.cache import DiskCache

logger = logging.getLogger("app-research-agent.fetcher")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


class AsyncFetcher:
    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        concurrency_limit: int = 5,
        timeout: int = 20,
        max_retries: int = 3,
    ):
        self.cache = cache or DiskCache()
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.timeout = timeout
        self.max_retries = max_retries
        self._ua_idx = 0

    def _get_headers(self) -> Dict[str, str]:
        ua = USER_AGENTS[self._ua_idx % len(USER_AGENTS)]
        self._ua_idx += 1
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        }

    def clean_html(self, html: str, url: str) -> Dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")

        # Extract title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        # Extract meta description
        meta_desc = ""
        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if desc_tag and desc_tag.get("content"):
            meta_desc = desc_tag["content"].strip()

        # Remove noisy elements
        for tag in soup(
            ["script", "style", "noscript", "svg", "nav", "footer", "iframe", "header"]
        ):
            tag.decompose()

        # Extract textual content preserving structure
        lines = []
        for elem in soup.find_all(
            ["h1", "h2", "h3", "h4", "p", "li", "pre", "code", "table", "td", "th"]
        ):
            txt = elem.get_text(separator=" ", strip=True)
            if txt and len(txt) > 3:
                # Add markdown headers for context
                if elem.name in ["h1", "h2", "h3"]:
                    lines.append(f"\n### {txt}\n")
                else:
                    lines.append(txt)

        text = "\n".join(lines)
        # Normalize whitespace while preserving line structure
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # If sparse parsing, fallback to general get_text
        if len(text) < 100:
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"[ \t]+", " ", text)

        return {
            "url": url,
            "title": title,
            "meta_description": meta_desc,
            "clean_text": text[:50000],  # Keep up to 50k chars for LLM context
        }

    async def fetch(self, url: str, bypass_cache: bool = False) -> Dict[str, str]:
        if not url or not url.startswith("http"):
            return {
                "url": url,
                "title": "",
                "clean_text": "",
                "error": "Invalid URL",
                "cached": False,
            }

        # Check disk cache
        if not bypass_cache and self.cache:
            cached = self.cache.get_page(url)
            if cached and "clean_text" in cached and cached.get("clean_text"):
                cached["cached"] = True
                return cached

        async with self.semaphore:
            for attempt in range(1, self.max_retries + 1):
                try:
                    logger.debug(
                        f"[FETCH] Fetching ({attempt}/{self.max_retries}): {url}"
                    )
                    async with httpx.AsyncClient(
                        timeout=self.timeout,
                        follow_redirects=True,
                        verify=False,
                    ) as client:
                        response = await client.get(url, headers=self._get_headers())

                        if response.status_code == 200:
                            content_type = response.headers.get("content-type", "")
                            if (
                                "text" in content_type
                                or "json" in content_type
                                or "html" in content_type
                            ):
                                parsed = self.clean_html(response.text, str(response.url))
                                parsed["status_code"] = 200
                                parsed["cached"] = False
                                parsed["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")

                                if self.cache:
                                    self.cache.set_page(url, parsed)
                                return parsed
                            else:
                                return {
                                    "url": url,
                                    "title": "",
                                    "clean_text": f"Non-text content-type: {content_type}",
                                    "status_code": response.status_code,
                                    "error": "Non-text content",
                                    "cached": False,
                                }
                        elif response.status_code in [429, 503]:
                            logger.warning(
                                f"[FETCH] Rate limit or service unavailable ({response.status_code}) on {url}, backing off..."
                            )
                            await asyncio.sleep(2 * attempt)
                        elif response.status_code in [403, 404]:
                            logger.info(
                                f"[FETCH] Page {url} returned HTTP {response.status_code}"
                            )
                            return {
                                "url": url,
                                "title": "",
                                "clean_text": "",
                                "status_code": response.status_code,
                                "error": f"HTTP {response.status_code}",
                                "cached": False,
                            }
                except Exception as e:
                    logger.warning(
                        f"[FETCH] Attempt {attempt} failed for {url}: {type(e).__name__} - {e}"
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(1.5 * attempt)
                    else:
                        return {
                            "url": url,
                            "title": "",
                            "clean_text": "",
                            "error": str(e),
                            "cached": False,
                        }

        return {
            "url": url,
            "title": "",
            "clean_text": "",
            "error": "Failed after max retries",
            "cached": False,
        }

    async def fetch_many(
        self, urls: List[str], bypass_cache: bool = False
    ) -> List[Dict[str, str]]:
        tasks = [self.fetch(u, bypass_cache=bypass_cache) for u in urls if u]
        return await asyncio.gather(*tasks)
