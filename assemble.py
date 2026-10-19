#!/usr/bin/env python3
"""يجمع مقاطع الفيديو المولّدة (clips/shot_001.mp4 …) في فيديو واحد مع ترجمة محروقة اختيارية.

    python assemble.py -c clips -s shots/shots.json -o output/final.mp4

- يرتّب المقاطع برقم اللقطة الموجود في اسم الملف (shot_007.mp4 أو 7.mp4 أو أي اسم فيه الرقم).
- يوحّد الأبعاد والإطارات والصوت، ويضيف صوتًا صامتًا لأي مقطع بلا صوت.
- إن وُجد shots.json يُكتب ملف ترجمة .srt بحوار كل لقطة موزعًا على مدتها، ويمكن حرقه في الفيديو (--burn).
- اللقطات الناقصة تُتخطى ويُذكر رقمها في آخر التقرير.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"فشل الأمر: {' '.join(cmd[:4])} …\n{res.stderr[-800:]}")
    return res.stdout


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise SystemExit(f"الملف ليس فيديو: {path.name}")
    dur = float(data["format"].get("duration") or v.get("duration") or 0)
    return {"w": int(v["width"]), "h": int(v["height"]), "dur": dur,
            "audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def shot_number(name: str) -> int | None:
    m = re.search(r"shot[_\- ]?(\d+)", name, re.I) or re.search(r"(?<!\d)(\d{1,3})(?!\d)", name)
    return int(m.group(1)) if m else None


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def normalize(src: Path, dst: Path, w: int, h: int, fps: int, info: dict) -> None:
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
          f"setsar=1,fps={fps},format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if info["audio"]:
        cmd += ["-vf", vf, "-af", "aresample=48000,aformat=channel_layouts=stereo"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{info['dur']:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-vf", vf, "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart", str(dst)]
    run(cmd)


def build_srt(entries: list[tuple[float, float, list[dict]]]) -> str:
    lines, n = [], 0
    for start, dur, spoken in entries:
        if not spoken:
            continue
        weights = [max(1, len(x["text"])) for x in spoken]
        total_w = sum(weights)
        usable = max(0.5, dur - 0.5)
        t = start + 0.2
        for x, wgt in zip(spoken, weights):
            span = usable * wgt / total_w
            n += 1
            lines.append(f"{n}\n{srt_time(t)} --> {srt_time(t + span - 0.05)}\n{x['text']}\n")
            t += span
    return "\n".join(lines)


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def build_ass(entries: list[tuple[float, float, list[dict]]], w: int, h: int, font: str) -> str:
    """ملف ASS بأبعاد الفيديو نفسها، فيكون حجم الخط بالبكسل الحقيقي."""
    size = max(20, h // 30)
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
            "MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,"
            f"{max(2, size // 12)},0,2,{w // 10},{w // 10},{h // 9},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    rows = []
    for start, dur, spoken in entries:
        if not spoken:
            continue
        weights = [max(1, len(x["text"])) for x in spoken]
        total_w = sum(weights)
        usable = max(0.5, dur - 0.5)
        t = start + 0.2
        for x, wgt in zip(spoken, weights):
            span = usable * wgt / total_w
            rows.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + span - 0.05)},Default,,0,0,0,,"
                        f"{x['text'].replace(chr(10), ' ')}")
            t += span
    return head + "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="تجميع مقاطع اللقطات في فيديو واحد")
    ap.add_argument("-c", "--clips", default="clips")
    ap.add_argument("-s", "--shots", default="shots/shots.json")
    ap.add_argument("-o", "--output", default="output/final.mp4")
    ap.add_argument("--burn", action="store_true", help="احرق الترجمة داخل الفيديو")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--font", default="", help="اسم خط للترجمة المحروقة (مثل Noto Naskh Arabic)")
    args = ap.parse_args()

    clips_dir = Path(args.clips)
    files = [p for p in sorted(clips_dir.glob("*")) if p.suffix.lower() in VIDEO_EXT]
    if not files:
        raise SystemExit(f"لا توجد مقاطع داخل {clips_dir}/ — ضع الملفات بأسماء shot_001.mp4 …")

    numbered: dict[int, Path] = {}
    unnumbered: list[Path] = []
    for p in files:
        n = shot_number(p.stem)
        if n is None or n in numbered:
            unnumbered.append(p)
        else:
            numbered[n] = p
    order = [numbered[k] for k in sorted(numbered)] + unnumbered
    ids = sorted(numbered) + [None] * len(unnumbered)
    if unnumbered:
        print("تنبيه: ملفات بلا رقم لقطة وُضعت في الآخر:", ", ".join(p.name for p in unnumbered))

    shots = {}
    shots_path = Path(args.shots)
    if shots_path.exists():
        data = json.loads(shots_path.read_text(encoding="utf-8"))
        shots = {s["id"]: s for s in data["shots"]}
        total = max(shots) if shots else 0
        missing = [i for i in range(1, total + 1) if i not in numbered]
    else:
        missing = []

    first = probe(order[0])
    w, h = first["w"] - first["w"] % 2, first["h"] - first["h"] % 2
    print(f"الأبعاد: {w}x{h} | عدد المقاطع: {len(order)}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    t = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for i, (src, sid) in enumerate(zip(order, ids)):
            info = probe(src)
            dst = tmp / f"n_{i:04d}.mp4"
            normalize(src, dst, w, h, args.fps, info)
            real = probe(dst)["dur"]
            parts.append(dst)
            spoken = shots.get(sid, {}).get("lines", []) if sid else []
            entries.append((t, real, spoken))
            t += real
            print(f"  {src.name} → {real:.1f}s")
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        joined = tmp / "joined.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(joined)])

        srt_text = build_srt(entries)
        srt_path = out_path.with_suffix(".srt")
        if srt_text:
            srt_path.write_text(srt_text, encoding="utf-8")
        if args.burn and srt_text:
            ass = tmp / "subs.ass"
            ass.write_text(build_ass(entries, w, h, args.font or "Noto Naskh Arabic"), encoding="utf-8")
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-vf", f"ass={ass.as_posix()}",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-c:a", "copy",
                 "-movflags", "+faststart", str(out_path)])
        else:
            shutil.copy(joined, out_path)

    print(f"تم: {out_path} ({t:.1f}s)")
    if missing:
        print("لقطات ناقصة (لم تُرفع بعد):", ", ".join(f"{i:03d}" for i in missing))


if __name__ == "__main__":
    main()
