from datetime import datetime
from sqlmodel import SQLModel, Field


class UsageEvent(SQLModel, table=True):
    __tablename__ = "usage_events"

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(default="")
    feature: str = Field(default="")
    model: str = Field(default="")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    audio_seconds: float = Field(default=0.0)
    estimated_cost_rmb: float = Field(default=0.0)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
