import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database import get_session
from app.models.vocab import VocabSearch
from app.services.vocab_image_service import vocab_image_service

router = APIRouter(prefix="/api/vocab", tags=["vocab"])


@router.get("/images")
async def get_vocab_images(word: str, s: Session = Depends(get_session)):
    if not word.strip():
        raise HTTPException(status_code=400, detail="Word is required")

    output = await vocab_image_service.search(word.strip())

    # Save to history
    search = VocabSearch(
        word=word.strip(),
        results_json=json.dumps(output, ensure_ascii=False),
        created_at=datetime.utcnow().isoformat(),
    )
    s.add(search)
    s.commit()

    return output
