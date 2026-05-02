import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models.speaking import SpeakingSession, Utterance, Note
from app.models.diary import DiaryEntry
from app.models.vocab import VocabSearch
from app.services.recording_service import recording_service

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/speaking")
def get_speaking_history(s: Session = Depends(get_session)):
    sessions = s.exec(
        select(SpeakingSession).order_by(SpeakingSession.started_at.desc()).limit(50)
    ).all()

    result = []
    for sess in sessions:
        utterances = s.exec(
            select(Utterance).where(Utterance.session_id == sess.id).order_by(Utterance.started_at.asc())
        ).all()
        notes = s.exec(
            select(Note).where(Note.session_id == sess.id).order_by(Note.created_at.asc())
        ).all()

        result.append({
            "id": sess.id,
            "topic": sess.topic,
            "mode": sess.mode,
            "coach_focus": sess.coach_focus,
            "started_at": sess.started_at,
            "ended_at": sess.ended_at,
            "summary": sess.summary,
            "cost_rmb": sess.cost_rmb,
            "recording_path": sess.recording_path,
            "utterance_count": len(utterances),
            "note_count": len(notes),
            "utterances": [
                {"role": u.role, "text": u.text, "confidence": u.confidence}
                for u in utterances
            ],
            "notes": [
                {"source": n.source, "type": n.type, "content": n.content}
                for n in notes
            ],
        })
    return result


@router.get("/diary")
def get_diary_history(s: Session = Depends(get_session)):
    entries = s.exec(
        select(DiaryEntry).order_by(DiaryEntry.created_at.desc()).limit(50)
    ).all()
    return [
        {
            "id": e.id,
            "date": e.date,
            "original_text": e.original_text,
            "better_version": e.better_version,
            "feedback_json": e.feedback_json,
            "created_at": e.created_at,
        }
        for e in entries
    ]


@router.get("/vocab")
def get_vocab_history(s: Session = Depends(get_session)):
    searches = s.exec(
        select(VocabSearch).order_by(VocabSearch.created_at.desc()).limit(50)
    ).all()
    return [
        {
            "id": s.id,
            "word": s.word,
            "results_json": s.results_json,
            "created_at": s.created_at,
        }
        for s in searches
    ]


@router.delete("/speaking/{session_id}")
def delete_speaking_history(session_id: int, s: Session = Depends(get_session)):
    sess = s.get(SpeakingSession, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Speaking session not found")

    utterances = s.exec(select(Utterance).where(Utterance.session_id == session_id)).all()
    notes = s.exec(select(Note).where(Note.session_id == session_id)).all()
    for item in utterances:
        s.delete(item)
    for item in notes:
        s.delete(item)
    s.delete(sess)
    s.commit()

    session_dir = Path(recording_service.base_dir) / str(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)

    return {"ok": True}


@router.delete("/diary/{entry_id}")
def delete_diary_history(entry_id: int, s: Session = Depends(get_session)):
    entry = s.get(DiaryEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Diary entry not found")
    s.delete(entry)
    s.commit()
    return {"ok": True}


@router.delete("/vocab/{search_id}")
def delete_vocab_history(search_id: int, s: Session = Depends(get_session)):
    entry = s.get(VocabSearch, search_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Vocabulary history item not found")
    s.delete(entry)
    s.commit()
    return {"ok": True}
