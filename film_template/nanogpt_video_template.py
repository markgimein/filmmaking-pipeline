#!/usr/bin/env python3
"""
NANO-GPT VIDEO GENERATION + UPSCALING -- BACKUP / ALTERNATE PROVIDER
====================================================================
Helper for generating Seedance 2 video and for upscaling finished clips
through Nano-GPT's video API instead of Lunostudio. make_movie.py and
generate_voices.py use it three ways:

  1. Automatic fallback -- when a Lunostudio (Seedance) generation fails
     MAX_LUNO_ATTEMPTS times, that segment is re-routed here.
  2. Primary provider -- when film_config.VIDEO_PROVIDER == "nanogpt",
     every segment is generated here from the start.
  3. Upscaling -- `upscale()` enlarges a finished local clip to 720p/1080p/2K/4K
     with SeedVR2 (model `seedvr2-video-upscaler`), the default upscaler.

Same model family as Lunostudio (ByteDance Seedance 2.0), different host.
Nano-GPT does NOT use Lunostudio's @-tag prompt syntax; like OpenRouter it
takes a plain prompt plus reference URLs:
  - opening still / continuity start frame -> imageUrl  (image-to-video)
  - locked closing frame for a continued shot -> last_image
  - character & style reference images       -> reference_images  (JSON array)
  - voice reference audio                     -> reference_audios  (JSON array)
  - previous-scene video                      -> reference_videos  (JSON array)

IMPORTANT -- file storage: every reference Nano-GPT consumes must be at a
PUBLIC URL (it will not accept base64 data URLs the way OpenRouter did). The
project's stills/char-refs/voice audio are already hosted on the Lunostudio
CDN by upload_images.py, so those URLs work as-is. Anything that only exists
locally -- a continuity frame extracted from a previous clip, or a local clip
being upscaled -- is uploaded to litterbox (litterbox.catbox.moe) first to get
a temporary public URL. Litterbox files auto-expire (default 72h), so nothing
is left hosted permanently.

Copy this file into the project as nanogpt_video.py (both template sets).

API (https://nano-gpt.com):
  - Submit:   POST /api/generate-video        -> { runId / id / requestId, ... }
  - Status:   GET  /api/video/status?runId=.. -> { data: { status, output } }
  - Download: GET the output.video.url returned on completion
Auth: `Authorization: Bearer <NANOGPT_API_KEY>` (x-api-key also accepted).
Model list: GET /api/v1/video-models
"""

import json
import ssl
import time
import urllib.request
import uuid
from pathlib import Path

import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
NANOGPT_BASE = "https://nano-gpt.com"
LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"

# Seedance 2.0 on Nano-GPT -- same model family as Lunostudio's "seedance-2".
# "doubao-seedance-2-0" is the plain Seedance 2.0 (image-to-video, supports the
# full reference set below). Faster/alternate ids: "doubao-seedance-2-0-fast",
# "bytedance-seedance-2-0" (Turbo). For a variant with guaranteed native audio
# generation use "bytedance/seedance-2.0/image-to-video-spicy".
DEFAULT_MODEL = "doubao-seedance-2-0"

# Default upscaler. SeedVR2 supports 720p / 1080p / 2k / 4k output.
DEFAULT_UPSCALE_MODEL = "seedvr2-video-upscaler"

TERMINAL_OK = {"completed", "complete", "succeeded", "success"}
TERMINAL_FAIL = {"failed", "error", "canceled", "cancelled", "expired"}


# === HTTP ===

def _request(method, path, api_key, body=None, raw=False, timeout=300):
    url = NANOGPT_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("x-api-key", api_key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
        content = resp.read()
    return content if raw else json.loads(content.decode())


# === LITTERBOX FILE STORAGE ===
# Local files that need a public URL for Nano-GPT (continuity frames, clips to
# upscale) are staged here. Files auto-expire (default 72h).

def litterbox_upload(path, expiry="72h", attempts=3, log=print):
    """Upload a local file to litterbox and return its temporary public URL,
    or None on failure. expiry: one of 1h, 12h, 24h, 72h."""
    path = Path(path)
    boundary = uuid.uuid4().hex
    file_data = path.read_bytes()
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="reqtype"\r\n\r\nfileupload\r\n',
        f'--{boundary}\r\nContent-Disposition: form-data; name="time"\r\n\r\n{expiry}\r\n',
        (f'--{boundary}\r\nContent-Disposition: form-data; name="fileToUpload"; '
         f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'),
    ]
    body = "".join(parts).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(LITTERBOX_API, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            with urllib.request.urlopen(req, timeout=540, context=SSL_CONTEXT) as resp:
                url = resp.read().decode().strip()
            if url.startswith("https://") and "catbox.moe" in url and "<" not in url:
                return url
            log(f"  [LITTERBOX] attempt {attempt}: bad response {url[:80]!r}")
        except Exception as e:
            log(f"  [LITTERBOX] attempt {attempt} error: {e}")
        time.sleep(3)
    return None


def public_url_from_file(path, expiry="72h", log=print):
    """Stage a local image/clip on litterbox so it can be passed to Nano-GPT as
    a reference URL. Replaces OpenRouter's base64 data-URL approach (Nano-GPT
    requires real URLs). Returns the public URL or None."""
    return litterbox_upload(path, expiry=expiry, log=log)


# === REQUEST BODY ===

def _json_array(urls):
    """Nano-GPT's reference_* fields take a JSON-encoded array string."""
    return json.dumps([u for u in urls if u])


def build_payload(spec):
    """Build the POST /api/generate-video body from a normalized spec dict
    (the same dict make_movie.py builds for the OpenRouter client). Keys:

      model, prompt, duration, resolution, aspect_ratio   -- core params
      first_frame_url  -- opening still / continuity start frame (str | None)
      last_frame_url   -- locked closing frame for a continued segment (str | None)
      image_ref_urls   -- character/style reference image URLs (list)
      audio_urls       -- voice reference audio URLs, one per speaker (list)
      audio_url        -- single voice reference audio URL (str | None; back-compat)
      video_url        -- previous-scene video URL for continuity (str | None)
      generate_audio   -- bool (default True)
    """
    payload = {
        "model": spec.get("model", DEFAULT_MODEL),
        "prompt": spec["prompt"],
        "duration": spec["duration"],
        "resolution": spec["resolution"],
        "aspect_ratio": spec["aspect_ratio"],
    }
    if spec.get("first_frame_url"):
        payload["imageUrl"] = spec["first_frame_url"]
    if spec.get("last_frame_url"):
        payload["last_image"] = spec["last_frame_url"]
    if spec.get("image_ref_urls"):
        payload["reference_images"] = _json_array(spec["image_ref_urls"])
    audios = spec.get("audio_urls") or ([spec["audio_url"]] if spec.get("audio_url") else [])
    if audios:
        payload["reference_audios"] = _json_array(audios)
    if spec.get("video_url"):
        payload["reference_videos"] = _json_array([spec["video_url"]])
    if spec.get("generate_audio", True):
        # camelCase is Nano-GPT's switch name on audio-capable Seedance models.
        payload["generateAudio"] = True
    return payload


# === JOB LIFECYCLE ===

def _run_id(resp):
    for key in ("runId", "run_id", "id", "requestId", "request_id", "jobId"):
        val = resp.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def submit(spec, api_key):
    """Submit a generation job. Returns (run_id, raw_response)."""
    resp = _request("POST", "/api/generate-video", api_key, build_payload(spec))
    return _run_id(resp), resp


def submit_upscale(video_url, api_key, model=DEFAULT_UPSCALE_MODEL, resolution="720p"):
    """Submit an upscale job for a hosted video. Returns (run_id, raw)."""
    body = {"model": model, "videoUrl": video_url, "resolution": resolution}
    resp = _request("POST", "/api/generate-video", api_key, body)
    return _run_id(resp), resp


def poll(run_id, api_key):
    """Return (status, output_url, raw_response) for a job. status is lowered;
    output_url is the finished video URL (str) or None."""
    resp = _request("GET", f"/api/video/status?runId={run_id}", api_key,
                    timeout=120)
    inner = resp.get("data", resp) if isinstance(resp, dict) else {}
    if not isinstance(inner, dict):
        inner = {}
    status = str(inner.get("status") or resp.get("status") or "unknown").lower()
    url = _extract_output_url(inner) or _extract_output_url(resp)
    return status, url, resp


def _extract_output_url(obj):
    if not isinstance(obj, dict):
        return None
    out = obj.get("output")
    if isinstance(out, dict):
        vid = out.get("video")
        if isinstance(vid, dict) and vid.get("url"):
            return vid["url"]
        urls = out.get("videoUrls")
        if isinstance(urls, list) and urls:
            return urls[0]
    for key in ("videoUrl", "video_url", "url", "outputUrl", "output_url"):
        if isinstance(obj.get(key), str) and obj[key]:
            return obj[key]
    return None


def download(url, dest):
    """Download finished video bytes from a URL to dest. Returns True on success."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=600, context=SSL_CONTEXT) as resp, \
            open(dest, "wb") as f:
        f.write(resp.read())
    return dest.stat().st_size > 0


def _await_job(run_id, api_key, dest, interval, timeout_min, log, tag):
    deadline = time.time() + timeout_min * 60
    last = None
    while time.time() < deadline:
        try:
            status, url, resp = poll(run_id, api_key)
        except Exception as e:
            log(f"  [{tag} POLL ERROR] {e}")
            time.sleep(interval)
            continue
        if status != last:
            log(f"  [{tag}] job {run_id}: {status}")
            last = status
        if status in TERMINAL_OK:
            if not url:
                log(f"  [{tag}] completed but no output URL for {run_id}")
                return False
            try:
                if download(url, dest):
                    log(f"  [{tag} SAVED] {dest}")
                    return True
                log(f"  [{tag}] empty download for {run_id}")
                return False
            except Exception as e:
                log(f"  [{tag} DOWNLOAD ERROR] {e}")
                return False
        if status in TERMINAL_FAIL:
            log(f"  [{tag} FAILED] job {run_id}: {json.dumps(resp)[:300]}")
            return False
        time.sleep(interval)
    log(f"  [{tag} TIMEOUT] job {run_id}")
    return False


def generate(spec, api_key, dest, interval=20, timeout_min=45, log=print):
    """Full lifecycle: submit -> poll to a terminal state -> download.
    Returns True if a video file was saved to dest, else False. Signature
    matches openrouter_video.generate so callers can swap the import."""
    try:
        run_id, _ = submit(spec, api_key)
    except Exception as e:
        log(f"  [NANOGPT SUBMIT ERROR] {e}")
        return False
    if not run_id:
        log("  [NANOGPT] no run id returned")
        return False
    log(f"  [NANOGPT] job {run_id} submitted")
    return _await_job(run_id, api_key, dest, interval, timeout_min, log, "NANOGPT")


def upscale(video_file, dest, api_key, resolution="720p",
            model=DEFAULT_UPSCALE_MODEL, interval=20, timeout_min=45,
            expiry="72h", log=print):
    """Upscale a LOCAL video clip and save the result to dest. The clip is
    uploaded to litterbox to obtain a public URL, then run through the Nano-GPT
    upscaler (SeedVR2 by default). Returns True if dest was written."""
    video_file = Path(video_file)
    log(f"  [UPSCALE] staging {video_file.name} on litterbox...")
    url = litterbox_upload(video_file, expiry=expiry, log=log)
    if not url:
        log(f"  [UPSCALE] could not host {video_file.name}")
        return False
    try:
        run_id, _ = submit_upscale(url, api_key, model=model, resolution=resolution)
    except Exception as e:
        log(f"  [UPSCALE SUBMIT ERROR] {e}")
        return False
    if not run_id:
        log("  [UPSCALE] no run id returned")
        return False
    log(f"  [UPSCALE] job {run_id} submitted ({resolution}, {model})")
    return _await_job(run_id, api_key, dest, interval, timeout_min, log, "UPSCALE")
