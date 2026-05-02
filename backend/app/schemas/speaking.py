from pydantic import BaseModel
from datetime import datetime


class SpeakingSessionCreate(BaseModel):
    topic: str = ""
    mode: str = "free_chat"
    coach_focus: str = "general"
    llm_model: str | None = None
    asr_model: str = "qwen3_asr_mlx"


class SpeakingSessionOut(BaseModel):
    id: int
    topic: str
    mode: str
    coach_focus: str
    started_at: str
    ended_at: str | None = None
    summary: str | None = None
    cost_rmb: float = 0.0
    recording_path: str | None = None
    llm_model: str | None = None
    asr_model: str = "qwen3_asr_mlx"


class UtteranceOut(BaseModel):
    id: int
    role: str
    text: str
    confidence: float = 0.0


class NoteOut(BaseModel):
    id: int
    source: str
    type: str
    content: str
