import json
from pathlib import Path
from datetime import datetime, timezone

from app.config import settings as app_settings

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
BUDGET_FILE = _PROJECT_ROOT / "data/budget_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class BudgetService:
    def __init__(self):
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if BUDGET_FILE.exists():
            try:
                return json.loads(BUDGET_FILE.read_text())
            except (json.JSONDecodeError, IOError):
                pass
        return {"month": _now_month(), "used_rmb": 0.0}

    def _save_state(self):
        BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        BUDGET_FILE.write_text(json.dumps(self._state))

    def _reset_if_new_month(self):
        current_month = _now_month()
        if self._state["month"] != current_month:
            self._state = {"month": current_month, "used_rmb": 0.0}
            self._save_state()

    @property
    def monthly_used_rmb(self) -> float:
        self._reset_if_new_month()
        return self._state["used_rmb"]

    @property
    def monthly_budget_rmb(self) -> float:
        return app_settings.monthly_budget_rmb

    @property
    def is_warning(self) -> bool:
        return self.monthly_used_rmb >= app_settings.budget_warning_rmb

    @property
    def is_exceeded(self) -> bool:
        return self.monthly_used_rmb >= self.monthly_budget_rmb

    def compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1000) * app_settings.input_price_per_1k_tokens_rmb
        output_cost = (output_tokens / 1000) * app_settings.output_price_per_1k_tokens_rmb
        return round(input_cost + output_cost, 6)

    def check_budget(self) -> bool:
        return not self.is_exceeded

    def add_cost(self, amount: float):
        self._reset_if_new_month()
        self._state["used_rmb"] += amount
        self._save_state()

    def get_status(self) -> dict:
        return {
            "month": _now_month(),
            "monthly_budget_rmb": self.monthly_budget_rmb,
            "monthly_used_rmb": round(self.monthly_used_rmb, 6),
            "is_warning": self.is_warning,
            "is_exceeded": self.is_exceeded,
            "input_price_per_1k_tokens_rmb": app_settings.input_price_per_1k_tokens_rmb,
            "output_price_per_1k_tokens_rmb": app_settings.output_price_per_1k_tokens_rmb,
        }


budget_service = BudgetService()
