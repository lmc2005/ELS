from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from app.models.game import InterrogationRun
from app.services.games_service import games_service
from app.services.llm_client import llm_client
from app.services.tts_service import tts_service

OPERATIONS = [
    {
        "name": "Operation Glass Echo",
        "brief": "Break the suspect with clear reasoning, concrete examples, and controlled spoken pressure.",
    },
    {
        "name": "Operation Hollow Signal",
        "brief": "Force the room into contradiction by building long, connected answers that keep momentum.",
    },
    {
        "name": "Operation Black Ledger",
        "brief": "Use calm logic and evidence-rich speech to strip away the suspect's confidence phase by phase.",
    },
]

OPERATION_MODES = {
    "classic": {
        "label": "Classic Pressure",
        "blurb": "Balanced chamber pacing. Build long answers, crack phases, and finish the confession before the room stabilises.",
        "target_rounds": 6,
        "starting_heat": 10,
        "directive_count": 3,
        "directive_starters": ["connector_chain"],
        "positive_scale": 1.0,
        "steady_scale": 1.0,
        "negative_scale": 1.0,
        "combo_cap": 5,
        "weak_heat_bonus": 0,
        "neutral_heat_shift": 0,
        "cooldown_bonus": 0,
        "reward_multiplier": 1.0,
    },
    "onslaught": {
        "label": "Onslaught Contract",
        "blurb": "Shorter, hotter, and louder. Protect your combo early or the chamber snowballs against you.",
        "target_rounds": 5,
        "starting_heat": 22,
        "directive_count": 4,
        "directive_starters": ["combo_engine", "pressure_burst", "phase_crack"],
        "positive_scale": 1.18,
        "steady_scale": 1.08,
        "negative_scale": 1.18,
        "combo_cap": 6,
        "weak_heat_bonus": 5,
        "neutral_heat_shift": 2,
        "cooldown_bonus": 3,
        "reward_multiplier": 1.22,
    },
    "precision": {
        "label": "Precision Dossier",
        "blurb": "Fewer turns. Cleaner demands. The room rewards exact wording, sharp connectors, and grounded examples.",
        "target_rounds": 4,
        "starting_heat": 6,
        "directive_count": 3,
        "directive_starters": ["clean_fluency", "example_lock"],
        "positive_scale": 1.08,
        "steady_scale": 0.94,
        "negative_scale": 1.34,
        "combo_cap": 4,
        "weak_heat_bonus": 2,
        "neutral_heat_shift": 1,
        "cooldown_bonus": 1,
        "reward_multiplier": 1.16,
    },
}

TOPICS = [
    "Should governments regulate AI-generated media more aggressively?",
    "Why do some communities trust technology faster than others?",
    "How does convenience change the way people make ethical decisions?",
    "Do modern cities make people better communicators or worse listeners?",
    "Why do young professionals delay difficult conversations at work?",
]

SUSPECTS = [
    {
        "name": "Mara Voss",
        "title": "Strategic Dissenter",
        "archetype": "a cool strategist who tests weak logic immediately",
        "threat_level": "High pressure tactician",
        "weak_spot": "Backs away when you link causes and consequences cleanly.",
        "pressure_cue": "Punish vague claims and reward sharp causal chains.",
    },
    {
        "name": "Elias Thorn",
        "title": "Narrative Manipulator",
        "archetype": "a sly operator who pushes for precise examples",
        "threat_level": "Adaptive counter-puncher",
        "weak_spot": "Loses ground when you support your argument with vivid examples.",
        "pressure_cue": "Use examples early before he reframes the topic.",
    },
    {
        "name": "Nadia Cross",
        "title": "Forensic Skeptic",
        "archetype": "a composed analyst who mocks vague answers",
        "threat_level": "Cold logic specialist",
        "weak_spot": "Hates contrastive arguments that expose trade-offs clearly.",
        "pressure_cue": "Show balance, then commit to a conclusion.",
    },
    {
        "name": "Julian Pike",
        "title": "Psychological Grinder",
        "archetype": "a patient manipulator who exploits hesitation",
        "threat_level": "Attrition specialist",
        "weak_spot": "Retreats when you stay fluent and avoid fillers for multiple turns.",
        "pressure_cue": "Keep tempo steady and do not let pauses hand him control.",
    },
]

PHASES = [
    {
        "key": "breach",
        "label": "Phase I · Breach the Alibi",
        "objective": "Open with a clear position and one believable reason instead of a vague reaction.",
        "threshold": 100,
        "reward_hint": "Longer answers start the combo engine.",
    },
    {
        "key": "fracture",
        "label": "Phase II · Fracture the Story",
        "objective": "Connect causes and consequences with advanced linkers and controlled sentence flow.",
        "threshold": 70,
        "reward_hint": "Strong connectors trigger bonus pressure.",
    },
    {
        "key": "collapse",
        "label": "Phase III · Force the Confession",
        "objective": "Finish with a concrete example or contrast that leaves the suspect nowhere to hide.",
        "threshold": 35,
        "reward_hint": "Examples and contrast moves hit hardest here.",
    },
]

ADVANCED_LINKERS = {
    "nevertheless",
    "consequently",
    "moreover",
    "whereas",
    "therefore",
    "nonetheless",
    "admittedly",
    "ultimately",
    "meanwhile",
    "in contrast",
    "for instance",
    "as a result",
    "on the other hand",
}

FILLERS = {"um", "uh", "like", "you know", "sort of", "kind of", "i mean"}
EXAMPLE_MARKERS = {"for example", "for instance", "such as", "to illustrate", "if we take"}
HEDGES = {"maybe", "perhaps", "i guess", "probably"}

DIRECTIVE_LIBRARY = {
    "connector_chain": {
        "label": "Connector Chain",
        "detail": "Land two turns that use advanced linking clearly.",
        "reward": 12,
        "target": 2,
    },
    "example_lock": {
        "label": "Example Lock",
        "detail": "Use one concrete example that pins the suspect down.",
        "reward": 10,
        "target": 1,
    },
    "clean_fluency": {
        "label": "Clean Fluency",
        "detail": "Deliver a full answer without fillers or hedges.",
        "reward": 14,
        "target": 1,
    },
    "phase_crack": {
        "label": "Phase Crack",
        "detail": "Force at least one live phase break during the operation.",
        "reward": 16,
        "target": 1,
    },
    "cold_blooded": {
        "label": "Cold Blooded",
        "detail": "Keep heat controlled while still landing pressure.",
        "reward": 12,
        "target": 2,
    },
    "pressure_burst": {
        "label": "Pressure Burst",
        "detail": "Deliver one dominant turn that hits like a finishing blow.",
        "reward": 18,
        "target": 1,
    },
    "combo_engine": {
        "label": "Combo Engine",
        "detail": "Reach a live combo of two or better.",
        "reward": 14,
        "target": 1,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def spoken_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z']+", text or ""))


def collapse_repeated_short_tokens(text: str, max_repeat: int = 2) -> str:
    words = text.split()
    if not words:
        return ""

    collapsed: list[str] = []
    last_key = ""
    repeat_count = 0
    for word in words:
        bare = re.sub(r"[^A-Za-z']", "", word).lower()
        key = bare or word.lower()
        if key == last_key and len(key) <= 4:
            repeat_count += 1
            if repeat_count > max_repeat:
                continue
        else:
            last_key = key
            repeat_count = 1
        collapsed.append(word)
    return " ".join(collapsed)


def collapse_repeated_sentences(text: str, max_repeat: int = 1) -> str:
    segments = re.findall(r"[^.!?]+[.!?]?", text or "")
    if not segments:
        return text

    collapsed: list[str] = []
    last_key = ""
    repeat_count = 0
    for segment in segments:
        normalized = re.sub(r"[^a-z0-9 ]+", " ", segment.lower())
        key = re.sub(r"\s+", " ", normalized).strip()
        if not key:
            continue
        if key == last_key:
            repeat_count += 1
            if repeat_count > max_repeat:
                continue
        else:
            last_key = key
            repeat_count = 1
        collapsed.append(segment.strip())
    return " ".join(collapsed).strip()


def sanitize_spoken_reply_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"[^\x00-\x7F]+", " ", cleaned)
    cleaned = re.sub(r"\{.*?\}", " ", cleaned)
    cleaned = cleaned.replace("```", " ")
    cleaned = collapse_repeated_short_tokens(cleaned)
    cleaned = collapse_repeated_sentences(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip('"')
    if not cleaned:
        return "That barely scratches the surface. Try again with a clearer point."
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(parts[:2]).strip()


def pop_stream_segment(buffer: str, force: bool = False) -> tuple[str, str]:
    working = re.sub(r"\s+", " ", buffer or "").strip()
    if not working:
        return "", ""
    match = re.search(r"^(.+?[.!?]+)(?:\s+|$)", working)
    if match:
        segment = match.group(1).strip()
        remainder = working[match.end() :].strip()
        if force or spoken_word_count(segment) >= 5:
            return segment, remainder
    words = working.split()
    if force:
        return working, ""
    if len(words) >= 12:
        segment = " ".join(words[:12]).strip()
        remainder = " ".join(words[12:]).strip()
        if segment[-1] not in ".?!":
            segment += "."
        return segment, remainder
    return "", working


def normalized_mode_key(mode: str | None) -> str:
    key = (mode or "classic").strip().lower()
    return key if key in OPERATION_MODES else "classic"


class InterrogationService:
    @staticmethod
    def _directive_payload(key: str) -> dict:
        config = DIRECTIVE_LIBRARY[key]
        return {
            "key": key,
            "label": config["label"],
            "detail": config["detail"],
            "reward": int(config["reward"]),
            "progress": 0,
            "target": int(config["target"]),
            "completed": False,
        }

    def _seed_directives(self, rng: random.Random, mode_key: str) -> list[dict]:
        config = OPERATION_MODES[normalized_mode_key(mode_key)]
        guaranteed = list(dict.fromkeys(config.get("directive_starters", [])))
        target_count = int(config.get("directive_count", 3))
        if len(guaranteed) > target_count:
            guaranteed = guaranteed[:target_count]
        pool = [key for key in DIRECTIVE_LIBRARY.keys() if key not in guaranteed]
        remaining = min(len(pool), max(0, target_count - len(guaranteed)))
        keys = guaranteed + rng.sample(pool, k=remaining)
        return [self._directive_payload(key) for key in keys]

    @staticmethod
    def _difficulty_from_suspect(suspect: dict) -> tuple[str, int]:
        mapping = {
            "High pressure tactician": ("Pressure Room", 5),
            "Adaptive counter-puncher": ("Counterplay Room", 4),
            "Cold logic specialist": ("Logic Room", 4),
            "Attrition specialist": ("Endurance Room", 5),
        }
        return mapping.get(str(suspect.get("threat_level", "")), ("Black Chamber", 4))

    @staticmethod
    def _rank_for_summary(analysis: dict, defense_meter: int, collapsed: bool) -> str:
        max_combo = int(analysis.get("max_combo", 0))
        phase_breaks = int(analysis.get("phase_breaks", 0))
        directives_done = sum(1 for item in analysis.get("directives", []) if item.get("completed"))
        score = (100 - defense_meter) + max_combo * 12 + phase_breaks * 18 + directives_done * 10
        if collapsed and score >= 90:
            return "S"
        if score >= 70:
            return "A"
        if score >= 48:
            return "B"
        if score >= 28:
            return "C"
        return "D"

    def apply_directive_progress(
        self,
        analysis: dict,
        quality: dict,
        *,
        phase_break: bool,
    ) -> list[dict]:
        directives = analysis.get("directives", [])
        if not directives:
            return []

        unlocked: list[dict] = []
        for directive in directives:
            if directive.get("completed"):
                continue

            key = directive.get("key")
            hit = False
            if key == "connector_chain":
                hit = quality.get("advanced_linkers", 0) >= 1 and quality.get("word_count", 0) >= 18
            elif key == "example_lock":
                hit = quality.get("example_count", 0) >= 1
            elif key == "clean_fluency":
                hit = (
                    quality.get("word_count", 0) >= 20
                    and quality.get("filler_count", 0) == 0
                    and quality.get("hedge_count", 0) == 0
                )
            elif key == "phase_crack":
                hit = phase_break
            elif key == "cold_blooded":
                hit = analysis.get("heat", 100) <= 35 and quality.get("pressure_delta", 0) >= 8
            elif key == "pressure_burst":
                hit = quality.get("pressure_delta", 0) >= 18
            elif key == "combo_engine":
                hit = int(analysis.get("combo", 0)) >= 2

            if not hit:
                continue

            directive["progress"] = min(int(directive.get("target", 1)), int(directive.get("progress", 0)) + 1)
            if directive["progress"] >= int(directive.get("target", 1)):
                directive["completed"] = True
                analysis["bonus_reward_total"] = int(analysis.get("bonus_reward_total", 0)) + int(directive.get("reward", 0))
                unlocked.append(dict(directive))

        analysis["directives"] = directives
        return unlocked

    def _build_operation(self, session: Session, mode_key: str) -> dict:
        rng = random.Random(datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"))
        suspect = rng.choice(SUSPECTS)
        operation = rng.choice(OPERATIONS)
        topic = rng.choice(TOPICS)
        profile = games_service.ensure_profile(session)
        upgrades = games_service.parse_upgrades(profile)
        mode_key = normalized_mode_key(mode_key)
        mode = OPERATION_MODES[mode_key]
        case_code = f"BC-{rng.randint(100, 999)}"
        opening_scene = (
            "A rusted lamp swings over the steel table. The tape clicks into place. "
            f"{suspect['name']} studies you through the smoke and waits for a mistake."
        )
        difficulty_label, difficulty_stars = self._difficulty_from_suspect(suspect)
        dossier = [
            {
                "label": "Threat Level",
                "value": suspect["threat_level"],
                "detail": "Expect sharp interruptions if your answer becomes vague or fragmented.",
            },
            {
                "label": "Weak Spot",
                "value": suspect["weak_spot"],
                "detail": "This is the line that usually forces the suspect backward.",
            },
            {
                "label": "Pressure Cue",
                "value": suspect["pressure_cue"],
                "detail": "Use this to decide what kind of spoken answer to build next.",
            },
            {
                "label": "Mission Core",
                "value": operation["brief"],
                "detail": "The room rewards control, logic, and concrete support.",
            },
        ]
        return {
            "case_code": case_code,
            "operation_name": operation["name"],
            "mission_brief": operation["brief"],
            "topic": topic,
            "suspect_name": suspect["name"],
            "suspect_title": suspect["title"],
            "mode_key": mode_key,
            "mode_label": str(mode["label"]),
            "mode_blurb": str(mode["blurb"]),
            "archetype": suspect["archetype"],
            "threat_level": suspect["threat_level"],
            "weak_spot": suspect["weak_spot"],
            "pressure_cue": suspect["pressure_cue"],
            "opening_scene": opening_scene,
            "target_rounds": int(mode["target_rounds"]),
            "difficulty_label": difficulty_label,
            "difficulty_stars": difficulty_stars,
            "dossier": dossier,
            "phases": PHASES,
            "directives": self._seed_directives(rng, mode_key),
            "active_phase": 0,
            "combo": 0,
            "max_combo": 0,
            "heat": int(mode["starting_heat"]),
            "phase_breaks": 0,
            "bonus_reward_total": 0,
            "history": [],
            "turn_metrics": [],
            "loadout": [item.model_dump() for item in games_service.loadout_items(upgrades)],
            "upgrades": upgrades,
        }

    def create_run(self, session: Session, mode: str | None = None) -> InterrogationRun:
        operation = self._build_operation(session, normalized_mode_key(mode))
        analysis = {
            **operation,
            "defense_meter": 100,
            "last_quality": {},
        }
        run = InterrogationRun(
            case_code=operation["case_code"],
            operation_name=operation["operation_name"],
            topic=operation["topic"],
            suspect_name=operation["suspect_name"],
            transcript="",
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            defense_delta=0.0,
            reward=0,
            rounds_completed=0,
            status="ready",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    @staticmethod
    def parse_analysis(run: InterrogationRun) -> dict:
        data = json.loads(run.analysis_json or "{}")
        data.setdefault("topic", run.topic)
        data.setdefault("case_code", run.case_code)
        data.setdefault("operation_name", run.operation_name)
        mode_key = normalized_mode_key(data.get("mode_key"))
        mode = OPERATION_MODES[mode_key]
        data["mode_key"] = mode_key
        data.setdefault("mode_label", str(mode["label"]))
        data.setdefault("mode_blurb", str(mode["blurb"]))
        data.setdefault("defense_meter", max(0, 100 - int(run.defense_delta)))
        data.setdefault("target_rounds", int(mode["target_rounds"]))
        data.setdefault("history", [])
        data.setdefault("opening_scene", "")
        data.setdefault("difficulty_label", "Black Chamber")
        data.setdefault("difficulty_stars", 4)
        data.setdefault("dossier", [])
        data.setdefault("phases", PHASES)
        data.setdefault("directives", [])
        data.setdefault("active_phase", 0)
        data.setdefault("combo", 0)
        data.setdefault("max_combo", 0)
        data.setdefault("heat", int(mode["starting_heat"]))
        data.setdefault("phase_breaks", 0)
        data.setdefault("bonus_reward_total", 0)
        data.setdefault("loadout", [])
        data.setdefault("upgrades", games_service.default_upgrades())
        data.setdefault("turn_metrics", [])
        return data

    @staticmethod
    def _count_occurrences(text: str, terms: set[str]) -> int:
        lowered = text.lower()
        return sum(lowered.count(term) for term in terms)

    @staticmethod
    def phase_index_for_meter(defense_meter: int) -> int:
        if defense_meter <= 35:
            return 2
        if defense_meter <= 70:
            return 1
        return 0

    def active_phase(self, analysis: dict) -> dict:
        phases = analysis.get("phases", PHASES)
        index = max(0, min(len(phases) - 1, int(analysis.get("active_phase", 0))))
        return phases[index]

    def analyze_transcript(self, text: str) -> dict:
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        lowered = cleaned.lower()
        words = re.findall(r"[A-Za-z']+", cleaned)
        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", cleaned) if segment.strip()]
        word_count = len(words)
        avg_sentence = word_count / max(1, len(sentences))
        linker_count = self._count_occurrences(cleaned, ADVANCED_LINKERS)
        filler_count = self._count_occurrences(cleaned, FILLERS)
        repair_count = len(re.findall(r"\b(i mean|sorry|let me rephrase|rather)\b", lowered))
        example_count = self._count_occurrences(cleaned, EXAMPLE_MARKERS)
        hedge_count = self._count_occurrences(cleaned, HEDGES)
        clause_bonus = 6 if avg_sentence >= 15 else 3 if avg_sentence >= 10 else -5
        length_bonus = 8 if word_count >= 34 else 4 if word_count >= 20 else -6 if word_count < 9 else 0
        connector_bonus = linker_count * 6
        example_bonus = example_count * 5
        fluency_bonus = 4 if filler_count == 0 and repair_count == 0 and word_count >= 14 else 0
        hedge_penalty = hedge_count * 2
        filler_penalty = filler_count * 3 + repair_count * 2
        score = max(-14, min(32, clause_bonus + length_bonus + connector_bonus + example_bonus + fluency_bonus - filler_penalty - hedge_penalty))
        label = "dominant" if score >= 18 else "steady" if score >= 8 else "shaky"

        hit_tags: list[str] = []
        weakness_tags: list[str] = []
        if linker_count:
            hit_tags.append("connector chain")
        if example_count:
            hit_tags.append("evidence hit")
        if avg_sentence >= 14:
            hit_tags.append("long-form control")
        if word_count >= 24:
            hit_tags.append("sustained pressure")
        if filler_count:
            weakness_tags.append("filler drift")
        if hedge_count:
            weakness_tags.append("hedge language")
        if word_count < 10:
            weakness_tags.append("answer too short")
        if not example_count and word_count >= 14:
            weakness_tags.append("missing concrete example")

        return {
            "word_count": word_count,
            "avg_sentence_len": round(avg_sentence, 1),
            "advanced_linkers": linker_count,
            "filler_count": filler_count,
            "repair_count": repair_count,
            "example_count": example_count,
            "hedge_count": hedge_count,
            "pressure_delta": score,
            "label": label,
            "hit_tags": hit_tags,
            "weakness_tags": weakness_tags,
        }

    def apply_turn_scoring(self, analysis: dict, quality: dict) -> dict:
        mode = OPERATION_MODES[normalized_mode_key(analysis.get("mode_key"))]
        upgrades = analysis.get("upgrades", {})
        combo = int(analysis.get("combo", 0))
        heat = int(analysis.get("heat", 10))
        base_delta = int(quality["pressure_delta"])
        strong_turn = base_delta >= 12
        shaky_turn = base_delta < 0

        if strong_turn:
            combo = min(int(mode.get("combo_cap", 5)), combo + 1)
        elif shaky_turn:
            combo = 0
        else:
            combo = max(0, combo - 1)

        combo_bonus = combo * (2 + int(upgrades.get("chain_amplifier", 0)))
        if strong_turn:
            adjusted_delta = round((base_delta + combo_bonus) * float(mode.get("positive_scale", 1.0)))
        elif shaky_turn:
            adjusted_delta = round(base_delta * float(mode.get("negative_scale", 1.0)))
        else:
            adjusted_delta = round(base_delta * float(mode.get("steady_scale", 1.0)))

        weak_heat_gain = 8 + max(0, -base_delta)
        weak_heat_gain = max(2, weak_heat_gain - int(upgrades.get("focus_filter", 0)) * 2)
        if strong_turn:
            heat = max(0, heat - (10 + combo + int(mode.get("cooldown_bonus", 0))))
        elif shaky_turn:
            heat = min(100, heat + weak_heat_gain + int(mode.get("weak_heat_bonus", 0)))
        else:
            neutral_shift = int(mode.get("neutral_heat_shift", 0))
            heat = min(100, max(0, heat + neutral_shift + 3 - int(upgrades.get("tempo_lock", 0)) * 2))

        return {
            "combo": combo,
            "heat": heat,
            "adjusted_delta": adjusted_delta,
            "combo_bonus": combo_bonus if strong_turn else 0,
            "strong_turn": strong_turn,
            "shaky_turn": shaky_turn,
        }

    @staticmethod
    def _totals_from_turns(analysis: dict) -> dict[str, int]:
        turns = analysis.get("turn_metrics", [])
        totals = {
            "advanced_linkers": 0,
            "example_count": 0,
            "filler_count": 0,
            "hedge_count": 0,
            "repair_count": 0,
            "word_count": 0,
            "strong_turns": 0,
            "shaky_turns": 0,
            "clean_turns": 0,
        }
        for item in turns:
            totals["advanced_linkers"] += int(item.get("advanced_linkers", 0))
            totals["example_count"] += int(item.get("example_count", 0))
            totals["filler_count"] += int(item.get("filler_count", 0))
            totals["hedge_count"] += int(item.get("hedge_count", 0))
            totals["repair_count"] += int(item.get("repair_count", 0))
            totals["word_count"] += int(item.get("word_count", 0))
            if int(item.get("pressure_delta", 0)) >= 18:
                totals["strong_turns"] += 1
            if int(item.get("pressure_delta", 0)) < 0:
                totals["shaky_turns"] += 1
            if (
                int(item.get("word_count", 0)) >= 18
                and int(item.get("filler_count", 0)) == 0
                and int(item.get("hedge_count", 0)) == 0
            ):
                totals["clean_turns"] += 1
        return totals

    def build_debrief(self, analysis: dict, defense_meter: int, collapsed: bool) -> dict[str, str]:
        totals = self._totals_from_turns(analysis)
        mode_key = normalized_mode_key(analysis.get("mode_key"))
        max_combo = int(analysis.get("max_combo", 0))
        heat = int(analysis.get("heat", 0))
        directives_done = sum(1 for item in analysis.get("directives", []) if item.get("completed"))

        if collapsed and max_combo >= 3:
            headline = "You broke the chamber by keeping pressure alive across multiple turns."
        elif collapsed:
            headline = "The suspect finally cracked before the room could stabilise."
        elif defense_meter <= 25:
            headline = "You had the suspect staggering, but extraction arrived a beat too early."
        elif heat >= 90:
            headline = "The room overheated and the suspect slipped back behind the glass."
        else:
            headline = "The suspect survived this contract, but several fractures in the story are already showing."

        if totals["advanced_linkers"] >= max(2, totals["example_count"]) and totals["advanced_linkers"] > 0:
            strength = "Your strongest weapon was chained reasoning. Each clean cause-and-effect link visibly moved the room."
        elif totals["example_count"] > 0:
            strength = "Concrete examples did the real damage. Whenever you grounded the point, the suspect lost room to maneuver."
        elif totals["clean_turns"] > 0:
            strength = "Clean fluency carried the best turns. The moment you stopped hedging, your answers felt far more dangerous."
        else:
            strength = "Your best moments came when you stayed in the answer long enough to sound committed rather than reactive."

        if totals["filler_count"] + totals["hedge_count"] >= 4:
            warning = "Too many fillers or hedges reopened the door for the suspect. The chamber punishes uncertainty fast."
        elif heat >= 70:
            warning = "Heat stayed too high for too long. A couple of unstable turns erased pressure you had already built."
        elif totals["example_count"] == 0:
            warning = "You kept several claims abstract. One vivid example would have made the argument much harder to dodge."
        elif directives_done == 0:
            warning = "You moved the room, but never converted that momentum into a directive clear. The run needs sharper intent."
        else:
            warning = "The argument landed in flashes, but the finishing turns still lacked the final squeeze."

        if mode_key == "onslaught":
            next_focus = "Next Onslaught run: protect your first combo, then cash it in with an example before the fifth round closes."
        elif mode_key == "precision":
            next_focus = "Next Precision run: give every answer one connector, one concrete example, and no hedge language."
        else:
            next_focus = "Next Classic run: open with a position faster, then drive a cleaner example into the active phase objective."

        return {
            "headline": headline,
            "strength": strength,
            "warning": warning,
            "next_focus": next_focus,
        }

    def suspect_prompt(self, run: InterrogationRun, analysis: dict, transcript: str) -> tuple[str, str]:
        quality = self.analyze_transcript(transcript)
        recent = analysis.get("history", [])[-8:]
        conversation = "\n".join(f"{item['role']}: {item['text']}" for item in recent)
        phase = self.active_phase(analysis)
        directives = ", ".join(
            item["label"] for item in analysis.get("directives", []) if not item.get("completed")
        ) or "none"
        system_prompt = (
            "You are roleplaying a dangerous but intelligent IELTS-style suspect in a cinematic interrogation room. "
            "Stay completely in character. Reply in spoken British English only. "
            "Keep replies short, tense, and game-like: one or two vivid sentences followed by one sharp follow-up question. "
            "If the student's logic is strong, sound pressured, irritated, and slightly defensive. "
            "If the student's logic is weak, sound amused, predatory, and dismissive. "
            "Never break the scene. Never mention being an AI."
        )
        user_prompt = (
            f"Operation: {analysis.get('operation_name', run.operation_name)}\n"
            f"Case code: {analysis.get('case_code', run.case_code)}\n"
            f"Topic: {run.topic}\n"
            f"Suspect: {run.suspect_name}, {analysis.get('suspect_title', 'suspect')}\n"
            f"Threat level: {analysis.get('threat_level', 'High')}\n"
            f"Defense meter: {analysis.get('defense_meter', 100)}\n"
            f"Current phase: {phase['label']}\n"
            f"Current phase objective: {phase['objective']}\n"
            f"Live directives still in play: {directives}\n"
            f"Combo: {analysis.get('combo', 0)}\n"
            f"Heat: {analysis.get('heat', 0)}\n"
            f"Transcript quality: {json.dumps(quality)}\n\n"
            f"Recent room log:\n{conversation}\n\n"
            f"Latest student speech:\n{transcript}\n\n"
            "Reply in plain text only."
        )
        return system_prompt, user_prompt

    async def stream_suspect_reply(
        self,
        *,
        ws,
        run: InterrogationRun,
        analysis: dict,
        transcript: str,
        reply_started_at: float,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        system_prompt, user_prompt = self.suspect_prompt(run, analysis, transcript)
        raw_reply = ""
        raw_buffer = ""
        chunk_index = 0
        reply_id = f"reply-{run.id or 0}-{int(reply_started_at * 1000)}"
        last_spoken_chunk = ""
        chunk_paths: list[Path] = []
        first_text_ms: int | None = None
        first_audio_ms: int | None = None
        out_dir = games_service.recordings_dir("interrogation", run.id or 0)
        phase = self.active_phase(analysis)
        await ws.send_json({
            "type": "suspect.thinking",
            "message": f"Rewinding the tape for {phase['label']}...",
            "reply_id": reply_id,
        })

        async def emit_chunk(text: str, chunk_no: int) -> None:
            nonlocal first_text_ms, first_audio_ms, last_spoken_chunk
            if cancel_event and cancel_event.is_set():
                return
            spoken = sanitize_spoken_reply_text(text)
            if not spoken or spoken == last_spoken_chunk:
                return
            last_spoken_chunk = spoken
            if first_text_ms is None:
                first_text_ms = round((time.perf_counter() - reply_started_at) * 1000)
            await ws.send_json({
                "type": "reply.text.delta",
                "text": spoken,
                "chunk_index": chunk_no,
                "reply_id": reply_id,
            })
            if cancel_event and cancel_event.is_set():
                return
            chunk_path = out_dir / f"suspect_chunk_{chunk_no:02d}.wav"
            await asyncio.to_thread(tts_service.synthesize_for_speaking, spoken, chunk_path)
            if cancel_event and cancel_event.is_set():
                return
            if chunk_path.exists():
                if first_audio_ms is None:
                    first_audio_ms = round((time.perf_counter() - reply_started_at) * 1000)
                chunk_paths.append(chunk_path)
                await ws.send_json({
                    "type": "reply.audio.chunk",
                    "chunk_index": chunk_no,
                    "reply_id": reply_id,
                    "audio": base64.b64encode(chunk_path.read_bytes()).decode(),
                })

        async for delta in llm_client.chat_text_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.65,
            max_tokens=72,
        ):
            if cancel_event and cancel_event.is_set():
                break
            raw_reply += delta
            raw_buffer += delta
            while True:
                segment, raw_buffer = pop_stream_segment(raw_buffer)
                if not segment:
                    break
                chunk_index += 1
                await emit_chunk(segment, chunk_index)
                if cancel_event and cancel_event.is_set():
                    break
            if cancel_event and cancel_event.is_set():
                break

        if cancel_event and cancel_event.is_set():
            return {
                "text": "",
                "reply_id": reply_id,
                "audio_path": None,
                "llm_first_text_ms": first_text_ms or 0,
                "first_audio_ms": first_audio_ms or 0,
                "cancelled": True,
            }

        final_segment, _ = pop_stream_segment(raw_buffer, force=True)
        if final_segment and not (cancel_event and cancel_event.is_set()):
            chunk_index += 1
            await emit_chunk(final_segment, chunk_index)

        final_text = sanitize_spoken_reply_text(raw_reply)
        merged = games_service.merge_audio_files(chunk_paths, out_dir / "suspect_full.wav")
        return {
            "text": final_text,
            "reply_id": reply_id,
            "audio_path": str(merged) if merged else None,
            "llm_first_text_ms": first_text_ms or 0,
            "first_audio_ms": first_audio_ms or 0,
            "cancelled": bool(cancel_event and cancel_event.is_set()),
        }

    def collapse_reward(self, analysis: dict) -> int:
        upgrades = analysis.get("upgrades", {})
        phase_bonus = int(analysis.get("phase_breaks", 0)) * (10 + int(upgrades.get("mirror_chip", 0)) * 4)
        combo_bonus = int(analysis.get("max_combo", 0)) * 5
        collapse_bonus = int(upgrades.get("collapse_dividend", 0)) * 8
        directive_bonus = int(analysis.get("bonus_reward_total", 0))
        base_reward = 38 + phase_bonus + combo_bonus + collapse_bonus + directive_bonus
        multiplier = float(OPERATION_MODES[normalized_mode_key(analysis.get("mode_key"))].get("reward_multiplier", 1.0))
        return round(base_reward * multiplier)

    def survival_reward(self, analysis: dict, defense_meter: int) -> int:
        directive_bonus = int(analysis.get("bonus_reward_total", 0))
        phase_bonus = int(analysis.get("phase_breaks", 0)) * 6
        pressure_bonus = max(0, 100 - defense_meter) // 4
        base_reward = 10 + directive_bonus + phase_bonus + pressure_bonus
        multiplier = float(OPERATION_MODES[normalized_mode_key(analysis.get("mode_key"))].get("reward_multiplier", 1.0))
        return round(base_reward * multiplier)

    def build_summary_payload(
        self,
        *,
        run: InterrogationRun,
        analysis: dict,
        defense_meter: int,
        collapsed: bool,
        profile,
    ) -> dict:
        debrief = self.build_debrief(analysis, defense_meter, collapsed)
        return {
            "run_id": run.id or 0,
            "collapsed": collapsed,
            "reward": run.reward,
            "defense_meter": defense_meter,
            "rounds_completed": run.rounds_completed,
            "transcript": run.transcript,
            "case_code": run.case_code,
            "operation_name": run.operation_name,
            "mode_label": str(analysis.get("mode_label", "")),
            "max_combo": int(analysis.get("max_combo", 0)),
            "phases_cleared": int(analysis.get("phase_breaks", 0)),
            "bonus_reward": int(analysis.get("bonus_reward_total", 0)),
            "directives_cleared": sum(1 for item in analysis.get("directives", []) if item.get("completed")),
            "rank": str(analysis.get("rank", "D")),
            "debrief_headline": debrief["headline"],
            "debrief_strength": debrief["strength"],
            "debrief_warning": debrief["warning"],
            "next_focus": debrief["next_focus"],
            "profile": profile,
        }

    def persist_round(
        self,
        session: Session,
        run: InterrogationRun,
        transcript: str,
        analysis: dict,
        defense_meter: int,
        user_audio_paths: list[Path],
        collapsed: bool,
        rare_drop: bool,
    ) -> dict:
        analysis["defense_meter"] = defense_meter
        analysis["rare_drop"] = rare_drop
        analysis["rank"] = self._rank_for_summary(analysis, defense_meter, collapsed)
        run.analysis_json = json.dumps(analysis, ensure_ascii=False)
        run.transcript = transcript.strip()
        run.defense_delta = 100 - defense_meter
        run.rounds_completed = len([item for item in analysis.get("history", []) if item.get("role") == "user"])
        run.status = "collapsed" if collapsed else "contained"
        run.reward = (
            self.collapse_reward(analysis) + (18 if rare_drop else 0)
            if collapsed
            else self.survival_reward(analysis, defense_meter)
        )
        run.completed_at = datetime.now(timezone.utc).isoformat()
        merged = games_service.merge_audio_files(
            user_audio_paths,
            games_service.recordings_dir("interrogation", run.id or 0) / "player_full.wav",
        )
        if merged:
            run.recording_path = games_service.media_url(merged)
        session.add(run)
        session.commit()
        session.refresh(run)
        return analysis


interrogation_service = InterrogationService()
