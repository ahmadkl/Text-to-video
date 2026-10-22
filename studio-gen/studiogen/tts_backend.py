"""Free Arabic speech with edge-tts (Microsoft Edge's online voices; no key, unofficial service).

Levantine-leaning voices, so each character can get its own: Syria (Laith, Amany), Lebanon
(Rami, Layla), Jordan (Taim, Sana). List what exists: `edge-tts --list-voices | grep ar-`.
Per character you may set in characters.json: tts_voice, tts_rate ("-10%"), tts_pitch ("-20Hz").
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

MALE_VOICES = ["ar-SY-LaithNeural", "ar-LB-RamiNeural", "ar-JO-TaimNeural"]
FEMALE_VOICES = ["ar-SY-AmanyNeural", "ar-LB-LaylaNeural", "ar-JO-SanaNeural"]
# Pitch/rate offsets so characters sharing a voice still sound different.
VARIANTS = [("+0Hz", "+0%"), ("-25Hz", "-8%"), ("+20Hz", "+6%"), ("-45Hz", "-14%")]


def assign_voices(names: list[str], overrides: dict[str, dict] | None = None, female: set[str] | None = None) -> dict[str, dict]:
    """Deterministic voice per character: explicit setting wins, else round-robin over the voice lists."""
    overrides, female = overrides or {}, female or set()
    out, mi, fi = {}, 0, 0
    for n in names:
        o = overrides.get(n) or {}
        if o.get("voice"):
            out[n] = {"voice": o["voice"], "pitch": o.get("pitch") or "+0Hz", "rate": o.get("rate") or "+0%"}
            continue
        if n in female:
            v, k = FEMALE_VOICES[fi % 3], fi // 3
            fi += 1
        else:
            v, k = MALE_VOICES[mi % 3], mi // 3
            mi += 1
        pitch, rate = VARIANTS[k % len(VARIANTS)]
        out[n] = {"voice": v, "pitch": o.get("pitch") or pitch, "rate": o.get("rate") or rate}
    return out


async def _save(text: str, voice: str, rate: str, pitch: str, path: str) -> None:
    import edge_tts  # pip install edge-tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(path)


def synthesize(text: str, spec: dict, out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_save(text, spec["voice"], spec["rate"], spec["pitch"], out_path))
    return out_path


def duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())
