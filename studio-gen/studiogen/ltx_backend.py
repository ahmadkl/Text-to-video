"""Run the LTX-2.5 distilled pipeline (video + synchronized speech) per shot.

Needs a CUDA GPU and the model files (~66 GiB, see README). This module only
builds and runs the command; it does not import torch.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LTXConfig:
    ltx_repo: str = "LTX-2"  # path to the cloned Lightricks/LTX-2 repo
    models_dir: str = "models/ltx-2.5"  # where `hf download` put the files
    width: int = 512  # 9:16 -> 512x896 (both divisible by 32)
    height: int = 896
    fps: int = 24
    quantization: str | None = "fp8-cast"  # lowers VRAM; None to disable
    offload: str | None = "cpu"  # "cpu" | "disk" | None
    enhance_prompt: bool = False

    def model_args(self) -> list[str]:
        m = Path(self.models_dir)
        return [
            "--transformer-path", str(m / "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors"),
            "--text-encoder-path", str(m / "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"),
            "--video-vae-path", str(m / "vae/ltx-2.5-video-vae-bf16.safetensors"),
            "--audio-vae-path", str(m / "vae/ltx-2.5-audio-vae-bf16.safetensors"),
            "--spatial-upsampler-path", str(m / "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"),
        ]


def build_command(cfg: LTXConfig, prompt: str, out_path: str, num_frames: int, seed: int,
                  image: str | None = None) -> list[str]:
    cmd = ["uv", "run", "python", "-m", "ltx_pipelines.distilled", *cfg.model_args(),
           "--num-frames", str(num_frames), "--frame-rate", str(cfg.fps),
           "--width", str(cfg.width), "--height", str(cfg.height),
           "--seed", str(seed), "--output-path", out_path, "--prompt", prompt]
    if image:
        # Image conditioning: PATH FRAME_IDX STRENGTH -> pin frame 0 to the character portrait
        cmd += ["--image", image, "0", "0.9"]
    if cfg.quantization:
        cmd += ["--quantization", cfg.quantization]
    if cfg.offload:
        cmd += ["--offload", cfg.offload]
    if cfg.enhance_prompt:
        cmd += ["--enhance-prompt"]
    return cmd


def generate_shot(cfg: LTXConfig, prompt: str, out_path: str, num_frames: int, seed: int,
                  image: str | None = None, dry_run: bool = False) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = build_command(cfg, prompt, out_path, num_frames, seed, image)
    if dry_run:
        print(" ".join(shlex.quote(c) for c in cmd))
        return out_path
    subprocess.run(cmd, cwd=cfg.ltx_repo, check=True)
    return out_path
