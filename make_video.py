#!/usr/bin/env python3
"""مولّد فيديو من سيناريو — شخصيات مرسومة + أصوات + حوار.

يقرأ سيناريو بالشكل التالي ويحوّله إلى فيديو MP4:

    المشهد 7
    خارجي – جانب منزل علي – نهار

    عبد الله جالس مع عامر وأبو عامر. علي يقف أمامهم.

    علي:
    السلام عليكم.

    عبد الله (بصوت منخفض):
    من أولها عم يعاند.

- سطر `# عنوان` أو `المشهد N` = بطاقة عنوان.
- سطر يبدأ بـ داخلي / خارجي / INT / EXT = بطاقة مكان وتحديد الخلفية.
- `الاسم:` ثم الكلام = حوار بصوت مستقل لكل شخصية وحركة فم.
- أي فقرة أخرى = وصف حركة (يقرؤه راوٍ).
- اختياري: سطر `الشخصيات: علي، سارة (أنثى)، أبو عامر (كبير)` لتحديد الممثلين الصامتين والجنس والعمر.
"""
from __future__ import annotations

import argparse
import array
import asyncio
import hashlib
import math
import random
import re
import subprocess
import sys
import tempfile
import types
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import text_to_video as ttv
from text_to_video import (FPS, ASPECTS, PALETTES, SENTENCE_RE, TextShaper, clean, find_font,
                           is_arabic, lerp, log, probe_duration, render_text_layer, smooth,
                           srt_time)

# ----------------------------------------------------------------------------
# الأصوات
# ----------------------------------------------------------------------------
VOICES = {
    "ar": {
        "m": ["ar-SY-LaithNeural", "ar-JO-TaimNeural", "ar-LB-RamiNeural", "ar-EG-ShakirNeural"],
        "f": ["ar-SY-AmanyNeural", "ar-JO-SanaNeural", "ar-LB-LaylaNeural", "ar-EG-SalmaNeural"],
        "narrator": "ar-SA-HamedNeural",
        "crowd": "ar-SA-HamedNeural",
        "fallback": "ar-SA-HamedNeural",
    },
    "en": {
        "m": ["en-US-GuyNeural", "en-GB-RyanNeural", "en-US-ChristopherNeural", "en-AU-WilliamNeural"],
        "f": ["en-US-AriaNeural", "en-US-JennyNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural"],
        "narrator": "en-US-DavisNeural",
        "crowd": "en-US-GuyNeural",
        "fallback": "en-US-AriaNeural",
    },
}

CROWD_NAMES = {"الجميع", "الكل", "كلهم", "everyone", "all", "everybody"}
FEMALE_NAMES = {"سارة", "مريم", "فاطمة", "ليلى", "سلمى", "نور", "هدى", "رنا", "ريم", "ندى", "لينا",
                "هبة", "زينب", "عائشة", "خديجة", "سعاد", "دعاء", "آية", "ياسمين", "أماني", "رغد",
                "لمى", "جود", "ميس", "رولا", "سناء", "هالة", "منى", "ديما", "رهف", "شهد", "أم",
                "mary", "sara", "sarah", "anna", "emma", "lily", "laila", "mona", "nora"}
MALE_TA = {"حمزة", "معاوية", "أسامة", "طلحة", "عكرمة", "قتيبة", "عبيدة", "أبو"}
OLD_WORDS = ("أبو", "الحاج", "العم", "الشيخ", "جد", "جدي", "حاج")

SLUG_RE = re.compile(r"^(خارجي|داخلي|خارجى|داخلى|EXT\b|INT\b)", re.I)
TITLE_RE = re.compile(r"^(المشهد|مشهد|scene|الفصل|chapter|الحلقة|episode)\b", re.I)
SPEAKER_RE = re.compile(r"^\s*([^\s:：()（）][^:：()（）]{0,38}?)\s*(?:[(（]([^)）]*)[)）])?\s*[:：]\s*(.*)$")
CAST_RE = re.compile(r"^\s*(الشخصيات|الممثلون|الممثلين|cast|characters)\s*[:：]\s*(.+)$", re.I)
WITH_RE = re.compile(r"(?:^|\s)مع\s+([^.،:!?؟\n]+)")
LEAVE_RE = re.compile(r"(ينصرف|ينصرفون|يخرج|يغادر|يرحل|يمضي|exits|leaves|walks away|walks out)", re.I)
NIGHT_RE = re.compile(r"(ليل|مساء|ليلي|night|evening)", re.I)

SKIN = [(255, 219, 177), (241, 194, 150), (224, 172, 125), (198, 134, 92), (150, 95, 62)]
SHIRTS = [(66, 133, 244), (219, 68, 55), (15, 157, 88), (232, 150, 20), (171, 71, 188),
          (0, 150, 170), (240, 100, 60), (84, 110, 122)]
HAIRS = [(30, 22, 18), (52, 36, 26), (78, 52, 34), (20, 20, 24)]
SCARVES = [(190, 60, 100), (60, 110, 190), (70, 150, 110), (150, 90, 190), (230, 160, 60)]


def tokens_of(name: str) -> list[str]:
    return name.split()


def guess_gender(name: str) -> str:
    first = tokens_of(name)[0].lower() if tokens_of(name) else ""
    if first in FEMALE_NAMES:
        return "f"
    if first in MALE_TA:
        return "m"
    if first.endswith("ة") and name.strip() not in MALE_TA:
        return "f"
    return "m"


def find_name(text: str, name: str):
    return re.search(rf"(?<!\w)و?{re.escape(name)}(?!\w)", text)


# ----------------------------------------------------------------------------
# تحليل السيناريو
# ----------------------------------------------------------------------------
def valid_speaker(name: str) -> bool:
    name = name.strip()
    if not name or re.search(r"[.!?؟,،]", name) or name[0].isdigit():
        return False
    return len(tokens_of(name)) <= 3


def parse_cast_header(value: str) -> dict:
    decl: dict[str, dict] = {}
    for item in re.split(r"[،,;؛]", value):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^(.*?)\s*[(（]([^)）]*)[)）]\s*$", item)
        name, tags = (m.group(1).strip(), m.group(2).lower()) if m else (item, "")
        info = {"gender": None, "old": False, "child": False}
        if re.search(r"(أنثى|انثى|امرأة|بنت|فتاة|female|girl|woman)", tags):
            info["gender"] = "f"
        elif re.search(r"(ذكر|رجل|ولد|شاب|male|boy|man)", tags):
            info["gender"] = "m"
        info["old"] = bool(re.search(r"(كبير|عجوز|مسن|شيخ|old|elder)", tags))
        info["child"] = bool(re.search(r"(طفل|طفلة|صغير|child|kid)", tags))
        decl[name] = info
    return decl


def split_long(beat: dict, max_chars: int) -> list[dict]:
    text = beat["text"]
    if len(text) <= max_chars:
        return [beat]
    out, chunk = [], ""
    for sentence in SENTENCE_RE.split(text):
        if chunk and len(chunk) + 1 + len(sentence) > max_chars:
            out.append(chunk)
            chunk = sentence
        else:
            chunk = f"{chunk} {sentence}".strip()
    if chunk:
        out.append(chunk)
    beats = []
    for i, piece in enumerate(out):
        b = dict(beat)
        b["text"] = piece
        if i:
            b["paren"] = ""
        beats.append(b)
    return beats


def parse_screenplay(raw: str, max_chars: int) -> tuple[list[dict], dict]:
    raw = raw.replace("\r", "").replace(" || ", "\n\n").strip()
    beats: list[dict] = []
    decl: dict[str, dict] = {}
    cur: dict | None = None
    seen_any = False

    def flush():
        nonlocal cur
        if cur and cur["text"].strip():
            beats.append(cur)
        cur = None

    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            flush()
            continue
        first_line = not seen_any
        seen_any = True

        if s.startswith("# "):
            flush()
            beats.append({"kind": "title", "text": clean(s[2:])})
            continue
        m = CAST_RE.match(s)
        if m:
            flush()
            decl.update(parse_cast_header(m.group(2)))
            continue
        if SLUG_RE.match(s):
            flush()
            beats.append({"kind": "location", "text": clean(s)})
            continue
        if first_line and TITLE_RE.match(s) and len(s) < 40 and ":" not in s:
            beats.append({"kind": "title", "text": clean(s)})
            continue
        m = SPEAKER_RE.match(s)
        if m and valid_speaker(m.group(1)):
            flush()
            cur = {"kind": "dialogue", "speaker": m.group(1).strip(),
                   "paren": (m.group(2) or "").strip(), "text": m.group(3).strip()}
            continue
        if cur:
            cur["text"] = f"{cur['text']} {s}".strip()
        else:
            cur = {"kind": "action", "text": s}
    flush()

    result: list[dict] = []
    for b in beats:
        b["text"] = clean(b["text"])
        if not b["text"]:
            continue
        if b["kind"] in ("dialogue", "action"):
            result.extend(split_long(b, max_chars))
        else:
            result.append(b)
    return result, decl


def build_cast(beats: list[dict], decl: dict) -> list[dict]:
    """يستخرج الشخصيات بترتيب ظهورها."""
    speakers = []
    for b in beats:
        if b["kind"] == "dialogue" and b["speaker"] not in CROWD_NAMES and b["speaker"] not in speakers:
            speakers.append(b["speaker"])
    known = list(dict.fromkeys(list(decl) + speakers))
    order: list[str] = list(decl)

    def add(name: str):
        if name and name not in order:
            order.append(name)

    for b in beats:
        if b["kind"] == "dialogue":
            if b["speaker"] not in CROWD_NAMES:
                add(b["speaker"])
        elif b["kind"] == "action":
            found = []
            for name in known:
                m = find_name(b["text"], name)
                if m:
                    found.append((m.start(), name))
            if not decl:  # كشف الممثلين الصامتين تلقائيًا فقط عند غياب سطر الشخصيات
                for wm in WITH_RE.finditer(b["text"]):
                    pieces = re.split(r"\s+و(?=\w)|\s*[،,]\s*", wm.group(1).strip())
                    if len(pieces) > 4:
                        continue
                    for piece in pieces:
                        piece = piece.strip()
                        t = tokens_of(piece)
                        if not t or len(t) > 3 or (t[0].startswith("ال") and t[0] != "الحاج"):
                            continue
                        if piece in CROWD_NAMES:
                            continue
                        pos = b["text"].find(piece)
                        found.append((pos if pos >= 0 else 10**6, piece))
            for _, name in sorted(found):
                add(name)

    cast = []
    for idx, name in enumerate(order[:6]):
        info = decl.get(name, {})
        old = info.get("old") or any(name.startswith(w) for w in OLD_WORDS)
        gender = info.get("gender") or guess_gender(name)
        cast.append(make_look(name, gender, bool(old), bool(info.get("child")), idx))
    return cast


def make_look(name: str, gender: str, old: bool, child: bool, idx: int) -> dict:
    h = int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(h)
    look = {
        "name": name, "gender": gender, "old": old, "child": child,
        "skin": rng.choice(SKIN), "shirt": SHIRTS[(h + idx) % len(SHIRTS)],
        "pants": rng.choice([(40, 48, 70), (60, 52, 46), (34, 34, 40)]),
        "hair_color": (232, 232, 232) if old else rng.choice(HAIRS),
        "scarf": rng.choice(SCARVES), "pitch": (h % 15) - 7,
        "phase": rng.uniform(0, 6.28), "blink_period": rng.uniform(2.6, 4.8),
    }
    if gender == "f":
        look["style"] = "scarf" if (old or rng.random() < 0.5) else "long"
        look["beard"] = False
    else:
        look["style"] = "bald" if old and rng.random() < 0.5 else rng.choice(["short", "short", "cap"])
        look["beard"] = old or rng.random() < 0.45
    return look


# ----------------------------------------------------------------------------
# رسم الشخصيات
# ----------------------------------------------------------------------------
BASE_W, BASE_H = 300, 520
S = 3  # تكبير للتنعيم


def make_sprite(look: dict, scale: float) -> Image.Image:
    im = Image.new("RGBA", (BASE_W * S, BASE_H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def R(x0, y0, x1, y1):
        return [x0 * S, y0 * S, x1 * S, y1 * S]

    skin = look["skin"]
    dark_skin = lerp(skin, (0, 0, 0), 0.12)
    style = look["style"]

    if style == "long":
        d.ellipse(R(84, 70, 216, 310), fill=look["hair_color"])
    # ساقان وحذاء
    for x in (104, 160):
        d.rounded_rectangle(R(x, 385, x + 38, 500), radius=10 * S, fill=look["pants"])
        d.ellipse(R(x - 6, 488, x + 46, 516), fill=(35, 30, 30))
    # الجذع والذراعان
    d.rounded_rectangle(R(78, 228, 222, 405), radius=34 * S, fill=look["shirt"])
    for x0 in (52, 212):
        d.rounded_rectangle(R(x0, 240, x0 + 38, 385), radius=18 * S, fill=lerp(look["shirt"], (0, 0, 0), 0.08))
        d.ellipse(R(x0 + 2, 372, x0 + 36, 404), fill=skin)
    if style == "scarf":
        d.ellipse(R(70, 188, 230, 320), fill=look["scarf"])
        d.rounded_rectangle(R(92, 250, 208, 405), radius=30 * S, fill=look["shirt"])
        d.ellipse(R(84, 196, 216, 290), fill=look["scarf"])
    else:
        d.rectangle(R(132, 198, 168, 242), fill=dark_skin)
    # الرأس
    if style == "scarf":
        d.ellipse(R(82, 66, 218, 230), fill=look["scarf"])
        d.ellipse(R(104, 88, 196, 214), fill=skin)
    else:
        for x in (88, 212):
            d.ellipse(R(x - 12, 132, x + 12, 160), fill=dark_skin)
        d.ellipse(R(92, 76, 208, 210), fill=skin)
    # شعر / قبعة / لحية
    hair = look["hair_color"]
    if style == "short":
        d.pieslice(R(88, 58, 212, 200), 180, 360, fill=hair)
    elif style == "cap":
        d.pieslice(R(88, 58, 212, 196), 180, 360, fill=look["scarf"])
        d.ellipse(R(130, 110, 232, 128), fill=lerp(look["scarf"], (0, 0, 0), 0.2))
    elif style == "long":
        d.pieslice(R(88, 62, 212, 200), 180, 360, fill=hair)
    if look["beard"]:
        d.pieslice(R(94, 118, 206, 222), 0, 180, fill=hair)
        d.ellipse(R(130, 160, 170, 196), fill=skin)
    return im.resize((int(BASE_W * scale), int(BASE_H * scale)), Image.LANCZOS)


FACE_BOX = (96, 104, 108, 104)  # x, y, w, h


def face_patch(look: dict, scale: float, open_amt: float, blink: bool, look_dx: float,
               brow_up: float) -> Image.Image:
    ox, oy, pw, ph = FACE_BOX
    im = Image.new("RGBA", (pw * S, ph * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def P(x, y):
        return ((x - ox) * S, (y - oy) * S)

    brow = look["hair_color"] if not look["old"] else (170, 170, 170)
    if look["style"] == "scarf":
        brow = (60, 40, 30)
    for ex in (129, 171):
        cy = 143
        if blink:
            d.line([P(ex - 10, cy + 3), P(ex + 10, cy + 3)], fill=(60, 40, 30), width=3 * S)
        else:
            d.ellipse([*P(ex - 11, cy - 13), *P(ex + 11, cy + 13)], fill=(255, 255, 255))
            d.ellipse([*P(ex - 6 + look_dx, cy - 6), *P(ex + 6 + look_dx, cy + 6)], fill=(40, 28, 22))
            d.ellipse([*P(ex - 3 + look_dx, cy - 5), *P(ex + look_dx, cy - 2)], fill=(255, 255, 255))
        d.line([P(ex - 13, cy - 21 - brow_up), P(ex + 13, cy - 24 - brow_up)], fill=brow, width=4 * S)
    mx, my = 150, 182
    if open_amt < 0.1:
        d.arc([*P(mx - 16, my - 10), *P(mx + 16, my + 6)], 20, 160, fill=(120, 48, 52), width=3 * S)
    else:
        hh = 5 + open_amt * 26
        d.ellipse([*P(mx - 14, my - hh / 2 + 2), *P(mx + 14, my + hh / 2 + 2)], fill=(110, 28, 44))
        if hh > 14:
            d.ellipse([*P(mx - 8, my + hh / 2 - 7), *P(mx + 8, my + hh / 2 + 2)], fill=(225, 110, 120))
    return im.resize((int(pw * scale), int(ph * scale)), Image.LANCZOS)


def pill(shaper: TextShaper, text: str, size: int, bg: tuple, fg=(255, 255, 255)) -> Image.Image:
    rtl = is_arabic(text)
    shown = shaper.prep(text, rtl)
    font = shaper.font(size)
    kw = shaper.kwargs(rtl)
    tw = int(font.getlength(shown, **kw))
    pad = size // 2
    im = Image.new("RGBA", (tw + pad * 2, int(size * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=im.height // 2, fill=bg)
    d.text((pad, int(size * 0.22)), shown, font=font, fill=fg, **kw)
    return im


def caption_layer(shaper: TextShaper, text: str, w: int, zone_h: int, dialogue: bool,
                  base_size: int) -> Image.Image:
    rtl = is_arabic(text)
    size = base_size
    max_w = int(w * 0.86)
    while True:
        lines = shaper.wrap(text, size, max_w, rtl)
        line_h = int(size * 1.4)
        if (len(lines) * line_h + size <= zone_h and len(lines) <= 3) or size <= 18:
            break
        size -= 2
    widest = int(max(shaper.width(ln, size, rtl) for ln in lines))
    pad = size // 2
    bw, bh = min(w - 20, widest + pad * 3), len(lines) * line_h + pad
    im = Image.new("RGBA", (w, zone_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0 = (w - bw) // 2, (zone_h - bh) // 2
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=size // 2,
                        fill=(0, 0, 0, 165) if dialogue else (35, 35, 45, 150))
    font = shaper.font(size)
    kw = shaper.kwargs(rtl)
    y = y0 + pad // 2 + (line_h - size) // 3
    for ln in lines:
        shown = shaper.prep(ln, rtl)
        tw = font.getlength(shown, **kw)
        d.text(((w - tw) / 2, y), shown, font=font,
               fill=(255, 255, 255) if dialogue else (225, 225, 235), **kw)
        y += line_h
    return im


# ----------------------------------------------------------------------------
# الخلفيات
# ----------------------------------------------------------------------------
def vgrad(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    mask = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), mask)


def build_bg(w: int, h: int, kind: str, night: bool, seed: int) -> Image.Image:
    rng = random.Random(seed)
    gy = int(h * 0.745)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    if kind == "outdoor":
        top, bot = ((12, 20, 55), (62, 72, 120)) if night else ((84, 160, 232), (205, 235, 252))
        img.paste(vgrad(w, gy, top, bot), (0, 0))
        # شمس أو قمر
        r = int(min(w, h) * 0.065)
        cx, cy = int(w * rng.choice([0.2, 0.8])), int(h * 0.15)
        glow = Image.new("RGBA", (r * 8, r * 8), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([r * 2, r * 2, r * 6, r * 6],
                                     fill=(235, 235, 255, 90) if night else (255, 240, 170, 120))
        glow = glow.filter(ImageFilter.GaussianBlur(r * 0.9))
        img.paste(glow, (cx - r * 4, cy - r * 4), glow)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(245, 245, 255) if night else (255, 236, 150))
        # بيوت
        tones = [(170, 150, 120), (190, 168, 135), (150, 132, 110), (205, 185, 150), (140, 120, 100)]
        if night:
            tones = [lerp(t, (10, 14, 40), 0.55) for t in tones]
        x = -int(w * 0.03)
        while x < w:
            bw = int(w * rng.uniform(0.13, 0.22))
            bh = int(h * rng.uniform(0.12, 0.25))
            c = rng.choice(tones)
            d.rectangle([x, gy - bh, x + bw, gy], fill=c)
            d.rectangle([x, gy - bh, x + bw, gy - bh + max(4, h // 90)], fill=lerp(c, (0, 0, 0), 0.25))
            for i in range(rng.randint(1, 2)):
                wx = x + int(bw * (0.18 + i * 0.45))
                ws = int(bw * 0.2)
                wc = (250, 215, 120) if night else (60, 80, 100)
                d.rectangle([wx, gy - bh + int(bh * 0.2), wx + ws, gy - bh + int(bh * 0.2) + ws], fill=wc)
            d.rectangle([x + int(bw * 0.4), gy - int(bh * 0.4), x + int(bw * 0.6), gy],
                        fill=lerp(c, (30, 20, 10), 0.55))
            x += bw + int(w * rng.uniform(0.0, 0.03))
        ground = (52, 74, 60) if night else (198, 172, 124)
        img.paste(vgrad(w, h - gy, ground, lerp(ground, (0, 0, 0), 0.25)), (0, gy))
        d.rectangle([0, gy, w, gy + max(3, h // 140)], fill=lerp(ground, (255, 255, 255), 0.25))
    else:
        wall = rng.choice([(222, 196, 160), (190, 205, 215), (214, 180, 170), (200, 210, 180)])
        if night:
            wall = lerp(wall, (20, 24, 40), 0.55)
        img.paste(vgrad(w, gy, lerp(wall, (255, 255, 255), 0.12), lerp(wall, (0, 0, 0), 0.12)), (0, 0))
        floor = lerp(wall, (90, 60, 40), 0.6)
        img.paste(vgrad(w, h - gy, floor, lerp(floor, (0, 0, 0), 0.3)), (0, gy))
        d.rectangle([0, gy - max(10, h // 45), w, gy], fill=lerp(wall, (0, 0, 0), 0.3))
        ww, wh = int(w * 0.2), int(h * 0.3)
        wx, wy = int(w * rng.choice([0.14, 0.62])), int(h * 0.1)
        d.rectangle([wx - 8, wy - 8, wx + ww + 8, wy + wh + 8], fill=(245, 245, 245))
        img.paste(vgrad(ww, wh, (30, 40, 80), (90, 100, 140)) if night
                  else vgrad(ww, wh, (130, 190, 240), (215, 238, 250)), (wx, wy))
        d.line([wx + ww // 2, wy, wx + ww // 2, wy + wh], fill=(245, 245, 245), width=6)
        d.line([wx, wy + wh // 2, wx + ww, wy + wh // 2], fill=(245, 245, 245), width=6)
    return img


def make_cloud(w: int, h: int, rng: random.Random) -> Image.Image:
    cw, ch = int(w * 0.28), int(h * 0.12)
    im = Image.new("RGBA", (cw, int(ch * 1.8)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for _ in range(5):
        x, y = rng.uniform(0.1, 0.55) * cw, rng.uniform(0.2, 0.45) * ch
        r = rng.uniform(0.18, 0.3) * ch * 1.6
        d.ellipse([x, y, x + r * 1.5, y + r], fill=(255, 255, 255, 190))
    return im.filter(ImageFilter.GaussianBlur(ch * 0.06))


# ----------------------------------------------------------------------------
# الصوت
# ----------------------------------------------------------------------------
def style_from_paren(paren: str) -> dict:
    p = paren.lower()
    if re.search(r"(منخفض|همس|هامس|خافت|whisper|quiet|low)", p):
        return {"volume": "-35%", "rate": "-8%", "pitch": 0}
    if re.search(r"(غضب|غاضب|صارخ|يصرخ|بحدة|angry|shout|loud)", p):
        return {"volume": "+25%", "rate": "+8%", "pitch": 4}
    if re.search(r"(حزن|حزين|بكاء|sad|crying)", p):
        return {"volume": "-10%", "rate": "-12%", "pitch": -4}
    return {"volume": "+0%", "rate": "+0%", "pitch": 0}


def assign_voices(beats: list[dict], cast: list[dict], lang: str, narrate: bool, voice_arg: str) -> None:
    pool = VOICES[lang]
    counters = {"m": 0, "f": 0}
    for c in cast:
        g = c["gender"]
        c["voice"] = pool[g][counters[g] % len(pool[g])]
        counters[g] += 1
    by_name = {c["name"]: c for c in cast}
    for b in beats:
        b["voice"], b["rate"], b["volume"], b["pitch"] = None, "+0%", "+0%", "+0Hz"
        if b["kind"] == "dialogue":
            style = style_from_paren(b.get("paren", ""))
            if b["speaker"] in CROWD_NAMES or b["speaker"] not in by_name:
                b["voice"], base_pitch = pool["crowd"], 0
            else:
                c = by_name[b["speaker"]]
                b["voice"], base_pitch = c["voice"], c["pitch"]
            b["rate"], b["volume"] = style["rate"], style["volume"]
            b["pitch"] = f"{base_pitch + style['pitch']:+d}Hz"
            if voice_arg not in ("auto", "none"):
                b["voice"] = voice_arg
        elif b["kind"] == "action" and narrate:
            b["voice"] = voice_arg if voice_arg not in ("auto", "none") else pool["narrator"]
            b["rate"] = "-5%"


def synthesize(beats: list[dict], tmp: Path, fallback: str) -> list[str | None]:
    try:
        import edge_tts
    except Exception:
        log("تحذير: edge-tts غير مثبّت، سيُنتج الفيديو بدون صوت.")
        return [None] * len(beats)

    async def run() -> list[str | None]:
        out: list[str | None] = []
        for i, b in enumerate(beats):
            if not b["voice"]:
                out.append(None)
                continue
            path = str(tmp / f"voice_{i:03d}.mp3")
            done = None
            for voice in dict.fromkeys([b["voice"], fallback]):
                try:
                    await edge_tts.Communicate(b["text"], voice, rate=b["rate"], volume=b["volume"],
                                               pitch=b["pitch"]).save(path)
                    if Path(path).stat().st_size > 500:
                        done = path
                        break
                except Exception as exc:  # noqa: BLE001
                    log(f"تحذير: الصوت {voice} فشل للمقطع {i + 1}: {exc}")
            out.append(done)
        return out

    return asyncio.run(run())


def amp_envelope(audio: str, tmp: Path, idx: int) -> list[float]:
    wav_path = tmp / f"env_{idx:03d}.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", audio, "-ac", "1", "-ar", "8000",
                    "-f", "wav", str(wav_path)], check=True)
    with wave.open(str(wav_path), "rb") as wf:
        samples = array.array("h")
        samples.frombytes(wf.readframes(wf.getnframes()))
    step = 8000 // FPS
    rms = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if not chunk:
            break
        rms.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    peak = max(rms) if rms else 1.0
    env, prev = [], 0.0
    for v in rms:
        cur = min(1.0, (v / (peak * 0.85 + 1e-6)))
        prev = prev * 0.45 + cur * 0.55
        env.append(prev)
    return env


# ----------------------------------------------------------------------------
# التوليد
# ----------------------------------------------------------------------------
def looks_like_screenplay(raw: str) -> bool:
    raw = raw.replace(" || ", "\n")
    speaker_lines = 0
    for line in raw.split("\n"):
        s = line.strip()
        if SLUG_RE.match(s):
            return True
        m = SPEAKER_RE.match(s)
        if m and not m.group(3).strip() and valid_speaker(m.group(1)):
            speaker_lines += 1
    return speaker_lines >= 2


def generate_screenplay(args) -> Path:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    beats, decl = parse_screenplay(raw or "", args.max_chars)
    if not beats:
        raise SystemExit("النص فارغ.")
    cast = build_cast(beats, decl)
    lang = "ar" if is_arabic(raw) else "en"
    log(f"عدد المقاطع: {len(beats)} | الشخصيات: {', '.join(c['name'] for c in cast) or '-'}")

    w, h = ASPECTS[args.aspect]
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else int(hashlib.sha1(raw.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    narrate = not args.no_narrator
    assign_voices(beats, cast, lang, narrate, args.voice)
    use_voice = args.voice != "none" and not args.no_voice

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        voices: list[str | None] = [None] * len(beats)
        if use_voice:
            log("توليد الأصوات...")
            voices = synthesize(beats, tmp, VOICES[lang]["fallback"])

        lead_in = 0.45
        envs: dict[int, list[float]] = {}
        for i, (b, v) in enumerate(zip(beats, voices)):
            b["audio"] = v
            if v:
                b["dur"] = max(args.min_beat, probe_duration(v) + lead_in + (0.55 if b["kind"] == "dialogue" else 0.7))
                if b["kind"] == "dialogue":
                    envs[i] = amp_envelope(v, tmp, i)
            elif b["kind"] in ("title", "location"):
                b["dur"] = max(3.0, args.min_beat)
            else:
                b["dur"] = max(args.min_beat, len(b["text"]) / 14 + 1.4)
        t0 = 0.0
        for b in beats:
            b["start"] = t0
            t0 += b["dur"]
        total = t0
        log(f"مدة الفيديو: {total:.1f} ثانية")

        # ---- الحلقات (كل مكان جديد) ----
        episodes: list[dict] = []
        beat_ep: list[int] = []
        for i, b in enumerate(beats):
            if b["kind"] == "location" or not episodes:
                text = b["text"] if b["kind"] == "location" else ""
                kind = "indoor" if re.search(r"(داخلي|داخلى|INT\b)", text, re.I) else (
                    "outdoor" if text else "indoor")
                episodes.append({"kind": kind, "night": bool(NIGHT_RE.search(text)), "leave": {},
                                 "seed": rng.randrange(10**6)})
            beat_ep.append(len(episodes) - 1)

        cast_names = [c["name"] for c in cast]
        last_speaker = None
        for i, b in enumerate(beats):
            ep = episodes[beat_ep[i]]
            if b["kind"] == "dialogue" and b["speaker"] in cast_names:
                last_speaker = b["speaker"]
            if b["kind"] == "action":
                lm = LEAVE_RE.search(b["text"])
                if lm:
                    before = [(m.start(), n) for n in cast_names
                              if (m := find_name(b["text"][:lm.start()], n))]
                    who = max(before)[1] if before else last_speaker
                    if who and who not in ep["leave"]:
                        ep["leave"][who] = b["start"] + max(0.4, b["dur"] * 0.45)
        for ep in episodes:
            ep["bg"] = build_bg(w, h, ep["kind"], ep["night"], ep["seed"])

        # ---- العناصر المرسومة مسبقًا ----
        shaper = TextShaper(find_font())
        n = max(1, len(cast))
        slot_w = w * 0.9 / n
        target_h = h * 0.50
        scale = min(target_h / BASE_H, slot_w * 0.9 / BASE_W, 1.3)
        y_base = int(h * 0.745)
        label_size = max(18, int(min(w, h) * 0.034))
        for k, c in enumerate(cast):
            mul = 0.72 if c["child"] else 1.0
            c["scale"] = scale * mul
            c["sprite"] = make_sprite(c, c["scale"])
            slot = (n - 1 - k) if lang == "ar" else k
            c["cx"] = int(w * 0.05 + slot_w * (slot + 0.5))
            c["label"] = pill(shaper, c["name"], label_size, (20, 20, 30, 190))
            c["label_on"] = pill(shaper, c["name"], label_size, c["shirt"] + (235,))
            sw = int(c["sprite"].width * 0.8)
            shadow = Image.new("RGBA", (sw, int(sw * 0.22)), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).ellipse([0, 0, sw - 1, shadow.height - 1], fill=(0, 0, 0, 90))
            c["shadow"] = shadow.filter(ImageFilter.GaussianBlur(4))

        for i, b in enumerate(beats):
            if b["kind"] in ("title", "location"):
                pal = PALETTES[int(hashlib.sha1(b["text"].encode()).hexdigest()[:4], 16) % len(PALETTES)]
                b["pal"] = pal
                b["layer"] = render_text_layer(shaper, b["text"], (w, h), b["kind"] == "title", pal[2])
                b["card"] = vgrad(w, h, pal[0], pal[1])
            else:
                text = b["text"]
                if b["kind"] == "dialogue":
                    sp = b["speaker"] + (f" ({b['paren']})" if b.get("paren") else "")
                    text = f"{sp}: {text}"
                zone_h = int(h * 0.165)
                b["layer"] = caption_layer(shaper, text, w, zone_h, b["kind"] == "dialogue",
                                           int(min(w, h) * 0.05))
        cap_y = int(h * 0.81)
        clouds_rng = random.Random(seed + 7)
        clouds = [(make_cloud(w, h, clouds_rng), clouds_rng.uniform(0.05, 0.14), clouds_rng.uniform(0.05, 0.3),
                   clouds_rng.uniform(0, 1)) for _ in range(3)]

        silent = tmp / "silent.mp4"
        ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent)], stdin=subprocess.PIPE)

        n_frames = int(round(total * FPS))
        bi = 0
        for f in range(n_frames):
            t = f / FPS
            while bi < len(beats) - 1 and t >= beats[bi + 1]["start"]:
                bi += 1
            b = beats[bi]
            local = t - b["start"]

            if b["kind"] in ("title", "location"):
                frame = b["card"].copy()
                fin = smooth((local - 0.1) / 0.5)
                fout = 1 - smooth((local - (b["dur"] - 0.4)) / 0.35)
                a = fin * fout
                if a > 0.01:
                    layer = b["layer"]
                    al = layer.getchannel("A").point(lambda v, k=a: int(v * k))
                    lay = layer.copy()
                    lay.putalpha(al)
                    frame.paste(lay, ((w - lay.width) // 2, (h - lay.height) // 2), lay)
            else:
                ep = episodes[beat_ep[bi]]
                frame = ep["bg"].copy()
                if ep["kind"] == "outdoor":
                    for sprite, speed, yfrac, ph in clouds:
                        x = int(((t * speed * 0.05 + ph) % 1.3 - 0.2) * w)
                        a = 90 if ep["night"] else 255
                        cl = sprite if a == 255 else sprite.copy()
                        if a != 255:
                            cl.putalpha(cl.getchannel("A").point(lambda v: int(v * 0.25)))
                        frame.paste(cl, (x, int(h * (0.05 + yfrac * 0.25))), cl)

                intro = smooth((t - episodes_start(beats, beat_ep, bi)) / 0.6)
                speaker = b.get("speaker") if b["kind"] == "dialogue" else None
                crowd = speaker in CROWD_NAMES
                env = envs.get(bi)
                amp = 0.0
                if speaker is not None:
                    idx = int((local - lead_in) * FPS)
                    if env is not None:
                        amp = env[idx] if 0 <= idx < len(env) else 0.0
                    elif 0 <= local - lead_in < b["dur"] - 0.6:
                        amp = 0.5 + 0.5 * math.sin(t * 13)
                speaker_c = next((c for c in cast if c["name"] == speaker), None)

                for c in cast:
                    leave_t = ep["leave"].get(c["name"])
                    dx = 0.0
                    if leave_t is not None and t > leave_t:
                        dx = smooth((t - leave_t) / 1.4) * (w * 0.7) * (-1 if c["cx"] < w / 2 else 1)
                        if abs(dx) > w * 0.69:
                            continue
                    talking = crowd or c is speaker_c
                    open_amt = amp if talking else 0.0
                    bounce = -open_amt * 6 if talking else 0.0
                    bob = math.sin(t * 2.0 + c["phase"]) * 1.5
                    blink = ((t + c["phase"]) % c["blink_period"]) < 0.14
                    if speaker_c is not None and c is not speaker_c:
                        look_dx = 3 if speaker_c["cx"] > c["cx"] else -3
                    else:
                        look_dx = 0
                    spr = c["sprite"].copy()
                    patch = face_patch(c, c["scale"], open_amt, blink, look_dx, open_amt * 3)
                    spr.alpha_composite(patch, (int(FACE_BOX[0] * c["scale"]), int(FACE_BOX[1] * c["scale"])))
                    rise = int((1 - intro) * h * 0.08)
                    if intro < 0.99:
                        spr.putalpha(spr.getchannel("A").point(lambda v, k=intro: int(v * k)))
                    x = int(c["cx"] + dx - spr.width / 2)
                    y = int(y_base - spr.height + bounce + bob + rise)
                    sh = c["shadow"]
                    frame.paste(sh, (int(c["cx"] + dx - sh.width / 2), y_base - sh.height // 3), sh)
                    frame.paste(spr, (x, y), spr)
                    lab = c["label_on"] if talking else c["label"]
                    if abs(dx) < w * 0.3:
                        frame.paste(lab, (int(c["cx"] - lab.width / 2), y_base + int(h * 0.012)), lab)

                cap = b["layer"]
                ca = smooth(local / 0.25)
                if ca < 0.99:
                    cap = cap.copy()
                    cap.putalpha(cap.getchannel("A").point(lambda v, k=ca: int(v * k)))
                frame.paste(cap, (0, cap_y), cap)

                # انتقال ناعم من بطاقة العنوان/المكان
                if bi > 0 and beats[bi - 1]["kind"] in ("title", "location") and local < 0.4:
                    frame = Image.blend(beats[bi - 1]["card"], frame, smooth(local / 0.4))

            d = ImageDraw.Draw(frame)
            bar_h = max(4, h // 150)
            d.rectangle((0, h - bar_h, int(w * t / total), h), fill=(255, 205, 90))
            ff.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            if f % (FPS * 5) == 0:
                log(f"  {f * 100 // n_frames}%")

        ff.stdin.close()  # type: ignore[union-attr]
        if ff.wait() != 0:
            raise SystemExit("فشل ffmpeg أثناء ترميز الفيديو.")

        audio = ttv.build_audio(beats, [b["audio"] for b in beats], [b["dur"] for b in beats], lead_in, tmp)
        if audio:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", audio,
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path)],
                           check=True)
        else:
            import shutil
            shutil.copy(silent, out_path)

    srt = out_path.with_suffix(".srt")
    with open(srt, "w", encoding="utf-8") as fh:
        for i, b in enumerate(beats):
            line = f"{b['speaker']}: {b['text']}" if b["kind"] == "dialogue" else b["text"]
            fh.write(f"{i + 1}\n{srt_time(b['start'] + lead_in)} --> {srt_time(b['start'] + b['dur'] - 0.2)}\n{line}\n\n")
    log(f"تم: {out_path} ({total:.1f}s, {w}x{h})")
    return out_path


def episodes_start(beats: list[dict], beat_ep: list[int], bi: int) -> float:
    """وقت أول مقطع (غير بطاقة) داخل نفس الحلقة."""
    ep = beat_ep[bi]
    for j, b in enumerate(beats):
        if beat_ep[j] == ep and b["kind"] not in ("title", "location"):
            return b["start"]
    return beats[bi]["start"]


def main() -> None:
    p = argparse.ArgumentParser(description="توليد فيديو من نص أو سيناريو")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-f", "--file")
    src.add_argument("-t", "--text")
    p.add_argument("-o", "--output", default="output/video.mp4")
    p.add_argument("--aspect", choices=list(ASPECTS), default="16:9")
    p.add_argument("--mode", choices=["auto", "screenplay", "simple"], default="auto")
    p.add_argument("--voice", default="auto", help="auto أو اسم صوت أو none")
    p.add_argument("--no-voice", action="store_true")
    p.add_argument("--no-narrator", action="store_true", help="لا تقرأ أوصاف الحركة")
    p.add_argument("--min-beat", type=float, default=2.6)
    p.add_argument("--max-chars", type=int, default=160)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    mode = args.mode
    if mode == "auto":
        mode = "screenplay" if looks_like_screenplay(raw or "") else "simple"
    log(f"الوضع: {mode}")
    if mode == "screenplay":
        generate_screenplay(args)
    else:
        ns = types.SimpleNamespace(file=args.file, text=args.text, output=args.output, aspect=args.aspect,
                                   voice=args.voice, no_voice=args.no_voice, rate="+0%",
                                   min_scene=3.0, max_chars=140, seed=args.seed)
        ttv.generate(ns)


if __name__ == "__main__":
    main()
