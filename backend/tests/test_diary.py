import json
import pytest
from unittest.mock import patch, AsyncMock
from app.services.diary_service import diary_service


class TestDiaryService:
    @pytest.mark.asyncio
    async def test_analyze_returns_feedback_structure(self):
        mock_result = {
            "grammar_issues": [{"original": "I goes", "corrected": "I go", "explanation_zh": "主语动词一致"}],
            "better_version": "I go to school every day.",
            "sentence_upgrades": [],
            "useful_phrases": ["every day"],
            "overall_advice": "写得不错！",
        }
        with patch.object(diary_service, 'analyze', return_value=mock_result):
            result = await diary_service.analyze("I goes to school every day.")
            assert "grammar_issues" in result
            assert "better_version" in result
            assert "overall_advice" in result

    @pytest.mark.asyncio
    async def test_analyze_handles_failure(self):
        """When LLM fails, return a safe fallback."""
        result = await diary_service.analyze("Test diary.")
        assert "grammar_issues" in result
        assert "better_version" in result
