import base64
import asyncio
import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import soundfile as sf
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
from sqlmodel import Session

from app.database import get_session
from app.schemas.speaking import SpeakingSessionCreate, SpeakingSessionOut
from app.models.speaking import SpeakingSession, Utterance, Note
from app.services.llm_client import llm_client, LLMClientError, BudgetExceededError
from app.services.asr_service import asr_service
from app.services.tts_service import tts_service
from app.services.vad_service import vad_service
from app.services.recording_service import recording_service
from app.services.budget_service import budget_service

logger = logging.getLogger("speaking")

router = APIRouter(prefix="/api/speaking", tags=["speaking"])

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "speaking_teacher.md"
TEACHER_PROMPT = PROMPT_PATH.read_text()
IDLE_PROMPT_WORDS = 7
LIVE_TRANSCRIPT_FALLBACK_MIN_WORDS = 4
STREAM_SEGMENT_MIN_WORDS = 3
STREAM_SEGMENT_FORCE_WORDS = 10
STREAMING_PCM_FLUSH_SAMPLES = 3200


def _now():
    return datetime.now(timezone.utc).isoformat()


def _mime_to_suffix(raw_mime: str | None) -> str:
    mime = (raw_mime or "").lower()
    if "mp4" in mime:
        return "m4a"
    if "ogg" in mime:
        return "ogg"
    if "wav" in mime:
        return "wav"
    return "webm"


def _decode_pcm16_base64(data: str) -> np.ndarray:
    raw = base64.b64decode(data)
    if not raw:
        return np.array([], dtype=np.float32)
    pcm_i16 = np.frombuffer(raw, dtype="<i2")
    return (pcm_i16.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


def build_initial_greeting(topic: str, mode: str) -> str:
    if topic:
        if mode == "topic_chat":
            return f"Hi, let's chat about {topic}. What's your first thought?"
        return f"Hi, I'm ready. Shall we start with {topic}?"
    return "Hi, I'm ready. What shall we talk about?"


def has_english_word(text: str) -> bool:
    return bool(re.search(r"\b(?:[A-Za-z]{2,}|I)\b", text or ""))


def spoken_word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def normalize_spoken_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def collapse_repeated_short_tokens(text: str, max_repeat: int = 2) -> str:
    words = text.split()
    if not words:
        return ""

    collapsed: list[str] = []
    last_key = ""
    repeat_count = 0
    for word in words:
        bare = re.sub(r"[^A-Za-z']", "", word).lower()
        key = bare or word.lower()
        if key == last_key and len(key) <= 4:
            repeat_count += 1
            if repeat_count > max_repeat:
                continue
        else:
            last_key = key
            repeat_count = 1
        collapsed.append(word)
    return " ".join(collapsed)


def extract_reply_text_from_json(text: str) -> str:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text or "", re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append((text or "")[start : end + 1].strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        parts: list[str] = []
        for key in ("reply_text", "text", "reply", "assistant_reply", "response"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
        follow_up = payload.get("follow_up_question") or payload.get("question")
        if isinstance(follow_up, str) and follow_up.strip():
            parts.append(follow_up.strip())
        if parts:
            return " ".join(parts)
    return text


def sanitize_spoken_reply_text(text: str, *, final: bool) -> str:
    cleaned = extract_reply_text_from_json(text or "")
    cleaned = re.sub(r"```(?:json)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[{}\[\]`*_#<>|]+", " ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"(?im)^\s*(assistant|teacher|tutor|reply_text|follow_up_question)\s*[:：-]\s*", "", cleaned)
    cleaned = re.sub(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]+", " ", cleaned)
    cleaned = collapse_repeated_short_tokens(cleaned)
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = normalize_spoken_text(cleaned).strip(" ,;:-")
    if final:
        cleaned = trim_spoken_reply(cleaned)
    return cleaned if has_english_word(cleaned) else ""


def make_context_snippet(text: str, max_words: int = IDLE_PROMPT_WORDS) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9' -]+", " ", text or "")
    words = [word for word in cleaned.split() if word]
    return " ".join(words[:max_words]).strip()


def latest_turn_text(transcript_list: list[dict], role: str) -> str:
    for item in reversed(transcript_list):
        if item.get("role") == role and item.get("text"):
            return str(item["text"]).strip()
    return ""


def latest_turn_entry(transcript_list: list[dict]) -> dict | None:
    for item in reversed(transcript_list):
        if item.get("role") in {"user", "assistant"} and item.get("text"):
            return item
    return None


def extract_last_question(text: str) -> str:
    cleaned = normalize_spoken_text(text)
    if not cleaned:
        return ""

    question_spans = re.findall(r'[^?!.]*\?(?:["”’\')\]]*)', cleaned)
    if question_spans:
        return question_spans[-1].strip()

    q_mark = cleaned.rfind("?")
    if q_mark != -1:
        prior = cleaned[: q_mark + 1]
        start = max(prior.rfind(". "), prior.rfind("! "), prior.rfind("? "))
        return prior[start + 2 :].strip() if start != -1 else prior.strip()
    return ""


def build_idle_prompt(context: dict, transcript_list: list[dict], prompt_count: int) -> str:
    topic = (context.get("topic") or "").strip()
    last_user = latest_turn_text(transcript_list, "user")
    last_assistant = latest_turn_text(transcript_list, "assistant")
    latest = latest_turn_entry(transcript_list)
    user_turn_count = sum(1 for item in transcript_list if item.get("role") == "user" and item.get("text"))

    if user_turn_count == 0:
        if topic:
            if prompt_count == 1:
                return f"Take your time. What is your first thought about {topic}?"
            if prompt_count == 2:
                return f"Start simple with {topic}. Just give me one idea in English."
            return f"We can keep it easy on {topic}. Try one short answer first."
        if prompt_count == 1:
            return "Take your time. You can start with one simple idea about your day."
        if prompt_count == 2:
            return "No rush. One short answer is enough to get us going."
        return "Pick the easiest topic in your mind and say one sentence."

    if latest and latest.get("role") == "assistant":
        last_question = extract_last_question(last_assistant)
        if last_question:
            if prompt_count == 1:
                return f"Take your time. {last_question}"
            if prompt_count == 2:
                return f"Let's keep it simple. {last_question}"
            return "Start with one short answer, then add one detail."

    if last_user and latest and latest.get("role") == "user":
        focus = make_context_snippet(last_user)
        if focus:
            if prompt_count == 1:
                return f"You mentioned {focus}. What part of that matters most to you?"
            if prompt_count == 2:
                return f"Stay with {focus}. What is one clear example from your life?"
            return f"Let's keep going with {focus}. What happened next, or why does it matter to you?"

    if topic:
        if prompt_count == 1:
            return f"We are still on {topic}. What is your own view on it?"
        if prompt_count == 2:
            return f"About {topic}, what is one small example or experience you can share?"
        return f"Let's keep it simple on {topic}. Start with one idea you really believe."

    if prompt_count == 1:
        return "Take your time. Start with one simple English idea."
    if prompt_count == 2:
        return "You can begin with I think, I feel, or In my experience."
    return "Pick the easiest thought in your mind and say it in one short sentence."


def should_use_live_transcript_fallback(text: str) -> bool:
    return has_english_word(text) and spoken_word_count(text) >= LIVE_TRANSCRIPT_FALLBACK_MIN_WORDS


def trim_spoken_reply(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip()).strip('"')
    if not cleaned:
        return "Could you tell me a little more about that?"
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    trimmed = " ".join(parts[:2]).strip()
    words = trimmed.split()
    if len(words) > 24:
        trimmed = " ".join(words[:24]).rstrip(",;:")
        if trimmed and trimmed[-1] not in ".?!":
            trimmed += "."
    return trimmed


def pop_stream_segment(buffer: str, force: bool = False) -> tuple[str, str]:
    working = re.sub(r"\s+", " ", buffer or "").strip()
    if not working:
        return "", ""

    sentence_match = re.search(r"^(.+?[.!?]+(?:['\")\]]*)?)(?:\s+|$)", working)
    if sentence_match:
        candidate = sentence_match.group(1).strip()
        remainder = working[sentence_match.end() :].strip()
        if force or spoken_word_count(candidate) >= STREAM_SEGMENT_MIN_WORDS:
            return candidate, remainder

    words = working.split()
    if force:
        return working, ""
    if len(words) >= STREAM_SEGMENT_FORCE_WORDS:
        segment = " ".join(words[:STREAM_SEGMENT_FORCE_WORDS]).strip()
        remainder = " ".join(words[STREAM_SEGMENT_FORCE_WORDS:]).strip()
        if segment and segment[-1] not in ".?!":
            segment += "."
        return segment, remainder
    return "", working


def build_reply_prompt(context: dict, transcript_list: list[dict], transcript_text: str) -> tuple[str, str]:
    teacher_msg = TEACHER_PROMPT
    if context["coach_focus"] != "general":
        teacher_msg += f"\n\nCurrent focus: {context['coach_focus']}."
    teacher_msg += (
        "\n\nReply like a warm British tutor in spoken English. "
        "Plain text only. Keep it short enough to sound instant in voice chat."
    )
    recent_context = "\n".join(
        f"{item['role']}: {item['text']}" for item in transcript_list[-4:]
    )
    user_prompt = (
        f"Session topic: {context['topic'] or 'open conversation'}\n"
        f"Mode: {context['mode']}\n"
        f"Recent conversation:\n{recent_context}\n\n"
        f"Latest user message:\n{transcript_text}\n\n"
        "Reply in plain text only."
    )
    return teacher_msg, user_prompt


async def deliver_turn_feedback(
    *,
    ws: WebSocket,
    session_id: int,
    context: dict,
    transcript_list: list[dict],
    notes_list: list[dict],
    transcript_text: str,
) -> None:
    from app.database import engine

    try:
        prompt = (
            "You are a British English tutor. Return JSON only with keys "
            "`corrections` and `note_suggestions`. "
            "`corrections` can contain at most 1 item. "
            "`note_suggestions` can contain at most 2 items. "
            "Keep every correction reason under 12 words and keep notes concise."
        )
        if context["coach_focus"] != "general":
            prompt += f" Focus especially on {context['coach_focus']}."
        recent_context = "\n".join(
            f"{item['role']}: {item['text']}" for item in transcript_list[-6:]
        )
        user_prompt = (
            f"Session topic: {context['topic'] or 'open conversation'}\n"
            f"Recent conversation:\n{recent_context}\n\n"
            f"Latest user message:\n{transcript_text}\n\n"
            "Return only JSON."
        )
        llm_result = await llm_client.chat_json(
            system_prompt=prompt,
            user_prompt=user_prompt,
            schema={},
            temperature=0.25,
            model_override=context.get("llm_model"),
            max_tokens=140,
        )
        corrections = llm_result.get("corrections", [])
        auto_notes = llm_result.get("note_suggestions", [])

        if corrections:
            await ws.send_json({"type": "correction", "items": corrections[:1]})

        if auto_notes:
            await ws.send_json({"type": "auto_note", "items": auto_notes[:2]})
            with Session(engine) as db_session:
                for note_item in auto_notes[:2]:
                    note_type = note_item.get("type", "phrase") if isinstance(note_item, dict) else "phrase"
                    note_content = note_item.get("content", str(note_item)) if isinstance(note_item, dict) else str(note_item)
                    n = Note(
                        session_id=session_id,
                        source="ai",
                        type=note_type,
                        content=note_content,
                        created_at=_now(),
                    )
                    db_session.add(n)
                    notes_list.append({"source": "ai", "type": n.type, "content": n.content})
                db_session.commit()
    except Exception as exc:
        logger.debug("Post-turn feedback skipped: %s", exc)


async def _send_status(ws: WebSocket, phase: str, message: str, metrics: dict | None = None) -> None:
    payload: dict[str, object] = {"type": "status", "phase": phase, "message": message}
    if metrics:
        payload["metrics"] = metrics
    await ws.send_json(payload)


def _merge_stream_audio(chunk_paths: list[Path], output_path: Path) -> str | None:
    valid_paths = [path for path in chunk_paths if path.exists() and path.stat().st_size > 0]
    if not valid_paths:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(valid_paths) == 1:
        shutil.copyfile(valid_paths[0], output_path)
        return str(output_path)

    try:
        sample_rate = None
        chunks: list[np.ndarray] = []
        for path in valid_paths:
            audio, current_rate = sf.read(path, dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sample_rate is None:
                sample_rate = current_rate
            if current_rate != sample_rate:
                continue
            chunks.append(audio)

        if not chunks or sample_rate is None:
            shutil.copyfile(valid_paths[0], output_path)
            return str(output_path)

        sf.write(output_path, np.concatenate(chunks), sample_rate)
        return str(output_path) if output_path.exists() and output_path.stat().st_size > 0 else None
    except Exception:
        shutil.copyfile(valid_paths[0], output_path)
        return str(output_path)


def _assistant_turn_path(session_id: int, turn_index: int, chunk_index: int | None = None) -> Path:
    session_dir = recording_service.session_dir(session_id)
    if chunk_index is None:
        return session_dir / f"assistant_turn_{turn_index:03d}.wav"
    return session_dir / f"assistant_turn_{turn_index:03d}_chunk_{chunk_index:02d}.wav"


async def _persist_assistant_utterance(
    session_id: int,
    text: str,
    audio_path: str | None,
) -> None:
    from app.database import engine

    with Session(engine) as db_session:
        assistant_utt = Utterance(
            session_id=session_id,
            role="assistant",
            text=text,
            audio_path=audio_path,
            started_at=_now(),
            ended_at=_now(),
            confidence=1.0,
        )
        db_session.add(assistant_utt)
        db_session.commit()


async def send_assistant_turn(
    ws: WebSocket,
    session_id: int,
    text: str,
    turn_index: int,
    transcript_list: list[dict],
    *,
    live_voice: bool = True,
) -> str | None:
    text = sanitize_spoken_reply_text(text, final=True) or "Could you tell me a little more about that?"
    await ws.send_json({"type": "assistant.text", "text": text})
    assistant_audio_path = None
    try:
        tts_path = _assistant_turn_path(session_id, turn_index)
        synthesize = tts_service.synthesize_for_speaking if live_voice else tts_service.synthesize
        await asyncio.to_thread(synthesize, text, tts_path)
        if tts_path.exists() and tts_path.stat().st_size > 0:
            assistant_audio_path = str(tts_path)
            await ws.send_json({
                "type": "assistant.audio",
                "audio": base64.b64encode(tts_path.read_bytes()).decode(),
            })
    except Exception as e:
        logger.warning("TTS failed for turn %d: %s", turn_index, e)
        await ws.send_json({"type": "assistant.audio_error", "message": "Tutor audio could not be generated."})

    await _persist_assistant_utterance(session_id, text, assistant_audio_path)
    transcript_list.append({"role": "assistant", "text": text})
    return assistant_audio_path


async def stream_assistant_turn(
    *,
    ws: WebSocket,
    session_id: int,
    turn_index: int,
    transcript_list: list[dict],
    teacher_msg: str,
    user_prompt: str,
    model_override: str | None,
    reply_started_at: float,
    live_voice: bool = True,
) -> dict:
    assistant_segments: list[str] = []
    raw_buffer = ""
    raw_reply = ""
    tts_tasks: list[asyncio.Task] = []
    first_text_ms: int | None = None
    first_audio_state: dict[str, int | None] = {"value": None}

    await ws.send_json({"type": "assistant.stream.start", "turn_index": turn_index})

    async def queue_segment(segment_text: str, chunk_index: int) -> None:
        nonlocal first_text_ms

        cleaned = sanitize_spoken_reply_text(segment_text, final=False)
        if not cleaned:
            return

        assistant_segments.append(cleaned)
        if first_text_ms is None:
            first_text_ms = round((time.perf_counter() - reply_started_at) * 1000)

        await ws.send_json({
            "type": "assistant.text.chunk",
            "turn_index": turn_index,
            "chunk_index": chunk_index,
            "text": cleaned,
        })

        async def synthesize_chunk() -> Path | None:
            try:
                chunk_path = _assistant_turn_path(session_id, turn_index, chunk_index)
                synthesize = tts_service.synthesize_for_speaking if live_voice else tts_service.synthesize
                await asyncio.to_thread(synthesize, cleaned, chunk_path)
                if not chunk_path.exists() or chunk_path.stat().st_size <= 0:
                    return None

                if first_audio_state["value"] is None:
                    first_audio_state["value"] = round((time.perf_counter() - reply_started_at) * 1000)

                await ws.send_json({
                    "type": "assistant.audio.chunk",
                    "turn_index": turn_index,
                    "chunk_index": chunk_index,
                    "audio": base64.b64encode(chunk_path.read_bytes()).decode(),
                })
                return chunk_path
            except Exception as exc:
                logger.warning("TTS failed for streamed chunk %d on turn %d: %s", chunk_index, turn_index, exc)
                return None

        tts_tasks.append(asyncio.create_task(synthesize_chunk()))

    chunk_index = 0
    async for delta in llm_client.chat_text_stream(
        system_prompt=teacher_msg,
        user_prompt=user_prompt,
        temperature=0.45,
        model_override=model_override,
        max_tokens=72,
    ):
        raw_reply += delta
        raw_buffer += delta
        while True:
            segment, raw_buffer = pop_stream_segment(raw_buffer)
            if not segment:
                break
            chunk_index += 1
            await queue_segment(segment, chunk_index)

    final_segment, _ = pop_stream_segment(raw_buffer, force=True)
    if final_segment:
        chunk_index += 1
        await queue_segment(final_segment, chunk_index)

    if not assistant_segments:
        fallback_text = sanitize_spoken_reply_text(raw_reply, final=True) or "Could you tell me a little more about that?"
        chunk_index += 1
        await queue_segment(fallback_text, chunk_index)

    chunk_results = await asyncio.gather(*tts_tasks, return_exceptions=True) if tts_tasks else []
    chunk_paths = [path for path in chunk_results if isinstance(path, Path)]
    assistant_text = (
        sanitize_spoken_reply_text(" ".join(assistant_segments), final=True)
        or sanitize_spoken_reply_text(raw_reply, final=True)
        or "Could you tell me a little more about that?"
    )
    assistant_audio_path = _merge_stream_audio(chunk_paths, _assistant_turn_path(session_id, turn_index))

    if assistant_text and not chunk_paths:
        await ws.send_json({"type": "assistant.audio_error", "message": "Tutor audio could not be generated."})

    await _persist_assistant_utterance(session_id, assistant_text, assistant_audio_path)
    transcript_list.append({"role": "assistant", "text": assistant_text})
    await ws.send_json({
        "type": "assistant.stream.end",
        "turn_index": turn_index,
        "text": assistant_text,
    })

    return {
        "assistant_text": assistant_text,
        "assistant_audio_path": assistant_audio_path,
        "llm_first_text_ms": first_text_ms or 0,
        "first_audio_ms": first_audio_state["value"] or 0,
        "chunk_count": chunk_index,
    }


async def process_turn_audio(
    *,
    ws: WebSocket,
    session_id: int,
    context: dict,
    transcript_list: list[dict],
    notes_list: list[dict],
    turn_index: int,
    audio_bytes: bytes,
    audio_suffix: str,
    streaming_transcript: str | None = None,
    background_tasks: set[asyncio.Task] | None = None,
) -> int:
    from app.database import engine

    turn_index += 1

    try:
        audio_path = recording_service.save_turn(
            session_id,
            turn_index,
            audio_bytes,
            "user",
            suffix=audio_suffix,
        )
    except Exception:
        audio_path = Path(tempfile.mktemp(suffix=f".{audio_suffix}"))
        audio_path.write_bytes(audio_bytes)

    total_started = time.perf_counter()
    streaming_fallback_text = normalize_spoken_text(streaming_transcript or "")
    transcript_text = ""
    confidence = 0.0
    asr_ms = 0
    transcript_source = "full_asr"
    prefer_live_transcript = should_use_live_transcript_fallback(streaming_fallback_text)

    if prefer_live_transcript:
        transcript_text = streaming_fallback_text
        confidence = 0.82
        transcript_source = "stream_fallback"
        logger.info("Using low-latency streaming transcript for turn %d: '%s'", turn_index, transcript_text[:80])
    else:
        await _send_status(ws, "transcribing", "Refining your transcript with full local ASR...")
        logger.info("Transcribing turn %d, %d bytes...", turn_index, len(audio_bytes))
        asr_started = time.perf_counter()
        try:
            result = await asr_service.transcribe_with_model_async(audio_path, context["asr_model"])
            transcript_text = normalize_spoken_text(result["text"])
            confidence = result["confidence"]
            logger.info("ASR result: '%s' (confidence=%.2f)", transcript_text[:80], confidence)
        except Exception:
            logger.exception("ASR failed for turn %d", turn_index)
            transcript_text = ""
            confidence = 0.0
        asr_ms = round((time.perf_counter() - asr_started) * 1000)

        if not has_english_word(transcript_text) and should_use_live_transcript_fallback(streaming_fallback_text):
            transcript_text = streaming_fallback_text
            confidence = 0.82
            transcript_source = "stream_fallback"
            logger.info("Using streaming transcript fallback for turn %d: '%s'", turn_index, transcript_text[:80])

    if not has_english_word(transcript_text):
        await _send_status(ws, "voicing", "No clear English came through, so the tutor is keeping the chat moving...")
        await send_assistant_turn(
            ws,
            session_id,
            build_idle_prompt(context, transcript_list, 1),
            turn_index,
            transcript_list,
        )
        await _send_status(ws, "assistant_speaking", "Tutor is giving you an easy way back in...")
        return turn_index

    now = _now()
    with Session(engine) as db_session:
        utt = Utterance(
            session_id=session_id,
            role="user",
            text=transcript_text,
            audio_path=str(audio_path),
            started_at=now,
            ended_at=now,
            confidence=confidence,
        )
        db_session.add(utt)
        db_session.commit()

    transcript_list.append({"role": "user", "text": transcript_text})
    await ws.send_json({"type": "transcript.final", "text": transcript_text, "source": transcript_source})

    await _send_status(
        ws,
        "thinking",
        "Using the live transcript fast path and opening the reply stream..."
        if prefer_live_transcript
        else "Using your fallback live transcript and opening the reply stream..."
        if transcript_source == "stream_fallback"
        else "Using the refined local transcript and getting the reply ready...",
    )
    llm_started = time.perf_counter()
    try:
        teacher_msg, user_prompt = build_reply_prompt(context, transcript_list, transcript_text)
        model_override = context.get("llm_model")
        logger.info("Calling LLM (model=%s)...", model_override or "default")
        stream_result = await stream_assistant_turn(
            ws=ws,
            session_id=session_id,
            turn_index=turn_index,
            transcript_list=transcript_list,
            teacher_msg=teacher_msg,
            user_prompt=user_prompt,
            model_override=model_override,
            reply_started_at=llm_started,
        )
        llm_ms = stream_result["llm_first_text_ms"] or round((time.perf_counter() - llm_started) * 1000)
        first_audio_ms = stream_result["first_audio_ms"] or llm_ms
        tts_ms = max(0, first_audio_ms - llm_ms)

        metrics = {
            "asr_ms": asr_ms,
            "llm_ms": llm_ms,
            "tts_ms": tts_ms,
            "first_audio_ms": first_audio_ms,
            "total_ms": round((time.perf_counter() - total_started) * 1000),
            "transcript_source": transcript_source,
            "streaming_reply": True,
        }
        await ws.send_json({"type": "turn.metrics", "metrics": metrics})
        await _send_status(ws, "assistant_speaking", "Tutor is replying in real time...", metrics=metrics)
        if background_tasks is not None:
            task = asyncio.create_task(
                deliver_turn_feedback(
                    ws=ws,
                    session_id=session_id,
                    context=context,
                    transcript_list=list(transcript_list),
                    notes_list=notes_list,
                    transcript_text=transcript_text,
                )
            )
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
    except BudgetExceededError:
        await ws.send_json({"type": "error", "message": "Monthly budget exceeded. LLM unavailable."})
        await _send_status(ws, "listening", "Listening for your next turn...")
    except LLMClientError as e:
        logger.error("LLM error: %s", e)
        await ws.send_json({"type": "error", "message": str(e)})
        await _send_status(ws, "listening", "Listening for your next turn...")
    except Exception as e:
        logger.exception("Unexpected error in turn processing")
        await ws.send_json({"type": "error", "message": f"Unexpected error: {e}"})
        await _send_status(ws, "listening", "Listening for your next turn...")

    return turn_index


@router.post("/sessions", response_model=SpeakingSessionOut)
def create_session(body: SpeakingSessionCreate, s: Session = Depends(get_session)):
    sess = SpeakingSession(
        topic=body.topic,
        mode=body.mode,
        coach_focus=body.coach_focus,
        llm_model=body.llm_model,
        asr_model=body.asr_model,
        started_at=_now(),
    )
    s.add(sess)
    s.commit()
    s.refresh(sess)
    return SpeakingSessionOut(
        id=sess.id,
        topic=sess.topic,
        mode=sess.mode,
        coach_focus=sess.coach_focus,
        started_at=sess.started_at,
        recording_path=sess.recording_path,
        llm_model=sess.llm_model,
        asr_model=sess.asr_model,
    )


@router.get("/sessions/{session_id}", response_model=SpeakingSessionOut)
def get_session_detail(session_id: int, s: Session = Depends(get_session)):
    sess = s.get(SpeakingSession, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return SpeakingSessionOut(
        id=sess.id,
        topic=sess.topic,
        mode=sess.mode,
        coach_focus=sess.coach_focus,
        started_at=sess.started_at,
        ended_at=sess.ended_at,
        summary=sess.summary,
        cost_rmb=sess.cost_rmb,
        recording_path=sess.recording_path,
        llm_model=sess.llm_model,
        asr_model=sess.asr_model,
    )


@router.post("/sessions/{session_id}/end", response_model=SpeakingSessionOut)
def end_session(session_id: int, s: Session = Depends(get_session)):
    sess = s.get(SpeakingSession, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    full_recording = recording_service.finalize_full_recording(session_id)
    sess.ended_at = _now()
    if full_recording:
        sess.recording_path = recording_service.media_url(full_recording)
    s.commit()
    s.refresh(sess)
    return SpeakingSessionOut(
        id=sess.id,
        topic=sess.topic,
        mode=sess.mode,
        coach_focus=sess.coach_focus,
        started_at=sess.started_at,
        ended_at=sess.ended_at,
        summary=sess.summary,
        cost_rmb=sess.cost_rmb,
        recording_path=sess.recording_path,
        llm_model=sess.llm_model,
        asr_model=sess.asr_model,
    )


@router.websocket("/sessions/{session_id}/stream")
async def speaking_stream(ws: WebSocket, session_id: int):
    await ws.accept()
    turn_index = 1
    transcript_list: list[dict] = []
    notes_list: list[dict] = []
    background_tasks: set[asyncio.Task] = set()
    start_cost = budget_service.monthly_used_rmb
    audio_chunks: list[bytes] = []
    audio_suffix = "webm"
    streaming_state = None
    streaming_text = ""
    streaming_pcm_parts: list[np.ndarray] = []
    streaming_pcm_samples = 0
    idle_prompt_count = 0
    idle_prompt_serial = 0

    from app.database import engine
    with Session(engine) as db_session:
        sess = db_session.get(SpeakingSession, session_id)
        if not sess:
            await ws.send_json({"type": "error", "message": "Session not found"})
            await ws.close()
            return

        context = {
            "topic": sess.topic,
            "mode": sess.mode,
            "coach_focus": sess.coach_focus,
            "llm_model": sess.llm_model,
            "asr_model": sess.asr_model,
        }

    # Send initial greeting
    initial_greeting = build_initial_greeting(context["topic"], context["mode"])
    await send_assistant_turn(ws, session_id, initial_greeting, turn_index, transcript_list)

    async def flush_streaming_pcm() -> None:
        nonlocal streaming_state, streaming_text, streaming_pcm_parts, streaming_pcm_samples
        if streaming_state is None or not streaming_pcm_parts:
            return

        pcm = np.concatenate(streaming_pcm_parts).astype(np.float32, copy=False)
        streaming_pcm_parts = []
        streaming_pcm_samples = 0
        try:
            streaming_state = await asr_service.feed_streaming_async(streaming_state, pcm)
            next_text = str(getattr(streaming_state, "text", "") or "").strip()
            if next_text and next_text != streaming_text:
                streaming_text = next_text
                await ws.send_json({"type": "transcript.partial", "text": streaming_text})
        except Exception as exc:
            logger.warning("Streaming ASR chunk failed: %s", exc)
            streaming_state = None

    try:
        while True:
            msg = await ws.receive_json()

            if msg["type"] == "audio.chunk":
                try:
                    raw = base64.b64decode(msg["data"])
                    audio_chunks.append(raw)
                    audio_suffix = _mime_to_suffix(msg.get("mimeType"))
                except Exception:
                    pass

            elif msg["type"] == "pcm.start":
                streaming_state = None
                streaming_text = ""
                streaming_pcm_parts = []
                streaming_pcm_samples = 0
                if context["asr_model"] == "qwen3_asr_mlx":
                    try:
                        streaming_state = await asr_service.init_streaming_async(context["asr_model"])
                        await ws.send_json({"type": "transcript.partial", "text": ""})
                    except Exception as exc:
                        logger.warning("Streaming ASR could not start: %s", exc)

            elif msg["type"] == "pcm.chunk":
                if streaming_state is None:
                    continue
                try:
                    pcm = _decode_pcm16_base64(msg.get("data", ""))
                    if pcm.size == 0:
                        continue
                    streaming_pcm_parts.append(pcm)
                    streaming_pcm_samples += pcm.size
                    if streaming_pcm_samples >= STREAMING_PCM_FLUSH_SAMPLES:
                        await flush_streaming_pcm()
                except Exception as exc:
                    logger.warning("Streaming ASR buffer failed: %s", exc)
                    streaming_state = None
                    streaming_pcm_parts = []
                    streaming_pcm_samples = 0

            elif msg["type"] == "pcm.end":
                if streaming_state is not None:
                    try:
                        await flush_streaming_pcm()
                    except Exception as exc:
                        logger.warning("Streaming ASR flush failed: %s", exc)
                    finally:
                        streaming_state = None
                        streaming_pcm_parts = []
                        streaming_pcm_samples = 0

            elif msg["type"] == "audio.turn":
                try:
                    audio_bytes = base64.b64decode(msg["data"])
                    audio_suffix = _mime_to_suffix(msg.get("mimeType"))
                except Exception:
                    await ws.send_json({"type": "error", "message": "Audio upload failed. Please try again."})
                    await _send_status(ws, "listening", "Listening for your voice again...")
                    continue

                if not audio_bytes:
                    await ws.send_json({"type": "error", "message": "No audio received. Please try again."})
                    await _send_status(ws, "listening", "Listening for your voice again...")
                    continue

                turn_index = await process_turn_audio(
                    ws=ws,
                    session_id=session_id,
                    context=context,
                    transcript_list=transcript_list,
                    notes_list=notes_list,
                    turn_index=turn_index,
                    audio_bytes=audio_bytes,
                    audio_suffix=audio_suffix,
                    streaming_transcript=streaming_text,
                    background_tasks=background_tasks,
                )
                streaming_text = ""
                idle_prompt_count = 0

            elif msg["type"] == "vad.state":
                # Frontend sends its VAD state based on AnalyserNode (raw PCM)
                # Just relay it back so the UI can display speaking/silent indicator
                await ws.send_json({"type": "vad.state", "state": msg.get("state", "silent")})

            elif msg["type"] == "turn.end":
                if not audio_chunks:
                    continue

                combined = b"".join(audio_chunks)
                audio_chunks = []
                turn_index = await process_turn_audio(
                    ws=ws,
                    session_id=session_id,
                    context=context,
                    transcript_list=transcript_list,
                    notes_list=notes_list,
                    turn_index=turn_index,
                    audio_bytes=combined,
                    audio_suffix=audio_suffix,
                    streaming_transcript=streaming_text,
                    background_tasks=background_tasks,
                )
                streaming_text = ""
                idle_prompt_count = 0

            elif msg["type"] == "interrupt":
                # User started speaking while assistant was talking — barge-in
                # Stop TTS and clear pending audio chunks so we start fresh
                audio_chunks = []
                streaming_state = None
                streaming_text = ""
                streaming_pcm_parts = []
                streaming_pcm_samples = 0
                await ws.send_json({"type": "interrupt.ack"})

            elif msg["type"] == "idle.prompt":
                if active_prompt := build_idle_prompt(context, transcript_list, idle_prompt_count + 1):
                    idle_prompt_count += 1
                    idle_prompt_serial += 1
                    await send_assistant_turn(
                        ws,
                        session_id,
                        active_prompt,
                        900 + idle_prompt_serial,
                        transcript_list,
                    )

            elif msg["type"] == "note.update":
                note_content = msg.get("content", "")
                with Session(engine) as db_session:
                    n = Note(
                        session_id=session_id,
                        source="manual",
                        type="phrase",
                        content=note_content,
                        created_at=_now(),
                    )
                    db_session.add(n)
                    db_session.commit()
                notes_list.append({"source": "manual", "type": "phrase", "content": note_content})

            elif msg["type"] == "session.end":
                break

    except WebSocketDisconnect:
        pass
    finally:
        for task in list(background_tasks):
            task.cancel()
        try:
            recording_service.save_transcript(session_id, transcript_list)
            recording_service.save_notes(session_id, notes_list)
            full_recording = recording_service.finalize_full_recording(session_id)
        except Exception:
            full_recording = None

        with Session(engine) as db_session:
            sess = db_session.get(SpeakingSession, session_id)
            if sess:
                sess.ended_at = _now()
                total_cost = max(0.0, budget_service.monthly_used_rmb - start_cost)
                sess.cost_rmb = total_cost
                if full_recording:
                    sess.recording_path = recording_service.media_url(full_recording)
                if transcript_list:
                    lines = [f"{t['role']}: {t['text'][:100]}" for t in transcript_list[-6:]]
                    sess.summary = "\n".join(lines)
                db_session.commit()
