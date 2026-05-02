from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.database import get_session
from app.config import settings
from app.models.story import RetellAttempt, Story
from app.schemas.story import (
    LevelInfo,
    RetellAttemptCreate,
    RetellAttemptOut,
    StoryProgressOut,
    StoryStartIn,
    StoryStartOut,
)
from app.services.asr_service import asr_service
from app.services.recording_service import recording_service
from app.services.story_service import story_service

router = APIRouter(prefix="/api/story", tags=["story"])

LEVEL_DEFS = [
    {"level": 1, "label": "Warm-up Lane", "description": "45-55s, one clear event and simple sequence"},
    {"level": 2, "label": "Starter Quest", "description": "50-60s, 3-4 linked actions in daily life"},
    {"level": 3, "label": "Story Sprint", "description": "55-65s, stronger detail tracking and time words"},
    {"level": 4, "label": "Twist Track", "description": "60-70s, one surprise or change in direction"},
    {"level": 5, "label": "Memory Climb", "description": "65-75s, more characters and richer setting"},
    {"level": 6, "label": "Focus Run", "description": "70-80s, denser detail and clearer cause/effect"},
    {"level": 7, "label": "Challenge Loop", "description": "75-85s, emotion, motivation, and precise recall"},
    {"level": 8, "label": "Logic Tower", "description": "80-90s, inference and hidden links between events"},
    {"level": 9, "label": "Master Route", "description": "85-90s, layered story with sharper summarising"},
    {"level": 10, "label": "Legend Stage", "description": "85-90s, full-score retell with strong structure"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_web_story(story: Story) -> bool:
    tags = json.loads(story.difficulty_tags_json or "[]")
    return "web_story" in tags


def _story_audio_url(audio_ref: str | None) -> str:
    if not audio_ref:
        return ""
    if audio_ref.startswith("http://") or audio_ref.startswith("https://"):
        return audio_ref
    path = Path(audio_ref)
    return recording_service.media_url(path) if path.exists() else ""


def _did_pass(total_score: float, scores: dict) -> bool:
    return total_score >= 75 and scores.get("main_idea", 0) >= 20


def _get_highest_unlocked(s: Session) -> int:
    passed_levels = s.exec(
        select(Story.level).join(RetellAttempt).where(RetellAttempt.passed == True)
    ).all()
    if not passed_levels:
        return 1
    highest_completed = max(passed_levels)
    return min(highest_completed + 1, len(LEVEL_DEFS))


def _build_attempt_out(a: RetellAttempt, advice_audio_path: str = "") -> RetellAttemptOut:
    score_data = json.loads(a.score_json or "{}")
    return RetellAttemptOut(
        id=a.id,
        story_id=a.story_id,
        transcript=a.transcript,
        total_score=a.score_total,
        passed=a.passed,
        scores=score_data.get("scores", {}),
        key_points_covered=score_data.get("key_points_covered", []),
        key_points_missed=score_data.get("key_points_missed", []),
        advice=score_data.get("advice", ""),
        advice_audio_path=advice_audio_path,
        missed_points=score_data.get("missed_points", []),
        language_feedback=score_data.get("language_feedback", []),
        next_tip=score_data.get("next_tip", "Keep practicing!"),
    )


def _build_start_out(story: Story, story_mode: str, source_name: str, source_url: str) -> StoryStartOut:
    return StoryStartOut(
        story_id=story.id,
        level=story.level,
        title=story.title,
        audio_path=_story_audio_url(story.audio_path),
        story_text=story.story_text,
        story_mode=story_mode,
        source_name=source_name,
        source_url=source_url,
        difficulty_tags=json.loads(story.difficulty_tags_json or "[]"),
    )


@router.post("/start", response_model=StoryStartOut)
async def start_story(body: StoryStartIn, s: Session = Depends(get_session)):
    highest = _get_highest_unlocked(s)
    selected_level = body.level or highest
    if selected_level < 1 or selected_level > len(LEVEL_DEFS):
        raise HTTPException(status_code=400, detail="Invalid level.")
    if selected_level > highest:
        raise HTTPException(status_code=403, detail="This level is still locked.")
    story_mode = body.mode or "local_story"

    if story_mode == "web_story":
        story_data = await story_service.build_story_payload(selected_level, "web_story")
        story = Story(
            level=selected_level,
            title=story_data.get("title", f"Level {selected_level} Story"),
            story_text=story_data.get("story_text", ""),
            audio_path=story_data.get("audio_url"),
            key_points_json=json.dumps(story_data.get("key_points", []), ensure_ascii=False),
            difficulty_tags_json=json.dumps(story_data.get("difficulty_tags", []), ensure_ascii=False),
        )
        s.add(story)
        s.commit()
        s.refresh(story)

        audio_url = story_data.get("audio_url", "")
        if audio_url:
            try:
                cached_audio = await story_service.cache_remote_story_audio(
                    story.id,
                    audio_url,
                    max_seconds=story_data.get("target_duration_seconds"),
                )
                if cached_audio:
                    story.audio_path = str(cached_audio)
                    s.add(story)
                    s.commit()
                    s.refresh(story)
            except Exception:
                pass

        return _build_start_out(
            story=story,
            story_mode="web_story",
            source_name=story_data.get("source_name", "Storynory"),
            source_url=story_data.get("source_url", ""),
        )

    existing_local = None
    stories = s.exec(select(Story).where(Story.level == selected_level).order_by(Story.id.desc())).all()
    for story in stories:
        if not _is_web_story(story):
            existing_local = story
            break

    if not existing_local:
        story_data = await story_service.build_story_payload(selected_level, "local_story")
        existing_local = Story(
            level=selected_level,
            title=story_data.get("title", f"Level {selected_level} Story"),
            story_text=story_data.get("story_text", ""),
            key_points_json=json.dumps(story_data.get("key_points", []), ensure_ascii=False),
            difficulty_tags_json=json.dumps(story_data.get("difficulty_tags", []), ensure_ascii=False),
        )
        s.add(existing_local)
        s.commit()
        s.refresh(existing_local)

    expected_audio_path = story_service.expected_story_audio_path(existing_local.id)
    audio_path = Path(existing_local.audio_path) if existing_local.audio_path else None
    if (
        not audio_path
        or not audio_path.exists()
        or audio_path.resolve() != expected_audio_path.resolve()
    ):
        generated_audio = story_service.synthesize_story_audio(existing_local.id, existing_local.story_text)
        if generated_audio:
            existing_local.audio_path = str(generated_audio)
            s.add(existing_local)
            s.commit()
            s.refresh(existing_local)

    return _build_start_out(
        story=existing_local,
        story_mode="local_story",
        source_name="AI Tutor",
        source_url="",
    )


async def _score_attempt(
    *,
    s: Session,
    story: Story,
    transcript: str,
    audio_path: str | None = None,
) -> RetellAttemptOut:
    key_points = json.loads(story.key_points_json or "[]")
    result = await story_service.grade_attempt(story.story_text, key_points, transcript)
    if not result.get("advice"):
        result["advice"] = await story_service.get_advice(story.story_text, key_points, transcript)

    total_score = result.get("total_score", 0)
    scores = result.get("scores", {})
    passed = _did_pass(total_score, scores)

    attempt = RetellAttempt(
        story_id=story.id,
        audio_path=audio_path,
        transcript=transcript,
        score_total=total_score,
        score_json=json.dumps(result, ensure_ascii=False),
        passed=passed,
        created_at=_now(),
    )
    s.add(attempt)
    s.commit()
    s.refresh(attempt)

    feedback_audio = story_service.synthesize_feedback_audio(attempt.id, result["advice"])
    return _build_attempt_out(attempt, _story_audio_url(str(feedback_audio) if feedback_audio else None))


@router.post("/{story_id}/attempt", response_model=RetellAttemptOut)
async def submit_attempt(story_id: int, body: RetellAttemptCreate, s: Session = Depends(get_session)):
    story = s.get(Story, story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    transcript = body.transcript.strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript is empty.")
    return await _score_attempt(s=s, story=story, transcript=transcript)


@router.post("/{story_id}/attempt-audio", response_model=RetellAttemptOut)
async def submit_audio_attempt(story_id: int, audio: UploadFile = File(...), s: Session = Depends(get_session)):
    story = s.get(Story, story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")

    attempts_dir = Path(__file__).parent.parent.parent.parent / "data" / "recordings" / "story_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(audio.filename or "attempt.webm").suffix or ".webm"
    audio_path = attempts_dir / f"story_{story_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    audio_path.write_bytes(await audio.read())

    try:
        transcript = (await asr_service.transcribe_with_model_async(audio_path, settings.asr_model))["text"]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not transcribe audio: {exc}")

    transcript = transcript.strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="No speech detected in the submitted audio.")

    return await _score_attempt(
        s=s,
        story=story,
        transcript=transcript,
        audio_path=str(audio_path),
    )


@router.get("/progress", response_model=StoryProgressOut)
def get_progress(s: Session = Depends(get_session)):
    highest = _get_highest_unlocked(s)
    attempts = s.exec(
        select(RetellAttempt).order_by(RetellAttempt.created_at.desc()).limit(10)
    ).all()
    all_attempts = s.exec(select(RetellAttempt).order_by(RetellAttempt.created_at.desc())).all()
    passed_levels = {story_level for story_level in s.exec(
        select(Story.level).join(RetellAttempt).where(RetellAttempt.passed == True)
    ).all()}

    streak = 0
    for attempt in all_attempts:
        if not attempt.passed:
            break
        streak += 1

    average_score = 0.0
    if all_attempts:
        average_score = round(sum(a.score_total for a in all_attempts) / len(all_attempts), 1)

    attempt_outs = [_build_attempt_out(a) for a in attempts]

    return StoryProgressOut(
        current_level=highest,
        highest_unlocked=highest,
        total_attempts=len(all_attempts),
        cleared_levels=len(passed_levels),
        win_streak=streak,
        average_score=average_score,
        attempts=attempt_outs,
    )


@router.get("/levels", response_model=list[LevelInfo])
def get_levels(s: Session = Depends(get_session)):
    highest = _get_highest_unlocked(s)
    attempts = s.exec(select(RetellAttempt, Story.level).join(Story, RetellAttempt.story_id == Story.id)).all()
    level_attempts: dict[int, list[RetellAttempt]] = {}
    for attempt, level in attempts:
        level_attempts.setdefault(level, []).append(attempt)

    result = []
    for ld in LEVEL_DEFS:
        level = ld["level"]
        completed = False
        if level < highest:
            completed = True
        elif level == highest:
            passed_attempt = s.exec(
                select(RetellAttempt)
                .join(Story, RetellAttempt.story_id == Story.id)
                .where(Story.level == level, RetellAttempt.passed == True)
            ).first()
            completed = passed_attempt is not None

        result.append(
            LevelInfo(
                level=level,
                label=ld["label"],
                description=ld["description"],
                unlocked=level <= highest,
                completed=completed,
                attempts=len(level_attempts.get(level, [])),
                best_score=max((attempt.score_total for attempt in level_attempts.get(level, [])), default=0.0),
            )
        )

    return result
