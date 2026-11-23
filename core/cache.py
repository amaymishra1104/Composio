import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional


class DiskCache:
    def __init__(self, base_dir: str = "cache"):
        self.base_dir = Path(base_dir)
        self.pages_dir = self.base_dir / "pages"
        self.search_dir = self.base_dir / "search"
        self.llm_dir = self.base_dir / "llm"
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.search_dir.mkdir(parents=True, exist_ok=True)
        self.llm_dir.mkdir(parents=True, exist_ok=True)

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()

    def get_page(self, url: str) -> Optional[dict]:
        path = self.pages_dir / f"{self._hash_key(url)}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set_page(self, url: str, data: dict):
        path = self.pages_dir / f"{self._hash_key(url)}.json"
        try:
            temp_path = path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_path.replace(path)
        except Exception as e:
            print(f"[CACHE] Warning: Failed to write page cache for {url}: {e}")

    def get_search(self, query: str) -> Optional[list]:
        path = self.search_dir / f"{self._hash_key(query)}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set_search(self, query: str, results: list):
        path = self.search_dir / f"{self._hash_key(query)}.json"
        try:
            temp_path = path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            temp_path.replace(path)
        except Exception as e:
            print(f"[CACHE] Warning: Failed to write search cache for {query}: {e}")

    def get_llm(self, prompt_key: str) -> Optional[dict]:
        path = self.llm_dir / f"{self._hash_key(prompt_key)}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set_llm(self, prompt_key: str, data: dict):
        path = self.llm_dir / f"{self._hash_key(prompt_key)}.json"
        try:
            temp_path = path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_path.replace(path)
        except Exception as e:
            print(f"[CACHE] Warning: Failed to write LLM cache: {e}")
