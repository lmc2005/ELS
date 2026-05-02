from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GameProfile(SQLModel, table=True):
    __tablename__ = "game_profiles"

    id: int | None = Field(default=1, primary_key=True)
    credits: int = Field(default=180)
    streak: int = Field(default=0)
    upgrades_json: str = Field(default="{}")
    stats_json: str = Field(default="{}")
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class InterrogationRun(SQLModel, table=True):
    __tablename__ = "interrogation_runs"

    id: int | None = Field(default=None, primary_key=True)
    case_code: str = Field(default="", index=True)
    operation_name: str = Field(default="")
    topic: str = Field(default="")
    suspect_name: str = Field(default="")
    transcript: str = Field(default="")
    analysis_json: str = Field(default="{}")
    defense_delta: float = Field(default=0.0)
    reward: int = Field(default=0)
    recording_path: str | None = Field(default=None)
    rounds_completed: int = Field(default=0)
    status: str = Field(default="pending", index=True)
    created_at: str = Field(default_factory=_now)
    completed_at: str | None = Field(default=None)
