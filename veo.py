"""عميل توليد الفيديو (Veo عبر Gemini API): مقطع واحد من وصف نصي، بصوت أصلي وصور مرجعية للشخصيات.

الواجهة (حسب توثيق Gemini API لـ Veo 3.1):
  POST  {BASE}/models/{model}:predictLongRunning   ← يعيد عملية طويلة (operation)
  GET   {BASE}/{operation.name}                    ← استطلاع كل 10 ثوانٍ حتى done=true
  النتيجة: response.generateVideoResponse.generatedSamples[0].video.uri  (تنزيل بنفس المفتاح)

كل مقطع يُخزَّن مؤقتًا بحسب وصفه، فإعادة التشغيل لا تعيد الدفع على ما وُلّد سابقًا.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "veo-3.1-generate-preview"


class VeoError(Exception):
    pass


class VeoBlocked(VeoError):
    """رفضت الخدمة الوصف أو الناتج (سياسة المحتوى). لا يُحتسب عادةً."""


def base_url() -> str:
    return os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")


def _call(url: str, key: str, body: dict | None = None, timeout: int = 120) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET",
        headers={"x-goog-api-key": key, "Content-Type": "application/json", "User-Agent": "text-to-video"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _with_retry(fn, tries: int = 4):
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(20 * (attempt + 1))
                last = f"HTTP {exc.code}: {detail}"
                continue
            raise VeoError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
            if attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise VeoError(f"تعذّر الاتصال: {last}") from exc
    raise VeoError(last or "فشل غير معروف")


def _image_part(path: Path) -> dict:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return {"image": {"inlineData": {"mimeType": mime, "data": base64.b64encode(path.read_bytes()).decode()}},
            "referenceType": "asset"}


def cache_key(prompt: str, aspect: str, duration: int, resolution: str, model: str, refs) -> str:
    h = hashlib.sha1(f"{model}|{aspect}|{duration}|{resolution}|{prompt}".encode())
    for r in refs:
        h.update(Path(r).read_bytes())
    return h.hexdigest()[:24]


def generate_clip(prompt: str, *, aspect: str, key: str, cache_dir: Path, model: str = DEFAULT_MODEL,
                  duration: int = 8, resolution: str = "720p", refs=(), poll: float = 10.0,
                  timeout_s: int = 1500, log=print) -> Path:
    """يولّد مقطعًا ويعيد مساره. يستخدم المخزَّن إن وُجد."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"veo_{cache_key(prompt, aspect, duration, resolution, model, refs)}.mp4"
    if out.exists() and out.stat().st_size > 10_000:
        return out
    if not key:
        raise VeoError("المتغير GEMINI_API_KEY غير موجود")

    instance: dict = {"prompt": prompt}
    if refs:
        instance["referenceImages"] = [_image_part(Path(r)) for r in list(refs)[:3]]
        duration = 8  # الصور المرجعية تتطلب مقطعًا من 8 ثوانٍ
    params = {"aspectRatio": aspect, "resolution": resolution, "durationSeconds": duration}
    url = f"{base_url()}/models/{model}:predictLongRunning"

    def start(p):
        return json.loads(_with_retry(lambda: _call(url, key, {"instances": [instance], "parameters": p})))

    try:
        op = start(params)
    except VeoError as exc:
        if "duration" in str(exc).lower():  # بعض الإصدارات تتوقع نصًا لا رقمًا
            op = start({**params, "durationSeconds": str(duration)})
        else:
            raise
    name = op.get("name")
    if not name:
        raise VeoError(f"رد غير متوقع: {str(op)[:300]}")

    poll = float(os.environ.get("VEO_POLL", poll))
    t0 = time.time()
    while not op.get("done"):
        if time.time() - t0 > timeout_s:
            raise VeoError("انتهت مهلة انتظار الفيديو")
        time.sleep(poll)
        op = json.loads(_with_retry(lambda: _call(f"{base_url()}/{name}", key)))
    if op.get("error"):
        raise VeoError(f"فشل التوليد: {json.dumps(op['error'], ensure_ascii=False)[:400]}")

    resp = op.get("response", {}).get("generateVideoResponse", {}) or op.get("response", {})
    samples = resp.get("generatedSamples") or resp.get("videos") or []
    if not samples:
        reasons = resp.get("raiMediaFilteredReasons") or resp.get("raiMediaFilteredCount")
        raise VeoBlocked(f"لم يُنتج فيديو (غالبًا حجبته سياسة المحتوى): {reasons or str(op)[:300]}")
    uri = (samples[0].get("video") or {}).get("uri") or samples[0].get("uri")
    if not uri:
        raise VeoError(f"لا يوجد رابط فيديو في الرد: {str(samples[0])[:300]}")

    def download():
        req = urllib.request.Request(uri, headers={"x-goog-api-key": key, "User-Agent": "text-to-video"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.read()

    data = _with_retry(download)
    if len(data) < 10_000:
        raise VeoError(f"ملف الفيديو صغير بشكل مريب ({len(data)} بايت)")
    tmp = out.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(out)
    return out
