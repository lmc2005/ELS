import asyncio
import json
import hashlib
import html
import re
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from app.config import settings
from app.services.llm_client import BudgetExceededError, LLMClientError, llm_client

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CACHE_DIR = _PROJECT_ROOT / "data/cache/images"


class VocabImageService:
    CACHE_DIR = CACHE_DIR
    CACHE_DAYS = settings.image_cache_days
    CACHE_VERSION = 4
    VISUAL_QUERY_HINTS = {
        "apple": "apple fruit",
        "orange": "orange fruit",
        "pear": "pear fruit",
        "peach": "peach fruit",
        "plum": "plum fruit",
        "date": "date fruit",
        "take off": "airplane taking off",
        "wake up": "person waking up",
        "sit down": "person sitting down",
        "stand up": "person standing up",
        "pick up": "person picking up object",
        "turn on": "person turning on light",
        "turn off": "person turning off light",
    }
    AMBIGUOUS_VISUAL_GUARDS = {
        "apple": ("fruit", "apples", "malus"),
        "orange": ("fruit", "citrus", "oranges"),
        "date": ("fruit", "dates", "palm"),
    }

    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def search(self, word: str) -> dict:
        cached = self._check_cache(word)
        if cached:
            return cached

        explanation_task = self._build_explanation(word)
        if self._should_search_images(word):
            images_task = self._fetch_wikimedia(word)
            explanation, results = await asyncio.gather(explanation_task, images_task)
        else:
            explanation = await explanation_task
            results = []

        output = {"word": word, **explanation, "results": results or []}
        self._save_cache(word, output)
        return output

    def _check_cache(self, word: str) -> dict | None:
        cache_file = self._cache_file(word)
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text())
            if data.get("cache_version") != self.CACHE_VERSION:
                return None
            cached_time = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
            if datetime.utcnow() - cached_time < timedelta(days=self.CACHE_DAYS):
                return {
                    key: value
                    for key, value in data.items()
                    if key not in {"cached_at", "cache_version"}
                }
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
        return None

    def _save_cache(self, word: str, data: dict):
        cache_file = self._cache_file(word)
        payload = dict(data)
        payload["cached_at"] = datetime.utcnow().isoformat()
        payload["cache_version"] = self.CACHE_VERSION
        cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def _cache_file(self, word: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", word.strip().lower()).strip("_")[:40] or "word"
        digest = hashlib.sha1(word.strip().lower().encode()).hexdigest()[:10]
        return self.CACHE_DIR / f"{safe}_{digest}.json"

    async def _build_explanation(self, word: str) -> dict:
        term = word.strip()
        prompt = (
            "You explain English vocabulary to an English learner. "
            "Return JSON only with keys: definition, part_of_speech, usage_note, examples. "
            "definition must be one simple English sentence. "
            "examples must be exactly 3 objects with sentence and note. "
            "Use natural English examples and keep the notes short."
        )
        user_prompt = f"Explain this word or phrase: {term}"
        try:
            result = await llm_client.chat_json(
                system_prompt=prompt,
                user_prompt=user_prompt,
                schema={},
                temperature=0.25,
                max_tokens=360,
            )
            examples = result.get("examples", [])
            if isinstance(examples, list):
                examples = [
                    {
                        "sentence": str(item.get("sentence", "") if isinstance(item, dict) else item).strip(),
                        "note": str(item.get("note", "") if isinstance(item, dict) else "").strip(),
                    }
                    for item in examples
                ]
                examples = [item for item in examples if item["sentence"]][:3]
            else:
                examples = []
            if len(examples) < 3:
                examples.extend(self._fallback_examples(term)[len(examples):])
            return {
                "definition": str(result.get("definition") or self._fallback_definition(term)).strip(),
                "part_of_speech": str(result.get("part_of_speech") or self._guess_part_of_speech(term)).strip(),
                "usage_note": str(result.get("usage_note") or self._fallback_usage_note(term)).strip(),
                "examples": examples[:3],
            }
        except (LLMClientError, BudgetExceededError):
            return {
                "definition": self._fallback_definition(term),
                "part_of_speech": self._guess_part_of_speech(term),
                "usage_note": self._fallback_usage_note(term),
                "examples": self._fallback_examples(term),
            }

    def _should_search_images(self, word: str) -> bool:
        normalized = word.strip().lower()
        if normalized in self.VISUAL_QUERY_HINTS:
            return True
        tokens = [token for token in re.split(r"[\s-]+", normalized) if token]
        if len(tokens) > 1:
            return False
        abstract_suffixes = ("tion", "ness", "ment", "ity", "ism", "ance", "ence")
        return not normalized.endswith(abstract_suffixes)

    async def _fetch_wikimedia(self, word: str) -> list[dict]:
        results = []
        try:
            search_query = self.VISUAL_QUERY_HINTS.get(word.strip().lower(), word)
            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": search_query,
                "gsrnamespace": 6,
                "gsrlimit": 12,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime",
                "iiurlwidth": 600,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params=params,
                    headers={"User-Agent": "ELS/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()

            pages = data.get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                image_info = page_data.get("imageinfo", [{}])[0]
                url = image_info.get("url", "")
                mime = image_info.get("mime", "")
                if not url or (mime and (not mime.startswith("image/") or mime == "image/svg+xml")):
                    continue
                ext_meta = image_info.get("extmetadata", {})
                raw_description = ext_meta.get("ImageDescription", {}).get("value", "")
                title = page_data.get("title", "").replace("File:", "")
                description = self._strip_html(raw_description)
                results.append({
                    "title": title,
                    "image_url": url,
                    "thumbnail_url": image_info.get("thumburl", url),
                    "source": "Wikimedia Commons",
                    "license": ext_meta.get("LicenseShortName", {}).get("value", "Unknown"),
                    "description": description,
                })
        except Exception:
            pass
        ranked = self._rank_results(word, results)
        return [item for item in ranked if item.get("relevance_score", 0) >= self._min_relevance(word)][:8]

    @staticmethod
    def _strip_html(value: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()

    def _rank_results(self, word: str, results: list[dict]) -> list[dict]:
        normalized_word = word.strip().lower()
        query = self.VISUAL_QUERY_HINTS.get(normalized_word, word).lower()
        tokens = [token for token in re.split(r"\W+", query) if len(token) > 2]
        negative_terms = {
            "logo", "company", "headquarters", "computer", "iphone", "macbook",
            "software", "store", "campus", "warehouse", "poster", "icon", "symbol",
        }
        guard_terms = self.AMBIGUOUS_VISUAL_GUARDS.get(normalized_word, ())

        def score(item: dict) -> int:
            haystack = f"{item.get('title', '')} {item.get('description', '')}".lower()
            value = 5 if query in haystack else 0
            positive_hits = sum(4 for token in tokens if token in haystack)
            value += positive_hits
            if guard_terms:
                if any(term in haystack for term in guard_terms):
                    value += 12
                else:
                    value -= 18
            if len(tokens) > 1 and positive_hits < 4:
                value -= 8
            value -= sum(10 for term in negative_terms if term in haystack)
            return value

        scored = []
        for item in results:
            item = dict(item)
            item["relevance_score"] = score(item)
            scored.append(item)
        return sorted(scored, key=lambda item: item["relevance_score"], reverse=True)

    @staticmethod
    def _min_relevance(word: str) -> int:
        return 12 if len(word.strip().split()) > 1 else 7

    @staticmethod
    def _guess_part_of_speech(word: str) -> str:
        return "phrase" if len(word.strip().split()) > 1 else "word"

    @staticmethod
    def _fallback_definition(word: str) -> str:
        if len(word.strip().split()) > 1:
            return f'"{word}" is an English phrase whose meaning depends on the situation around it.'
        return f'"{word}" is an English word best learned through example sentences and context.'

    @staticmethod
    def _fallback_usage_note(word: str) -> str:
        if len(word.strip().split()) > 1:
            return "Pay attention to the object and context because phrases often change meaning."
        return "Look at how the word works inside a full sentence."

    @staticmethod
    def _fallback_examples(word: str) -> list[dict]:
        return [
            {"sentence": f"I saw the word \"{word}\" in an article and checked how it was used.", "note": "Notice the surrounding context."},
            {"sentence": f"Can you make a short sentence with \"{word}\"?", "note": "Active use helps memory."},
            {"sentence": f"The meaning of \"{word}\" becomes clearer in a real conversation.", "note": "Context reduces guessing."},
        ]


vocab_image_service = VocabImageService()
