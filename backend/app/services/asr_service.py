from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger("asr")


@dataclass(frozen=True)
class ASRModelOption:
    key: str
    label: str
    provider: str
    model_ref: str


MODEL_OPTIONS = {
    "qwen3_asr_mlx": ASRModelOption(
        key="qwen3_asr_mlx",
        label="Qwen3-ASR-0.6B (MLX, local, recommended)",
        provider="qwen_mlx",
        model_ref="Qwen/Qwen3-ASR-0.6B",
    ),
    "whisper_tiny_en": ASRModelOption(
        key="whisper_tiny_en",
        label="Whisper tiny.en (fallback, fastest)",
        provider="whisper",
        model_ref="tiny.en",
    ),
    "whisper_base_en": ASRModelOption(
        key="whisper_base_en",
        label="Whisper base.en (fallback, balanced)",
        provider="whisper",
        model_ref="base.en",
    ),
    "whisper_small_en": ASRModelOption(
        key="whisper_small_en",
        label="Whisper small.en (fallback, more accurate)",
        provider="whisper",
        model_ref="small.en",
    ),
}

LEGACY_MODEL_ALIASES = {
    "tiny.en": "whisper_tiny_en",
    "base.en": "whisper_base_en",
    "small.en": "whisper_small_en",
}

AVAILABLE_MODELS = {key: option.label for key, option in MODEL_OPTIONS.items()}


class ASRService:
    def __init__(self):
        self._model: Any | None = None
        self._model_key = self._normalize_model_name(settings.asr_model)
        self._option = MODEL_OPTIONS[self._model_key]
        self._lock = threading.RLock()
        self._fallback_models: dict[str, Any] = {}
        self._load_thread_id: int | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="els-asr")

    def _normalize_model_name(self, model_name: str | None) -> str:
        if not model_name:
            return "qwen3_asr_mlx"
        normalized = LEGACY_MODEL_ALIASES.get(model_name, model_name)
        if normalized not in MODEL_OPTIONS:
            logger.warning("Unknown ASR model %s, using default", model_name)
            return "qwen3_asr_mlx"
        return normalized

    def _load_model(self):
        with self._lock:
            option = MODEL_OPTIONS[self._model_key]
            self._model = self._build_model(option)
            self._option = option
            self._load_thread_id = threading.get_ident()
            logger.info("ASR model loaded: %s (%s)", option.key, option.model_ref)

    def _build_model(self, option: ASRModelOption):
        if option.provider == "qwen_mlx":
            from mlx_qwen3_asr import Session

            return Session(model=option.model_ref)

        from faster_whisper import WhisperModel

        return WhisperModel(
            option.model_ref,
            device="cpu",
            compute_type="float32",
            num_workers=1,
        )

    def ensure_loaded(self):
        with self._lock:
            if (
                self._model is not None
                and self._option.provider == "qwen_mlx"
                and self._load_thread_id != threading.get_ident()
            ):
                logger.info("Reloading Qwen ASR on current thread to satisfy MLX runtime requirements")
                self._model = None
                self._load_thread_id = None

            if self._model is None:
                self._load_model()

    def _warm_configured_sync(self) -> bool:
        self.set_model(settings.asr_model)
        self.ensure_loaded()
        return True

    def warm_if_configured(self) -> bool:
        try:
            return self._executor.submit(self._warm_configured_sync).result()
        except Exception as exc:
            logger.warning("ASR preload skipped: %s", exc)
            return False

    @property
    def model(self):
        self.ensure_loaded()
        return self._model

    def set_model(self, model_name: str):
        with self._lock:
            normalized = self._normalize_model_name(model_name)
            thread_mismatch = (
                normalized == self._model_key
                and self._model is not None
                and self._option.provider == "qwen_mlx"
                and self._load_thread_id != threading.get_ident()
            )
            if normalized == self._model_key and self._model is not None and not thread_mismatch:
                return
            self._model_key = normalized
            self._option = MODEL_OPTIONS[self._model_key]
            self._model = None
            self._load_thread_id = None
        self._load_model()

    def _get_fallback_model(self, model_key: str):
        with self._lock:
            model = self._fallback_models.get(model_key)
            if model is not None:
                return model
            option = MODEL_OPTIONS[model_key]
            model = self._build_model(option)
            self._fallback_models[model_key] = model
            logger.info("ASR fallback model loaded: %s (%s)", option.key, option.model_ref)
            return model

    def transcribe(self, audio_path: Path) -> dict:
        self.ensure_loaded()
        wav_path = self._ensure_wav(audio_path)
        try:
            return self._transcribe_with_model_instance(
                model=self.model,
                option=self._option,
                wav_path=wav_path,
            )
        finally:
            if wav_path != audio_path and wav_path.exists():
                wav_path.unlink(missing_ok=True)

    def transcribe_with_model(self, audio_path: Path, model_name: str, allow_fallback: bool = True) -> dict:
        normalized = self._normalize_model_name(model_name)
        try:
            self.set_model(normalized)
            return self.transcribe(audio_path)
        except Exception as exc:
            if not allow_fallback or normalized != "qwen3_asr_mlx":
                raise

            fallback_key = "whisper_base_en"
            fallback_model = self._get_fallback_model(fallback_key)
            fallback_option = MODEL_OPTIONS[fallback_key]
            wav_path = self._ensure_wav(audio_path)
            try:
                logger.warning("Qwen ASR failed (%s); using %s fallback for this request", exc, fallback_key)
                return self._transcribe_with_model_instance(
                    model=fallback_model,
                    option=fallback_option,
                    wav_path=wav_path,
                )
            finally:
                if wav_path != audio_path and wav_path.exists():
                    wav_path.unlink(missing_ok=True)

    async def transcribe_with_model_async(
        self,
        audio_path: Path,
        model_name: str,
        allow_fallback: bool = True,
    ) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.transcribe_with_model,
            audio_path,
            model_name,
            allow_fallback,
        )

    def init_streaming(self, model_name: str):
        normalized = self._normalize_model_name(model_name)
        self.set_model(normalized)
        if self._option.provider != "qwen_mlx":
            raise RuntimeError("Streaming ASR is only available for Qwen3-ASR MLX.")
        return self.model.init_streaming(
            language="en",
            chunk_size_sec=1.0,
            endpointing_mode="fixed",
            endpoint_min_chunk_sec=0.5,
            max_new_tokens=128,
        )

    def feed_streaming(self, state: Any, pcm: np.ndarray):
        if self._option.provider != "qwen_mlx":
            raise RuntimeError("Streaming ASR is only available for Qwen3-ASR MLX.")
        return self.model.feed_audio(pcm.astype(np.float32, copy=False), state)

    def finish_streaming(self, state: Any):
        if self._option.provider != "qwen_mlx":
            raise RuntimeError("Streaming ASR is only available for Qwen3-ASR MLX.")
        return self.model.finish_streaming(state)

    async def init_streaming_async(self, model_name: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.init_streaming, model_name)

    async def feed_streaming_async(self, state: Any, pcm: np.ndarray):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.feed_streaming, state, pcm)

    async def finish_streaming_async(self, state: Any):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.finish_streaming, state)

    @staticmethod
    def _transcribe_with_model_instance(*, model: Any, option: ASRModelOption, wav_path: Path) -> dict:
        if option.provider == "qwen_mlx":
            result = model.transcribe(str(wav_path), language="en")
            return {
                "text": result.text.strip(),
                "language": result.language or "en",
                "confidence": 1.0,
                "provider": option.provider,
                "model": option.model_ref,
            }

        segments, info = model.transcribe(
            str(wav_path),
            beam_size=5,
            language="en",
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return {
            "text": text,
            "language": info.language,
            "confidence": info.language_probability,
            "provider": option.provider,
            "model": option.model_ref,
        }

    @staticmethod
    def _ensure_wav(path: Path) -> Path:
        suffix = path.suffix.lower()
        if suffix == ".wav" and not ASRService._needs_conversion(path):
            return path

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("ffmpeg not found — passing audio as-is; transcription may fail")
            return path

        out = path.parent / f"{path.stem}_asr.wav"
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            str(out),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            if out.exists() and out.stat().st_size > 0:
                return out
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("ffmpeg conversion failed: %s", exc)
        return path

    @staticmethod
    def _needs_conversion(path: Path) -> bool:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return True
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=sample_rate,channels,codec_name",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                sample_rate, channels = int(parts[0]), int(parts[1])
                return sample_rate != 16000 or channels != 1
        except Exception:
            pass
        return True


asr_service = ASRService()
