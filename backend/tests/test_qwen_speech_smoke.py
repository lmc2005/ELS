import os
from pathlib import Path

import pytest

from app.config import settings
from app.services.asr_service import asr_service
from app.services.tts_service import tts_service


RUN_LOCAL_QWEN_SMOKE = os.getenv("RUN_LOCAL_QWEN_SMOKE") == "1"


pytestmark = pytest.mark.skipif(
    not RUN_LOCAL_QWEN_SMOKE,
    reason="Set RUN_LOCAL_QWEN_SMOKE=1 to run local Qwen speech smoke tests.",
)


def test_qwen_tts_smoke(tmp_path: Path):
    settings.tts_provider = "qwen3_tts_mlx"
    settings.tts_model = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    settings.tts_voice = "Ryan"

    output_path = tmp_path / "qwen_smoke.wav"
    result = tts_service.synthesize(
        "Hello there. This is a short Qwen TTS smoke test.",
        output_path,
    )

    assert result.exists()
    assert result.stat().st_size > 10_000


def test_qwen_asr_smoke():
    sample_path = Path("/Users/lin20051105/Desktop/ELS/data/media/stories/story_1.wav")
    if not sample_path.exists():
        pytest.skip(f"Sample audio not found: {sample_path}")

    result = asr_service.transcribe_with_model(
        sample_path,
        "qwen3_asr_mlx",
        allow_fallback=False,
    )

    assert result["provider"] == "qwen_mlx"
    assert "Mina" in result["text"]
