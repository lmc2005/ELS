import asyncio
import hashlib
import html
import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import httpx

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CACHE_DIR = _PROJECT_ROOT / "data/cache/news"


class NewsService:
    CACHE_DIR = CACHE_DIR
    MAX_PER_CATEGORY = 20

    FEEDS = {
        "ai_tech": [
            "https://feeds.feedburner.com/TheHackersNews",
            "https://www.artificialintelligence-news.com/feed/",
        ],
        "current_affairs": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        ],
    }

    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def fetch_all(self) -> dict[str, list[dict]]:
        results = {}
        for category, urls in self.FEEDS.items():
            articles = []
            for url in urls:
                try:
                    feed_articles = await self._parse_feed(url, category)
                    articles.extend(feed_articles)
                except Exception:
                    continue
            articles = self._deduplicate(articles)
            results[category] = articles[:self.MAX_PER_CATEGORY]
        return results

    async def _parse_feed(self, url: str, category: str) -> list[dict]:
        articles = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "ELS/1.0"})
                resp.raise_for_status()
                content = resp.text
        except Exception:
            return articles

        items = self._extract_rss_items(content, url)
        missing_image_items = [item for item in items if not item.get("image_url") and item.get("url")][:8]
        if missing_image_items:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                image_results = await asyncio.gather(
                    *[self._fetch_open_graph_image(client, item["url"]) for item in missing_image_items],
                    return_exceptions=True,
                )
            for item, image_result in zip(missing_image_items, image_results):
                if isinstance(image_result, str) and image_result:
                    item["image_url"] = image_result

        for item in items:
            articles.append({
                "category": category,
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "image_url": item.get("image_url", ""),
                "published_at": item.get("published_at", ""),
                "summary": item.get("summary", ""),
                "keywords_json": json.dumps(item.get("keywords", [])),
                "difficulty": "intermediate",
            })
        return articles

    @staticmethod
    def _extract_rss_items(xml_content: str, feed_url: str = "") -> list[dict]:
        items = []
        try:
            root = ET.fromstring(xml_content.encode("utf-8"))
        except ET.ParseError:
            return items

        source = urlparse(feed_url).netloc.replace("www.", "")
        channel_title = root.findtext("./channel/title")
        if channel_title:
            source = channel_title.strip()

        for block in root.findall(".//item"):
            title_text = (block.findtext("title") or "").strip()
            link_text = (block.findtext("link") or "").strip()
            desc_text = block.findtext("description") or ""
            pub_text = (block.findtext("pubDate") or "").strip()
            items.append({
                "title": title_text,
                "url": link_text,
                "source": source,
                "image_url": NewsService._extract_image_url(block, desc_text),
                "summary": NewsService._strip_html(desc_text)[:500],
                "published_at": NewsService._normalize_date(pub_text),
                "keywords": NewsService._keywords_from_title(title_text),
            })
        return items

    @staticmethod
    def _extract_image_url(block: ET.Element, description_html: str) -> str:
        media_namespaces = (
            "{http://search.yahoo.com/mrss/}thumbnail",
            "{http://search.yahoo.com/mrss/}content",
        )
        for tag in media_namespaces:
            node = block.find(tag)
            url = (node.attrib.get("url", "") if node is not None else "").strip()
            if NewsService._looks_like_image_url(url):
                return url

        enclosure = block.find("enclosure")
        if enclosure is not None:
            url = enclosure.attrib.get("url", "").strip()
            mime = enclosure.attrib.get("type", "").lower()
            if mime.startswith("image/") and NewsService._looks_like_image_url(url):
                return url

        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description_html or "", flags=re.IGNORECASE)
        if match and NewsService._looks_like_image_url(match.group(1)):
            return html.unescape(match.group(1))
        return ""

    @staticmethod
    async def _fetch_open_graph_image(client: httpx.AsyncClient, url: str) -> str:
        try:
            response = await client.get(url, headers={"User-Agent": "ELS/1.0"})
            response.raise_for_status()
        except Exception:
            return ""
        html_text = response.text[:300_000]
        patterns = (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, html_text, flags=re.IGNORECASE)
            if match and NewsService._looks_like_image_url(match.group(1)):
                return html.unescape(match.group(1))
        return ""

    @staticmethod
    def _looks_like_image_url(url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        lowered = url.lower()
        blocked = ("logo", "icon", "avatar", "placeholder", "spinner")
        return not any(term in lowered for term in blocked)

    @staticmethod
    def _strip_html(text: str) -> str:
        import re
        return html.unescape(re.sub(r'<[^>]+>', '', text)).strip()

    @staticmethod
    def _normalize_date(value: str) -> str:
        if not value:
            return datetime.utcnow().isoformat()
        try:
            return parsedate_to_datetime(value).isoformat()
        except (TypeError, ValueError, IndexError):
            return value

    @staticmethod
    def _keywords_from_title(title: str) -> list[str]:
        stopwords = {
            "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
            "as", "by", "from", "at", "is", "are", "be", "after", "over", "new",
        }
        words = [
            word.strip(".,:;!?()[]'\"").lower()
            for word in title.split()
            if len(word.strip(".,:;!?()[]'\"")) > 3
        ]
        unique = []
        for word in words:
            if word not in stopwords and word not in unique:
                unique.append(word)
        return unique[:5]

    def _deduplicate(self, articles: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for a in articles:
            h = hashlib.md5(f"{a['url']}{a['title']}".encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                unique.append(a)
        return unique


news_service = NewsService()
