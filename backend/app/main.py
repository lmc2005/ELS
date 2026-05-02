import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import create_db_and_tables
from app.routers import settings, speaking, diary, news, vocab, story, history, games
from app.services.settings_service import settings_service

logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
logger = logging.getLogger("els")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    settings_service.load_persisted_settings()
    logger.info("Application settings loaded")

    try:
        from app.services.tts_service import tts_service
        if tts_service.warm_if_cached():
            logger.info("Qwen TTS warmed from local cache")
        if tts_service.warm_speaking_voice():
            logger.info("Kokoro speaking voice warmed from local cache")
    except Exception as e:
        logger.warning("TTS preload skipped: %s", e)

    try:
        from app.services.asr_service import asr_service
        if asr_service.warm_if_configured():
            logger.info("ASR warmed from local configuration")
    except Exception as e:
        logger.warning("ASR preload skipped: %s", e)

    yield


app = FastAPI(title="English Learning System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(speaking.router)
app.include_router(diary.router)
app.include_router(news.router)
app.include_router(vocab.router)
app.include_router(story.router)
app.include_router(history.router)
app.include_router(games.router)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(DATA_DIR)), name="media")


@app.get("/api/health")
def health():
    return {"status": "ok"}
