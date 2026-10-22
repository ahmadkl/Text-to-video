"""Free-GPU backend: LTX-Video 2B distilled (v0.9.8) via the older Lightricks/LTX-Video repo.

Small enough for a free Colab/Kaggle T4 (16 GB) with --offload_to_cpu. Output is SILENT video
(speech is added afterwards from TTS). Quality is well below LTX-2.5 / Veo.
The repo's CLI takes an output *directory* and names the file itself, so we pick up the newest mp4.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, cartoon, text, subtitles, watermark"


@dataclass
class LiteConfig:
    repo: str = "LTX-Video"  # path to the cloned Lightricks/LTX-Video repo
    pipeline_config: str = "configs/ltxv-2b-0.9.8-distilled.yaml"
    width: int = 480  # 9:16, divisible by 32 (the script pads anyway)
    height: int = 832
    fps: int = 24
    offload: bool = True
    max_seconds: float = 10.0

    def frames_for(self, seconds: float) -> int:
        s = min(seconds, self.max_seconds)
        return 8 * max(1, round(s * self.fps / 8)) + 1


def build_command(cfg: LiteConfig, prompt: str, out_dir: str, num_frames: int, seed: int,
                  image: str | None = None) -> list[str]:
    cmd = [sys.executable, "inference.py", "--prompt", prompt, "--negative_prompt", NEGATIVE,
           "--pipeline_config", cfg.pipeline_config, "--height", str(cfg.height), "--width", str(cfg.width),
           "--num_frames", str(num_frames), "--frame_rate", str(cfg.fps), "--seed", str(seed),
           "--output_path", out_dir]
    if cfg.offload:
        cmd += ["--offload_to_cpu", "True"]
    if image:
        cmd += ["--conditioning_media_paths", image, "--conditioning_start_frames", "0"]
    return cmd


def generate_shot(cfg: LiteConfig, prompt: str, out_path: str, seconds: float, seed: int,
                  image: str | None = None) -> str:
    work = Path(out_path).with_suffix("")  # scratch directory next to the final clip
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(build_command(cfg, prompt, str(work.resolve()), cfg.frames_for(seconds), seed,
                                 str(Path(image).resolve()) if image else None), cwd=cfg.repo, check=True)
    vids = sorted(work.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not vids:
        raise RuntimeError(f"LTX-Video produced no mp4 in {work}")
    shutil.move(str(vids[-1]), out_path)
    shutil.rmtree(work, ignore_errors=True)
    return out_path
