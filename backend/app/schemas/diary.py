from pydantic import BaseModel


class DiaryCreate(BaseModel):
    text: str
    date: str | None = None


class DiaryOut(BaseModel):
    id: int
    date: str
    original_text: str
    better_version: str | None = None
    feedback_json: str | None = None
    created_at: str
