"""قصة كاملة ← مقاطع فيديو حقيقية (Veo) بصوت وحركة، مجمّعة في فيديو واحد.

الخطوات:
  1) قراءة النص (حتى لو متصلًا) واستخراج الشخصيات والمشاهد.
  2) تقسيم كل مشهد إلى لقطات من ≤8 ثوانٍ (مقدار ما يتحمله مقطع Veo).
  3) وصف كل لقطة: المكان + الشخصيات + الحوار بين علامتي تنصيص (Veo يتكلم الحوار بنفسه).
  4) (اختياري) صورة مرجعية لكل شخصية لتثبيت الشكل بين المقاطع.
  5) توليد المقاطع، ثم تجميعها (مع السرد الافتتاحي كصور + صوت راوٍ).

التكلفة تُطبع قبل أي توليد، و--max-clips لتجربة عدد قليل، و--dry-run لكتابة الخطة فقط.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cinematic as cn
import make_video as mv
import story_parser
import veo
from text_to_video import ASPECTS, is_arabic, log, srt_time

WORDS_PER_SEC = 2.3
MAX_SHOT_SECS = 7.4
REF_EXT = ("png", "jpg", "jpeg", "webp")

VEO_STYLES = {
    "realistic": ("Cinematic live-action footage from a realistic Syrian TV drama, natural lighting, "
                  "35mm film look, subtle handheld camera, shallow depth of field, realistic faces"),
    "anime": cn.LOOKS["anime"] + ", fluid animation",
    "cartoon": cn.LOOKS["cartoon"] + ", fluid animation",
}


def speech_secs(text: str) -> float:
    return max(1.2, len(text.split()) / WORDS_PER_SEC + 0.35)


def split_line(text: str, max_secs: float = MAX_SHOT_SECS) -> list[str]:
    """يقسم السطر الطويل إلى أجزاء يتسع كل منها للقطة واحدة."""
    if speech_secs(text) <= max_secs:
        return [text]
    words = text.split()
    per = max(6, int((max_secs - 0.35) * WORDS_PER_SEC))
    pieces, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= per:
            pieces.append(" ".join(cur))
            cur = []
    if cur:
        if pieces and len(cur) < 4:
            pieces[-1] += " " + " ".join(cur)
        else:
            pieces.append(" ".join(cur))
    return pieces


def voice_of(c: dict) -> str:
    if c["child"]:
        return "a child's voice"
    if c["gender"] == "f":
        return "an older, warm female voice" if c["old"] else "a soft female voice"
    return "an older, raspy male voice" if c["old"] else "a calm mid-pitched male voice"


def tone_of(paren: str) -> str:
    p = paren or ""
    if re.search(r"(منخفض|همس|خافت)", p):
        return "in a low voice"
    if re.search(r"(غضب|غاضب|صارخ|يصرخ|بحدة)", p):
        return "angrily"
    if re.search(r"(حزن|حزين|بكاء)", p):
        return "sadly"
    return ""


def plan(beats: list[dict], cast: list[dict], cdesc: dict, locdescs: dict):
    """يعيد (نص السرد الافتتاحي، قائمة اللقطات)."""
    names = [c["name"] for c in cast]
    narration: list[str] = []
    scenes: list[dict] = []
    cur: dict | None = None
    for b in beats:
        if b["kind"] == "location":
            cur = {"loc_ar": b["text"], "loc_en": locdescs.get(b["text"]) or cn.describe_location(b["text"]),
                   "items": []}
            scenes.append(cur)
        elif b["kind"] == "title":
            continue
        elif cur is None:
            if b["kind"] == "action":
                narration.append(b["text"])
            else:
                cur = {"loc_ar": "", "loc_en": cn.describe_location(""), "items": [b]}
                scenes.append(cur)
        else:
            cur["items"].append(b)

    shots: list[dict] = []
    for si, sc in enumerate(scenes):
        lines: list[tuple] = []
        secs = 0.0
        actions: list[str] = []

        def flush():
            nonlocal lines, secs, actions
            if not lines and not actions:
                return
            present = []
            for sp, _, _ in lines:
                if sp in names and sp not in present:
                    present.append(sp)
            for a in actions:
                for n in names:
                    if mv.find_name(a, n) and n not in present:
                        present.append(n)
            shots.append({"scene": si, "loc_ar": sc["loc_ar"], "loc_en": sc["loc_en"], "lines": lines,
                          "actions": actions, "chars": present[:3], "est": secs})
            lines, secs, actions = [], 0.0, []

        for b in sc["items"]:
            if b["kind"] == "action":
                if lines:
                    flush()
                actions.append(b["text"])
                continue
            if b["kind"] != "dialogue":
                continue
            for piece in split_line(b["text"]):
                s = speech_secs(piece)
                if lines and (secs + s > MAX_SHOT_SECS or len(lines) >= 3):
                    flush()
                lines.append((b["speaker"], b.get("paren", ""), piece))
                secs += s
        flush()
    return "\n\n".join(narration), shots


def build_prompt(shot: dict, cast_by_name: dict, cdesc: dict, style: str, has_refs: bool) -> str:
    parts = [style + ".", f"Setting: {shot['loc_en']}" + (f" (scene: {shot['loc_ar']})" if shot["loc_ar"] else "") + "."]
    speakers = list(dict.fromkeys(s for s, _, _ in shot["lines"] if s in cast_by_name))
    if len(speakers) == 1:
        parts.append("Medium close-up on the speaker, camera slowly pushing in.")
    elif len(speakers) >= 2:
        parts.append("Medium two-shot over the shoulder, natural cuts between speakers.")
    else:
        parts.append("Wide establishing shot with natural movement.")
    if shot["chars"]:
        chars = "; ".join(f"{n} is {cdesc[n]}, speaking with {voice_of(cast_by_name[n])}" for n in shot["chars"])
        parts.append(f"Characters: {chars}.")
        if has_refs:
            parts.append("Keep each character's face, hair and clothing exactly as in the reference images.")
    if shot["actions"]:
        parts.append("Action (in Arabic): " + " ".join(shot["actions"]))
    if shot["lines"]:
        parts.append("Dialogue, spoken in Syrian Levantine Arabic dialect with natural acting and accurate lip-sync:")
        for sp, paren, text in shot["lines"]:
            tone = tone_of(paren)
            parts.append(f'{sp} says{" " + tone if tone else ""}: "{text}"')
    parts.append("Ambient sound only, no background music. No subtitles, no captions, no on-screen text.")
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# الصور المرجعية للشخصيات
# ----------------------------------------------------------------------------
def find_ref(name: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        for ext in REF_EXT:
            p = d / f"{name}.{ext}"
            if p.exists():
                return p
    return None


def make_refs(names: list[str], cdesc: dict, dirs: list[Path], out_dir: Path, mode: str, look: str,
              aspect_hint: str) -> dict[str, Path]:
    refs: dict[str, Path] = {}
    if mode == "none":
        return refs
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        found = find_ref(n, dirs)
        if found:
            refs[n] = found
            continue
        if mode != "auto" or not os.environ.get("GEMINI_API_KEY"):
            continue
        prompt = (f"{cn.LOOKS[look]}. Character reference portrait, upper body, facing the camera with a neutral "
                  f"expression, plain neutral background, soft studio light: {cdesc[n]}. {cn.NEGATIVE}")
        seed = int(hashlib.sha1(n.encode()).hexdigest()[:6], 16)
        try:
            img = cn.fetch_image(prompt, 768, 1024, seed, "gemini", out_dir / "cache", max_attempts=2)
            path = out_dir / f"{n}.png"
            img.save(path)
            refs[n] = path
            log(f"  صورة مرجعية لـ {n}")
        except cn.ImageError as exc:
            log(f"  تعذّرت الصورة المرجعية لـ {n}: {exc}")
    return refs


# ----------------------------------------------------------------------------
# التجميع
# ----------------------------------------------------------------------------
def has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=codec_type", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return "audio" in out


def normalize_clip(src: Path, dst: Path, w: int, h: int) -> None:
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
          f"fps=24,format=yuv420p,setsar=1")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if not has_audio(src):
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
    cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-ar", "48000",
            "-ac", "2", "-b:a", "160k", str(dst)]
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "default=nw=1:nk=1", str(path)], capture_output=True, text=True).stdout.strip())


def write_plan(path: Path, shots: list[dict], prompts: list[str], narration: str, refs: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# خطة اللقطات ({len(shots)} لقطة)\n\n")
        fh.write("كل لقطة = مقطع فيديو ≤8 ثوانٍ. يمكنك لصق الوصف في Google Flow يدويًا إن أردت.\n\n")
        if narration:
            fh.write(f"## السرد الافتتاحي ({len(narration.split())} كلمة)\nيُقرأ بصوت راوٍ فوق صور.\n\n")
        for i, (shot, pr) in enumerate(zip(shots, prompts), 1):
            who = "، ".join(shot["chars"]) or "-"
            ref = "، ".join(n for n in shot["chars"] if n in refs) or "-"
            fh.write(f"## لقطة {i} — مشهد {shot['scene'] + 1}\nالشخصيات: {who} | الصور المرجعية: {ref}\n\n```\n{pr}\n```\n\n")


def generate(args) -> Path:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    raw = story_parser.normalize(raw or "")
    raw, descs, style_override, locdescs = cn.preprocess(raw)
    beats, decl = mv.parse_screenplay(raw, args.max_chars)
    if not beats:
        raise SystemExit("النص فارغ.")
    cast = mv.build_cast(beats, decl)
    cdesc = {c["name"]: descs.get(c["name"]) or cn.default_desc(c) for c in cast}
    cast_by_name = {c["name"]: c for c in cast}
    style = style_override or VEO_STYLES[args.look]

    narration, shots = plan(beats, cast, cdesc, locdescs)
    total_shots = len(shots)
    if args.max_clips:
        shots = shots[:args.max_clips]
    key = os.environ.get("GEMINI_API_KEY", "")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    root = Path(__file__).parent
    names_needed = list(dict.fromkeys(n for s in shots for n in s["chars"]))
    refs = make_refs(names_needed, cdesc, [root / "characters", Path("characters")],
                     out_path.parent / "refs", args.refs, args.look, args.aspect) if not args.dry_run else {
        n: p for n in names_needed if (p := find_ref(n, [root / "characters", Path("characters")]))}
    prompts = [build_prompt(s, cast_by_name, cdesc, style,
                            bool([n for n in s["chars"] if n in refs])) for s in shots]
    plan_path = out_path.with_name(out_path.stem + "_shots.md")
    write_plan(plan_path, shots, prompts, narration, refs)

    seconds = len(shots) * 8
    log(f"الشخصيات: {', '.join(cdesc) or '-'}")
    log(f"اللقطات: {len(shots)} من {total_shots} → {seconds} ثانية فيديو مولّد "
        f"({'كل القصة' if len(shots) == total_shots else 'تجربة محدودة'})")
    rate = {"fast": 0.10, "standard": 0.40, "lite": 0.05}[getattr(args, "veo_quality", "fast")]
    log(f"التكلفة التقديرية: نحو {seconds * rate:.1f} دولارًا بسعر {rate} دولار للثانية "
        "(تقدير من أسعار منشورة، تأكد من صفحة تسعير Gemini API؛ كل لقطة تُحاسَب حتى لو لم تعجبك)")
    log(f"الخطة المكتوبة: {plan_path}")
    if args.dry_run:
        log("وضع --dry-run: لم يُولَّد أي شيء ولم يُصرف أي مبلغ.")
        return plan_path
    if not key:
        raise SystemExit("توليد الفيديو يحتاج GEMINI_API_KEY (مفتاح بحساب فوترة). أضِفه في Secrets، "
                         "أو شغّل --dry-run للحصول على خطة اللقطات فقط.")

    veo_model = veo.resolve_model(key, getattr(args, "veo_model", ""), getattr(args, "veo_quality", "fast"), log)
    w, h = ASPECTS[args.aspect]
    cache = out_path.parent / "cache"
    aspect = "9:16" if args.aspect == "9:16" else "16:9"
    if args.aspect == "1:1":
        log("تنبيه: Veo يدعم 16:9 و9:16 فقط، سيُستخدم 16:9.")

    def run_shot(i: int):
        shot = shots[i]
        r = [refs[n] for n in shot["chars"] if n in refs][:3]
        log(f"  لقطة {i + 1}/{len(shots)}: إرسال للتوليد...")
        try:
            clip = veo.generate_clip(prompts[i], aspect=aspect, key=key, cache_dir=cache, model=veo_model,
                                     refs=r, resolution=args.resolution)
            log(f"  لقطة {i + 1}/{len(shots)}: جاهزة")
            return i, clip, None
        except veo.VeoError as exc:
            log(f"  لقطة {i + 1}/{len(shots)}: فشلت — {exc}")
            return i, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = sorted(pool.map(run_shot, range(len(shots))), key=lambda r: r[0])
    ok = [r for r in results if r[1]]
    if not ok:
        raise SystemExit("فشلت كل اللقطات. آخر خطأ: " + str(results[-1][2]))
    if len(ok) < len(results):
        log(f"تحذير: نجحت {len(ok)} لقطة من {len(results)}؛ اللقطات الفاشلة تُركت خارج الفيديو.")

    parts: list[Path] = []
    tmp = out_path.parent / "parts"
    tmp.mkdir(parents=True, exist_ok=True)
    offset = 0.0
    if narration and args.narration != "skip" and (not args.max_clips or os.environ.get("FORCE_NARRATION")):
        log("إنتاج السرد الافتتاحي (صور + صوت راوٍ)...")
        nargs = types.SimpleNamespace(**{**vars(args), "file": None, "text": narration,
                                         "output": str(tmp / "narration_raw.mp4"), "narrator": True,
                                         "min_beat": 2.6, "max_chars": 160})
        cn.generate(nargs)
        n_norm = tmp / "part_000.mp4"
        normalize_clip(Path(nargs.output), n_norm, w, h)
        parts.append(n_norm)
        offset = probe_duration(n_norm)

    srt_rows: list[tuple[float, float, str]] = []
    for idx, clip, _ in ok:
        dst = tmp / f"part_{idx + 1:03d}.mp4"
        normalize_clip(clip, dst, w, h)
        parts.append(dst)
        dur = probe_duration(dst)
        total_chars = sum(len(t) for _, _, t in shots[idx]["lines"]) or 1
        t = offset + 0.4
        for sp, _, text in shots[idx]["lines"]:
            span = max(1.0, (dur - 0.8) * len(text) / total_chars)
            srt_rows.append((t, min(t + span, offset + dur - 0.1), f"{sp}: {text}"))
            t += span
        offset += dur

    listing = tmp / "list.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    joined = tmp / "joined.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(joined)], check=True)

    srt = out_path.with_suffix(".srt")
    with open(srt, "w", encoding="utf-8") as fh:
        for i, (a, b, text) in enumerate(srt_rows, 1):
            fh.write(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n\n")

    if args.burn_subs and srt_rows:
        style_s = (f"Alignment=2,FontSize={max(18, int(min(w, h) * 0.045))},Outline=2,Shadow=0,"
                   f"MarginV={int(h * 0.06)}")
        esc = str(srt).replace("\\", "/").replace(":", "\\:")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-vf",
                        f"subtitles='{esc}':original_size={w}x{h}:force_style='{style_s}'", "-c:v", "libx264", "-crf", "20",
                        "-c:a", "copy", str(out_path)], check=True)
    else:
        joined.replace(out_path)
    log(f"تم: {out_path} ({offset:.0f}s، {len(ok)} لقطة مولّدة)")
    return out_path
