from datetime import datetime
from sqlmodel import SQLModel, Field


class VocabSearch(SQLModel, table=True):
    __tablename__ = "vocab_searches"

    id: int | None = Field(default=None, primary_key=True)
    word: str = Field(default="", index=True)
    results_json: str | None = Field(default=None)  # JSON string
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
