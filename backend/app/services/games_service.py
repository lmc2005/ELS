from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.models.game import GameProfile, InterrogationRun
from app.schemas.games import (
    DailyBountyOut,
    GameHistoryEntryOut,
    GameProfileOut,
    InterrogationLoadoutOut,
    ShopItemOut,
)
from app.services.recording_service import recording_service

SHOP_CATALOG: dict[str, dict[str, object]] = {
    "focus_filter": {
        "label": "Focus Filter",
        "description": "Stabilises weak turns before the room punishes them.",
        "effect": "Softens heat gain when your answer is shaky.",
        "cost": 42,
        "max_level": 3,
    },
    "chain_amplifier": {
        "label": "Chain Amplifier",
        "description": "Turns consecutive strong answers into brutal pressure swings.",
        "effect": "Adds bonus damage to combo turns.",
        "cost": 48,
        "max_level": 3,
    },
    "mirror_chip": {
        "label": "Mirror Chip",
        "description": "Extracts extra credits whenever a phase cracks open.",
        "effect": "Boosts rewards on phase breakthroughs.",
        "cost": 56,
        "max_level": 2,
    },
    "tempo_lock": {
        "label": "Tempo Lock",
        "description": "Keeps the interrogation flow cleaner under pressure.",
        "effect": "Reduces hesitation penalties and keeps combo alive longer.",
        "cost": 60,
        "max_level": 2,
    },
    "collapse_dividend": {
        "label": "Collapse Dividend",
        "description": "Turns full suspect breakdowns into much larger payouts.",
        "effect": "Adds a flat reward bonus to final collapses.",
        "cost": 68,
        "max_level": 3,
    },
}

BOUNTIES = [
    ("BOT-303", "Pressure Break", "Trigger two phase breaks in a single operation.", 28),
    ("NEX-512", "Connector Chain", "Land three strong answers without a shaky turn.", 24),
    ("ELS-777", "Cold Finish", "Collapse the suspect while keeping heat below fifty.", 26),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GamesService:
    def __init__(self):
        self._data_root = Path(__file__).parent.parent.parent.parent / "data"
        self._recordings_root = self._data_root / "recordings" / "games"
        self._recordings_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def default_upgrades() -> dict[str, int]:
        return {key: 0 for key in SHOP_CATALOG}

    @staticmethod
    def default_stats() -> dict:
        return {
            "interrogation_runs": 0,
            "interrogation_collapses": 0,
            "perfect_phases": 0,
            "longest_combo": 0,
            "rare_drops": 0,
        }

    def ensure_profile(self, session: Session) -> GameProfile:
        profile = session.get(GameProfile, 1)
        if profile:
            if not profile.upgrades_json:
                profile.upgrades_json = json.dumps(self.default_upgrades())
            if not profile.stats_json:
                profile.stats_json = json.dumps(self.default_stats())
            return profile

        profile = GameProfile(
            id=1,
            credits=180,
            streak=0,
            upgrades_json=json.dumps(self.default_upgrades()),
            stats_json=json.dumps(self.default_stats()),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile

    def parse_upgrades(self, profile: GameProfile) -> dict[str, int]:
        data = json.loads(profile.upgrades_json or "{}")
        result = self.default_upgrades()
        for key in result:
            result[key] = int(data.get(key, 0) or 0)
        return result

    def parse_stats(self, profile: GameProfile) -> dict:
        data = json.loads(profile.stats_json or "{}")
        result = self.default_stats()
        result.update({key: data.get(key, value) for key, value in result.items()})
        return result

    def loadout_items(self, upgrades: dict[str, int]) -> list[InterrogationLoadoutOut]:
        active: list[InterrogationLoadoutOut] = []
        for key, level in upgrades.items():
            if level <= 0:
                continue
            config = SHOP_CATALOG.get(key)
            if not config:
                continue
            active.append(
                InterrogationLoadoutOut(
                    label=f"{config['label']} Lv.{level}",
                    detail=str(config["effect"]),
                )
            )
        return active

    def _daily_bounty(self) -> DailyBountyOut:
        date_seed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        code, title, description, bonus = random.Random(date_seed).choice(BOUNTIES)
        return DailyBountyOut(
            code=code,
            title=title,
            description=description,
            bonus_credits=bonus,
        )

    def shop_items(self, upgrades: dict[str, int]) -> list[ShopItemOut]:
        items: list[ShopItemOut] = []
        for key, config in SHOP_CATALOG.items():
            items.append(
                ShopItemOut(
                    key=key,
                    label=str(config["label"]),
                    description=str(config["description"]),
                    effect=str(config["effect"]),
                    cost=int(config["cost"]),
                    max_level=int(config["max_level"]),
                    level=int(upgrades.get(key, 0)),
                )
            )
        return items

    def profile_payload(self, session: Session) -> GameProfileOut:
        profile = self.ensure_profile(session)
        upgrades = self.parse_upgrades(profile)
        stats = self.parse_stats(profile)
        return GameProfileOut(
            credits=profile.credits,
            streak=profile.streak,
            upgrades=upgrades,
            stats=stats,
            shop=self.shop_items(upgrades),
            daily_bounty=self._daily_bounty(),
        )

    def purchase_upgrade(self, session: Session, upgrade_key: str) -> tuple[GameProfileOut, int]:
        profile = self.ensure_profile(session)
        upgrades = self.parse_upgrades(profile)
        if upgrade_key not in SHOP_CATALOG:
            raise ValueError("Unknown upgrade key.")

        config = SHOP_CATALOG[upgrade_key]
        current_level = int(upgrades.get(upgrade_key, 0))
        max_level = int(config["max_level"])
        if current_level >= max_level:
            raise ValueError("This upgrade is already maxed out.")

        cost = int(config["cost"]) * (current_level + 1)
        if profile.credits < cost:
            raise ValueError("Not enough credits for this upgrade.")

        upgrades[upgrade_key] = current_level + 1
        profile.credits -= cost
        profile.upgrades_json = json.dumps(upgrades, ensure_ascii=False)
        profile.updated_at = utc_now()
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return self.profile_payload(session), upgrades[upgrade_key]

    def recordings_dir(self, game_key: str, run_id: int) -> Path:
        path = self._recordings_root / game_key / str(run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def media_url(self, path: Path) -> str:
        return recording_service.media_url(path)

    def merge_audio_files(self, files: list[Path], output_path: Path) -> Path | None:
        valid = [path for path in files if path.exists() and path.stat().st_size > 0]
        if not valid:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if len(valid) == 1:
            shutil.copyfile(valid[0], output_path)
            return output_path

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            shutil.copyfile(valid[0], output_path)
            return output_path

        inputs: list[str] = []
        labels: list[str] = []
        for index, path in enumerate(valid):
            inputs.extend(["-i", str(path)])
            labels.append(f"[{index}:a]")
        filter_complex = "".join(labels) + f"concat=n={len(valid)}:v=0:a=1[out]"
        cmd = [
            ffmpeg,
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path
        except (subprocess.SubprocessError, OSError):
            pass

        shutil.copyfile(valid[0], output_path)
        return output_path if output_path.exists() else None

    def recent_interrogation_runs(self, session: Session, limit: int = 5) -> list[InterrogationRun]:
        return session.exec(
            select(InterrogationRun).order_by(InterrogationRun.created_at.desc()).limit(limit)
        ).all()

    def update_profile_after_interrogation(
        self,
        session: Session,
        reward: int,
        collapsed: bool,
        rare_drop: bool,
        max_combo: int,
        phases_cleared: int,
    ) -> GameProfileOut:
        profile = self.ensure_profile(session)
        stats = self.parse_stats(profile)
        stats["interrogation_runs"] = int(stats.get("interrogation_runs", 0)) + 1
        if collapsed:
            stats["interrogation_collapses"] = int(stats.get("interrogation_collapses", 0)) + 1
            profile.streak += 1
        else:
            profile.streak = 0
        if rare_drop:
            stats["rare_drops"] = int(stats.get("rare_drops", 0)) + 1
        stats["perfect_phases"] = int(stats.get("perfect_phases", 0)) + max(0, phases_cleared)
        stats["longest_combo"] = max(int(stats.get("longest_combo", 0)), max_combo)
        profile.credits += reward
        profile.stats_json = json.dumps(stats, ensure_ascii=False)
        profile.updated_at = utc_now()
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return self.profile_payload(session)

    def history_payload(self, session: Session) -> list[GameHistoryEntryOut]:
        entries: list[GameHistoryEntryOut] = []
        interrogation_runs = session.exec(
            select(InterrogationRun).order_by(InterrogationRun.created_at.desc()).limit(12)
        ).all()

        for run in interrogation_runs:
            analysis = json.loads(run.analysis_json or "{}")
            entries.append(
                GameHistoryEntryOut(
                    id=run.id or 0,
                    kind="interrogation",
                    title=run.operation_name or run.suspect_name or "Interrogation",
                    subtitle=" · ".join(
                        part
                        for part in [
                            str(analysis.get("mode_label", "")).strip(),
                            str(analysis.get("threat_level", "pressure room")).strip(),
                        ]
                        if part
                    ),
                    score=round(run.defense_delta, 1),
                    reward=run.reward,
                    status=run.status,
                    created_at=run.created_at,
                )
            )

        return entries[:12]

    @staticmethod
    def slugify(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


games_service = GamesService()
