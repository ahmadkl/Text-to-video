"""Build LTX-2 prompts: one flowing English paragraph, dialogue quoted verbatim.

LTX-2 prompting guidance (from the repo README): a single chronological
paragraph, literal and cinematographic, under ~200 words, with spoken lines in
quotes and the voice described.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .screenplay import Shot


@dataclass
class Character:
    name: str  # the name as written in the screenplay (Arabic)
    look: str  # English visual description: age, face, clothes
    voice: str  # English voice description: "deep, hoarse, calm"
    reference_image: str | None = None  # optional portrait used to keep the face consistent
    female: bool = False  # picks a female TTS voice in the free pipeline
    tts_voice: str | None = None  # e.g. "ar-SY-LaithNeural"
    tts_rate: str | None = None  # e.g. "-10%"
    tts_pitch: str | None = None  # e.g. "-20Hz"


def load_characters(path: str | Path) -> dict[str, Character]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {c["name"]: Character(**c) for c in data["characters"]}


def load_style(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")).get("style", {})


DEFAULT_STYLE = {
    "language": "Levantine Syrian Arabic",
    "look": "photorealistic cinematic Syrian TV drama, natural light, shallow depth of field",
    "aspect": "vertical 9:16",
}


def shot_prompt(shot: Shot, chars: dict[str, Character], style: dict | None = None,
                setting_en: str = "", silent: bool = False) -> str:
    """silent=True: for video models without audio. The words are NOT put in the prompt (they come from TTS)."""
    st = {**DEFAULT_STYLE, **(style or {})}
    setting = setting_en or shot.setting or "an outdoor residential street"
    parts = [f"{st['look']}. Location: {setting}."]
    if shot.speaker:
        c = chars.get(shot.speaker)
        look = c.look if c else "a man"
        voice = c.voice if c else "natural"
        parts.append(f"A medium close-up of {look}, facing the camera slightly off-axis.")
        if shot.action:
            parts.append(f"{shot.action}.")
        if silent:
            parts.append(f"{'She' if c and c.female else 'He'} talks, lips moving naturally, with subtle head and "
                         "hand movement and expressive eyes. The camera is steady with a slight handheld feel.")
            return " ".join(parts)
        parts.append(
            f"He speaks in {st['language']} with a {voice} voice and natural lip movement, "
            f"saying: \"{shot.line}\""
        )
        parts.append("Natural room tone and ambient street sound. The camera holds steady with a subtle handheld feel.")
    else:
        parts.append(f"{shot.action}. No one speaks. Natural ambient sound only. Slow, steady camera movement.")
    return " ".join(parts)


NEGATIVE_HINT = "no subtitles, no on-screen text, no watermark, no cartoon style"
