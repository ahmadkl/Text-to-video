"""Join shot clips (video + their own audio) into one MP4 with ffmpeg."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def concat(clips: list[str], out_path: str, width: int = 512, height: int = 896, fps: int = 24) -> str:
    """Re-encode to a common format, then concatenate, so mismatched clips never break the join."""
    if not clips:
        raise ValueError("no clips to join")
    with tempfile.TemporaryDirectory() as td:
        norm = []
        for i, c in enumerate(clips):
            n = str(Path(td) / f"n{i:04d}.mp4")
            has_audio = "audio" in subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", c],
                capture_output=True, text=True, check=True).stdout
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", c]
            if not has_audio:  # pure-silence track so every segment has audio
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
            cmd += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p",
                    "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", n]
            subprocess.run(cmd, check=True)
            norm.append(n)
        lst = Path(td) / "list.txt"
        lst.write_text("".join(f"file '{n}'\n" for n in norm))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", out_path], check=True)
    return out_path


def mux_audio(video: str, audio: str, out_path: str, pad: float = 0.4) -> str:
    """Put speech on a silent clip. The video is frozen on its last frame if the speech is longer,
    and cut if it is shorter, so the shot always lasts speech + a short pause."""
    import subprocess
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio],
                               capture_output=True, text=True, check=True).stdout) + pad
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", audio,
                    "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={dur:.2f}[v];[1:a]apad[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-c:v", "libx264", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", out_path], check=True)
    return out_path


def burn_subtitle(video: str, text: str, out_path: str, font: str = "Noto Naskh Arabic") -> str:
    """Burn Arabic text at the bottom of the clip (libass shapes and orders Arabic correctly)."""
    import subprocess
    import tempfile
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "csv=p=0:s=x", video],
                           capture_output=True, text=True, check=True).stdout.split()
    w, h = (int(x) for x in probe[0].split("x")[:2])
    dur = float(probe[-1].split("x")[-1]) if len(probe) > 1 else 10.0
    size = max(24, h // 26)

    def t(x):
        cs = int(round(x * 100)); return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n\n[V4+ Styles]\n"
           "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
           "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
           "MarginV,Encoding\nStyle: D,%s,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,"
           "%d,0,2,%d,%d,%d,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
           "Dialogue: 0,%s,%s,D,,0,0,0,,%s\n") % (w, h, font, size, max(2, size // 12), w // 12, w // 12, h // 8,
                                                  t(0), t(dur + 1), text.replace("\n", " "))
    with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False, encoding="utf-8") as f:
        f.write(ass)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vf", f"ass={f.name}",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", out_path], check=True)
    return out_path
