import json
import shutil
import subprocess
import re
from pathlib import Path
from datetime import datetime

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DATA_ROOT = _PROJECT_ROOT / "data"


class RecordingService:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or (_PROJECT_ROOT / "data/recordings")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: int) -> Path:
        d = self.base_dir / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_turn(
        self,
        session_id: int,
        turn_index: int,
        audio_data: bytes,
        role: str,
        suffix: str | None = None,
    ) -> Path:
        d = self.session_dir(session_id)
        ext = (suffix or ("webm" if role == "user" else "wav")).lstrip(".")
        filename = f"{role}_turn_{turn_index:03d}.{ext}"
        filepath = d / filename
        filepath.write_bytes(audio_data)
        return filepath

    def save_transcript(self, session_id: int, transcript: list[dict]):
        d = self.session_dir(session_id)
        (d / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    def save_notes(self, session_id: int, notes: list[dict]):
        d = self.session_dir(session_id)
        (d / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2))

    def get_session_recording_path(self, session_id: int) -> Path:
        return self.session_dir(session_id) / "full.webm"

    @staticmethod
    def media_url(path: Path) -> str:
        try:
            rel = path.resolve().relative_to(_DATA_ROOT.resolve())
            return "/media/" + "/".join(rel.parts)
        except ValueError:
            return str(path)

    def finalize_full_recording(self, session_id: int) -> Path | None:
        d = self.session_dir(session_id)
        output_path = d / "full.webm"
        turn_files = [
            p for p in d.iterdir()
            if p.is_file() and re.match(r"(user|assistant)_turn_\d+\.(webm|wav|mp4|m4a|ogg)$", p.name)
        ]
        turn_files.sort(key=lambda p: (
            int(re.search(r"turn_(\d+)", p.name).group(1)),
            0 if p.name.startswith("user_") else 1,
        ))

        if not turn_files:
            return None

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            inputs: list[str] = []
            labels: list[str] = []
            for index, path in enumerate(turn_files):
                inputs.extend(["-i", str(path)])
                labels.append(f"[{index}:a]")
            filter_complex = "".join(labels) + f"concat=n={len(turn_files)}:v=0:a=1[out]"
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

        # Fallback: at least expose the first user turn as a playable artifact.
        first = turn_files[0]
        fallback = d / f"full{first.suffix}"
        if fallback != first:
            fallback.write_bytes(first.read_bytes())
        return fallback


recording_service = RecordingService()
