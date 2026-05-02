from pydantic import BaseModel


class StoryStartIn(BaseModel):
    mode: str = "local_story"
    level: int | None = None


class StoryStartOut(BaseModel):
    story_id: int
    level: int
    title: str
    audio_path: str
    story_text: str = ""
    story_mode: str = "local_story"
    source_name: str = ""
    source_url: str = ""
    difficulty_tags: list[str] = []


class RetellAttemptCreate(BaseModel):
    story_id: int
    transcript: str


class RetellAttemptOut(BaseModel):
    id: int
    story_id: int
    transcript: str = ""
    total_score: float
    passed: bool
    scores: dict
    key_points_covered: list[str] = []
    key_points_missed: list[str] = []
    advice: str = ""
    advice_audio_path: str = ""
    missed_points: list[str] = []
    language_feedback: list[str] = []
    next_tip: str = ""


class StoryProgressOut(BaseModel):
    current_level: int
    highest_unlocked: int
    total_attempts: int = 0
    cleared_levels: int = 0
    win_streak: int = 0
    average_score: float = 0.0
    attempts: list[RetellAttemptOut] = []


class LevelInfo(BaseModel):
    level: int
    label: str
    description: str
    unlocked: bool
    completed: bool
    attempts: int = 0
    best_score: float = 0.0
