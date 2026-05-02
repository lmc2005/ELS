from datetime import datetime
from sqlmodel import SQLModel, Field


class DiaryEntry(SQLModel, table=True):
    __tablename__ = "diary_entries"

    id: int | None = Field(default=None, primary_key=True)
    date: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))
    original_text: str = Field(default="")
    better_version: str | None = Field(default=None)
    feedback_json: str | None = Field(default=None)  # JSON string
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
