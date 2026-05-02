from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship


class SpeakingSession(SQLModel, table=True):
    __tablename__ = "speaking_sessions"

    id: int | None = Field(default=None, primary_key=True)
    topic: str = Field(default="")
    mode: str = Field(default="free_chat")
    coach_focus: str = Field(default="general")
    started_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    ended_at: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    cost_rmb: float = Field(default=0.0)
    recording_path: str | None = Field(default=None)
    llm_model: str | None = Field(default=None)
    asr_model: str = Field(default="qwen3_asr_mlx")


class Utterance(SQLModel, table=True):
    __tablename__ = "utterances"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="speaking_sessions.id", index=True)
    role: str = Field(default="user")
    text: str = Field(default="")
    audio_path: str | None = Field(default=None)
    started_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    ended_at: str | None = Field(default=None)
    confidence: float = Field(default=0.0)


class Note(SQLModel, table=True):
    __tablename__ = "notes"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="speaking_sessions.id", index=True)
    source: str = Field(default="ai")
    type: str = Field(default="phrase")
    content: str = Field(default="")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
