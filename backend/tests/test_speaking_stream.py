import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.database import engine
from app.main import app
from app.models.speaking import Note, SpeakingSession, Utterance
from app.routers import speaking as speaking_router
from app.services.asr_service import asr_service
from app.services.llm_client import llm_client


class DummyWS:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, payload: dict):
        self.messages.append(payload)


def test_live_transcript_fallback_requires_longer_phrase():
    assert not speaking_router.should_use_live_transcript_fallback("hello there")
    assert speaking_router.should_use_live_transcript_fallback("hello there I really enjoy travelling")


def test_sanitize_spoken_reply_text_strips_json_and_cjk():
    cleaned = speaking_router.sanitize_spoken_reply_text(
        '{"reply_text":"That sounds great 中中中.","follow_up_question":"What happened next?"}',
        final=True,
    )
    assert cleaned == "That sounds great. What happened next?"


def test_idle_prompt_uses_latest_assistant_question_instead_of_echoing_user():
    transcript = [
        {"role": "user", "text": "I'm great today, and you."},
        {"role": "assistant", "text": "I’m good too, thanks. Better: “And you?” What’s made your day great?"},
    ]
    prompt = speaking_router.build_idle_prompt({"topic": "", "mode": "free_chat"}, transcript, 1)
    assert prompt == "Take your time. What’s made your day great?"


def test_idle_prompt_before_first_user_turn_stays_generic():
    transcript = [
        {"role": "assistant", "text": "Hi, I'm ready. What shall we talk about?"},
    ]
    prompt = speaking_router.build_idle_prompt({"topic": "", "mode": "free_chat"}, transcript, 1)
    assert "Hi I'm ready" not in prompt
    assert "What shall we talk about" not in prompt


@pytest.mark.asyncio
async def test_process_turn_prefers_live_preview_for_low_latency(monkeypatch):
    async def fake_stream_assistant_turn(**kwargs):
        return {
            "assistant_text": "Thanks for sharing that.",
            "assistant_audio_path": None,
            "llm_first_text_ms": 100,
            "first_audio_ms": 180,
            "chunk_count": 1,
        }

    async def fake_send_assistant_turn(*args, **kwargs):
        return None

    monkeypatch.setattr(speaking_router, "stream_assistant_turn", fake_stream_assistant_turn)
    monkeypatch.setattr(speaking_router, "send_assistant_turn", fake_send_assistant_turn)
    async def fail_asr(*args, **kwargs):
        raise AssertionError("Full ASR should be skipped when the live transcript is already strong enough.")

    monkeypatch.setattr(asr_service, "transcribe_with_model_async", fail_asr)

    with Session(engine) as db_session:
        speaking_session = SpeakingSession(
            topic="films",
            mode="free_chat",
            coach_focus="general",
            started_at="2026-05-01T00:00:00Z",
            llm_model=None,
            asr_model="qwen3_asr_mlx",
        )
        db_session.add(speaking_session)
        db_session.commit()
        db_session.refresh(speaking_session)
        session_id = speaking_session.id

    try:
        ws = DummyWS()
        transcript_list: list[dict] = []
        notes_list: list[dict] = []

        turn_index = await speaking_router.process_turn_audio(
            ws=ws,
            session_id=session_id,
            context={
                "topic": "films",
                "mode": "free_chat",
                "coach_focus": "general",
                "llm_model": None,
                "asr_model": "qwen3_asr_mlx",
            },
            transcript_list=transcript_list,
            notes_list=notes_list,
            turn_index=1,
            audio_bytes=b"fake-audio",
            audio_suffix="wav",
            streaming_transcript="I really enjoy watching British films with my friends.",
            background_tasks=None,
        )

        assert turn_index == 2
        final_messages = [message for message in ws.messages if message.get("type") == "transcript.final"]
        assert final_messages
        assert final_messages[-1]["text"] == "I really enjoy watching British films with my friends."
        assert final_messages[-1]["source"] == "stream_fallback"
    finally:
        with Session(engine) as db_session:
            for utterance in db_session.exec(select(Utterance).where(Utterance.session_id == session_id)).all():
                db_session.delete(utterance)
            speaking_session = db_session.get(SpeakingSession, session_id)
            if speaking_session:
                db_session.delete(speaking_session)
            db_session.commit()


def test_speaking_stream_accepts_audio_turn(monkeypatch):
    async def fake_send_assistant_turn(ws, session_id, text, turn_index, transcript_list, live_voice=True):
        await ws.send_json({"type": "assistant.text", "text": text})
        await ws.send_json({"type": "assistant.audio", "audio": base64.b64encode(b"fake-audio").decode()})
        transcript_list.append({"role": "assistant", "text": text})
        return None

    async def fake_stream_assistant_turn(
        *,
        ws,
        session_id,
        turn_index,
        transcript_list,
        teacher_msg,
        user_prompt,
        model_override,
        reply_started_at,
        live_voice=True,
    ):
        text = "Nice to meet you. What do you enjoy talking about?"
        await ws.send_json({"type": "assistant.stream.start", "turn_index": turn_index})
        await ws.send_json({
            "type": "assistant.text.chunk",
            "turn_index": turn_index,
            "chunk_index": 1,
            "text": text,
        })
        await ws.send_json({
            "type": "assistant.audio.chunk",
            "turn_index": turn_index,
            "chunk_index": 1,
            "audio": base64.b64encode(b"fake-audio").decode(),
        })
        await ws.send_json({"type": "assistant.stream.end", "turn_index": turn_index, "text": text})
        transcript_list.append({"role": "assistant", "text": text})
        return {
            "assistant_text": text,
            "assistant_audio_path": None,
            "llm_first_text_ms": 120,
            "first_audio_ms": 240,
            "chunk_count": 1,
        }

    async def fake_chat_text(**kwargs):
        return "Nice to meet you. What do you enjoy talking about?"

    async def fake_chat_json(**kwargs):
        return {"corrections": [], "note_suggestions": [{"type": "phrase", "content": "Nice to meet you"}]}

    monkeypatch.setattr(speaking_router, "send_assistant_turn", fake_send_assistant_turn)
    monkeypatch.setattr(
        asr_service,
        "transcribe_with_model",
        lambda audio_path, model_name, allow_fallback=True: {
            "text": "Hello, I like films and travel.",
            "confidence": 0.99,
            "language": "en",
        },
    )
    monkeypatch.setattr(llm_client, "chat_text", fake_chat_text)
    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    monkeypatch.setattr(speaking_router, "stream_assistant_turn", fake_stream_assistant_turn)

    client = TestClient(app)
    response = client.post(
        "/api/speaking/sessions",
        json={
            "topic": "small talk",
            "mode": "free_chat",
            "coach_focus": "general",
            "asr_model": "qwen3_asr_mlx",
        },
    )
    assert response.status_code == 200
    session_id = response.json()["id"]

    try:
        with client.websocket_connect(f"/api/speaking/sessions/{session_id}/stream") as websocket:
            initial_text = websocket.receive_json()
            initial_audio = websocket.receive_json()
            assert initial_text["type"] == "assistant.text"
            assert initial_audio["type"] == "assistant.audio"

            websocket.send_json(
                {
                    "type": "audio.turn",
                    "mimeType": "audio/wav",
                    "data": base64.b64encode(b"not-a-real-wav-but-asr-is-mocked").decode(),
                }
            )

            received_types = []
            for _ in range(10):
                message = websocket.receive_json()
                received_types.append(message["type"])
                if "assistant.stream.end" in received_types and "turn.metrics" in received_types:
                    break

            assert "status" in received_types
            assert "transcript.final" in received_types
            assert "assistant.stream.start" in received_types
            assert "assistant.text.chunk" in received_types
            assert "assistant.audio.chunk" in received_types
            assert "turn.metrics" in received_types
    finally:
        with Session(engine) as db_session:
            for note in db_session.exec(select(Note).where(Note.session_id == session_id)).all():
                db_session.delete(note)
            for utterance in db_session.exec(select(Utterance).where(Utterance.session_id == session_id)).all():
                db_session.delete(utterance)
            session = db_session.get(SpeakingSession, session_id)
            if session:
                db_session.delete(session)
            db_session.commit()
