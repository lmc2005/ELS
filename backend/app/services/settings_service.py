import json
from typing import Any

from sqlmodel import Session, select

from app.config import settings as app_settings
from app.database import engine
from app.models.settings_model import SettingsModel
from app.schemas.settings import SettingsOut, SettingsUpdate


PERSISTED_SETTING_KEYS = {
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_timeout_seconds",
    "input_price_per_1k_tokens_rmb",
    "output_price_per_1k_tokens_rmb",
    "monthly_budget_rmb",
    "budget_warning_rmb",
    "asr_model",
    "tts_provider",
    "tts_model",
    "tts_voice",
    "image_cache_days",
}


def _coerce_value(key: str, raw_value: str) -> Any:
    current_value = getattr(app_settings, key)
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value

    if isinstance(current_value, bool):
        return bool(value)
    if isinstance(current_value, int) and not isinstance(current_value, bool):
        return int(value)
    if isinstance(current_value, float):
        return float(value)
    return "" if value is None else str(value)


class SettingsService:
    def load_persisted_settings(self) -> None:
        with Session(engine) as session:
            rows = session.exec(select(SettingsModel)).all()
            for row in rows:
                if row.key in PERSISTED_SETTING_KEYS:
                    setattr(app_settings, row.key, _coerce_value(row.key, row.value))

    def update_settings(self, body: SettingsUpdate) -> None:
        updates = body.model_dump(exclude_none=True)
        with Session(engine) as session:
            for key, value in updates.items():
                if key not in PERSISTED_SETTING_KEYS:
                    continue
                setattr(app_settings, key, value)
                row = session.exec(select(SettingsModel).where(SettingsModel.key == key)).first()
                if row is None:
                    row = SettingsModel(key=key, value=json.dumps(value))
                    session.add(row)
                else:
                    row.value = json.dumps(value)
            session.commit()

    def to_settings_out(self, budget_status: dict) -> SettingsOut:
        return SettingsOut(
            llm_base_url=app_settings.llm_base_url,
            llm_api_key=app_settings.llm_api_key,
            llm_model=app_settings.llm_model,
            input_price_per_1k_tokens_rmb=app_settings.input_price_per_1k_tokens_rmb,
            output_price_per_1k_tokens_rmb=app_settings.output_price_per_1k_tokens_rmb,
            monthly_budget_rmb=app_settings.monthly_budget_rmb,
            budget_warning_rmb=app_settings.budget_warning_rmb,
            asr_model=app_settings.asr_model,
            tts_provider=app_settings.tts_provider,
            tts_model=app_settings.tts_model,
            tts_voice=app_settings.tts_voice,
            monthly_used_rmb=budget_status["monthly_used_rmb"],
            is_budget_warning=budget_status["is_warning"],
            is_budget_exceeded=budget_status["is_exceeded"],
        )


settings_service = SettingsService()
