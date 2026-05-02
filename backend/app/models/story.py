from datetime import datetime
from sqlmodel import SQLModel, Field


class Story(SQLModel, table=True):
    __tablename__ = "stories"

    id: int | None = Field(default=None, primary_key=True)
    level: int = Field(default=1)
    title: str = Field(default="")
    story_text: str = Field(default="")
    audio_path: str | None = Field(default=None)
    key_points_json: str | None = Field(default=None)  # JSON array string
    difficulty_tags_json: str | None = Field(default=None)  # JSON array string


class RetellAttempt(SQLModel, table=True):
    __tablename__ = "retell_attempts"

    id: int | None = Field(default=None, primary_key=True)
    story_id: int = Field(foreign_key="stories.id", index=True)
    audio_path: str | None = Field(default=None)
    transcript: str = Field(default="")
    score_total: float = Field(default=0.0)
    score_json: str | None = Field(default=None)  # JSON string
    passed: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
