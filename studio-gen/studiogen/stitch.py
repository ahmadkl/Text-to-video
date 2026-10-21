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
