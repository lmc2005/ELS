import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.schemas.diary import DiaryCreate, DiaryOut
from app.models.diary import DiaryEntry
from app.services.diary_service import diary_service

router = APIRouter(prefix="/api/diary", tags=["diary"])


@router.post("", response_model=DiaryOut)
async def create_diary(body: DiaryCreate, s: Session = Depends(get_session)):
    feedback = await diary_service.analyze(body.text)

    entry = DiaryEntry(
        date=body.date or datetime.utcnow().strftime("%Y-%m-%d"),
        original_text=body.text,
        better_version=feedback.get("better_version"),
        feedback_json=json.dumps(feedback, ensure_ascii=False),
        created_at=datetime.utcnow().isoformat(),
    )
    s.add(entry)
    s.commit()
    s.refresh(entry)

    return DiaryOut(
        id=entry.id,
        date=entry.date,
        original_text=entry.original_text,
        better_version=entry.better_version,
        feedback_json=entry.feedback_json,
        created_at=entry.created_at,
    )


@router.get("", response_model=list[DiaryOut])
def list_diaries(s: Session = Depends(get_session)):
    entries = s.exec(select(DiaryEntry).order_by(DiaryEntry.created_at.desc())).all()
    return [
        DiaryOut(
            id=e.id,
            date=e.date,
            original_text=e.original_text,
            better_version=e.better_version,
            feedback_json=e.feedback_json,
            created_at=e.created_at,
        )
        for e in entries
    ]


@router.get("/{diary_id}", response_model=DiaryOut)
def get_diary(diary_id: int, s: Session = Depends(get_session)):
    entry = s.get(DiaryEntry, diary_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Diary entry not found")
    return DiaryOut(
        id=entry.id,
        date=entry.date,
        original_text=entry.original_text,
        better_version=entry.better_version,
        feedback_json=entry.feedback_json,
        created_at=entry.created_at,
    )
