from __future__ import annotations

from pydantic import BaseModel


class ShopItemOut(BaseModel):
    key: str
    label: str
    description: str
    effect: str
    cost: int
    max_level: int
    level: int


class DailyBountyOut(BaseModel):
    code: str
    title: str
    description: str
    bonus_credits: int


class GameProfileOut(BaseModel):
    credits: int
    streak: int
    upgrades: dict[str, int]
    stats: dict
    shop: list[ShopItemOut]
    daily_bounty: DailyBountyOut


class GameHistoryEntryOut(BaseModel):
    id: int
    kind: str
    title: str
    subtitle: str
    score: float
    reward: int
    status: str
    created_at: str


class GamesHistoryOut(BaseModel):
    entries: list[GameHistoryEntryOut]


class ShopPurchaseIn(BaseModel):
    upgrade_key: str


class ShopPurchaseOut(BaseModel):
    purchased_key: str
    level: int
    profile: GameProfileOut


class InterrogationStartIn(BaseModel):
    mode: str | None = None


class InterrogationIntelCardOut(BaseModel):
    label: str
    value: str
    detail: str


class InterrogationPhaseOut(BaseModel):
    key: str
    label: str
    objective: str
    threshold: int
    reward_hint: str


class InterrogationLoadoutOut(BaseModel):
    label: str
    detail: str


class InterrogationDirectiveOut(BaseModel):
    key: str
    label: str
    detail: str
    reward: int
    progress: int
    target: int
    completed: bool


class InterrogationStartOut(BaseModel):
    run_id: int
    case_code: str
    operation_name: str
    topic: str
    suspect_name: str
    suspect_title: str
    mode_key: str
    mode_label: str
    mode_blurb: str
    threat_level: str
    mission_brief: str
    opening_scene: str
    defense_meter: int
    target_rounds: int
    active_phase: int
    difficulty_label: str
    difficulty_stars: int
    dossier: list[InterrogationIntelCardOut]
    phases: list[InterrogationPhaseOut]
    loadout: list[InterrogationLoadoutOut]
    directives: list[InterrogationDirectiveOut]


class InterrogationSummaryOut(BaseModel):
    run_id: int
    collapsed: bool
    reward: int
    defense_meter: int
    rounds_completed: int
    transcript: str
    case_code: str = ""
    operation_name: str = ""
    mode_label: str = ""
    max_combo: int = 0
    phases_cleared: int = 0
    bonus_reward: int = 0
    directives_cleared: int = 0
    rank: str = "D"
    debrief_headline: str = ""
    debrief_strength: str = ""
    debrief_warning: str = ""
    next_focus: str = ""
    profile: GameProfileOut | None = None
