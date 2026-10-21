"""Generate character portraits through a running ComfyUI server (HTTP API).

Start ComfyUI first (`python main.py --listen`). By default this uses the
stock SD workflow from ComfyUI's own api example; for Flux or SDXL export your
own graph from the ComfyUI UI ("Save (API Format)") and point `--workflow` at
it. Put these placeholders inside the JSON strings: {{prompt}}, {{negative}},
{{seed}}, {{width}}, {{height}}.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"cfg": 7, "denoise": 1, "latent_image": ["5", 0], "model": ["4", 0],
          "negative": ["7", 0], "positive": ["6", 0], "sampler_name": "euler", "scheduler": "normal",
          "seed": "{{seed}}", "steps": 25}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{checkpoint}}"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1, "height": "{{height}}", "width": "{{width}}"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "{{prompt}}"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "{{negative}}"}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "studiogen", "images": ["8", 0]}},
}


def fill_workflow(workflow: dict, **values) -> dict:
    """Replace "{{key}}" placeholders; whole-value placeholders keep their numeric type."""
    def sub(x):
        if isinstance(x, dict):
            return {k: sub(v) for k, v in x.items()}
        if isinstance(x, list):
            return [sub(v) for v in x]
        if isinstance(x, str):
            if x.startswith("{{") and x.endswith("}}") and x.count("{{") == 1:
                key = x[2:-2]
                if key in values:
                    return values[key]
            for k, v in values.items():
                x = x.replace("{{" + k + "}}", str(v))
        return x
    return sub(workflow)


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188"):
        self.base = base_url.rstrip("/")

    def _json(self, path: str, data: dict | None = None):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def generate_image(self, workflow: dict, out_path: str, timeout: int = 600) -> str:
        pid = self._json("/prompt", {"prompt": workflow})["prompt_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = self._json(f"/history/{pid}")
            if pid in hist:
                for node in hist[pid]["outputs"].values():
                    for img in node.get("images", []):
                        q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img["subfolder"],
                                                    "type": img["type"]})
                        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=60) as r:
                            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                            Path(out_path).write_bytes(r.read())
                        return out_path
                raise RuntimeError("ComfyUI finished but returned no image")
            time.sleep(1.5)
        raise TimeoutError("ComfyUI did not finish in time")


def make_portrait(client: ComfyClient, look: str, out_path: str, seed: int = 1, checkpoint: str = "",
                  workflow_path: str | None = None, width: int = 768, height: int = 1024) -> str:
    wf = json.loads(Path(workflow_path).read_text()) if workflow_path else DEFAULT_WORKFLOW
    wf = fill_workflow(wf, prompt=f"portrait photo, {look}, photorealistic, sharp focus, neutral background",
                       negative="cartoon, drawing, text, watermark, deformed", seed=seed,
                       width=width, height=height, checkpoint=checkpoint)
    return client.generate_image(wf, out_path)
