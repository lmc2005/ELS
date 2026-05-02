from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.database import engine, get_session
from app.models.game import InterrogationRun
from app.schemas.games import (
    GameProfileOut,
    GamesHistoryOut,
    InterrogationStartIn,
    InterrogationStartOut,
    InterrogationSummaryOut,
    ShopPurchaseIn,
    ShopPurchaseOut,
)
from app.services.asr_service import asr_service
from app.services.games_service import games_service
from app.services.interrogation_service import interrogation_service
from app.services.tts_service import tts_service

router = APIRouter(prefix="/api/games", tags=["games"])

STREAMING_PCM_FLUSH_SAMPLES = 12000


def _decode_pcm16_base64(value: str) -> np.ndarray:
    raw = base64.b64decode(value)
    if not raw:
        return np.array([], dtype=np.float32)
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm


def _write_pcm_wav(path: Path, audio_parts: list[np.ndarray], sample_rate: int = 16000) -> Path | None:
    if not audio_parts:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.concatenate(audio_parts).astype(np.float32, copy=False)
    sf.write(str(path), audio, sample_rate)
    return path if path.exists() and path.stat().st_size > 0 else None


@router.get("/profile", response_model=GameProfileOut)
def get_profile(session: Session = Depends(get_session)):
    return games_service.profile_payload(session)


@router.get("/history", response_model=GamesHistoryOut)
def get_history(session: Session = Depends(get_session)):
    return GamesHistoryOut(entries=games_service.history_payload(session))


@router.post("/shop/purchase", response_model=ShopPurchaseOut)
def purchase_upgrade(body: ShopPurchaseIn, session: Session = Depends(get_session)):
    try:
        profile, level = games_service.purchase_upgrade(session, body.upgrade_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ShopPurchaseOut(purchased_key=body.upgrade_key, level=level, profile=profile)


@router.post("/interrogation/start", response_model=InterrogationStartOut)
def start_interrogation(body: InterrogationStartIn | None = None, session: Session = Depends(get_session)):
    run = interrogation_service.create_run(session, mode=(body.mode if body else None))
    analysis = interrogation_service.parse_analysis(run)
    return InterrogationStartOut(
        run_id=run.id or 0,
        case_code=run.case_code,
        operation_name=run.operation_name,
        topic=run.topic,
        suspect_name=run.suspect_name,
        suspect_title=str(analysis.get("suspect_title", "Suspect")),
        mode_key=str(analysis.get("mode_key", "classic")),
        mode_label=str(analysis.get("mode_label", "Classic Pressure")),
        mode_blurb=str(analysis.get("mode_blurb", "")),
        threat_level=str(analysis.get("threat_level", "High pressure room")),
        mission_brief=str(analysis.get("mission_brief", "")),
        opening_scene=str(analysis.get("opening_scene", "")),
        defense_meter=int(analysis.get("defense_meter", 100)),
        target_rounds=int(analysis.get("target_rounds", 6)),
        active_phase=int(analysis.get("active_phase", 0)),
        difficulty_label=str(analysis.get("difficulty_label", "Black Chamber")),
        difficulty_stars=int(analysis.get("difficulty_stars", 4)),
        dossier=analysis.get("dossier", []),
        phases=analysis.get("phases", []),
        loadout=analysis.get("loadout", []),
        directives=analysis.get("directives", []),
    )


@router.websocket("/interrogation/{run_id}/stream")
async def interrogation_stream(ws: WebSocket, run_id: int):
    await ws.accept()
    streaming_state = None
    streaming_text = ""
    streaming_parts: list[np.ndarray] = []
    streaming_part_samples = 0
    user_audio_round: list[np.ndarray] = []
    user_audio_files: list[Path] = []
    reply_lock = asyncio.Lock()
    reply_task: asyncio.Task | None = None
    reply_cancel_event: asyncio.Event | None = None
    room_open = True

    with Session(engine) as session:
        run = session.get(InterrogationRun, run_id)
        if not run:
            await ws.send_json({"type": "error", "message": "Interrogation run not found."})
            await ws.close()
            return
        analysis = interrogation_service.parse_analysis(run)

    async def flush_streaming():
        nonlocal streaming_state, streaming_text, streaming_parts, streaming_part_samples, user_audio_round
        if streaming_state is None or not streaming_parts:
            return
        pcm = np.concatenate(streaming_parts).astype(np.float32, copy=False)
        user_audio_round.append(pcm)
        streaming_parts = []
        streaming_part_samples = 0
        streaming_state = await asr_service.feed_streaming_async(streaming_state, pcm)
        next_text = str(getattr(streaming_state, "text", "") or "").strip()
        if next_text and next_text != streaming_text:
            streaming_text = next_text
            await ws.send_json({"type": "ptt.partial", "text": streaming_text})

    async def emit_phase_update(analysis_state: dict):
        phase_index = int(analysis_state.get("active_phase", 0))
        phases = analysis_state.get("phases", [])
        phase = phases[phase_index] if 0 <= phase_index < len(phases) else {}
        await ws.send_json({
            "type": "phase.update",
            "active_phase": phase_index,
            "phase": phase,
            "combo": int(analysis_state.get("combo", 0)),
            "heat": int(analysis_state.get("heat", 0)),
            "phase_breaks": int(analysis_state.get("phase_breaks", 0)),
        })

    async def emit_opening():
        async with reply_lock:
            with Session(engine) as session:
                run = session.get(InterrogationRun, run_id)
                if not run:
                    return
                analysis_state = interrogation_service.parse_analysis(run)
                phase = interrogation_service.active_phase(analysis_state)
                opening = (
                    f"{analysis_state.get('operation_name', run.operation_name)} is live. "
                    f"{run.suspect_name}, {analysis_state.get('suspect_title', 'suspect')}, is in the chair. "
                    f"We're entering {phase.get('label', 'Phase I')}. Make your case."
                )
                analysis_state.setdefault("history", []).append({"role": "assistant", "text": opening})
                run.analysis_json = json.dumps(analysis_state, ensure_ascii=False)
                session.add(run)
                session.commit()
            audio_dir = games_service.recordings_dir("interrogation", run_id)
            opening_path = audio_dir / "opening.wav"
            await asyncio.to_thread(tts_service.synthesize_for_speaking, opening, opening_path)
            await ws.send_json({"type": "reply.text.delta", "text": opening, "chunk_index": 1, "reply_id": "opening"})
            if opening_path.exists():
                await ws.send_json({
                    "type": "reply.audio.chunk",
                    "chunk_index": 1,
                    "reply_id": "opening",
                    "audio": base64.b64encode(opening_path.read_bytes()).decode(),
                })
            await ws.send_json({
                "type": "meter.update",
                "defense_meter": int(analysis_state.get("defense_meter", 100)),
                "delta": 0,
                "label": "steady",
                "analysis": {},
                "combo": int(analysis_state.get("combo", 0)),
                "heat": int(analysis_state.get("heat", 0)),
                "hit_tags": [],
                "weakness_tags": [],
                "directives": analysis_state.get("directives", []),
                "bonus_reward": int(analysis_state.get("bonus_reward_total", 0)),
            })
            await emit_phase_update(analysis_state)

    async def request_reply_cancel(wait_ms: int = 450):
        nonlocal reply_task, reply_cancel_event
        active_task = reply_task
        active_cancel = reply_cancel_event
        if active_cancel and not active_cancel.is_set():
            active_cancel.set()
        if active_task and not active_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(active_task), timeout=wait_ms / 1000)
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass
        if reply_task is active_task:
            reply_task = None
        if reply_cancel_event is active_cancel:
            reply_cancel_event = None

    async def run_reply_turn(transcript: str, defense_meter: int, cancel_event: asyncio.Event):
        nonlocal reply_task, reply_cancel_event, room_open
        reply_result: dict | None = None
        analysis_snapshot: dict | None = None
        try:
            async with reply_lock:
                with Session(engine) as session:
                    run = session.get(InterrogationRun, run_id)
                    if not run or cancel_event.is_set():
                        return
                    analysis = interrogation_service.parse_analysis(run)
                    reply_result = await interrogation_service.stream_suspect_reply(
                        ws=ws,
                        run=run,
                        analysis=analysis,
                        transcript=transcript,
                        reply_started_at=time.perf_counter(),
                        cancel_event=cancel_event,
                    )
                    if cancel_event.is_set() or reply_result.get("cancelled"):
                        return

                    assistant_text = str(reply_result.get("text", "") or "").strip()
                    last_assistant_text = ""
                    for item in reversed(analysis.get("history", [])):
                        if item.get("role") == "assistant" and item.get("text"):
                            last_assistant_text = str(item["text"]).strip()
                            break
                    if assistant_text and assistant_text != last_assistant_text:
                        analysis.setdefault("history", []).append({
                            "role": "assistant",
                            "text": assistant_text,
                        })

                    run.analysis_json = json.dumps(analysis, ensure_ascii=False)
                    session.add(run)
                    session.commit()
                    analysis_snapshot = analysis

            if cancel_event.is_set() or not reply_result:
                return

            await ws.send_json({
                "type": "round.end",
                "collapsed": False,
                "defense_meter": defense_meter,
                "metrics": {
                    "first_text_ms": reply_result["llm_first_text_ms"],
                    "first_audio_ms": reply_result["first_audio_ms"],
                },
                "combo": int((analysis_snapshot or {}).get("combo", 0)),
                "heat": int((analysis_snapshot or {}).get("heat", 0)),
                "active_phase": int((analysis_snapshot or {}).get("active_phase", 0)),
            })
        except Exception:
            if room_open:
                await ws.send_json({
                    "type": "round.end",
                    "collapsed": False,
                    "interrupted": True,
                    "message": "The suspect lost the line for a moment. Cut back in and press the room again.",
                })
        finally:
            if reply_task is asyncio.current_task():
                reply_task = None
            if reply_cancel_event is cancel_event:
                reply_cancel_event = None

    await emit_opening()

    try:
        while room_open:
            msg = await ws.receive_json()
            msg_type = msg.get("type")

            if msg_type == "ptt.start":
                await request_reply_cancel()
                streaming_state = None
                streaming_text = ""
                streaming_parts = []
                streaming_part_samples = 0
                user_audio_round = []
                try:
                    streaming_state = await asr_service.init_streaming_async("qwen3_asr_mlx")
                except Exception:
                    streaming_state = None
                await ws.send_json({"type": "ptt.partial", "text": ""})

            elif msg_type == "audio.chunk":
                if streaming_state is None:
                    continue
                pcm = _decode_pcm16_base64(msg.get("data", ""))
                if pcm.size == 0:
                    continue
                streaming_parts.append(pcm)
                streaming_part_samples += pcm.size
                if streaming_part_samples >= STREAMING_PCM_FLUSH_SAMPLES:
                    await flush_streaming()

            elif msg_type == "ptt.stop":
                if streaming_state is not None:
                    await flush_streaming()
                    try:
                        streaming_state = await asr_service.finish_streaming_async(streaming_state)
                    except Exception:
                        pass
                transcript = (str(getattr(streaming_state, "text", "") or "").strip() if streaming_state is not None else streaming_text).strip()
                streaming_state = None
                streaming_parts = []
                streaming_part_samples = 0
                if not transcript:
                    await ws.send_json({
                        "type": "round.end",
                        "message": "The tape caught almost nothing. Try again with one fuller answer.",
                    })
                    continue

                audio_dir = games_service.recordings_dir("interrogation", run_id)
                round_path = _write_pcm_wav(audio_dir / f"user_round_{len(user_audio_files) + 1:02d}.wav", user_audio_round)
                if round_path:
                    user_audio_files.append(round_path)

                with Session(engine) as session:
                    run = session.get(InterrogationRun, run_id)
                    if not run:
                        break
                    analysis = interrogation_service.parse_analysis(run)
                    quality = interrogation_service.analyze_transcript(transcript)
                    scoring = interrogation_service.apply_turn_scoring(analysis, quality)
                    adjusted_delta = int(scoring["adjusted_delta"])
                    defense_meter = int(analysis.get("defense_meter", 100))
                    if adjusted_delta >= 0:
                        defense_meter = max(0, defense_meter - adjusted_delta)
                    else:
                        defense_meter = min(100, defense_meter + abs(adjusted_delta) + 4)

                    previous_phase = int(analysis.get("active_phase", 0))
                    next_phase = interrogation_service.phase_index_for_meter(defense_meter)
                    phase_break = next_phase > previous_phase

                    analysis.setdefault("history", []).append({"role": "user", "text": transcript})
                    analysis["defense_meter"] = defense_meter
                    analysis["active_phase"] = next_phase
                    analysis["combo"] = int(scoring["combo"])
                    analysis["max_combo"] = max(int(analysis.get("max_combo", 0)), int(scoring["combo"]))
                    analysis["heat"] = int(scoring["heat"])
                    analysis["last_quality"] = quality
                    analysis.setdefault("turn_metrics", []).append(quality)
                    if phase_break:
                        analysis["phase_breaks"] = int(analysis.get("phase_breaks", 0)) + 1
                    unlocked_directives = interrogation_service.apply_directive_progress(
                        analysis,
                        quality,
                        phase_break=phase_break,
                    )

                    run.analysis_json = json.dumps(analysis, ensure_ascii=False)
                    run.transcript = "\n".join(
                        item["text"] for item in analysis.get("history", []) if item.get("role") == "user"
                    )
                    run.defense_delta = 100 - defense_meter
                    run.rounds_completed = len([item for item in analysis.get("history", []) if item.get("role") == "user"])
                    session.add(run)
                    session.commit()
                    session.refresh(run)

                await ws.send_json({"type": "transcript.commit", "text": transcript})
                await ws.send_json({
                    "type": "meter.update",
                    "defense_meter": defense_meter,
                    "delta": adjusted_delta,
                    "label": quality["label"],
                    "analysis": quality,
                    "combo": int(analysis.get("combo", 0)),
                    "heat": int(analysis.get("heat", 0)),
                    "hit_tags": quality.get("hit_tags", []),
                    "weakness_tags": quality.get("weakness_tags", []),
                    "combo_bonus": int(scoring.get("combo_bonus", 0)),
                    "directives": analysis.get("directives", []),
                    "bonus_reward": int(analysis.get("bonus_reward_total", 0)),
                })

                if unlocked_directives:
                    await ws.send_json({
                        "type": "directive.update",
                        "unlocked": unlocked_directives,
                        "directives": analysis.get("directives", []),
                        "bonus_reward": int(analysis.get("bonus_reward_total", 0)),
                    })

                if phase_break:
                    await emit_phase_update(analysis)

                collapsed = defense_meter <= 0
                room_burned = int(analysis.get("heat", 0)) >= 100
                rounds_completed = int(run.rounds_completed or 0)
                rounds_exhausted = rounds_completed >= int(analysis.get("target_rounds", 6))
                operation_complete = collapsed or room_burned or rounds_exhausted
                rare_drop = False

                if collapsed:
                    await request_reply_cancel()
                    rare_drop = random.random() < 0.12
                    analysis.setdefault("history", []).append({
                        "role": "assistant",
                        "text": "Enough. Fine. That argument lands harder than it should. I'm done.",
                    })

                    with Session(engine) as session:
                        run = session.get(InterrogationRun, run_id)
                        if not run:
                            break
                        analysis = interrogation_service.persist_round(
                            session,
                            run,
                            transcript=transcript,
                            analysis=analysis,
                            defense_meter=defense_meter,
                            user_audio_paths=user_audio_files,
                            collapsed=True,
                            rare_drop=rare_drop,
                        )
                        profile = games_service.update_profile_after_interrogation(
                            session,
                            reward=run.reward,
                            collapsed=True,
                            rare_drop=rare_drop,
                            max_combo=int(analysis.get("max_combo", 0)),
                            phases_cleared=int(analysis.get("phase_breaks", 0)),
                        )
                        await ws.send_json({
                            "type": "collapse",
                            "message": "The lamp flares white. The suspect finally folds.",
                            "rare_drop": rare_drop,
                            "reward": run.reward,
                        })
                        await ws.send_json({
                            "type": "round.end",
                            "summary": InterrogationSummaryOut(
                                **interrogation_service.build_summary_payload(
                                    run=run,
                                    analysis=analysis,
                                    defense_meter=defense_meter,
                                    collapsed=True,
                                    profile=profile,
                                )
                            ).model_dump(),
                        })
                    room_open = False
                    continue

                if operation_complete:
                    await request_reply_cancel()
                    with Session(engine) as session:
                        run = session.get(InterrogationRun, run_id)
                        if not run:
                            break
                        analysis = interrogation_service.persist_round(
                            session,
                            run,
                            transcript=run.transcript,
                            analysis=analysis,
                            defense_meter=defense_meter,
                            user_audio_paths=user_audio_files,
                            collapsed=False,
                            rare_drop=False,
                        )
                        profile = games_service.update_profile_after_interrogation(
                            session,
                            reward=run.reward,
                            collapsed=False,
                            rare_drop=False,
                            max_combo=int(analysis.get("max_combo", 0)),
                            phases_cleared=int(analysis.get("phase_breaks", 0)),
                        )
                        await ws.send_json({
                            "type": "round.end",
                            "message": (
                                "The room overheated and the suspect shut the line down."
                                if room_burned
                                else "Extraction called. The contract window has closed."
                            ),
                            "summary": InterrogationSummaryOut(
                                **interrogation_service.build_summary_payload(
                                    run=run,
                                    analysis=analysis,
                                    defense_meter=defense_meter,
                                    collapsed=False,
                                    profile=profile,
                                )
                            ).model_dump(),
                        })
                    room_open = False
                    continue

                await request_reply_cancel(wait_ms=150)
                reply_cancel_event = asyncio.Event()
                reply_task = asyncio.create_task(
                    run_reply_turn(transcript, defense_meter, reply_cancel_event)
                )

            elif msg_type == "skip_reply":
                await request_reply_cancel()
                with Session(engine) as session:
                    run = session.get(InterrogationRun, run_id)
                    if not run:
                        break
                    analysis = interrogation_service.parse_analysis(run)
                await ws.send_json({
                    "type": "round.end",
                    "collapsed": False,
                    "interrupted": True,
                    "defense_meter": int(analysis.get("defense_meter", 100)),
                    "combo": int(analysis.get("combo", 0)),
                    "heat": int(analysis.get("heat", 0)),
                    "active_phase": int(analysis.get("active_phase", 0)),
                })

            elif msg_type == "close":
                room_open = False

    except WebSocketDisconnect:
        pass
    finally:
        await request_reply_cancel(wait_ms=250)
        with Session(engine) as session:
            run = session.get(InterrogationRun, run_id)
            if run and run.status == "ready":
                run.status = "aborted"
                merged = games_service.merge_audio_files(
                    user_audio_files,
                    games_service.recordings_dir("interrogation", run_id) / "player_full.wav",
                )
                if merged:
                    run.recording_path = games_service.media_url(merged)
                session.add(run)
                session.commit()
