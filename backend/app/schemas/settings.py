from pydantic import BaseModel


class SettingsOut(BaseModel):
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    input_price_per_1k_tokens_rmb: float = 0.001
    output_price_per_1k_tokens_rmb: float = 0.002
    monthly_budget_rmb: float = 50.0
    budget_warning_rmb: float = 45.0
    asr_model: str = "qwen3_asr_mlx"
    tts_provider: str = "kokoro"
    tts_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    tts_voice: str = "bm_lewis"

    monthly_used_rmb: float = 0.0
    is_budget_warning: bool = False
    is_budget_exceeded: bool = False


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    input_price_per_1k_tokens_rmb: float | None = None
    output_price_per_1k_tokens_rmb: float | None = None
    monthly_budget_rmb: float | None = None
    budget_warning_rmb: float | None = None
    asr_model: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None


class TestLLMResponse(BaseModel):
    success: bool
    message: str
