#!/usr/bin/env python3
"""مولّد فيديو من نص — Text-to-Video generator.

يحوّل نصًا (كل فقرة = مشهد) إلى فيديو MP4 فيه خلفيات متحركة، نص متحرك،
تعليق صوتي اختياري (edge-tts)، وملف ترجمة SRT.

الاستخدام:
    python text_to_video.py -f examples/arabic.txt -o output/video.mp4
    python text_to_video.py -t "مرحبا بكم || هذا مشهد ثان" --no-voice
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

FPS = 24
ASPECTS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024)}

# (أعلى، أسفل، لون التمييز)
PALETTES = [
    ((22, 30, 70), (88, 46, 140), (255, 196, 87)),
    ((10, 60, 74), (22, 120, 110), (255, 232, 140)),
    ((70, 22, 50), (170, 60, 80), (255, 214, 170)),
    ((18, 40, 90), (30, 110, 170), (160, 235, 255)),
    ((44, 28, 78), (120, 70, 160), (255, 170, 200)),
    ((20, 60, 40), (70, 130, 80), (240, 240, 150)),
]

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
SENTENCE_RE = re.compile(r"(?<=[.!?؟…。])\s+")
USE_RAQM = features.check("raqm")

DEFAULT_VOICES = {"ar": "ar-SA-HamedNeural", "en": "en-US-AriaNeural"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------
# النص والمشاهد
# ----------------------------------------------------------------------------
def is_arabic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if ARABIC_RE.match(c)) / len(letters) > 0.4


def clean(text: str) -> str:
    text = re.sub(r"[*_`#>]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_scenes(raw: str, max_chars: int) -> list[dict]:
    """يقسّم النص إلى مشاهد. الفقرات تُفصل بسطر فارغ أو --- أو ' || '."""
    raw = raw.replace("\r", "").strip()
    scenes: list[dict] = []
    lines = raw.split("\n")
    if lines and lines[0].startswith("# "):
        title = clean(lines[0][2:])
        if title:
            scenes.append({"text": title, "title": True})
        raw = "\n".join(lines[1:])

    blocks = re.split(r"\n\s*\n|^\s*-{3,}\s*$|\s\|\|\s", raw, flags=re.M)
    for block in blocks:
        block = clean(block)
        if not block:
            continue
        chunk = ""
        for sentence in SENTENCE_RE.split(block):
            if chunk and len(chunk) + 1 + len(sentence) > max_chars:
                scenes.append({"text": chunk, "title": False})
                chunk = sentence
            else:
                chunk = f"{chunk} {sentence}".strip()
        if chunk:
            scenes.append({"text": chunk, "title": False})
    return scenes


# ----------------------------------------------------------------------------
# الخطوط والرسم
# ----------------------------------------------------------------------------
def find_font(bold: bool = True) -> str:
    env = os.environ.get("FONT_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).parent / "fonts"
    if here.is_dir():
        found = sorted(here.glob("*.ttf")) + sorted(here.glob("*.otf"))
        if found:
            return str(found[0])
    style = "bold" if bold else "regular"
    for pattern in (f":lang=ar:{style}", ":lang=ar"):
        try:
            out = subprocess.run(
                ["fc-match", "-f", "%{file}", pattern],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if out and Path(out).exists() and not out.lower().endswith(".pcf"):
                return out
        except Exception:
            pass
    for cand in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ):
        if Path(cand).exists():
            return cand
    raise SystemExit("لم أجد خطًا يدعم العربية. ضع ملف .ttf داخل مجلد fonts/")


class TextShaper:
    """يعالج الاتجاه والتشكيل للعربية، عبر raqm أو arabic-reshaper كبديل."""

    def __init__(self, font_path: str):
        self.font_path = font_path
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._fallback = False
        if not USE_RAQM:
            try:
                import arabic_reshaper  # noqa: F401
                from bidi.algorithm import get_display  # noqa: F401

                self._fallback = True
            except Exception:
                log("تحذير: لا raqm ولا arabic-reshaper، قد يظهر النص العربي مفكّكًا.")

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._fonts:
            engine = ImageFont.Layout.RAQM if USE_RAQM else ImageFont.Layout.BASIC
            self._fonts[size] = ImageFont.truetype(self.font_path, size, layout_engine=engine)
        return self._fonts[size]

    def prep(self, line: str, rtl: bool) -> str:
        if rtl and self._fallback:
            import arabic_reshaper
            from bidi.algorithm import get_display

            return get_display(arabic_reshaper.reshape(line))
        return line

    def kwargs(self, rtl: bool) -> dict:
        if USE_RAQM:
            return {"direction": "rtl", "language": "ar"} if rtl else {"language": "en"}
        return {}

    def width(self, line: str, size: int, rtl: bool) -> float:
        return self.font(size).getlength(self.prep(line, rtl), **self.kwargs(rtl))

    def wrap(self, text: str, size: int, max_w: int, rtl: bool) -> list[str]:
        lines: list[str] = []
        cur: list[str] = []
        for word in text.split():
            trial = " ".join(cur + [word])
            if cur and self.width(trial, size, rtl) > max_w:
                lines.append(" ".join(cur))
                cur = [word]
            else:
                cur.append(word)
        if cur:
            lines.append(" ".join(cur))
        return lines


def render_text_layer(shaper: TextShaper, text: str, canvas: tuple[int, int],
                      title: bool, accent: tuple) -> Image.Image:
    """يرسم النص مرة واحدة على طبقة RGBA بحجم النص فقط."""
    w, h = canvas
    rtl = is_arabic(text)
    max_w = int(w * 0.82)
    size = int(min(w, h) * (0.105 if title else 0.072))
    min_size = int(min(w, h) * 0.035)
    max_h = int(h * (0.55 if title else 0.62))

    while True:
        lines = shaper.wrap(text, size, max_w, rtl)
        line_h = int(size * 1.45)
        widest = max(shaper.width(ln, size, rtl) for ln in lines)
        if (len(lines) * line_h <= max_h and widest <= max_w) or size <= min_size:
            break
        size -= 2

    pad = int(size * 0.8)
    lw = int(max(shaper.width(ln, size, rtl) for ln in lines)) + pad * 2
    lh = line_h * len(lines) + pad * 2 + (int(size * 0.5) if title else 0)

    shadow = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    sd, ld = ImageDraw.Draw(shadow), ImageDraw.Draw(layer)
    font = shaper.font(size)
    kw = shaper.kwargs(rtl)
    color = accent if title else (255, 255, 255)
    y = pad
    for ln in lines:
        shown = shaper.prep(ln, rtl)
        tw = font.getlength(shown, **kw)
        x = (lw - tw) / 2
        sd.text((x + 3, y + 4), shown, font=font, fill=(0, 0, 0, 170), **kw)
        ld.text((x, y), shown, font=font, fill=color + (255,), **kw)
        y += line_h
    if title:
        bar_w = int(lw * 0.35)
        ld.rounded_rectangle(
            ((lw - bar_w) // 2, y + 2, (lw + bar_w) // 2, y + 2 + max(4, size // 14)),
            radius=4, fill=accent + (255,),
        )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, size // 22)))
    shadow.alpha_composite(layer)
    return shadow


def make_orb(radius: int, color: tuple) -> Image.Image:
    size = radius * 2
    orb = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(orb).ellipse((radius * 0.3,) * 2 + (size - radius * 0.3,) * 2,
                                fill=color + (255,))
    return orb.filter(ImageFilter.GaussianBlur(radius * 0.28))


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------
# الصوت
# ----------------------------------------------------------------------------
def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def synthesize(scenes: list[dict], voice: str, rate: str, tmp: Path) -> list[str | None]:
    """يولّد ملف صوت لكل مشهد عبر edge-tts. يعيد None عند الفشل."""
    try:
        import edge_tts
    except Exception:
        log("تحذير: edge-tts غير مثبّت، سيُنتج الفيديو بدون صوت.")
        return [None] * len(scenes)

    async def run() -> list[str | None]:
        results: list[str | None] = []
        for i, scene in enumerate(scenes):
            path = str(tmp / f"voice_{i:03d}.mp3")
            v = voice
            if v == "auto":
                v = DEFAULT_VOICES["ar" if is_arabic(scene["text"]) else "en"]
            try:
                await edge_tts.Communicate(scene["text"], v, rate=rate).save(path)
                if Path(path).stat().st_size < 500:
                    raise RuntimeError("ملف صوت فارغ")
                results.append(path)
            except Exception as exc:  # noqa: BLE001
                log(f"تحذير: فشل توليد الصوت للمشهد {i + 1}: {exc}")
                results.append(None)
        return results

    return asyncio.run(run())


def build_audio(scenes: list[dict], voices: list[str | None], durations: list[float],
                lead_in: float, tmp: Path) -> str | None:
    if not any(voices):
        return None
    parts: list[Path] = []
    for i, (voice, dur) in enumerate(zip(voices, durations)):
        out = tmp / f"scene_{i:03d}.wav"
        if voice:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", voice, "-af",
                   f"adelay={int(lead_in * 1000)}:all=1,apad", "-t", f"{dur:.3f}",
                   "-ar", "44100", "-ac", "2", str(out)]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                   "anullsrc=r=44100:cl=stereo", "-t", f"{dur:.3f}", str(out)]
        subprocess.run(cmd, check=True)
        parts.append(out)
    listing = tmp / "audio_list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    merged = tmp / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(merged)], check=True)
    return str(merged)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ----------------------------------------------------------------------------
# التوليد
# ----------------------------------------------------------------------------
def generate(args: argparse.Namespace) -> Path:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    scenes = parse_scenes(raw or "", args.max_chars)
    if not scenes:
        raise SystemExit("النص فارغ: اكتب نصًا واحدًا على الأقل.")
    log(f"عدد المشاهد: {len(scenes)}")

    w, h = ASPECTS[args.aspect]
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else int(
        hashlib.sha1((raw or "").encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # الصوت أولًا لمعرفة مدة كل مشهد
        voices: list[str | None] = [None] * len(scenes)
        if args.voice != "none" and not args.no_voice:
            log("توليد التعليق الصوتي...")
            voices = synthesize(scenes, args.voice, args.rate, tmp)

        lead_in, tail = 0.5, 0.8
        durations: list[float] = []
        for scene, voice in zip(scenes, voices):
            if voice:
                dur = probe_duration(voice) + lead_in + tail
            else:
                dur = len(scene["text"]) / 14 + 1.5
            durations.append(max(args.min_scene, dur))
        total = sum(durations)
        log(f"مدة الفيديو: {total:.1f} ثانية")

        shaper = TextShaper(find_font())
        palettes = [PALETTES[(rng.randrange(len(PALETTES)) + i) % len(PALETTES)]
                    for i in range(len(scenes))]
        layers = [render_text_layer(shaper, s["text"], (w, h), s["title"], palettes[i][2])
                  for i, s in enumerate(scenes)]

        # كرات ضوء متحركة
        orbs = []
        for _ in range(7):
            radius = int(min(w, h) * rng.uniform(0.18, 0.38))
            orbs.append({
                "r": radius,
                "x": rng.uniform(0, w), "y": rng.uniform(0, h),
                "fx": rng.uniform(0.05, 0.14), "fy": rng.uniform(0.05, 0.14),
                "px": rng.uniform(0, 6.28), "py": rng.uniform(0, 6.28),
                "ax": w * rng.uniform(0.10, 0.22), "ay": h * rng.uniform(0.10, 0.22),
                "alpha": rng.uniform(0.18, 0.34),
            })
        orb_sprite_cache: dict[tuple[int, int], Image.Image] = {}

        grad = Image.linear_gradient("L").resize((w, h))
        silent = tmp / "silent.mp4"
        ffmpeg = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
             "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(silent)],
            stdin=subprocess.PIPE,
        )

        n_frames = int(round(total * FPS))
        bounds = [0.0]
        for d in durations:
            bounds.append(bounds[-1] + d)
        scene_i = 0
        xfade = 0.7

        for f in range(n_frames):
            t = f / FPS
            while scene_i < len(scenes) - 1 and t >= bounds[scene_i + 1]:
                scene_i += 1
            local = t - bounds[scene_i]
            dur = durations[scene_i]

            # لون الخلفية: انتقال سلس إلى لوحة المشهد التالي قرب النهاية
            cur = palettes[scene_i]
            top, bottom, accent = cur
            if scene_i < len(scenes) - 1 and local > dur - xfade:
                k = smooth((local - (dur - xfade)) / xfade)
                nxt = palettes[scene_i + 1]
                top, bottom, accent = lerp(top, nxt[0], k), lerp(bottom, nxt[1], k), lerp(accent, nxt[2], k)
            elif scene_i > 0 and local < xfade:
                prev = palettes[scene_i - 1]
                k = smooth(local / xfade)
                top, bottom, accent = lerp(prev[0], top, k), lerp(prev[1], bottom, k), lerp(prev[2], accent, k)

            frame = Image.composite(Image.new("RGB", (w, h), bottom),
                                    Image.new("RGB", (w, h), top), grad)

            # الكرات
            for orb in orbs:
                x = orb["x"] + math.sin(t * orb["fx"] * 6.28 + orb["px"]) * orb["ax"]
                y = orb["y"] + math.cos(t * orb["fy"] * 6.28 + orb["py"]) * orb["ay"]
                key = (orb["r"], accent)
                if key not in orb_sprite_cache:
                    if len(orb_sprite_cache) > 400:
                        orb_sprite_cache.clear()
                    orb_sprite_cache[key] = make_orb(orb["r"], accent)
                sprite = orb_sprite_cache[key]
                mask = sprite.getchannel("A").point(lambda v, a=orb["alpha"]: int(v * a))
                frame.paste(sprite.convert("RGB"), (int(x - orb["r"]), int(y - orb["r"])), mask)

            # النص: ظهور تدريجي + تكبير بطيء + اختفاء
            fade_in = smooth((local - lead_in * 0.4) / 0.6)
            fade_out = 1 - smooth((local - (dur - 0.55)) / 0.45) if scene_i == len(scenes) - 1 else 1.0
            alpha = fade_in * fade_out
            if alpha > 0.01:
                layer = layers[scene_i]
                scale = 0.94 + 0.08 * min(1.0, local / dur) + 0.03 * (1 - fade_in)
                lw, lh = max(1, int(layer.width * scale)), max(1, int(layer.height * scale))
                resized = layer.resize((lw, lh), Image.BILINEAR)
                a = resized.getchannel("A").point(lambda v, k=alpha: int(v * k))
                resized.putalpha(a)
                rise = int((1 - fade_in) * h * 0.03)
                frame.paste(resized, ((w - lw) // 2, (h - lh) // 2 + rise), resized)

            # شريط التقدّم
            draw = ImageDraw.Draw(frame)
            bar_h = max(4, h // 150)
            draw.rectangle((0, h - bar_h, int(w * t / total), h), fill=accent)

            ffmpeg.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            if f % (FPS * 5) == 0:
                log(f"  {f * 100 // n_frames}%")

        ffmpeg.stdin.close()  # type: ignore[union-attr]
        if ffmpeg.wait() != 0:
            raise SystemExit("فشل ffmpeg أثناء ترميز الفيديو.")

        audio = build_audio(scenes, voices, durations, lead_in, tmp)
        if audio:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", audio,
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                            str(out_path)], check=True)
        else:
            shutil.copy(silent, out_path)

    # ملف الترجمة
    srt = out_path.with_suffix(".srt")
    with open(srt, "w", encoding="utf-8") as fh:
        for i, scene in enumerate(scenes):
            start, end = bounds[i] + lead_in, bounds[i + 1] - 0.2
            fh.write(f"{i + 1}\n{srt_time(start)} --> {srt_time(end)}\n{scene['text']}\n\n")

    log(f"تم: {out_path}  ({total:.1f}s, {w}x{h})")
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="توليد فيديو من نص")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-f", "--file", help="ملف نصي (كل فقرة = مشهد)")
    src.add_argument("-t", "--text", help="النص مباشرة (افصل المشاهد بـ ' || ')")
    p.add_argument("-o", "--output", default="output/video.mp4")
    p.add_argument("--aspect", choices=list(ASPECTS), default="16:9")
    p.add_argument("--voice", default="auto",
                   help="auto، أو اسم صوت edge-tts مثل ar-EG-SalmaNeural، أو none")
    p.add_argument("--no-voice", action="store_true", help="بدون تعليق صوتي")
    p.add_argument("--rate", default="+0%", help="سرعة الصوت مثل -10%% أو +15%%")
    p.add_argument("--min-scene", type=float, default=3.0, help="أقل مدة للمشهد بالثواني")
    p.add_argument("--max-chars", type=int, default=140, help="أقصى طول نص للمشهد")
    p.add_argument("--seed", type=int, default=None, help="لتثبيت الألوان والحركة")
    generate(p.parse_args())


if __name__ == "__main__":
    main()
