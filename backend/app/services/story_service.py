from __future__ import annotations

import html
import json
import logging
import random
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from app.config import settings
from app.services.llm_client import BudgetExceededError, LLMClientError, llm_client
from app.services.tts_service import tts_service

logger = logging.getLogger("story")

ITUNES_NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


class StoryService:
    def __init__(self):
        self.gen_prompt_path = Path(__file__).parent.parent / "prompts" / "story_generation.md"
        self.grade_prompt_path = Path(__file__).parent.parent / "prompts" / "story_grading.md"
        self._gen_prompt: str | None = None
        self._grade_prompt_template: str | None = None

    @property
    def gen_prompt(self) -> str:
        if self._gen_prompt is None:
            self._gen_prompt = self.gen_prompt_path.read_text()
        return self._gen_prompt

    @property
    def grade_prompt_template(self) -> str:
        if self._grade_prompt_template is None:
            self._grade_prompt_template = self.grade_prompt_path.read_text()
        return self._grade_prompt_template

    async def build_story_payload(self, level: int, mode: str) -> dict:
        if mode == "web_story":
            try:
                return await self.fetch_story_from_web(level)
            except Exception as exc:
                logger.warning("Web story fetch failed, falling back to local story: %s", exc)

        story = await self.generate_story(level)
        story.setdefault("story_mode", "local_story")
        story.setdefault("source_name", "AI Tutor")
        story.setdefault("source_url", "")
        return story

    async def generate_story(self, level: int) -> dict:
        try:
            result = await llm_client.chat_json(
                system_prompt=self.gen_prompt,
                user_prompt=f"Generate a story for Level {level}.",
                schema={},
                temperature=0.7,
            )
            result.setdefault("story_mode", "local_story")
            result.setdefault("source_name", "AI Tutor")
            result.setdefault("source_url", "")
            return result
        except (LLMClientError, BudgetExceededError):
            story = self._fallback_story(level)
            story["story_mode"] = "local_story"
            story["source_name"] = "AI Tutor"
            story["source_url"] = ""
            return story

    async def fetch_story_from_web(self, level: int) -> dict:
        items = await self._fetch_storynory_items()
        candidates = self._select_story_candidates(items, level)
        if not candidates:
            raise RuntimeError("No suitable Storynory stories were found.")

        seed = f"{datetime.utcnow().date().isoformat()}-{level}"
        selected = random.Random(seed).choice(candidates[: min(len(candidates), 5)])
        story_text = selected["story_text"]
        key_points = await self.generate_key_points(selected["title"], story_text, level)
        target_seconds = self._target_audio_seconds(level, story_text)

        return {
            "title": selected["title"],
            "story_text": story_text,
            "audio_url": selected["audio_url"],
            "key_points": key_points,
            "difficulty_tags": [
                "web_story",
                "storynory",
                f"level_{level}",
                f"source_duration_{selected['duration_seconds']}",
                f"target_duration_{target_seconds}",
            ],
            "story_mode": "web_story",
            "source_name": "Storynory",
            "source_url": selected["source_url"],
            "target_duration_seconds": target_seconds,
        }

    async def generate_key_points(self, title: str, story_text: str, level: int) -> list[str]:
        prompt = (
            "You are extracting key checkpoints for an English retell exercise. "
            "Return JSON with a `key_points` array of 4 to 6 short English points. "
            "Each point should capture one important event or idea from the story."
        )
        user_prompt = (
            f"Level: {level}\n"
            f"Title: {title}\n\n"
            f"Story:\n{story_text}\n\n"
            "Return only JSON."
        )
        try:
            result = await llm_client.chat_json(
                system_prompt=prompt,
                user_prompt=user_prompt,
                schema={},
                temperature=0.2,
            )
            key_points = result.get("key_points", [])
            if isinstance(key_points, list) and key_points:
                return [str(item).strip() for item in key_points if str(item).strip()][:6]
        except (LLMClientError, BudgetExceededError):
            pass
        return self._heuristic_key_points(story_text, level)

    def synthesize_story_audio(self, story_id: int, story_text: str) -> Path | None:
        output_dir = Path(__file__).parent.parent.parent.parent / "data" / "media" / "stories"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.expected_story_audio_path(story_id)
        try:
            tts_service.synthesize_for_speaking(story_text, output_path)
            return output_path if output_path.exists() and output_path.stat().st_size > 0 else None
        except Exception as exc:
            logger.warning("Story audio synthesis failed for story %s: %s", story_id, exc)
            return None

    def expected_story_audio_path(self, story_id: int) -> Path:
        output_dir = Path(__file__).parent.parent.parent.parent / "data" / "media" / "stories"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"story_{story_id}_{tts_service.cache_key()}.wav"

    async def cache_remote_story_audio(
        self,
        story_id: int,
        audio_url: str,
        max_seconds: int | None = None,
    ) -> Path | None:
        output_dir = Path(__file__).parent.parent.parent.parent / "data" / "media" / "stories"
        output_dir.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(audio_url)
        suffix = Path(parsed.path).suffix or ".mp3"
        output_path = output_dir / f"story_{story_id}{suffix}"
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path

        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            response = await client.get(audio_url)
            response.raise_for_status()
            if not max_seconds:
                output_path.write_bytes(response.content)
                return output_path if output_path.exists() and output_path.stat().st_size > 0 else None

            with tempfile.TemporaryDirectory() as tmpdir:
                raw_path = Path(tmpdir) / f"remote{suffix}"
                raw_path.write_bytes(response.content)
                trimmed_path = output_dir / f"story_{story_id}.mp3"
                if self._trim_audio(raw_path, trimmed_path, max_seconds):
                    return trimmed_path
                output_path.write_bytes(response.content)

        return output_path if output_path.exists() and output_path.stat().st_size > 0 else None

    def synthesize_feedback_audio(self, attempt_id: int, advice_text: str) -> Path | None:
        output_dir = Path(__file__).parent.parent.parent.parent / "data" / "media" / "story_feedback"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"attempt_{attempt_id}_{tts_service.cache_key()}.wav"
        try:
            tts_service.synthesize_for_speaking(advice_text, output_path)
            return output_path if output_path.exists() and output_path.stat().st_size > 0 else None
        except Exception as exc:
            logger.warning("Feedback audio synthesis failed for attempt %s: %s", attempt_id, exc)
            return None

    async def grade_attempt(self, story_text: str, key_points: list, transcript: str) -> dict:
        prompt = self.grade_prompt_template.format(
            story_text=story_text,
            key_points=json.dumps(key_points),
        )
        try:
            result = await llm_client.chat_json(
                system_prompt=prompt,
                user_prompt=f"User transcript:\n\n{transcript}",
                schema={},
                temperature=0.3,
            )
            return result
        except (LLMClientError, BudgetExceededError):
            return {
                "total_score": 0,
                "passed": False,
                "scores": {},
                "key_points_covered": [],
                "key_points_missed": [],
                "advice": "Scoring unavailable. Try again later.",
                "missed_points": [],
                "language_feedback": [],
                "next_tip": "Scoring unavailable. Try again later.",
            }

    async def get_advice(self, story_text: str, key_points: list, transcript: str) -> str:
        prompt = (
            "You are an experienced English teacher. Compare the student's retelling with the original story. "
            "Give friendly, encouraging advice in 3-5 sentences. Focus on:\n"
            "- What key ideas they remembered well\n"
            "- What important points they missed\n"
            "- One specific suggestion for next time\n\n"
            "Be warm and supportive. Use plain English suitable for an English learner."
        )
        user_prompt = (
            f"Original story:\n{story_text}\n\n"
            f"Key points:\n{json.dumps(key_points)}\n\n"
            f"Student's retelling:\n{transcript}"
        )
        try:
            return await llm_client.chat_text(
                system_prompt=prompt,
                user_prompt=user_prompt,
                temperature=0.5,
            )
        except (LLMClientError, BudgetExceededError):
            return "Good effort! Try to remember more key details from the story next time. Practice makes perfect!"

    async def _fetch_storynory_items(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(settings.story_web_feed_url)
            response.raise_for_status()
        root = ET.fromstring(response.text)

        items: list[dict[str, Any]] = []
        for item in root.findall("./channel/item"):
            title = self._xml_text(item.find("title"))
            audio_url = (item.find("enclosure").attrib.get("url") if item.find("enclosure") is not None else "")
            description_html = self._xml_text(item.find("description"))
            summary_html = self._xml_text(item.find("itunes:summary", ITUNES_NS))
            duration_text = self._xml_text(item.find("itunes:duration", ITUNES_NS))
            link = self._xml_text(item.find("link"))

            story_text = self._clean_story_text(description_html or summary_html)
            duration_seconds = self._parse_duration(duration_text)
            source_url = link if link and link != "https://www.storynory.com" else audio_url

            if not title or not audio_url or not story_text:
                continue

            items.append(
                {
                    "title": title,
                    "audio_url": audio_url,
                    "duration_seconds": duration_seconds,
                    "story_text": story_text,
                    "source_url": source_url,
                }
            )
        return items

    def _select_story_candidates(self, items: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
        min_seconds, max_seconds, target_seconds = self._level_duration_range(level)
        blacklist = ("poem", "poetry", "lullaby", "music", "quiz")

        def is_candidate(item: dict[str, Any]) -> bool:
            title = item["title"].lower()
            if any(term in title for term in blacklist):
                return False
            word_count = len(item["story_text"].split())
            estimated_seconds = self._estimate_story_seconds(item["story_text"])
            if word_count < 55 or estimated_seconds > 95:
                return False
            return min_seconds <= estimated_seconds <= max_seconds

        filtered = sorted(
            [item for item in items if is_candidate(item)],
            key=lambda item: (
                abs(self._estimate_story_seconds(item["story_text"]) - target_seconds),
                item["duration_seconds"] if item["duration_seconds"] > 0 else 999,
                item["title"],
            ),
        )
        if filtered:
            return filtered

        fallback = sorted(
            [
                item for item in items
                if len(item["story_text"].split()) >= 50
                and self._estimate_story_seconds(item["story_text"]) <= 90
                and not any(term in item["title"].lower() for term in blacklist)
            ],
            key=lambda item: (
                abs(self._estimate_story_seconds(item["story_text"]) - target_seconds),
                item["duration_seconds"] if item["duration_seconds"] > 0 else 999,
                item["title"],
            ),
        )
        return fallback

    @staticmethod
    def _level_duration_range(level: int) -> tuple[int, int, int]:
        ranges = {
            1: (45, 65, 55),
            2: (48, 72, 58),
            3: (52, 78, 62),
            4: (56, 84, 68),
            5: (60, 90, 72),
            6: (62, 90, 75),
            7: (65, 90, 78),
            8: (68, 90, 80),
            9: (72, 90, 84),
            10: (75, 90, 86),
        }
        return ranges.get(level, ranges[10] if level > 10 else ranges[1])

    @staticmethod
    def _xml_text(node: Any) -> str:
        return (node.text or "").strip() if node is not None and node.text else ""

    @staticmethod
    def _parse_duration(raw_duration: str) -> int:
        if not raw_duration:
            return 0
        parts = [part.strip() for part in raw_duration.split(":") if part.strip()]
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return 0
        if len(numbers) == 3:
            hours, minutes, seconds = numbers
            return hours * 3600 + minutes * 60 + seconds
        if len(numbers) == 2:
            minutes, seconds = numbers
            return minutes * 60 + seconds
        if len(numbers) == 1:
            return numbers[0]
        return 0

    @staticmethod
    def _clean_story_text(raw_html: str) -> str:
        if not raw_html:
            return ""
        text = re.sub(r"<[^>]+>", " ", raw_html)
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r'https?://\S+', '', text, flags=re.IGNORECASE)
        text = re.sub(r'This is a ["\']?small story["\']?.*?(?=Support us!|$)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Read by [^.]+(?:\.)?', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Written by [^.]+(?:\.)?', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Dedicated to [^.]+(?:\.)?', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Support us!.*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r"If you need the MP3 File.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"For now, from me .*?$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text.strip()

    @staticmethod
    def _estimate_story_seconds(story_text: str) -> int:
        word_count = len(story_text.split())
        return max(35, min(95, round(word_count / 2.1) + 12))

    def _target_audio_seconds(self, level: int, story_text: str) -> int:
        min_seconds, max_seconds, target_seconds = self._level_duration_range(level)
        estimated = self._estimate_story_seconds(story_text)
        return max(min_seconds, min(max_seconds, round((estimated + target_seconds) / 2)))

    @staticmethod
    def _trim_audio(source_path: Path, output_path: Path, max_seconds: int) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(source_path),
            "-t",
            str(max_seconds),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=90)
            return output_path.exists() and output_path.stat().st_size > 0
        except (subprocess.SubprocessError, OSError):
            return False

    @staticmethod
    def _heuristic_key_points(story_text: str, level: int) -> list[str]:
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", story_text)
            if sentence.strip()
        ]
        limit = min(6, max(3, level + 2))
        return [sentence[:140] for sentence in sentences[:limit]]

    @staticmethod
    def _fallback_story(level: int) -> dict:
        stories = {
            1: {
                "title": "A Day at the Park",
                "story_text": "Tom went to the park on Saturday. He saw a dog playing with a ball. He sat on a bench and read a book. It was a nice day.",
                "key_points": ["Tom went to the park", "He saw a dog", "He read a book"],
                "difficulty_tags": ["simple", "present_tense"],
            },
            2: {
                "title": "The Lost Keys",
                "story_text": "Sarah couldn't find her keys. She looked in her bag. She checked the kitchen. Finally, she found them in her coat pocket.",
                "key_points": ["Sarah lost keys", "Looked in bag", "Checked kitchen", "Found in coat pocket"],
                "difficulty_tags": ["sequence", "past_tense"],
            },
            3: {
                "title": "The Wrong Train",
                "story_text": "Mia was travelling to visit her aunt. She was tired and got on the wrong train. At first she panicked, but then she met an old woman who was going to the same town by a different route. The woman showed Mia where to change trains, and Mia arrived only ten minutes late. She learned to check the platform number before boarding.",
                "key_points": ["Mia got on the wrong train", "She met an old woman", "The woman helped her change trains", "Mia arrived a little late", "She learned to check the platform"],
                "difficulty_tags": ["sequence", "minor_twist", "past_tense"],
            },
            4: {
                "title": "Two Notes on the Door",
                "story_text": "When Jack came home, he found two notes on his door. One was from his neighbour, saying she had taken in a parcel for him. The other was from his brother, saying he had borrowed Jack's bike for an emergency. Jack was annoyed until he learned that his brother had used the bike to bring medicine to their grandmother. Later, the neighbour's parcel turned out to be a birthday gift from the same brother.",
                "key_points": ["Jack found two notes", "The neighbour kept his parcel", "His brother borrowed his bike", "The bike was used to help their grandmother", "The parcel was a birthday gift"],
                "difficulty_tags": ["multiple_people", "cause_effect", "twist"],
            },
            5: {
                "title": "The Empty Frame",
                "story_text": "A young painter entered a competition with a frame that seemed empty. People laughed until the judge looked closely and saw faint lines showing a room full of light. The painter explained that the picture was about the moment before an idea becomes clear. The judge did not give him first prize, but invited him to study at the academy. The painter realised that being understood by one careful person mattered more than impressing a crowd.",
                "key_points": ["A painter submitted an almost empty frame", "People laughed", "The judge noticed faint lines", "The picture represented an unclear idea", "The painter was invited to study", "The moral is about being understood deeply"],
                "difficulty_tags": ["abstract", "inference", "moral"],
            },
            6: {
                "title": "The Lantern Map",
                "story_text": "During a summer blackout, Lena's neighbourhood lost all street lights. An elderly shopkeeper lit paper lanterns outside his closed store, and soon children began carrying lanterns to mark the safest corners and steps. Lena drew a quick map so people could find the pharmacy, the bus stop, and the only working water tap. By midnight, the map had been copied onto three doors. The next morning, the mayor asked Lena how one teenager had organised half the street, and Lena said she had only written down what everyone else was already trying to do.",
                "key_points": ["A blackout made the street unsafe", "The shopkeeper lit lanterns", "Children marked important corners", "Lena made a map for key places", "The map spread through the neighbourhood", "Lena said the street organised itself together"],
                "difficulty_tags": ["community", "cause_effect", "detail_dense"],
            },
            7: {
                "title": "The Last Rehearsal",
                "story_text": "On the night before a school concert, Amir realised that his violin section sounded polished but lifeless. Instead of making everyone repeat the music faster, he asked each player to describe what they imagined during the slowest part of the piece. One student pictured rain on a train window, another remembered saying goodbye to her sister, and suddenly the group began to play with more shape and patience. The next evening, the audience applauded longest during the quiet passage that had almost been cut. Amir learned that technique had carried the music to the stage, but imagination had carried it into the room.",
                "key_points": ["Amir felt the violin section sounded lifeless", "He asked players what they imagined", "Different personal images changed the performance", "The quiet passage became the highlight", "Amir learned imagination matters as much as technique"],
                "difficulty_tags": ["emotion", "reflection", "inference"],
            },
            8: {
                "title": "The Borrowed Snow Boots",
                "story_text": "When Nora arrived in the mountain town for her internship, she discovered that her suitcase had been sent to another country. The guesthouse owner lent her a huge pair of snow boots that made her walk like a robot, but they also led her into conversation with everyone she met. A bakery clerk recognised the boots from an old family photo, because the owner had worn them during the harsh winter when she delivered bread by sled. By the time Nora's suitcase finally arrived, she had already been invited to two dinners and a weekend hike. She returned the boots with thanks, realising that what first looked like inconvenience had quietly become her introduction to the town.",
                "key_points": ["Nora's suitcase was lost", "The guesthouse owner lent her snow boots", "The boots became a conversation starter", "The bakery clerk knew the boots' history", "Nora made connections before her suitcase arrived", "The inconvenience became a welcome into the town"],
                "difficulty_tags": ["multiple_details", "social_inference", "setting"],
            },
            9: {
                "title": "The Silent Competition",
                "story_text": "At a design fair, two teams were asked to create a low-cost shelter in three hours. One team spoke constantly and kept replacing its own ideas. The other worked almost silently, using short notes and quick gestures because one member had lost her voice. Visitors assumed the quiet team was falling behind, yet by the final hour their structure stood firmly while the louder team was still debating the roof. After the judging, the silent team explained that their constraint had forced them to decide what truly needed saying. The judges awarded them first place, not only for the shelter itself but for proving that clarity is often the hidden engine of speed.",
                "key_points": ["Two teams built shelters at a design fair", "One team talked constantly while the other stayed quiet", "The quiet team used notes because one member lost her voice", "Visitors underestimated the quiet team", "The quiet team finished with a strong structure", "The judges rewarded their clarity and speed"],
                "difficulty_tags": ["contrast", "constraint", "abstract_lesson"],
            },
            10: {
                "title": "The Museum After Closing",
                "story_text": "Every Thursday, Mira stayed late at the museum to catalogue small objects that rarely reached the main exhibits. One evening she found a cracked teacup with a handwritten label that simply read, 'Returned in 1986.' Curious, she searched old files and discovered letters about a cup borrowed by a young historian decades earlier. He had taken it home to sketch the pattern, then vanished from academic life after caring for his ill father. Years later, he returned the cup with a note apologising for the delay and thanking the museum for teaching him how to look closely at ordinary things. Mira proposed a new display about overlooked objects and unfinished lives, arguing that history is not only made of grand events but also of pauses, obligations, and quiet returns. The museum director agreed, and the cracked cup became the first object visitors saw in the new room.",
                "key_points": ["Mira catalogued overlooked museum objects", "She found a cracked teacup with a strange label", "Old files revealed the cup had been borrowed by a historian", "The historian disappeared from academia while caring for his father", "He later returned the cup with an apology and thanks", "Mira built a new exhibit about overlooked objects and quiet histories"],
                "difficulty_tags": ["timeline", "inference", "abstract_theme", "memory"],
            },
        }
        return stories.get(level, stories[10] if level > 10 else stories[1])


story_service = StoryService()
