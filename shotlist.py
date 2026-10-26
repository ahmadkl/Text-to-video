"""قائمة لقطات جاهزة (فيديو 001 / CHAR / LOC / SHOT / CAM / DIALOGUE / AUDIO / TIMING) ← حزمة لقطات.

إذا كان النص مكتوبًا أصلًا كلقطات مفصّلة، فلا يُعاد تقطيعه كقصة: كل «فيديو NNN» يصبح لقطة واحدة
وأمرًا كاملًا جاهزًا للنسخ إلى Veo / Flow، مع ملف shots.json بنفس صيغة بقية المستودع (يقرؤه assemble.py).

    python make_shots.py -f inputs/scene1_shotlist.txt -o results/scene1
"""
from __future__ import annotations

import re

HEAD = re.compile(r"^\s*فيديو\s+(\d+)\s*[—–-]\s*(\S+)\s*[—–-]\s*(.+?)\s*$")
SCENE = re.compile(r"^\s*المشهد\s+(\d+)\s*[—–-]\s*(.+?)\s*$")
FIELD = re.compile(r"^\s*[·•\-]\s*(CHAR|LOC|SHOT|CAM|DIALOGUE|AUDIO|TIMING)\s*:\s*(.*)$")
DIALOG = re.compile(r"([^:：«»\n]{1,30}?)\s*[:：]\s*[«\"“]([^»\"”]+)[»\"”]")
NAME = re.compile(r"(?<![A-Za-z])([A-Z]{2,})(?![A-Za-z])(?:\s*\(([^)]*)\))?")
STYLE = ("Cinematic photorealistic live-action footage from a realistic Syrian TV drama, natural light, "
         "35mm film look, shallow depth of field, realistic faces and skin")


def looks_like_shotlist(raw: str) -> bool:
    heads = sum(1 for ln in raw.splitlines() if HEAD.match(ln))
    fields = sum(1 for ln in raw.splitlines() if FIELD.match(ln))
    return heads >= 1 and fields >= 3


def _secs(timing: str) -> int:
    ends = [int(x) for x in re.findall(r"[–-]\s*(\d+)\s*s", timing)]
    return max(ends) if ends else 5


def parse(raw: str) -> tuple[list[dict], dict[str, str]]:
    shots: list[dict] = []
    cast: dict[str, str] = {}
    scene_no, scene_title, cur = 1, "", None
    for ln in raw.splitlines():
        if m := SCENE.match(ln):
            scene_no, scene_title = int(m.group(1)), m.group(2)
            continue
        if m := HEAD.match(ln):
            cur = {"id": int(m.group(1)), "scene": scene_no, "slug": scene_title, "code": m.group(2),
                   "kind": m.group(3), "f": {}}
            shots.append(cur)
            continue
        if cur and (m := FIELD.match(ln)):
            cur["f"][m.group(1)] = m.group(2).strip()
    for s in shots:
        f = s["f"]
        for nm, desc in NAME.findall(f.get("CHAR", "")):
            if desc and nm not in cast:
                cast[nm] = desc
        s["names"] = list(dict.fromkeys(n for n, _ in NAME.findall(f.get("CHAR", ""))))
        d = f.get("DIALOGUE", "")
        s["lines"] = [{"speaker": a.strip(), "text": t.strip()} for a, t in DIALOG.findall(d)]
        s["secs"] = _secs(f.get("TIMING", ""))
        s["prompt"] = prompt(s, style=STYLE)
    return shots, cast


def prompt(s: dict, style: str = STYLE, aspect: str = "9:16") -> str:
    f = s["f"]
    frame = f"Vertical {aspect} frame." if aspect == "9:16" else f"Widescreen {aspect} landscape frame."
    out = [f"{style}.", f"{frame} Clip length about {s['secs']} seconds."]
    if f.get("LOC"):
        out.append(f"Location: {f['LOC']}")
    if f.get("CHAR"):
        out.append(f"Characters (keep their look identical in every shot): {f['CHAR']}")
    if f.get("SHOT"):
        out.append(f"Shot: {f['SHOT']}")
    if f.get("CAM"):
        out.append(f"Camera and action: {f['CAM']}")
    if f.get("TIMING"):
        out.append(f"Timing: {f['TIMING']}")
    if s["lines"]:
        out.append("Dialogue, spoken aloud in natural Syrian (Levantine) Arabic with clear lip-sync; "
                   "use exactly these words:")
        out += [f'{ln["speaker"]}: "{ln["text"]}"' for ln in s["lines"]]
    else:
        out.append("No one speaks in this shot.")
    if f.get("AUDIO"):
        out.append(f"Audio: {f['AUDIO']}")
    out.append("No subtitles, no captions, no on-screen text, no logos.")
    return "\n".join(out)
