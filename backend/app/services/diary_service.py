import json
from pathlib import Path
from app.services.llm_client import llm_client, LLMClientError, BudgetExceededError


class DiaryService:
    def __init__(self):
        self.prompt_path = Path(__file__).parent.parent / "prompts" / "diary_feedback.md"
        self._system_prompt: str | None = None

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = self.prompt_path.read_text()
        return self._system_prompt

    async def analyze(self, diary_text: str) -> dict:
        try:
            result = await llm_client.chat_json(
                system_prompt=self.system_prompt,
                user_prompt=f"Here is my diary entry:\n\n{diary_text}",
                schema={},
                temperature=0.3,
            )
            return result
        except (LLMClientError, BudgetExceededError):
            return {
                "grammar_issues": [],
                "better_version": diary_text,
                "sentence_upgrades": [],
                "useful_phrases": [],
                "overall_advice": "LLM service unavailable. Your diary has been saved.",
            }


diary_service = DiaryService()
