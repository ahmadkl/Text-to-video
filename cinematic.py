#!/usr/bin/env python3
"""الوضع السينمائي: لقطات واقعية (أو أنمي) مولّدة بالذكاء الاصطناعي + حركة كاميرا + أصوات + ترجمة.

لكل مقطع من السيناريو تُولَّد صورة:
  - حوار  ← لقطة قريبة للمتحدث (وصفه ثابت في كل المشاهد ليبقى شكله متقاربًا).
  - وصف حركة ← لقطة عامة للمكان والشخصيات المذكورة.
ثم تُحرَّك الصورة (زوم/تحريك بطيء)، وتُدمج مع صوت كل شخصية والترجمة.

مزودات الصور (--images):
  pollinations  مجاني بدون مفتاح (الافتراضي)، يحتاج إنترنت.
  gemini        يحتاج المتغير GEMINI_API_KEY (جودة وثبات أعلى).
  placeholder   صور بديلة للاختبار فقط.

إضافات اختيارية داخل السيناريو:
  الشخصية: علي = a Syrian man in his 40s, short black hair, grey shirt
  خارجي – جانب منزل علي – نهار | a quiet village lane with old stone houses
  الأسلوب: cinematic still from a Syrian TV drama, photorealistic
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import make_video as mv
import text_to_video as ttv
from text_to_video import (ASPECTS, FPS, PALETTES, TextShaper, find_font, is_arabic, log,
                           probe_duration, render_text_layer, smooth, srt_time)

LOOKS = {
    "realistic": ("cinematic still frame from a Syrian TV drama, photorealistic, natural lighting, "
                  "35mm film look, shallow depth of field, realistic faces and skin texture, high detail"),
    "anime": ("high quality anime movie still, detailed cel shading, expressive faces, "
              "soft cinematic lighting, vibrant colors, Studio Ghibli inspired background art"),
    "cartoon": "3D animated movie still, Pixar style, expressive faces, soft lighting",
}
NEGATIVE = "no text, no subtitles, no watermark, no logo, no captions, no frames"

LOC_WORDS = [
    ("منزل", "a house"), ("بيت", "a house"), ("دار", "a house"), ("شارع", "a street"),
    ("زقاق", "a narrow alley"), ("قرية", "a village"), ("حقل", "a field"), ("أرض", "an open plot of land"),
    ("ارض", "an open plot of land"), ("غرفة", "a room"), ("مطبخ", "a kitchen"), ("مسجد", "near a mosque"),
    ("سوق", "a market"), ("مدرسة", "a school"), ("باب", "next to a wooden gate"), ("سطح", "a rooftop"),
    ("محل", "a small shop"), ("مكتب", "an office"), ("قبو", "a basement"), ("مزرعة", "a farm"),
    ("ساحة", "a courtyard"), ("حديقة", "a garden"), ("مستشفى", "a hospital"), ("سيارة", "a car"),
]
ACTION_WORDS = [
    ("جالس", "sitting"), ("يجلس", "sitting down"), ("يقف", "standing"), ("ينهض", "getting up"),
    ("ينصرف", "walking away"), ("يخرج", "walking out"), ("يراقب", "watching closely with narrowed eyes"),
    ("يدخل", "walking in"), ("يبتسم", "smiling"), ("يصرخ", "shouting"), ("يبكي", "crying"),
    ("يتحدث", "talking"), ("يشير", "pointing"), ("ينظر", "looking"), ("عينين ضيقتين", "narrowed eyes"),
]
NARR_MOODS = [
    "{who}, standing alone and looking thoughtfully at an empty plot of land where a house will be built.",
    "{who}, sitting at a simple table at home, worried and thinking.",
    "{who}, walking along a village road at golden hour, hopeful.",
    "{who}, talking quietly with a few neighbours in the street.",
    "{who}, gazing at a half-built concrete house under construction.",
]
SHOTS = ["medium close-up", "close-up portrait", "medium shot", "over-the-shoulder close-up"]
SHIRTS = ["grey", "navy blue", "olive green", "brown", "white", "dark red", "beige"]
HIJABS = ["beige", "dark grey", "navy blue", "cream", "dusty pink", "olive green"]
HAIRS = ["short black hair", "short dark brown hair", "thinning dark hair", "wavy black hair"]
FACIAL = ["light stubble", "a trimmed mustache", "a short dark beard", "light stubble"]


class ImageError(Exception):
    pass


# ----------------------------------------------------------------------------
# مزودات الصور
# ----------------------------------------------------------------------------
def http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 text-to-video",
                                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


_poll_lock = threading.Lock()
_poll_last = [0.0]


def gen_pollinations(prompt: str, w: int, h: int, seed: int) -> bytes:
    q = urllib.parse.quote(prompt[:1500], safe="")
    model = os.environ.get("POLLINATIONS_MODEL", "flux")
    url = (f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}&seed={seed}"
           f"&model={model}&nologo=true")
    headers = {}
    token = os.environ.get("POLLINATIONS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # الطبقة المجانية بلا مفتاح تقبل طلبًا واحدًا كل ~15 ثانية: نباعد الطلبات بدل ضربها بسرعة
    gap = 4.0 if token else 16.5
    with _poll_lock:
        wait = _poll_last[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _poll_last[0] = time.time()
    data, ctype = http(url, headers=headers)
    if not ctype.startswith("image"):
        raise ImageError(f"رد غير متوقع ({ctype}): {data[:150]!r}")
    return data


def _find_image_b64(obj):
    """يبحث في الرد عن أول كتلة صورة (type=image) ويعيد بياناتها base64."""
    if isinstance(obj, dict):
        if obj.get("type") == "image" and isinstance(obj.get("data"), str):
            return obj["data"]
        inline = obj.get("inlineData") or obj.get("inline_data")
        if isinstance(inline, dict) and isinstance(inline.get("data"), str):
            return inline["data"]
        for v in obj.values():
            r = _find_image_b64(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_image_b64(v)
            if r:
                return r
    return None


def gen_gemini(prompt: str, w: int, h: int, seed: int) -> bytes:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ImageError("المتغير GEMINI_API_KEY غير موجود")
    base = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    aspect = "16:9" if w > h else ("9:16" if h > w else "1:1")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1")
    errors = []
    attempts = [
        (f"{base}/interactions", {"model": model, "input": [{"type": "text", "text": prompt}],
                                  "response_format": {"type": "image", "mime_type": "image/jpeg",
                                                      "aspect_ratio": aspect, "image_size": "1K"}}),
        (f"{base}/models/gemini-2.5-flash-image:generateContent",
         {"contents": [{"parts": [{"text": f"{prompt}. Aspect ratio {aspect}."}]}],
          "generationConfig": {"responseModalities": ["IMAGE"]}}),
    ]
    for url, body in attempts:
        try:
            data, _ = http(url, json.dumps(body).encode(), headers)
            b64 = _find_image_b64(json.loads(data))
            if b64:
                return base64.b64decode(b64)
            errors.append(f"لا صورة في الرد من {url.rsplit('/', 1)[-1]}")
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP {exc.code} من {url.rsplit('/', 1)[-1]}: {exc.read()[:200]!r}")
            if exc.code in (401, 403, 429):
                raise
    raise ImageError("؛ ".join(errors))


def gen_placeholder(prompt: str, w: int, h: int, seed: int) -> bytes:
    rng = random.Random(seed)
    top = tuple(rng.randint(20, 90) for _ in range(3))
    bot = tuple(rng.randint(60, 150) for _ in range(3))
    img = mv.vgrad(w, h, top, bot)
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x, y, r = rng.randint(0, w), rng.randint(0, h), rng.randint(h // 8, h // 3)
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=3)
    d.text((20, 20), prompt[:90], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


PROVIDERS = {"pollinations": gen_pollinations, "gemini": gen_gemini, "placeholder": gen_placeholder}


def fetch_image(prompt: str, w: int, h: int, seed: int, provider: str, cache: Path,
                max_attempts: int = 6) -> Image.Image:
    key = hashlib.sha1(f"{provider}|{w}x{h}|{seed}|{prompt}".encode()).hexdigest()[:20]
    path = cache / f"{key}.jpg"
    if path.exists():
        return Image.open(path).convert("RGB")
    last = None
    for attempt in range(max_attempts):
        try:
            data = PROVIDERS[provider](prompt, w, h, seed)
            img = Image.open(io.BytesIO(data)).convert("RGB")
            cache.mkdir(parents=True, exist_ok=True)
            img.save(path, "JPEG", quality=92)
            return img
        except ImageError as exc:  # رفض مباشر من الخدمة: محاولة إضافية واحدة فقط
            last = str(exc)
            wait = 10
            if attempt >= 1:
                break
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.reason}"
            wait = 20 * (attempt + 1) if exc.code in (402, 429, 502, 503, 504) else 5
            if exc.code in (401, 403) and attempt >= 1:
                break
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            wait = 5 * (attempt + 1)
        log(f"  تعذّر توليد الصورة ({last}) — إعادة المحاولة بعد {wait}ث")
        time.sleep(wait)
    raise ImageError(last or "فشل غير معروف")


# ----------------------------------------------------------------------------
# تجهيز النص والأوصاف
# ----------------------------------------------------------------------------
def preprocess(raw: str):
    descs: dict[str, str] = {}
    style = None
    locdescs: dict[str, str] = {}
    out = []
    for line in raw.replace("\r", "").split("\n"):
        m = re.match(r"^\s*(?:الشخصية|character)\s*[:：]\s*(.+?)\s*=\s*(.+)$", line, re.I)
        if m:
            descs[m.group(1).strip()] = m.group(2).strip()
            continue
        m = re.match(r"^\s*(?:الأسلوب|الاسلوب|style)\s*[:：]\s*(.+)$", line, re.I)
        if m:
            style = m.group(1).strip()
            continue
        if mv.SLUG_RE.match(line.strip()) and "|" in line:
            text, desc = line.split("|", 1)
            locdescs[ttv.clean(text)] = desc.strip()
            line = text
        out.append(line)
    if descs:
        out.insert(0, "الشخصيات: " + "، ".join(descs))
    return "\n".join(out), descs, style, locdescs


def describe_location(text: str) -> str:
    parts = [p.strip() for p in re.split(r"[–—-]", text) if p.strip()]
    outdoor = not re.search(r"(داخلي|داخلى|INT\b)", text, re.I)
    words = [en for ar, en in LOC_WORDS if ar in text]
    base = "a Syrian village with old stone houses" if outdoor else "a modest Arab family home interior"
    if words:
        base = f"{words[0]} in a Syrian village" if outdoor else f"inside {words[0]}, modest Arab home"
    time_of_day = "at night, lit by street lamps" if re.search(r"(ليل|night)", text, re.I) else (
        "in the evening, golden light" if re.search(r"(مساء|غروب|evening)", text, re.I) else (
            "in the morning light" if re.search(r"(صباح|morning)", text, re.I) else "in soft daylight"))
    return f"{'exterior' if outdoor else 'interior'}, {base}, {time_of_day}" + (
        "" if len(parts) < 2 else "")


def default_desc(c: dict) -> str:
    h = int(__import__("hashlib").sha1(c["name"].encode()).hexdigest()[:8], 16)
    if c["child"]:
        return ("a 9 year old Syrian boy with short dark hair, big curious eyes" if c["gender"] == "m"
                else "a 9 year old Syrian girl with long dark braided hair, big curious eyes")
    if c["gender"] == "f":
        age = "in her 60s, gentle wrinkles" if c["old"] else f"in her {(30 + h % 10) // 10 * 10}s"
        return (f"a Syrian woman {age} wearing a {HIJABS[h % len(HIJABS)]} hijab and modest long clothing, "
                "expressive tired eyes")
    if c["old"]:
        return ("an elderly Syrian man in his 70s wearing a white and black checkered keffiyeh with a black agal, "
                "grey mustache, deeply wrinkled weathered face, dark jacket over a beige shirt")
    age = 28 + h % 22
    return (f"a Syrian man in his {age // 10 * 10}s, {HAIRS[h % len(HAIRS)]}, {FACIAL[(h >> 3) % len(FACIAL)]}, "
            f"olive skin, wearing a {SHIRTS[(h >> 5) % len(SHIRTS)]} shirt")


def emotion_of(b: dict) -> str:
    p = b.get("paren", "")
    if re.search(r"(منخفض|همس|خافت)", p):
        return "serious, quiet, intense expression"
    if re.search(r"(غضب|غاضب|صارخ|يصرخ|بحدة)", p):
        return "angry, tense expression"
    if re.search(r"(حزن|حزين|بكاء)", p):
        return "sad, teary eyes"
    if "؟" in b["text"] or "?" in b["text"]:
        return "questioning, serious expression"
    if "!" in b["text"]:
        return "emphatic, determined expression"
    return "calm, serious expression"


def action_hint(text: str) -> str:
    return ", ".join(dict.fromkeys(en for ar, en in ACTION_WORDS if ar in text)) or "standing together"


# ----------------------------------------------------------------------------
# الكاميرا والرسم
# ----------------------------------------------------------------------------
def prepare_base(img: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h)).convert("RGB")
    gh = int(h * 0.36)
    alpha = Image.linear_gradient("L").resize((w, gh)).point(lambda v: int(v * 0.65))
    img.paste(Image.new("RGB", (w, gh), (0, 0, 0)), (0, h - gh), alpha)
    return img


def camera(base: Image.Image, w: int, h: int, p: float, kb: dict, pulse: float = 0.0) -> Image.Image:
    z = kb["z0"] + (kb["z1"] - kb["z0"]) * p + pulse
    cw, ch = w / z, h / z
    cx = kb["x0"] + (kb["x1"] - kb["x0"]) * p
    cy = kb["y0"] + (kb["y1"] - kb["y0"]) * p
    left = min(max(cx * w - cw / 2, 0), w - cw)
    top = min(max(cy * h - ch / 2, 0), h - ch)
    return base.resize((w, h), Image.BICUBIC, box=(left, top, left + cw, top + ch))


def _json_array(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n|```$", "", text).strip()
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b < 0:
        return None
    try:
        arr = json.loads(text[a:b + 1])
        return [str(x) for x in arr] if isinstance(arr, list) else None
    except ValueError:
        return None


def llm_describe(passages: list[str]) -> list[str | None]:
    """وصف إنجليزي لكل مقطع عربي (للصور). Gemini إن وُجد المفتاح، وإلا Pollinations المجاني. يفشل بهدوء."""
    out: list[str | None] = [None] * len(passages)
    ask = ("You write image prompts for a realistic Syrian village TV drama storyboard. For each numbered Arabic "
           "passage, write ONE English sentence (max 40 words) saying what the camera shows: setting, people "
           "(generic: 'a young Syrian man', never names), action, mood, light. Return ONLY a JSON array of "
           "strings, same length and order.\n\n")
    for start in range(0, len(passages), 15):
        batch = passages[start:start + 15]
        prompt = ask + "\n".join(f"{i + 1}. {t[:400]}" for i, t in enumerate(batch))
        arr = None
        key = os.environ.get("GEMINI_API_KEY")
        try:
            if key:
                model = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
                data, _ = http(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                               json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
                               {"Content-Type": "application/json", "x-goog-api-key": key}, timeout=90)
                arr = _json_array(json.loads(data)["candidates"][0]["content"]["parts"][0]["text"])
            else:
                data, _ = http("https://text.pollinations.ai/openai",
                               json.dumps({"model": "openai", "messages": [{"role": "user", "content": prompt}]}).encode(),
                               {"Content-Type": "application/json"}, timeout=90)
                arr = _json_array(json.loads(data)["choices"][0]["message"]["content"])
        except Exception as exc:  # noqa: BLE001
            log(f"  تعذّر وصف المقاطع بنموذج اللغة ({type(exc).__name__}: {str(exc)[:80]}) — أستخدم الأوصاف الافتراضية")
            return out
        if not arr or len(arr) != len(batch):
            log("  رد نموذج اللغة غير صالح — أستخدم الأوصاف الافتراضية")
            return out
        out[start:start + len(batch)] = arr
    return out


def ken_burns(i: int) -> dict:
    if i % 2 == 0:
        return {"z0": 1.0, "z1": 1.13, "x0": 0.5, "x1": 0.47, "y0": 0.5, "y1": 0.47}
    return {"z0": 1.13, "z1": 1.0, "x0": 0.53, "x1": 0.5, "y0": 0.47, "y1": 0.5}


# ----------------------------------------------------------------------------
# التوليد
# ----------------------------------------------------------------------------
def generate(args) -> Path:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    raw, descs, style_override, locdescs = preprocess(raw or "")
    beats, decl = mv.parse_screenplay(raw, args.max_chars)
    if not beats:
        raise SystemExit("النص فارغ.")
    cast = mv.build_cast(beats, decl)
    lang = "ar" if is_arabic(raw) else "en"
    style = style_override or LOOKS[args.look]
    cdesc = {c["name"]: descs.get(c["name"]) or default_desc(c) for c in cast}
    cseed = {c["name"]: int(hashlib.sha1(c["name"].encode()).hexdigest()[:6], 16) for c in cast}
    log(f"المقاطع: {len(beats)} | الشخصيات: {', '.join(c['name'] for c in cast) or '-'} | المزود: {args.images}")

    w, h = ASPECTS[args.aspect]
    gen_w, gen_h = (1280, 720) if w > h else ((720, 1280) if h > w else (1024, 1024))
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = out_path.parent / "cache"
    seed0 = args.seed if args.seed is not None else int(hashlib.sha1(raw.encode()).hexdigest()[:6], 16)

    mv.assign_voices(beats, cast, lang, args.narrator, args.voice)
    use_voice = args.voice != "none" and not args.no_voice

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        voices: list[str | None] = [None] * len(beats)
        if use_voice:
            log("توليد الأصوات...")
            voices = mv.synthesize(beats, tmp, mv.VOICES[lang]["fallback"])

        lead_in = 0.4
        for b, v in zip(beats, voices):
            b["audio"] = v
            if v:
                b["dur"] = max(args.min_beat, probe_duration(v) + lead_in + 0.5)
            elif b["kind"] in ("title", "location"):
                b["dur"] = 3.0
            elif b["kind"] == "dialogue":
                b["dur"] = max(args.min_beat, len(b["text"]) / 13 + 1.4)
            else:
                b["dur"] = max(args.min_beat, 3.2)
        # نبضة كاميرا تتبع شدة الصوت في الحوار (تعوّض غياب حركة الفم)
        envs: dict[int, list[float]] = {}
        for i, b in enumerate(beats):
            if b["audio"] and b["kind"] == "dialogue":
                try:
                    envs[i] = mv.amp_envelope(b["audio"], tmp, i)
                except Exception:  # noqa: BLE001
                    pass
        t0 = 0.0
        for b in beats:
            b["start"] = t0
            t0 += b["dur"]
        total = t0
        log(f"مدة الفيديو: {total:.1f} ثانية")

        # ---- وصف كل لقطة ----
        loc_desc = describe_location("") if not any(b["kind"] == "location" for b in beats) else ""
        loc_desc = loc_desc or "exterior, a Syrian village with old stone houses, in soft daylight"
        shot_count: dict[str, int] = {}
        seen_location, narr_i = False, 0
        # وصف إنجليزي لكل خمسة مقاطع سرد متتالية ولكل وصف حركة (اختياري، يفشل بهدوء)
        llm_desc: dict[tuple, str] = {}
        if not getattr(args, "no_llm", False):
            items, keys_ = [], []
            seen_loc2, n2 = False, 0
            for i, b in enumerate(beats):
                if b["kind"] == "location":
                    seen_loc2 = True
                elif b["kind"] == "action" and not seen_loc2:
                    if n2 % 5 == 0:
                        group_text = " ".join(x["text"] for x in beats[i:i + 5]
                                              if x["kind"] == "action")
                        items.append(group_text)
                        keys_.append(i)
                    n2 += 1
            if items:
                log(f"وصف {len(items)} مقطع سرد بنموذج اللغة...")
                res = llm_describe(items)
                cur_desc, n3 = None, 0
                narr_idx = [i for i, b in enumerate(beats) if b["kind"] == "action"
                            and i < next((j for j, x in enumerate(beats) if x["kind"] == "location"), len(beats))]
                for pos, i in enumerate(narr_idx):
                    if pos % 5 == 0:
                        cur_desc = res[pos // 5]
                    if cur_desc:
                        llm_desc[(i, "n")] = cur_desc
                if llm_desc:
                    log(f"  وُصف {len(items)} مقطع")
        for i, b in enumerate(beats):
            if b["kind"] == "location":
                loc_desc = locdescs.get(b["text"]) or describe_location(b["text"])
                seen_location = True
                continue
            if b["kind"] == "title":
                continue
            names = [c["name"] for c in cast]
            if b["kind"] == "action" and not seen_location:
                g = narr_i // 5
                narr_i += 1
                lead = cdesc[names[0]] if names else "a Syrian man in his 30s"
                if (i, "n") in llm_desc:
                    b["prompt"] = f"{style}. {llm_desc[(i, 'n')]} {NEGATIVE}"
                    b["seed"] = seed0 + (narr_i - 1) * 31
                    continue
                b["prompt"] = (f"{style}. {NARR_MOODS[g % len(NARR_MOODS)].format(who=lead)} "
                               f"Setting: {loc_desc}. {NEGATIVE}")
                b["seed"] = seed0 + g * 31
                continue
            mentioned = [n for n in names if mv.find_name(b["text"], n)]
            if b["kind"] == "dialogue" and b["speaker"] in cdesc:
                sp = b["speaker"]
                k = shot_count.get(sp, 0)
                shot_count[sp] = k + 1
                prompt = (f"{style}. {SHOTS[k % len(SHOTS)]} of {cdesc[sp]}, {emotion_of(b)}, speaking. "
                          f"Background: {loc_desc}. {NEGATIVE}")
                b["seed"] = cseed[sp]
            else:
                group = mentioned or names
                group = group[:4]
                who = "; ".join(cdesc[n] for n in group) or "a few villagers"
                hint = action_hint(b["text"]) if b["kind"] == "action" else "reacting together"
                prompt = (f"{style}. Wide shot of {len(group) or 'several'} people: {who}. "
                          f"They are {hint}. Setting: {loc_desc}. {NEGATIVE}")
                b["seed"] = seed0 + i * 31
            b["prompt"] = prompt

        # ---- توليد الصور (بدون تكرار، وبالتوازي) ----
        unique: dict[tuple, Image.Image | None] = {}
        for b in beats:
            if "prompt" in b:
                unique.setdefault((b["prompt"], b["seed"]), None)
        keys = list(unique)
        log(f"عدد الصور المطلوب توليدها: {len(keys)}")
        errors: list[str] = []
        failed_keys: set = set()

        def one(k):
            try:
                return k, fetch_image(k[0], gen_w, gen_h, k[1], args.images, cache), None
            except ImageError as exc:
                return k, None, str(exc)

        workers = 1 if args.images == "pollinations" else 3
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, (k, img, err) in enumerate(pool.map(one, keys), 1):
                if err:
                    errors.append(err)
                    failed_keys.add(k)
                    log(f"  فشلت الصورة {n}/{len(keys)}: {err}")
                    unique[k] = None
                else:
                    log(f"  الصورة {n}/{len(keys)} جاهزة")
                    unique[k] = prepare_base(img, w, h)
        if keys and len(errors) == len(keys):
            raise SystemExit(f"فشل توليد كل الصور. آخر خطأ: {errors[-1]}\n"
                             "جرّب --images gemini مع GEMINI_API_KEY، أو أعد التشغيل لاحقًا.")
        if errors:
            log(f"تحذير: فشلت {len(errors)} صورة من {len(keys)}؛ تُستبدل بأقرب لقطة ناجحة.")
        with_prompt = [i for i, b in enumerate(beats) if "prompt" in b]
        for i in with_prompt:
            b = beats[i]
            k = (b["prompt"], b["seed"])
            if unique.get(k) is not None:
                b["base"] = unique[k]
        for i in with_prompt:
            b = beats[i]
            if "base" in b:
                continue
            same = [j for j in with_prompt if "base" in beats[j] and beats[j].get("speaker") == b.get("speaker")
                    and b.get("speaker")]
            near = sorted((j for j in with_prompt if "base" in beats[j]), key=lambda j: abs(j - i))
            pick = (sorted(same, key=lambda j: abs(j - i)) or near or [None])[0]
            b["base"] = beats[pick]["base"] if pick is not None else prepare_base(
                gen_placeholder_image(gen_w, gen_h, b["seed"]), w, h)

        shaper = TextShaper(find_font())
        zone_h = int(h * 0.17)
        cap_y = h - zone_h - int(h * 0.02)
        for i, b in enumerate(beats):
            if b["kind"] in ("title", "location"):
                pal = PALETTES[int(hashlib.sha1(b["text"].encode()).hexdigest()[:4], 16) % len(PALETTES)]
                b["card"] = mv.vgrad(w, h, pal[0], pal[1])
                b["layer"] = render_text_layer(shaper, b["text"], (w, h), b["kind"] == "title", pal[2])
            else:
                text = b["text"]
                if b["kind"] == "dialogue":
                    text = f"{b['speaker']}: {text}"
                b["layer"] = mv.caption_layer(shaper, text, w, zone_h, b["kind"] == "dialogue",
                                              int(min(w, h) * 0.05))
                b["kb"] = ken_burns(i)

        def render(i: int, local: float) -> Image.Image:
            b = beats[i]
            if b["kind"] in ("title", "location"):
                frame = b["card"].copy()
                a = smooth((local - 0.1) / 0.5) * (1 - smooth((local - (b["dur"] - 0.4)) / 0.35))
                if a > 0.01:
                    lay = b["layer"].copy()
                    lay.putalpha(lay.getchannel("A").point(lambda v, k=a: int(v * k)))
                    frame.paste(lay, ((w - lay.width) // 2, (h - lay.height) // 2), lay)
                return frame
            pulse = 0.0
            env = envs.get(i)
            if env is not None:
                k = int((local - lead_in) * FPS)
                if 0 <= k < len(env):
                    pulse = 0.012 * env[k]
            frame = camera(b["base"], w, h, min(1.0, local / b["dur"]), b["kb"], pulse)
            ca = smooth((local - 0.1) / 0.3)
            if ca > 0.01:
                cap = b["layer"]
                if ca < 0.99:
                    cap = cap.copy()
                    cap.putalpha(cap.getchannel("A").point(lambda v, k=ca: int(v * k)))
                frame.paste(cap, (0, cap_y), cap)
            return frame

        silent = tmp / "silent.mp4"
        ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent)], stdin=subprocess.PIPE)
        n_frames = int(round(total * FPS))
        bi = 0
        xf = 0.45
        for f in range(n_frames):
            t = f / FPS
            while bi < len(beats) - 1 and t >= beats[bi + 1]["start"]:
                bi += 1
            local = t - beats[bi]["start"]
            frame = render(bi, local)
            if bi > 0 and local < xf:
                frame = Image.blend(render(bi - 1, beats[bi - 1]["dur"]), frame, smooth(local / xf))
            if bi == len(beats) - 1 and local > beats[bi]["dur"] - 0.5:
                frame = Image.blend(frame, Image.new("RGB", (w, h)), smooth((local - (beats[bi]["dur"] - 0.5)) / 0.5))
            ff.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            if f % (FPS * 5) == 0:
                log(f"  ترميز {f * 100 // n_frames}%")
        ff.stdin.close()  # type: ignore[union-attr]
        if ff.wait() != 0:
            raise SystemExit("فشل ffmpeg أثناء ترميز الفيديو.")

        audio = ttv.build_audio(beats, [b["audio"] for b in beats], [b["dur"] for b in beats], lead_in, tmp)
        if audio:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", audio,
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path)], check=True)
        else:
            import shutil
            shutil.copy(silent, out_path)

    with open(out_path.with_suffix(".srt"), "w", encoding="utf-8") as fh:
        for i, b in enumerate(beats):
            line = f"{b['speaker']}: {b['text']}" if b["kind"] == "dialogue" else b["text"]
            fh.write(f"{i + 1}\n{srt_time(b['start'] + lead_in)} --> {srt_time(b['start'] + b['dur'] - 0.2)}\n{line}\n\n")
    with open(out_path.with_name(out_path.stem + "_prompts.txt"), "w", encoding="utf-8") as fh:
        for i, b in enumerate(beats):
            if "prompt" in b:
                fh.write(f"[{i + 1}] seed={b['seed']}\n{b['prompt']}\n\n")
    log(f"تم: {out_path} ({total:.1f}s, {w}x{h})")
    return out_path


def gen_placeholder_image(w: int, h: int, seed: int) -> Image.Image:
    return Image.open(io.BytesIO(gen_placeholder("", w, h, seed))).convert("RGB")
