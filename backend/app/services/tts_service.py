from __future__ import annotations

import logging
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.config import settings

logger = logging.getLogger("tts")


@dataclass(frozen=True)
class TTSProviderOption:
    key: str
    label: str


PROVIDER_OPTIONS = {
    "qwen3_tts_mlx": TTSProviderOption(
        key="qwen3_tts_mlx",
        label="Qwen3-TTS MLX (local Apple Silicon)",
    ),
    "qwen3_tts_torch": TTSProviderOption(
        key="qwen3_tts_torch",
        label="Qwen3-TTS PyTorch",
    ),
    "kokoro": TTSProviderOption(
        key="kokoro",
        label="Kokoro British voice (local low latency)",
    ),
    "macos_say": TTSProviderOption(
        key="macos_say",
        label="macOS Say fallback",
    ),
}

LEGACY_VOICE_ALIASES = {
    "en_GB": "Ryan",
    "british": "Ryan",
    "British": "Ryan",
    "Daniel": "Ryan",
    "daniel": "Ryan",
}

QWEN_LANGUAGE = "English"
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"
KOKORO_SAMPLE_RATE = 24000
KOKORO_DEFAULT_VOICE = "bm_lewis"
KOKORO_SPEED = 0.96
KOKORO_VOICE_ALIASES = {
    "Ryan": "bm_lewis",
    "ryan": "bm_lewis",
    "Daniel": "bm_daniel",
    "daniel": "bm_daniel",
    "British": "bm_lewis",
    "british": "bm_lewis",
    "en_GB": "bm_lewis",
}
KOKORO_VOICES = {
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
}


class TTSService:
    def __init__(self):
        self._engine: Any | None = None
        self._loaded_signature: tuple[str, str] | None = None
        self._warmup_signature: tuple[str, str] | None = None
        self._warmup_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._macos_voices: set[str] | None = None
        self._kokoro_pipeline: Any | None = None
        self._kokoro_generation_warmed = False

    def _normalize_provider(self, provider: str | None) -> str:
        if not provider:
            return "kokoro"
        if provider not in PROVIDER_OPTIONS:
            logger.warning("Unknown TTS provider %s, using kokoro", provider)
            return "kokoro"
        return provider

    def _normalize_voice(self, voice: str | None) -> str:
        if not voice:
            return "Ryan"
        return LEGACY_VOICE_ALIASES.get(voice, voice)

    def _provider_signature(self, provider_key: str) -> tuple[str, str]:
        return provider_key, settings.tts_model

    def cache_key(self) -> str:
        provider_key = self._normalize_provider(settings.tts_provider)
        voice = self._normalize_voice(settings.tts_voice)
        model_ref = settings.tts_model
        digest = hashlib.sha1(f"{provider_key}|{model_ref}|{voice}".encode("utf-8")).hexdigest()[:10]
        safe_voice = re.sub(r"[^a-z0-9]+", "-", voice.lower()).strip("-") or "voice"
        return f"{provider_key}-{safe_voice}-{digest}"

    @staticmethod
    def _repo_cache_dir(model_ref: str) -> Path | None:
        if "/" not in model_ref:
            return None
        return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_ref.replace('/', '--')}"

    def _model_cache_complete(self, model_ref: str) -> bool:
        model_path = Path(model_ref)
        if model_path.exists():
            return True

        repo_cache_dir = self._repo_cache_dir(model_ref)
        if not repo_cache_dir or not repo_cache_dir.exists():
            return False

        if any(repo_cache_dir.glob("blobs/*.incomplete")):
            return False

        snapshot_dir = repo_cache_dir / "snapshots"
        return snapshot_dir.exists() and any(snapshot_dir.iterdir())

    def _load_engine(self, provider_key: str):
        with self._lock:
            signature = self._provider_signature(provider_key)
            if self._loaded_signature == signature and self._engine is not None:
                return

            if provider_key == "qwen3_tts_mlx":
                self._clear_incomplete_hf_cache(settings.tts_model)
                from qwen_tts import Qwen3TTSMLXModel

                self._engine = Qwen3TTSMLXModel.from_pretrained(settings.tts_model)
            elif provider_key == "qwen3_tts_torch":
                self._clear_incomplete_hf_cache(settings.tts_model)
                from qwen_tts import Qwen3TTSModel

                self._engine = Qwen3TTSModel.from_pretrained(settings.tts_model)
            elif provider_key == "kokoro":
                self._engine = None
                self._load_kokoro_pipeline()
            else:
                self._engine = None

            self._loaded_signature = signature
            logger.info("TTS engine ready: %s (%s)", provider_key, settings.tts_model)

    def _is_engine_ready(self, provider_key: str) -> bool:
        signature = self._provider_signature(provider_key)
        return self._loaded_signature == signature and self._engine is not None

    def _ensure_background_warmup(self, provider_key: str) -> None:
        if provider_key not in {"qwen3_tts_mlx", "qwen3_tts_torch", "kokoro"}:
            return
        signature = self._provider_signature(provider_key)
        if self._is_engine_ready(provider_key):
            return
        if self._warmup_signature == signature and self._warmup_thread and self._warmup_thread.is_alive():
            return

        def warmup() -> None:
            try:
                self._load_engine(provider_key)
            except Exception as exc:
                logger.warning("Background TTS warmup failed for %s: %s", provider_key, exc)

        self._warmup_signature = signature
        self._warmup_thread = threading.Thread(target=warmup, name=f"tts-warmup-{provider_key}", daemon=True)
        self._warmup_thread.start()

    @staticmethod
    def _clear_incomplete_hf_cache(model_ref: str) -> None:
        model_path = Path(model_ref)
        if model_path.exists() or "/" not in model_ref:
            return

        repo_cache_dir = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_ref.replace('/', '--')}"
        if not repo_cache_dir.exists():
            return

        removed = 0
        stale_before = time.time() - 1800
        for pattern in ("blobs/*.incomplete",):
            for file_path in repo_cache_dir.glob(pattern):
                if file_path.stat().st_mtime >= stale_before:
                    continue
                file_path.unlink(missing_ok=True)
                removed += 1

        lock_dir = Path.home() / ".cache" / "huggingface" / "hub" / ".locks" / f"models--{model_ref.replace('/', '--')}"
        for file_path in lock_dir.glob("*.lock") if lock_dir.exists() else ():
            if file_path.stat().st_mtime >= stale_before:
                continue
            file_path.unlink(missing_ok=True)
            removed += 1

        if removed:
            logger.info("Cleared %d stale Hugging Face download files for %s", removed, model_ref)

    def synthesize(self, text: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        provider_key = self._normalize_provider(settings.tts_provider)
        if provider_key == "kokoro":
            return self._synthesize_with_kokoro(text, output_path)

        if provider_key in {"qwen3_tts_mlx", "qwen3_tts_torch"} and not self._is_engine_ready(provider_key):
            if self._model_cache_complete(settings.tts_model):
                logger.info("Qwen TTS cache is complete; loading the model for this request")
            else:
                self._ensure_background_warmup(provider_key)
                if shutil.which("say") and shutil.which("ffmpeg"):
                    logger.info("Qwen TTS is still downloading or warming up; using macOS Say fallback for this request")
                    return self._synthesize_with_macos_say(text, output_path)

        errors: list[str] = []
        for candidate in self._provider_chain(provider_key):
            try:
                if candidate == "macos_say":
                    return self._synthesize_with_macos_say(text, output_path)
                if candidate == "kokoro":
                    return self._synthesize_with_kokoro(text, output_path)
                return self._synthesize_with_qwen(candidate, text, output_path)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                logger.warning("TTS provider %s failed: %s", candidate, exc)

        raise RuntimeError("No usable TTS backend found. " + " | ".join(errors))

    def synthesize_for_speaking(self, text: str, output_path: Path) -> Path:
        """Use a low-latency British voice for live conversation."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for candidate in ("kokoro", "macos_say"):
            try:
                if candidate == "kokoro":
                    return self._synthesize_with_kokoro(text, output_path)
                return self._synthesize_with_macos_say(text, output_path)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                logger.warning("Speaking TTS provider %s failed: %s", candidate, exc)
        raise RuntimeError("No usable speaking TTS backend found. " + " | ".join(errors))

    def warm_speaking_voice(self) -> bool:
        try:
            self._load_kokoro_pipeline()
            if not self._kokoro_generation_warmed:
                with tempfile.TemporaryDirectory() as tmpdir:
                    self._synthesize_with_kokoro("Ready.", Path(tmpdir) / "kokoro_warmup.wav")
            return True
        except Exception as exc:
            logger.warning("Kokoro speaking voice preload skipped: %s", exc)
            return False

    def warm_if_cached(self) -> bool:
        provider_key = self._normalize_provider(settings.tts_provider)
        if provider_key not in {"qwen3_tts_mlx", "qwen3_tts_torch"}:
            return False
        if self._is_engine_ready(provider_key):
            return True
        if not self._model_cache_complete(settings.tts_model):
            return False
        self._load_engine(provider_key)
        return self._is_engine_ready(provider_key)

    def _provider_chain(self, provider_key: str) -> list[str]:
        if provider_key == "macos_say":
            return ["macos_say"]
        if provider_key == "kokoro":
            return ["kokoro", "macos_say"]
        return [provider_key, "macos_say"]

    def _load_kokoro_pipeline(self):
        with self._lock:
            if self._kokoro_pipeline is not None:
                return self._kokoro_pipeline
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from kokoro import KPipeline

            self._kokoro_pipeline = KPipeline(
                lang_code="b",
                repo_id=KOKORO_REPO_ID,
                device="cpu",
            )
            logger.info("Kokoro British TTS pipeline ready: %s", KOKORO_REPO_ID)
            return self._kokoro_pipeline

    def _resolve_kokoro_voice(self) -> str:
        preferred = self._normalize_voice(settings.tts_voice)
        preferred = KOKORO_VOICE_ALIASES.get(preferred, preferred).strip()
        if preferred in KOKORO_VOICES:
            return preferred
        lowered = preferred.lower()
        if lowered in KOKORO_VOICES:
            return lowered
        return KOKORO_DEFAULT_VOICE

    def _synthesize_with_kokoro(self, text: str, output_path: Path) -> Path:
        segments = self._segment_text(text)
        if not segments:
            raise ValueError("No text to synthesize.")

        pipeline = self._load_kokoro_pipeline()
        voice = self._resolve_kokoro_voice()
        audio_segments: list[np.ndarray] = []
        silence = np.zeros(int(KOKORO_SAMPLE_RATE * 0.12), dtype=np.float32)
        kokoro_text = "\n".join(segments)

        for result in pipeline(
            kokoro_text,
            voice=voice,
            speed=KOKORO_SPEED,
            split_pattern=r"\n+",
        ):
            audio = getattr(result, "audio", None)
            if audio is None and isinstance(result, tuple) and result:
                audio = result[-1]
            if audio is None:
                continue
            wav = np.asarray(audio, dtype=np.float32)
            if wav.size:
                audio_segments.append(wav)

        if not audio_segments:
            raise RuntimeError("Kokoro returned no audio.")

        stitched: list[np.ndarray] = []
        for index, wav in enumerate(audio_segments):
            stitched.append(wav)
            if index < len(audio_segments) - 1:
                stitched.append(silence)

        sf.write(str(output_path), np.concatenate(stitched), KOKORO_SAMPLE_RATE)
        self._kokoro_generation_warmed = True
        logger.info("Using Kokoro British voice: %s", voice)
        return output_path

    def _synthesize_with_qwen(self, provider_key: str, text: str, output_path: Path) -> Path:
        segments = self._segment_text(text)
        if not segments:
            raise ValueError("No text to synthesize.")

        self._load_engine(provider_key)
        engine = self._engine
        if engine is None:
            raise RuntimeError("Qwen TTS engine was not initialized.")

        speaker = self._resolve_speaker(engine)
        audio_segments: list[np.ndarray] = []
        sample_rate = None
        silence = None

        for segment in segments:
            wavs, current_sr = engine.generate_custom_voice(
                text=segment,
                language=QWEN_LANGUAGE,
                speaker=speaker,
                do_sample=True,
                max_new_tokens=224,
            )
            wav = np.asarray(wavs[0], dtype=np.float32)
            sample_rate = current_sr
            silence = np.zeros(int(current_sr * 0.18), dtype=np.float32)
            audio_segments.append(wav)

        if not audio_segments or sample_rate is None:
            raise RuntimeError("Qwen TTS returned no audio.")

        stitched: list[np.ndarray] = []
        for index, wav in enumerate(audio_segments):
            stitched.append(wav)
            if silence is not None and index < len(audio_segments) - 1:
                stitched.append(silence)

        final_audio = np.concatenate(stitched, axis=0)
        sf.write(str(output_path), final_audio, sample_rate)
        return output_path

    def _resolve_speaker(self, engine: Any) -> str:
        preferred = self._normalize_voice(settings.tts_voice)
        supported_lookup = None
        if hasattr(engine, "get_supported_speakers"):
            supported = engine.get_supported_speakers() or []
            supported_lookup = {speaker.lower(): speaker for speaker in supported}
            if supported_lookup:
                if preferred.lower() in supported_lookup:
                    return supported_lookup[preferred.lower()]
                if "ryan" in supported_lookup:
                    return supported_lookup["ryan"]
                return next(iter(supported_lookup.values()))
        return preferred

    def _synthesize_with_macos_say(self, text: str, output_path: Path) -> Path:
        say = shutil.which("say")
        ffmpeg = shutil.which("ffmpeg")
        if not say or not ffmpeg:
            raise RuntimeError(
                "No usable TTS backend found. Install Qwen3-TTS MLX, or use macOS 'say' with ffmpeg."
            )

        voice = self._resolve_macos_voice()
        logger.info("Using macOS Say fallback voice: %s", voice)
        enriched_text = self._build_macos_say_text(text)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_audio = Path(tmpdir) / "speech.aiff"
            say_result = subprocess.run(
                [say, "-v", voice, "-o", str(tmp_audio), enriched_text],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if say_result.returncode != 0:
                raise RuntimeError(f"macOS say failed: {say_result.stderr}")

            ffmpeg_result = subprocess.run(
                [ffmpeg, "-y", "-i", str(tmp_audio), str(output_path)],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if ffmpeg_result.returncode != 0:
                raise RuntimeError(f"ffmpeg TTS conversion failed: {ffmpeg_result.stderr}")
        return output_path

    def _resolve_macos_voice(self) -> str:
        preferred = self._normalize_voice(settings.tts_voice)
        voices = self._load_macos_voices()

        if preferred in voices:
            return preferred

        for candidate in (
            "Eddy (English (UK))",
            "Reed (English (UK))",
            "Shelley (English (UK))",
            "Flo (English (UK))",
            "Daniel",
        ):
            if candidate in voices:
                return candidate

        return "Daniel"

    def _load_macos_voices(self) -> set[str]:
        if self._macos_voices is not None:
            return self._macos_voices

        say = shutil.which("say")
        if not say:
            self._macos_voices = set()
            return self._macos_voices

        try:
            result = subprocess.run(
                [say, "-v", "?"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            voices = set()
            for line in result.stdout.splitlines():
                voice = line.split("#", 1)[0].rsplit(None, 1)[0].strip()
                if voice:
                    voices.add(voice)
            self._macos_voices = voices
        except (subprocess.SubprocessError, OSError):
            self._macos_voices = set()
        return self._macos_voices

    def _build_macos_say_text(self, text: str) -> str:
        segments = self._segment_text(text)
        if not segments:
            return text

        enriched: list[str] = []
        for segment in segments:
            pause_ms = 220
            rate = 175
            if segment.endswith("?"):
                rate = 168
                pause_ms = 260
            elif segment.endswith("!"):
                rate = 182
                pause_ms = 240
            elif len(segment) > 120:
                rate = 166

            enriched.append(f"[[rate {rate}]] {segment} [[slnc {pause_ms}]]")

        return " ".join(enriched)

    @staticmethod
    def _segment_text(text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []

        sentences = re.split(r"(?<=[.!?;:])\s+", normalized)
        chunks: list[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if not current:
                current = sentence
                continue
            if len(current) + len(sentence) + 1 <= 220:
                current = f"{current} {sentence}"
            else:
                chunks.append(current)
                current = sentence

        if current:
            chunks.append(current)
        return chunks


tts_service = TTSService()
