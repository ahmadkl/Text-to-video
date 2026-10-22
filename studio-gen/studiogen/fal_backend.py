"""Generate shots on fal.ai's hosted LTX-2.5 (video + native audio/speech) — no GPU on your side.

Needs the FAL_KEY environment variable (a key from fal.ai, prepaid credits).
Prices (docs.ltx.io/pricing, per second of output): fast 720p $0.09, 1080p $0.13.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ENDPOINT = "lightricks/ltx-2.5/text-to-video/fast"
DURATIONS = [6, 8, 10, 12, 14, 16, 18, 20]
PRICE_PER_SEC = {"720p": 0.09, "1080p": 0.13, "1440p": 0.19, "2160p": 0.30}


@dataclass
class FalConfig:
    endpoint: str = ENDPOINT
    resolution: str = "720p"
    aspect_ratio: str = "9:16"
    fps: int = 24


def pick_duration(seconds: float) -> int:
    return next((d for d in DURATIONS if d >= seconds), DURATIONS[-1])


def estimate_cost(durations: list[int], resolution: str = "720p") -> float:
    return sum(durations) * PRICE_PER_SEC.get(resolution, 0.13)


def build_input(cfg: FalConfig, prompt: str, seconds: float) -> dict:
    return {"prompt": prompt, "duration": str(pick_duration(seconds)), "resolution": cfg.resolution,
            "aspect_ratio": cfg.aspect_ratio, "fps": cfg.fps, "generate_audio": True}


def _req(url: str, key: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Key {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def generate_shot(cfg: FalConfig, prompt: str, seconds: float, out_path: str, base: str = "https://queue.fal.run",
                  timeout: int = 1200, poll: float = 4.0) -> str:
    key = os.environ.get("FAL_KEY")
    if not key:
        raise SystemExit("FAL_KEY is not set")
    job = _req(f"{base}/{cfg.endpoint}", key, build_input(cfg, prompt, seconds))
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _req(job["status_url"], key)
        if st.get("status") == "COMPLETED":
            res = _req(job["response_url"], key)
            url = res["video"]["url"]
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=300) as r:
                Path(out_path).write_bytes(r.read())
            return out_path
        if st.get("status") not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RuntimeError(f"fal job failed: {st}")
        time.sleep(poll)
    raise TimeoutError("fal job timed out")
