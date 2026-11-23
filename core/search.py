import asyncio
import logging
import re
import urllib.parse
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
import httpx
from core.cache import DiskCache

logger = logging.getLogger("app-research-agent.search")


class SearchRouter:
    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        composio_api_key: Optional[str] = None,
        tavily_api_key: Optional[str] = None,
        brave_api_key: Optional[str] = None,
        concurrency_limit: int = 5,
    ):
        self.cache = cache or DiskCache()
        self.composio_api_key = composio_api_key
        self.tavily_api_key = tavily_api_key
        self.brave_api_key = brave_api_key
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.active_provider = "unknown"

    async def search(
        self, query: str, max_results: int = 5, bypass_cache: bool = False
    ) -> List[Dict[str, str]]:
        if not bypass_cache:
            cached = self.cache.get_search(query)
            if cached is not None:
                logger.debug(f"[SEARCH CACHE HIT] {query}")
                return cached[:max_results]

        async with self.semaphore:
            results = []

            # 1. Attempt Composio SDK if API key configured (Rule 5)
            if self.composio_api_key:
                try:
                    results = await self._search_composio(query, max_results)
                    if results:
                        self.active_provider = "composio_sdk"
                        logger.info(
                            f"[SEARCH] Provider: COMPOSIO_SDK | Query: '{query}' ({len(results)} results)"
                        )
                        self.cache.set_search(query, results)
                        return results
                except Exception as e:
                    logger.warning(
                        f"[SEARCH] Composio search failed ({e}). Falling back to plain search API..."
                    )

            # 2. Attempt Tavily if API key configured
            if self.tavily_api_key:
                try:
                    results = await self._search_tavily(query, max_results)
                    if results:
                        self.active_provider = "tavily_api"
                        logger.info(
                            f"[SEARCH] Provider: TAVILY_API | Query: '{query}' ({len(results)} results)"
                        )
                        self.cache.set_search(query, results)
                        return results
                except Exception as e:
                    logger.warning(f"[SEARCH] Tavily failed: {e}")

            # 3. Fallback to Plain DDG HTML Search API
            try:
                results = await self._search_ddg_html(query, max_results)
                if results:
                    self.active_provider = "plain_ddg_search"
                    logger.info(
                        f"[SEARCH] Provider: PLAIN_DDG_SEARCH | Query: '{query}' ({len(results)} results)"
                    )
                    self.cache.set_search(query, results)
                    return results
            except Exception as e:
                logger.warning(f"[SEARCH] Plain DDG HTML search failed: {e}")

            # 4. Fallback to DDG Lite API
            try:
                results = await self._search_ddg_lite(query, max_results)
                if results:
                    self.active_provider = "plain_ddg_lite"
                    logger.info(
                        f"[SEARCH] Provider: PLAIN_DDG_LITE | Query: '{query}' ({len(results)} results)"
                    )
                    self.cache.set_search(query, results)
                    return results
            except Exception as e:
                logger.warning(f"[SEARCH] Plain DDG Lite search failed: {e}")

            # 5. Fallback for MCP queries to GitHub API
            if "mcp" in query.lower():
                try:
                    results = await self._search_github_mcp(query, max_results)
                    if results:
                        self.active_provider = "github_mcp_api"
                        logger.info(
                            f"[SEARCH] Provider: GITHUB_MCP_API | Query: '{query}' ({len(results)} results)"
                        )
                        self.cache.set_search(query, results)
                        return results
                except Exception as e:
                    logger.debug(f"[SEARCH] GitHub MCP search failed: {e}")

            logger.warning(f"[SEARCH] All search providers failed or returned 0 results for query '{query}'")
            return []

    async def _search_composio(
        self, query: str, max_results: int
    ) -> List[Dict[str, str]]:
        loop = asyncio.get_event_loop()

        def _run():
            from composio import Action, ComposioToolSet

            toolset = ComposioToolSet(api_key=self.composio_api_key)
            # Try SERPAPI or TAVILY actions if available in Composio
            try:
                res = toolset.execute_action(
                    action=Action.SERPAPI_SEARCH, params={"query": query}
                )
                items = res.get("data", {}).get("organic_results", [])
                out = []
                for item in items[:max_results]:
                    out.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("link", ""),
                            "snippet": item.get("snippet", ""),
                        }
                    )
                return out
            except Exception:
                # Try generic web search
                res = toolset.execute_action(
                    action=Action.TAVILY_SEARCH,
                    params={"query": query, "max_results": max_results},
                )
                items = res.get("data", {}).get("results", [])
                return [
                    {
                        "title": x.get("title", ""),
                        "url": x.get("url", ""),
                        "snippet": x.get("content", ""),
                    }
                    for x in items[:max_results]
                ]

        return await loop.run_in_executor(None, _run)

    async def _search_tavily(
        self, query: str, max_results: int
    ) -> List[Dict[str, str]]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.tavily_api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", ""),
                    }
                    for item in data.get("results", [])[:max_results]
                ]
        return []

    async def _search_ddg_html(
        self, query: str, max_results: int
    ) -> List[Dict[str, str]]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=headers,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for res in soup.select(".result"):
                    title_elem = res.select_one(".result__title a")
                    snippet_elem = res.select_one(".result__snippet")
                    if title_elem and title_elem.get("href"):
                        raw_href = title_elem["href"]
                        # Unpack DDG redirect url if present
                        if "uddg=" in raw_href:
                            match = re.search(r"uddg=([^&]+)", raw_href)
                            if match:
                                actual_url = urllib.parse.unquote(match.group(1))
                            else:
                                actual_url = raw_href
                        else:
                            actual_url = raw_href

                        # Filter out ads or ddg internal links
                        if "duckduckgo.com" not in actual_url and actual_url.startswith(
                            "http"
                        ):
                            title = title_elem.get_text(strip=True)
                            snippet = (
                                snippet_elem.get_text(strip=True)
                                if snippet_elem
                                else ""
                            )
                            results.append(
                                {"title": title, "url": actual_url, "snippet": snippet}
                            )
                            if len(results) >= max_results:
                                break
                return results
        return []

    async def _search_github_mcp(
        self, query: str, max_results: int
    ) -> List[Dict[str, str]]:
        clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
        url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(clean_q)}&sort=stars&order=desc"
        headers = {
            "User-Agent": "app-research-agent/1.0",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                out = []
                for item in items[:max_results]:
                    out.append(
                        {
                            "title": item.get("full_name", ""),
                            "url": item.get("html_url", ""),
                            "snippet": item.get("description", "") or "",
                        }
                    )
                return out
        return []

    async def _search_ddg_lite(
        self, query: str, max_results: int
    ) -> List[Dict[str, str]]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers=headers,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for a in soup.select("a.result-link"):
                    href = a.get("href", "")
                    if "uddg=" in href:
                        m = re.search(r"uddg=([^&]+)", href)
                        href = urllib.parse.unquote(m.group(1)) if m else href
                    if href.startswith("http") and "duckduckgo.com" not in href:
                        results.append(
                            {"title": a.get_text(strip=True), "url": href, "snippet": ""}
                        )
                        if len(results) >= max_results:
                            break
                return results
        return []

    async def _search_github_mcp(
        self, query: str, max_results: int
    ) -> List[Dict[str, str]]:
        clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
        url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(clean_q)}&sort=stars&order=desc"
        headers = {
            "User-Agent": "app-research-agent/1.0",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                out = []
                for item in items[:max_results]:
                    out.append(
                        {
                            "title": item.get("full_name", ""),
                            "url": item.get("html_url", ""),
                            "snippet": item.get("description", "") or "",
                        }
                    )
                return out
        return []
