"""Parse an Arabic (or any) screenplay into scenes and shots.

Accepted input styles (can be mixed):

    المشهد 7 — ساحة البيت، نهاراً
    علي: ليش سكرت الطريق؟
    عبد الله: هاد حقي.

and "flat" text where several lines are glued into one paragraph. Because the
character names are known (from characters.json) we can cut a flat paragraph
at every "الاسم:" occurrence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Average spoken pace used to guess clip length (words per second).
WORDS_PER_SECOND = 2.6
MIN_SECONDS = 3.0
MAX_SECONDS = 10.0  # LTX-2 clips are limited to ~10 s
FPS = 24

SCENE_RE = re.compile(r"^\s*(?:المشهد|مشهد|scene)\s*([0-9٠-٩]+)?\s*[-—:.]?\s*(.*)$", re.IGNORECASE)


@dataclass
class Shot:
    scene: int
    index: int
    setting: str
    speaker: str | None  # None for pure action shots
    line: str  # spoken text ("" for action shots)
    action: str  # stage direction / description attached to this shot
    seconds: float = 0.0

    @property
    def num_frames(self) -> int:
        """LTX needs frames = 8*k + 1."""
        k = max(1, round(self.seconds * FPS / 8))
        return 8 * k + 1


@dataclass
class Scene:
    number: int
    setting: str
    shots: list[Shot] = field(default_factory=list)


def _estimate_seconds(line: str, action: str) -> float:
    words = len(line.split())
    secs = words / WORDS_PER_SECOND + (1.5 if line else 0)
    if not line:
        secs = max(secs, 4.0)
    if action and not line:
        secs = max(secs, 4.0 + len(action.split()) / 10)
    return min(MAX_SECONDS, max(MIN_SECONDS, secs))


def _split_long(text: str, max_words: int) -> list[str]:
    """Split a long utterance at sentence ends so each part fits in one clip."""
    if len(text.split()) <= max_words:
        return [text]
    parts = re.split(r"(?<=[.!?؟…])\s+", text)
    out, cur = [], ""
    for p in parts:
        if cur and len((cur + " " + p).split()) > max_words:
            out.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    return out


def _tokenise(text: str, names: list[str]) -> list[tuple[str | None, str]]:
    """Return [(speaker | None, text)] in order. None = narration/action."""
    if not names:
        return [(None, text.strip())] if text.strip() else []
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    pat = re.compile(rf"(?:(?<=\s)|^)({alt})\s*[:：]\s*")
    out: list[tuple[str | None, str]] = []
    pos, speaker = 0, None
    for m in pat.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            out.append((speaker, chunk))
        speaker, pos = m.group(1), m.end()
    tail = text[pos:].strip()
    if tail:
        out.append((speaker, tail))
    return out


def parse(text: str, character_names: list[str]) -> list[Scene]:
    scenes: list[Scene] = []
    current: Scene | None = None
    pending_action: list[str] = []
    n_scene = 0

    def ensure_scene() -> Scene:
        nonlocal current, n_scene
        if current is None:
            n_scene += 1
            current = Scene(n_scene, "")
            scenes.append(current)
        return current

    def flush_action_as_shot() -> None:
        nonlocal pending_action
        if pending_action:
            sc = ensure_scene()
            act = " ".join(pending_action)
            sc.shots.append(Shot(sc.number, len(sc.shots) + 1, sc.setting, None, "", act,
                                 _estimate_seconds("", act)))
            pending_action = []

    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = SCENE_RE.match(raw)
        if m and (raw.startswith(("المشهد", "مشهد")) or raw.lower().startswith("scene")):
            flush_action_as_shot()
            digits = (m.group(1) or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
            n_scene = int(digits) if digits else n_scene + 1
            current = Scene(n_scene, m.group(2).strip())
            scenes.append(current)
            continue
        for speaker, chunk in _tokenise(raw, character_names):
            if speaker is None:
                # stage direction: attach to the next spoken shot
                pending_action.append(chunk.strip("()[] "))
                continue
            sc = ensure_scene()
            action = " ".join(pending_action)
            pending_action = []
            for piece in _split_long(chunk, int(MAX_SECONDS * WORDS_PER_SECOND) - 4):
                sc.shots.append(Shot(sc.number, len(sc.shots) + 1, sc.setting, speaker, piece, action,
                                     _estimate_seconds(piece, action)))
                action = ""  # only the first piece carries the stage direction
    flush_action_as_shot()
    return scenes
