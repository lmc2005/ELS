import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.llm_client import LLMClient, LLMClientError, BudgetExceededError
from app.services.budget_service import BudgetService


class TestLLMClient:
    def test_get_config_reads_settings(self):
        client = LLMClient()
        config = client._get_config()
        assert "base_url" in config
        assert "api_key" in config
        assert "model" in config

    def test_estimate_tokens(self):
        text = "Hello world, this is a test sentence with about twenty words."
        tokens = LLMClient._estimate_tokens(text)
        assert tokens > 0
        # Rough: len/2
        assert tokens == len(text) // 2

    @pytest.mark.asyncio
    async def test_chat_text_requires_config(self):
        client = LLMClient()
        with patch.object(client, '_get_config', return_value={"base_url": "", "api_key": "", "model": "", "timeout": 60}):
            with pytest.raises(LLMClientError, match="LLM not configured"):
                await client.chat_text("hello", "world")

    @pytest.mark.asyncio
    async def test_budget_exceeded_blocks_call(self):
        client = LLMClient()
        budget = BudgetService()
        budget._state["used_rmb"] = 999  # way over budget
        budget._save_state()
        with patch.object(client, '_get_config', return_value={"base_url": "https://x.com", "api_key": "sk-123", "model": "gpt-4", "timeout": 60}):
            with patch('app.services.llm_client.budget_service', budget):
                with pytest.raises(BudgetExceededError):
                    await client.chat_text("hello", "world")
        budget._state["used_rmb"] = 0
        budget._save_state()

    @pytest.mark.asyncio
    async def test_chat_text_stream_trims_cumulative_message_chunks(self):
        client = LLMClient()

        async def fake_stream(*args, **kwargs):
            yield {"choices": [{"message": {"content": "You were saying"}}]}
            yield {"choices": [{"message": {"content": "You were saying I'm great today"}}]}
            yield {"choices": [{"message": {"content": "You were saying I'm great today and you."}}]}

        with patch.object(
            client,
            "_get_config",
            return_value={"base_url": "https://x.com", "api_key": "sk-123", "model": "gpt-4", "timeout": 60},
        ), patch.object(client, "_stream_chat_completion", fake_stream), patch.object(
            client,
            "_record_usage",
            AsyncMock(),
        ), patch("app.services.llm_client.budget_service.check_budget", return_value=True), patch(
            "app.services.llm_client.budget_service.compute_cost",
            return_value=0.0,
        ), patch("app.services.llm_client.budget_service.add_cost"):
            chunks = [chunk async for chunk in client.chat_text_stream("system", "user")]

        assert chunks == ["You were saying", " I'm great today", " and you."]


class TestBudgetService:
    def test_initial_state(self):
        bs = BudgetService()
        status = bs.get_status()
        assert status["monthly_budget_rmb"] > 0
        assert status["is_exceeded"] is False

    def test_add_cost(self):
        bs = BudgetService()
        before = bs.monthly_used_rmb
        bs.add_cost(0.1)
        assert bs.monthly_used_rmb == before + 0.1
        bs._state["used_rmb"] = 0
        bs._save_state()

    def test_exceed_budget(self):
        bs = BudgetService()
        bs._state["used_rmb"] = bs.monthly_budget_rmb + 1
        assert bs.is_exceeded is True
        assert bs.check_budget() is False
        bs._state["used_rmb"] = 0
        bs._save_state()

    def test_warning(self):
        bs = BudgetService()
        bs._state["used_rmb"] = bs.monthly_budget_rmb - 1
        assert bs.is_warning is True
        bs._state["used_rmb"] = 0
        bs._save_state()

    def test_compute_cost(self):
        bs = BudgetService()
        cost = bs.compute_cost(1000, 500)
        assert cost > 0
