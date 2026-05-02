import os
from pathlib import Path
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Qwen speech models are pulled from Hugging Face. On some macOS setups the
# Xet-backed downloader stalls or fails with HTTP 416 during large model
# downloads, so we force the regular HTTP path.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
try:
    from huggingface_hub import file_download as _hf_file_download

    _hf_file_download.is_xet_available = lambda: False
except Exception:
    pass


class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "sqlite:///data/db/app.db"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60

    input_price_per_1k_tokens_rmb: float = 0.001
    output_price_per_1k_tokens_rmb: float = 0.002
    monthly_budget_rmb: float = 50.0
    budget_warning_rmb: float = 45.0

    asr_model: str = "qwen3_asr_mlx"
    tts_provider: str = "kokoro"
    tts_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    tts_voice: str = "bm_lewis"
    story_web_feed_url: str = "https://www.storynory.com/feeds/stories/"

    news_fetch_time: str = "07:30"
    image_cache_days: int = 30

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
