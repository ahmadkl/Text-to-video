#!/usr/bin/env python3
"""حزمة لقطات جاهزة لـ Google Flow / Veo من نص قصة أو سيناريو.

يقرأ النص (حتى لو كان فقرات طويلة متصلة)، ويقسمه إلى لقطات لا تتجاوز ~8 ثوانٍ، ويكتب لكل لقطة
أمرًا جاهزًا للنسخ فيه: المكان، وصف الشخصيات (ثابت في كل اللقطات)، التصوير، والحوار بصيغة تفهمها نماذج الفيديو.

    python make_shots.py -f story.txt -o shots --aspect 9:16

المخرجات: shots/shots.md (للقراءة والنسخ) و shots/shots.json (يستخدمه assemble.py للترجمة والترتيب).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cinematic as cn
import make_video as mv
import story_parser
import text_to_video as ttv

SECS_CAP = 7.0          # أقصى زمن كلام في اللقطة (الباقي حركة وصمت)
WORDS_PER_SEC = 2.3
MAX_LINES = 3
CAMERAS = ["medium two-shot", "over-the-shoulder shot", "medium close-up", "slightly low-angle medium shot"]

NAMES_EN = {
    "علي": "Ali", "عبد الله": "Abdullah", "عامر": "Amer", "خالد": "Khaled", "محمد": "Mohammad",
    "عمر": "Omar", "احمد": "Ahmad", "أحمد": "Ahmad", "سعيد": "Saeed", "نور": "Nour", "هديل": "Hadeel",
    "عبد الرحمن": "Abdulrahman", "ماسة": "Masa", "عمار": "Ammar", "يوسف": "Yousef", "حسن": "Hassan",
    "حسين": "Hussein", "مريم": "Maryam", "سارة": "Sara", "ليلى": "Layla", "فاطمة": "Fatima",
    "ابو": "Abu", "أبو": "Abu", "ام": "Umm", "أم": "Umm",
}
ROLE_EN = {"زوجته": "his wife", "زوجها": "her husband", "معلم العمار": "the construction foreman",
           "صاحب الارض": "the landowner", "الجالسون": "the men sitting together", "الجميع": "everyone"}


def name_en(name: str) -> str:
    if name in ROLE_EN:
        return ROLE_EN[name]
    if name in NAMES_EN:
        return NAMES_EN[name]
    words = []
    for w in name.split():
        if w in NAMES_EN:
            words.append(NAMES_EN[w])
        elif w == "زوجة":
            words.append("wife of")
        else:
            return f"{name}"
    return " ".join(words) or name


def voice_desc(c: dict) -> str:
    if c["child"]:
        return "a child's voice"
    if c["gender"] == "f":
        return "an older woman's voice" if c["old"] else "a calm adult woman's voice"
    return "a deep, slightly raspy elderly man's voice" if c["old"] else "an adult man's voice"


def speech_secs(text: str) -> float:
    return len(text.split()) / WORDS_PER_SEC + 0.4


def split_text(text: str) -> list[str]:
    """يقسم كلامًا طويلًا إلى أجزاء تناسب لقطة واحدة."""
    if speech_secs(text) <= SECS_CAP:
        return [text]
    limit = int((SECS_CAP - 0.4) * WORDS_PER_SEC)
    pieces = [p.strip() for p in re.split(r"(?<=[.!؟?،,])\s+", text) if p.strip()]
    parts, cur = [], []
    for piece in pieces:
        words = piece.split()
        while len(words) > limit:
            if cur:
                parts.append(" ".join(cur))
                cur = []
            parts.append(" ".join(words[:limit]))
            words = words[limit:]
        if len(cur) + len(words) > limit:
            parts.append(" ".join(cur))
            cur = []
        cur += words
    if cur:
        parts.append(" ".join(cur))
    return parts


AGE_WORDS = {"العشرين": 20, "الثلاثين": 30, "الاربعين": 40, "الأربعين": 40, "الخمسين": 50, "الستين": 60,
             "السبعين": 70}
FEMALE_ROLE = re.compile(r"^(زوجته|زوجة|ام |أم |امه|أمه|اخته|أخته|بنت|ابنته)")
GROUP_NAMES = set(mv.CROWD_NAMES) | {"الجالسون", "الجيران", "الحضور"}
HEADWEAR = ["a white and black checkered keffiyeh with a black agal", "a white knitted skullcap",
            "no headwear", "a red and white checkered keffiyeh"]
CLOTHES = ["a dark jacket over a beige shirt", "a grey traditional shirt", "a brown wool vest over a white shirt",
           "a plain navy shirt", "a worn olive jacket"]


def intro_info(beats: list[dict], names: list[str]) -> dict[str, dict]:
    """يلتقط أوصاف الشخصيات من سطور مثل: 'ابو محمد رجل في الخمسين من عمره ملتحي ...'."""
    info: dict[str, dict] = {}
    for b in beats:
        if b["kind"] != "action":
            continue
        for n in sorted(names, key=len, reverse=True):
            if b["text"].startswith(n + " ") and re.match(r"^(رجل|شاب|امرأة|فتاة|عجوز|شيخ|ابن خال|الجار)",
                                                          b["text"][len(n) + 1:]):
                t = b["text"]
                d = info.setdefault(n, {})
                for w, a in AGE_WORDS.items():
                    if w in t:
                        d["age"] = a
                d["beard"] = "ملتحي" in t
                d["long_hair"] = "شعر طويل" in t
                d["blond"] = "الاشقر" in t or "أشقر" in t
                d["teacher"] = "مدرس" in t
    return info


def male_desc(name: str, old: bool, info: dict) -> str:
    import hashlib
    h = int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)
    age = info.get("age") or (60 if old else 35 + (h % 3) * 5)
    parts = [f"a Syrian man in his {age}s"]
    if info.get("blond"):
        parts.append("light blond hair, fair skin")
    elif info.get("long_hair"):
        parts.append("long dark hair tied back")
    elif age >= 60:
        parts.append("grey hair, deeply wrinkled weathered face")
    else:
        parts.append(cn.HAIRS[h % len(cn.HAIRS)])
    if info.get("beard"):
        parts.append("a full dark beard")
    elif age >= 50:
        parts.append("a grey mustache")
    else:
        parts.append(cn.FACIAL[(h >> 3) % len(cn.FACIAL)])
    if age >= 50 or old:
        parts.append(f"wearing {HEADWEAR[h % len(HEADWEAR)]} and {CLOTHES[(h >> 4) % len(CLOTHES)]}")
    else:
        parts.append(f"wearing a {cn.SHIRTS[(h >> 5) % len(cn.SHIRTS)]} shirt")
    return ", ".join(parts)


def build_cast_all(beats: list[dict], decl: dict, descs: dict) -> dict[str, dict]:
    cast = {c["name"]: c for c in mv.build_cast(beats, decl) if c["name"] not in GROUP_NAMES}
    idx = len(cast)
    for b in beats:
        if b["kind"] == "dialogue" and b["speaker"] not in cast and b["speaker"] not in GROUP_NAMES:
            name = b["speaker"]
            old = bool(re.match(r"^(ا|أ)بو", name)) or any(name.startswith(w) for w in mv.OLD_WORDS)
            cast[name] = mv.make_look(name, mv.guess_gender(name), old, False, idx)
            idx += 1
    info = intro_info(beats, list(cast))
    for name, c in cast.items():
        if FEMALE_ROLE.match(name):
            c["gender"] = "f"
        abu = bool(re.match(r"^(ا|أ)بو", name))
        i = info.get(name, {})
        if c["gender"] == "m" and not c["child"] and (abu or i):
            c["desc"] = descs.get(name) or male_desc(name, abu and not i.get("age"), i)
            c["old"] = (i.get("age") or 60) >= 60
        else:
            c["desc"] = descs.get(name) or cn.default_desc(c)
        c["en"] = name_en(name)
        c["voice_desc"] = voice_desc(c)
    return cast


def plan(beats: list[dict], cast: dict[str, dict], locdescs: dict, style: str, aspect: str):
    narration: list[str] = []
    scenes: list[dict] = []
    cur_scene = None
    for b in beats:
        if b["kind"] == "title":
            continue
        if b["kind"] == "location":
            cur_scene = {"num": len(scenes) + 1, "slug": b["text"],
                         "loc_en": locdescs.get(b["text"]) or cn.describe_location(b["text"]), "items": []}
            scenes.append(cur_scene)
            continue
        if cur_scene is None:
            if b["kind"] == "action":
                narration.append(b["text"])
            continue
        cur_scene["items"].append(b)

    shots: list[dict] = []

    def add_shot(scene, lines, action_ar, names):
        shots.append({"id": len(shots) + 1, "scene": scene["num"], "slug": scene["slug"],
                      "loc_en": scene["loc_en"], "lines": lines, "action_ar": action_ar, "names": names})

    for sc in scenes:
        cur = None
        pending: list[str] = []

        def flush():
            nonlocal cur
            if cur:
                add_shot(sc, cur["lines"], cur["action"], cur["names"])
                cur = None

        for it in sc["items"]:
            if it["kind"] == "action":
                mentioned = [n for n in cast if mv.find_name(it["text"], n)]
                if cur and not cur["lines"]:
                    cur["action"] += " " + it["text"]
                    continue
                pending.append(it["text"])
                continue
            for part in split_text(it["text"]):
                secs = speech_secs(part)
                speaker = it["speaker"]
                fits = (cur and cur["lines"] and cur["secs"] + secs <= SECS_CAP and len(cur["lines"]) < MAX_LINES
                        and len({ln["speaker"] for ln in cur["lines"]} | {speaker}) <= 2 and not pending)
                if not fits:
                    flush()
                    cur = {"lines": [], "action": " ".join(pending), "secs": 0.0, "names": []}
                    pending = []
                cur["lines"].append({"speaker": speaker, "text": part, "paren": it.get("paren", "")})
                cur["secs"] += secs
                if speaker not in cur["names"] and speaker in cast:
                    cur["names"].append(speaker)
        flush()
        if pending:
            txt = " ".join(pending)
            people = any(ar in txt for ar, _ in cn.ACTION_WORDS)
            names = [n for n in cast if mv.find_name(txt, n)] if people else []
            add_shot(sc, [], txt, names)

    for s in shots:
        extra = [n for n in cast if s["action_ar"] and mv.find_name(s["action_ar"], n) and n not in s["names"]]
        if s["lines"] or any(ar in s["action_ar"] for ar, _ in cn.ACTION_WORDS):
            s["names"] += extra[:2]
        s["names"] = s["names"][:3]
        if len(s["action_ar"]) > 240:
            cut = s["action_ar"].rfind(" ", 0, 240)
            s["action_ar"] = s["action_ar"][:cut if cut > 120 else 240].strip() + " …"
        s["prompt"] = shot_prompt(s, cast, style, aspect)
    return shots, scenes, narration


def shot_prompt(s: dict, cast: dict, style: str, aspect: str) -> str:
    cam = CAMERAS[(s["id"] - 1) % len(CAMERAS)]
    speakers = list(dict.fromkeys(ln["speaker"] for ln in s["lines"]))
    if not s["lines"] and s["names"]:
        staging = "Medium wide shot, the characters move naturally as described, subtle handheld camera."
    elif not s["lines"]:
        staging = "Establishing wide shot, slow gentle camera movement, natural ambient life."
    elif len(speakers) >= 2:
        staging = (f"{cam}; the two characters face each other while talking, natural hand gestures, "
                   "subtle handheld camera, realistic reactions while the other speaks.")
    else:
        who = cast[speakers[0]]["en"] if speakers[0] in cast else "the speaker"
        staging = (f"{cam} of {who} speaking to someone just off-camera, expressive face, "
                   "natural small gestures, subtle handheld camera.")
    out = [f"{style}.", f"Vertical {aspect} frame." if aspect == "9:16" else f"Frame {aspect}.",
           f"Setting: {s['loc_en']}."]
    chars = [n for n in s["names"] if n in cast]
    if chars:
        out.append("Characters (keep their look identical in every shot):")
        for n in chars:
            out.append(f"- {cast[n]['en']}: {cast[n]['desc']}")
    out.append(f"Staging: {staging}")
    if s["action_ar"]:
        out.append(f"Action from the script (Arabic): {s['action_ar']}")
    if s["lines"]:
        out.append("Dialogue, spoken aloud in natural Syrian (Levantine) Arabic with clear lip-sync; "
                   "use exactly these words:")
        for ln in s["lines"]:
            c = cast.get(ln["speaker"])
            tag = f"{c['en']} ({c['voice_desc']})" if c else f"{name_en(ln['speaker'])}"
            mood = f", {cn.emotion_of({'paren': ln['paren'], 'text': ln['text']})}" if ln["paren"] else ""
            out.append(f'{tag}{mood}: "{ln["text"]}"')
    out.append("Audio: natural ambient sound of the place, no music. No subtitles, no captions, "
               "no on-screen text, no logos.")
    return "\n".join(out)


def day_plan(shots, per_day: int) -> list[str]:
    """جدول أيام لمن حصته المجانية محدودة: كم لقطة كل يوم وما يُنجز أولًا."""
    if per_day <= 0 or not shots:
        return []
    out = ["## خطة الأيام (حسب حصتك المجانية)", "", f"حصتك: **{per_day}** لقطة في اليوم.", "",
           "| اليوم | اللقطات | علامة |", "|---|---|---|"]
    n = len(shots)
    for d, start in enumerate(range(0, n, per_day), 1):
        end = min(start + per_day, n)
        out.append(f"| {d} | {shots[start]['id']:03d} – {shots[end - 1]['id']:03d} | ☐ |")
    days = -(-n // per_day)
    out += ["", f"المجموع {n} لقطة = **{days}** يومًا. اللقطات متسلسلة، فيمكن رفع ما أنجزته كل يوم إلى `clips/` "
            "وتشغيل التجميع لاحقًا؛ اللقطات الناقصة تُتخطى ويُذكر رقمها."]
    return out


def write_outputs(shots, scenes, narration, cast, out_dir: Path, aspect: str, style: str, per_day: int = 0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(shots)
    md = ["# حزمة لقطات الفيديو", "",
          f"عدد اللقطات: **{total}** — كل لقطة حتى 8 ثوانٍ — الأبعاد: **{aspect}**", "",
          "## طريقة العمل", "",
          "1. **صور مرجعية للشخصيات (مرة واحدة):** في Google Flow اصنع صورة لكل شخصية من وصفها في الجدول أدناه، "
          "واحفظها. استخدم الصورة المرجعية مع كل لقطة تظهر فيها الشخصية (خيار Ingredients أو Frames إن توفّر) "
          "ليبقى الوجه ثابتًا.",
          "2. **لكل لقطة:** انسخ الأمر كاملًا، اختر Veo 3.1 (Lite أو Fast تكفي للحصة المجانية) والأبعاد "
          f"{aspect}، ثم ولّد.",
          "3. **سمِّ الملف** عند التنزيل `shot_001.mp4` ثم `shot_002.mp4`… بنفس رقم اللقطة.",
          "4. **ارفع المقاطع** إلى مجلد `clips/` في المستودع، ثم شغّل **Actions ← Assemble video**: "
          "يجمعها بالترتيب مع الترجمة في فيديو واحد.",
          "", *day_plan(shots, per_day),
          "", "> راجع الحوار قبل التوليد: الاستخراج تلقائي وقد يخلط أحيانًا بين كلام المتحدث ووصف الحركة. "
          "عدّل النص هنا كما تشاء قبل نسخه.", "",
          "## الشخصيات", "", "| الاسم | الوصف (للصورة المرجعية) | الصوت |", "|---|---|---|"]
    for n, c in cast.items():
        md.append(f"| {n} ({c['en']}) | {c['desc']} | {c['voice_desc']} |")
    md += ["", "## اللقطات", ""]
    last_scene = None
    for s in shots:
        if s["scene"] != last_scene:
            md += ["", f"### المشهد {s['scene']}: {s['slug']}", ""]
            last_scene = s["scene"]
        dur = "6–8" if s["lines"] else "5–6"
        md += [f"**لقطة {s['id']:03d}** — `shot_{s['id']:03d}.mp4` — {dur} ثوانٍ", "", "```", s["prompt"], "```", ""]
    if narration:
        md += ["", "## السرد التمهيدي (اختياري)", "",
               "نص تقدمة يقرؤه راوٍ فوق لقطات تمهيدية، ولا يدخل في حزمة اللقطات أعلاه:", "", "```",
               "\n".join(story_parser._chunk(" ".join(narration), 260)), "```"]
    (out_dir / "shots.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    data = [{"id": s["id"], "scene": s["scene"], "slug": s["slug"], "names": s["names"], "lines": s["lines"],
             "prompt": s["prompt"]} for s in shots]
    (out_dir / "shots.json").write_text(json.dumps({"aspect": aspect, "style": style, "shots": data},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="حزمة لقطات لـ Google Flow / Veo")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-f", "--file")
    src.add_argument("-t", "--text")
    p.add_argument("-o", "--output", default="shots")
    p.add_argument("--aspect", choices=["9:16", "16:9"], default="9:16")
    p.add_argument("--look", choices=["realistic", "anime"], default="realistic")
    p.add_argument("--per-day", type=int, default=0, help="عدد اللقطات المسموح بها يوميًا في حصتك المجانية، لتوزيعها على أيام")
    args = p.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    raw = story_parser.normalize(raw or "")
    raw, descs, style_override, locdescs = cn.preprocess(raw)
    beats, decl = mv.parse_screenplay(raw, 400)
    style = style_override or {
        "realistic": ("Cinematic photorealistic live-action footage from a Syrian TV drama, natural light, "
                      "35mm film look, shallow depth of field, realistic faces and skin"),
        "anime": cn.LOOKS["anime"]}[args.look]
    cast = build_cast_all(beats, decl, descs)
    shots, scenes, narration = plan(beats, cast, locdescs, style, args.aspect)
    write_outputs(shots, scenes, narration, cast, Path(args.output), args.aspect, style, args.per_day)
    print(f"{len(shots)} لقطة | {len(cast)} شخصية | {len(scenes)} مشهد → {args.output}/shots.md")


if __name__ == "__main__":
    main()
