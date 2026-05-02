from fastapi import APIRouter, HTTPException
from app.schemas.settings import SettingsOut, SettingsUpdate, TestLLMResponse
from app.services.budget_service import budget_service
from app.services.llm_client import llm_client, LLMClientError, BudgetExceededError
from app.services.settings_service import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
def get_settings():
    return settings_service.to_settings_out(budget_service.get_status())


@router.put("", response_model=SettingsOut)
def update_settings(body: SettingsUpdate):
    settings_service.update_settings(body)
    return get_settings()


@router.post("/test-llm", response_model=TestLLMResponse)
async def test_llm():
    try:
        await llm_client.chat_text(
            system_prompt="You are a helpful assistant.",
            user_prompt="Reply with just the word 'OK'.",
            temperature=0.0,
        )
        return TestLLMResponse(success=True, message="LLM connection successful.")
    except (LLMClientError, BudgetExceededError) as e:
        raise HTTPException(status_code=400, detail=str(e))
