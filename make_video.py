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
import os
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

CROWD_NAMES = {"الجميع", "الكل", "كلهم", "الجالسون", "الحضور", "الجيران", "everyone", "all", "everybody"}
FEMALE_NAMES = {"سارة", "مريم", "فاطمة", "ليلى", "سلمى", "نور", "هدى", "رنا", "ريم", "ندى", "لينا",
                "هبة", "زينب", "عائشة", "خديجة", "سعاد", "دعاء", "آية", "ياسمين", "أماني", "رغد",
                "لمى", "جود", "ميس", "رولا", "سناء", "هالة", "منى", "ديما", "رهف", "شهد", "أم",
                "mary", "sara", "sarah", "anna", "emma", "lily", "laila", "mona", "nora"}
MALE_TA = {"حمزة", "معاوية", "أسامة", "طلحة", "عكرمة", "قتيبة", "عبيدة", "أبو"}
OLD_WORDS = ("أبو", "ابو", "الحاج", "العم", "الشيخ", "جد", "جدي", "حاج")

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


def build_cast(beats: list[dict], decl: dict, limit: int = 12) -> list[dict]:
    """يستخرج الشخصيات: المعلنة أولًا، ثم المتحدثون الأكثر كلامًا، ثم الصامتون المذكورون في المشاهد."""
    counts: dict[str, int] = {}
    for b in beats:
        if b["kind"] == "dialogue" and b["speaker"] not in CROWD_NAMES:
            counts[b["speaker"]] = counts.get(b["speaker"], 0) + 1
    speakers = sorted(counts, key=lambda n: -counts[n])
    known = list(dict.fromkeys(list(decl) + speakers))
    order: list[str] = list(decl)
    for n in speakers:
        if n not in order:
            order.append(n)

    def add(name: str):
        if name and name not in order:
            order.append(name)

    seen_location = False
    for b in beats:
        if b["kind"] == "location":
            seen_location = True
        if b["kind"] != "action" or not seen_location:
            continue  # السرد الافتتاحي لا يُستخرج منه ممثلون
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
    for idx, name in enumerate(order[:limit]):
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
    p.add_argument("--no-llm", action="store_true", help="لا تستخدم نموذج لغة لوصف مقاطع السرد")
    p.add_argument("--min-beat", type=float, default=2.6)
    p.add_argument("--max-chars", type=int, default=160)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--style", choices=["cinematic", "veo"], default="cinematic",
                   help="veo: مقاطع فيديو حقيقية بصوت (مدفوع)، cinematic: صور مولّدة بحركة كاميرا")
    p.add_argument("--max-clips", type=int, default=0, help="veo: أقصى عدد لقطات (0 = كل القصة)")
    p.add_argument("--dry-run", action="store_true", help="veo: اكتب خطة اللقطات فقط دون توليد أو صرف")
    p.add_argument("--veo-model", default=os.environ.get("VEO_MODEL", ""),
                   help="veo: اسم النموذج؛ إن تُرك فارغًا يُختار تلقائيًا من النماذج المتاحة لمفتاحك")
    p.add_argument("--veo-quality", choices=["fast", "standard", "lite"], default="fast",
                   help="veo: فئة النموذج عند الاختيار التلقائي (fast أرخص من standard)")
    p.add_argument("--resolution", default="720p", choices=["720p", "1080p"])
    p.add_argument("--refs", choices=["auto", "none"], default="auto",
                   help="veo: صور مرجعية للشخصيات (من مجلد characters/ أو تُولَّد تلقائيًا)")
    p.add_argument("--workers", type=int, default=2, help="veo: عدد اللقطات المتزامنة")
    p.add_argument("--narration", choices=["stills", "skip"], default="stills",
                   help="veo: السرد الافتتاحي كصور مع صوت راوٍ، أو تجاهله")
    p.add_argument("--burn-subs", action="store_true", help="veo: ثبّت الترجمة داخل الفيديو")
    p.add_argument("--look", choices=["realistic", "anime"], default="realistic",
                   help="شكل الصور في الوضع السينمائي")
    p.add_argument("--images", choices=["pollinations", "gemini", "placeholder"], default="pollinations",
                   help="مزود الصور (gemini يحتاج GEMINI_API_KEY)")
    args = p.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    import story_parser
    import shotlist
    if shotlist.looks_like_shotlist(raw or ""):
        raise SystemExit("هذا النص قائمة لقطات جاهزة (فيديو 001 / CHAR / CAM …) وليس قصة. "
                         "استعمل: python make_shots.py -f الملف -o results/اسم  ثم ولّد كل لقطة في Veo/Flow.")
    if story_parser.looks_like_prompt_pack(raw or "") and not os.environ.get("ALLOW_PROMPT_TEXT"):
        raise SystemExit(
            "هذا النص يبدو أوامر توليد جاهزة (Visual: / Audio: / Use the approved character...) وليس قصة.\n"
            "إدخاله هنا يعرض الأوامر نفسها ترجمةً ويقرؤها الراوي. أحد حلّين:\n"
            "  1) الصق القصة أو السيناريو نفسه (فقرات وحوار) وسيكتب المشروع الأوامر بنفسه (Make shot pack).\n"
            "  2) استخدم هذه الأوامر مباشرة في Google Flow ثم ارفع المقاطع إلى clips/ لتُدمج.")
    raw = story_parser.normalize(raw or "")
    args.file, args.text = None, raw
    mode = args.mode
    if mode == "auto":
        mode = "screenplay" if looks_like_screenplay(raw or "") else "simple"
    log(f"الوضع: {mode}")
    args.narrator = not args.no_narrator
    if mode == "screenplay" and args.style == "veo":
        import veo_story
        veo_story.generate(args)
    elif mode == "screenplay":
        import cinematic
        cinematic.generate(args)
    else:
        ns = types.SimpleNamespace(file=args.file, text=args.text, output=args.output, aspect=args.aspect,
                                   voice=args.voice, no_voice=args.no_voice, rate="+0%",
                                   min_scene=3.0, max_chars=140, seed=args.seed)
        ttv.generate(ns)


if __name__ == "__main__":
    main()
