"""Generate shots with the hosted LTX API (v2, asynchronous). No GPU needed.

Docs: https://docs.ltx.io  — submit POST /v2/text-to-video, poll GET /v2/text-to-video/{id}
until status is completed/failed, then download result.video_url (URLs expire: save at once).
Set the key in the LTX_API_KEY environment variable (a GitHub Actions secret).
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE = "https://api.ltx.io/v2/text-to-video"
# USD per second of output video, from docs.ltx.io/pricing (verify there; prices change).
PRICE = {
    ("ltx-2-5-fast", "720p"): 0.09, ("ltx-2-5-fast", "1080p"): 0.13,
    ("ltx-2-5-pro", "720p"): 0.12, ("ltx-2-5-pro", "1080p"): 0.17,
}
RES = {  # portrait 9:16 sizes from docs.ltx.io/models/ltx-2-5
    ("720p", True): "720x1280", ("1080p", True): "1080x1920",
    ("720p", False): "1280x720", ("1080p", False): "1920x1080",
}


@dataclass
class APIConfig:
    model: str = "ltx-2-5-fast"
    tier: str = "720p"
    portrait: bool = True
    fps: int = 24
    generate_audio: bool = True

    @property
    def resolution(self) -> str:
        return RES[(self.tier, self.portrait)]

    @property
    def max_seconds(self) -> int:
        return 10  # safe for every fps/model; fast allows up to 20 at 24 fps

    def duration_for(self, seconds: float) -> int:
        """API durations are even integers, at least 6 s."""
        return min(self.max_seconds, max(6, 2 * math.ceil(seconds / 2)))

    def cost(self, duration: int) -> float:
        return PRICE[(self.model, self.tier)] * duration


def _request(url: str, key: str, body: dict | None = None, timeout: int = 120):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _with_retry(fn, tries: int = 4):
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or i == tries - 1:
                raise RuntimeError(f"LTX API error {e.code}: {e.read().decode(errors='replace')[:400]}") from None
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
        time.sleep(2 ** i * 2 + random.random())


def generate_shot(cfg: APIConfig, prompt: str, out_path: str, seconds: float, timeout: int = 900) -> str:
    key = os.environ.get("LTX_API_KEY")
    if not key:
        raise SystemExit("LTX_API_KEY is not set")
    body = {"prompt": prompt, "model": cfg.model, "duration": cfg.duration_for(seconds),
            "resolution": cfg.resolution, "fps": cfg.fps, "generate_audio": cfg.generate_audio}
    job = json.loads(_with_retry(lambda: _request(BASE, key, body)).read())["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(6 + random.random() * 2)
        st = json.loads(_with_retry(lambda: _request(f"{BASE}/{job}", key)).read())
        if st["status"] == "completed":
            url = st["result"]["video_url"]
            data = _with_retry(lambda: urllib.request.urlopen(url, timeout=300)).read()
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(data)
            return out_path
        if st["status"] == "failed":
            raise RuntimeError(f"LTX job {job} failed: {st.get('error')}")
    raise TimeoutError(f"LTX job {job} did not finish in {timeout}s")
