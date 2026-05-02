from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.database import create_db_and_tables, engine
from app.main import app
from app.models.game import GameProfile, InterrogationRun
from app.services import asr_service as asr_service_module
from app.services.interrogation_service import interrogation_service


def write_silence(path: Path, sample_rate: int = 24000):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros(sample_rate // 8, dtype=np.float32), sample_rate)


def clear_game_tables():
    create_db_and_tables()
    with Session(engine) as session:
        for row in session.exec(select(InterrogationRun)).all():
            session.delete(row)
        profile = session.get(GameProfile, 1)
        if profile:
            session.delete(profile)
        session.commit()


def test_interrogation_start_returns_operation_briefing():
    clear_game_tables()
    client = TestClient(app)
    payload = client.post("/api/games/interrogation/start", json={"mode": "precision"}).json()

    assert payload["run_id"] > 0
    assert payload["case_code"]
    assert payload["operation_name"]
    assert payload["suspect_title"]
    assert payload["mode_key"] == "precision"
    assert payload["mode_label"]
    assert payload["mode_blurb"]
    assert payload["threat_level"]
    assert len(payload["dossier"]) == 4
    assert len(payload["phases"]) == 3
    assert payload["difficulty_label"]
    assert payload["difficulty_stars"] >= 1
    assert len(payload["directives"]) == 3


def test_interrogation_analysis_rewards_stronger_logic():
    strong = interrogation_service.analyze_transcript(
        "Nevertheless, people adapt because they want convenience, and consequently their habits change over time. For instance, remote work changes how they negotiate trust."
    )
    weak = interrogation_service.analyze_transcript("Um, people change. I mean, maybe it depends.")
    assert strong["pressure_delta"] > weak["pressure_delta"]
    assert strong["hit_tags"]
    assert weak["weakness_tags"]


def test_interrogation_stream_can_collapse_on_first_turn(monkeypatch, tmp_path):
    clear_game_tables()

    async def fake_init_streaming_async(model_name: str):
        return SimpleNamespace(text="")

    async def fake_feed_streaming_async(state, pcm):
        state.text = "Nevertheless, people respond quickly because convenience shapes daily behaviour, and for instance remote work changes what they expect from trust."
        return state

    async def fake_finish_streaming_async(state):
        return state

    def fake_tts(text: str, output_path: Path):
        write_silence(output_path)
        return output_path

    monkeypatch.setattr(asr_service_module.asr_service, "init_streaming_async", fake_init_streaming_async)
    monkeypatch.setattr(asr_service_module.asr_service, "feed_streaming_async", fake_feed_streaming_async)
    monkeypatch.setattr(asr_service_module.asr_service, "finish_streaming_async", fake_finish_streaming_async)
    monkeypatch.setattr(interrogation_service, "analyze_transcript", lambda text: {
        "word_count": 24,
        "avg_sentence_len": 24,
        "advanced_linkers": 2,
        "filler_count": 0,
        "repair_count": 0,
        "example_count": 1,
        "hedge_count": 0,
        "pressure_delta": 120,
        "label": "dominant",
        "hit_tags": ["connector chain", "evidence hit"],
        "weakness_tags": [],
    })

    from app.services.tts_service import tts_service

    monkeypatch.setattr(tts_service, "synthesize_for_speaking", fake_tts)

    client = TestClient(app)
    payload = client.post("/api/games/interrogation/start").json()
    with client.websocket_connect(f"/api/games/interrogation/{payload['run_id']}/stream") as websocket:
        opening_text = websocket.receive_json()
        opening_audio = websocket.receive_json()
        meter = websocket.receive_json()
        phase = websocket.receive_json()
        assert opening_text["type"] == "reply.text.delta"
        assert opening_audio["type"] == "reply.audio.chunk"
        assert meter["type"] == "meter.update"
        assert phase["type"] == "phase.update"

        websocket.send_json({"type": "ptt.start"})
        websocket.receive_json()
        pcm = base64.b64encode(np.zeros(3200, dtype=np.int16).tobytes()).decode()
        websocket.send_json({"type": "audio.chunk", "data": pcm})
        websocket.send_json({"type": "ptt.stop"})

        commit = websocket.receive_json()
        if commit["type"] == "ptt.partial":
            commit = websocket.receive_json()
        meter_update = websocket.receive_json()
        next_event = websocket.receive_json()
        while next_event["type"] in {"phase.update", "directive.update"}:
            next_event = websocket.receive_json()
        collapse = next_event
        summary = websocket.receive_json()

        assert commit["type"] == "transcript.commit"
        assert meter_update["type"] == "meter.update"
        assert collapse["type"] == "collapse"
        assert summary["type"] == "round.end"
        assert summary["summary"]["collapsed"] is True
        assert summary["summary"]["max_combo"] >= 1
        assert "rank" in summary["summary"]
        assert summary["summary"]["mode_label"]
        assert summary["summary"]["debrief_headline"]
        assert summary["summary"]["debrief_strength"]
        assert summary["summary"]["debrief_warning"]
