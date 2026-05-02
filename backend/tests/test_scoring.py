import json
from app.services.story_service import StoryService


class TestStoryScoring:
    def test_fallback_story_level1(self):
        story = StoryService._fallback_story(1)
        assert story["title"] == "A Day at the Park"
        assert len(story["key_points"]) > 0

    def test_fallback_story_level2(self):
        story = StoryService._fallback_story(2)
        assert story["title"] == "The Lost Keys"
        assert len(story["key_points"]) == 4

    def test_pass_conditions(self):
        """Total >= 75 AND main_idea >= 20 to pass."""
        assert self._check_pass(82, 30) is True
        assert self._check_pass(85, 15) is False
        assert self._check_pass(70, 30) is False
        assert self._check_pass(75, 20) is True
        assert self._check_pass(74, 20) is False

    def test_parse_storynory_duration(self):
        assert StoryService._parse_duration("02:09") == 129
        assert StoryService._parse_duration("1:02:03") == 3723
        assert StoryService._parse_duration("") == 0

    def test_clean_storynory_html(self):
        raw = "<p>Hello there.</p><p>And this is a story.</p><p>If you need the MP3 File, visit ...</p>"
        cleaned = StoryService._clean_story_text(raw)
        assert "Hello there." in cleaned
        assert "And this is a story." in cleaned
        assert "MP3 File" not in cleaned

    def test_heuristic_key_points(self):
        story = "Tom woke up. He missed the bus. He ran to school. He arrived late but learned a lesson."
        key_points = StoryService._heuristic_key_points(story, 2)
        assert len(key_points) >= 3
        assert key_points[0].startswith("Tom woke up")

    @staticmethod
    def _check_pass(total_score, main_idea):
        return total_score >= 75 and main_idea >= 20
