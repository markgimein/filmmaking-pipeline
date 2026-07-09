#!/usr/bin/env python3
"""
OPENROUTER VIDEO GENERATION -- FALLBACK / ALTERNATE PROVIDER
============================================================
Helper for generating Seedance 2 video through OpenRouter's /api/v1/videos
endpoint instead of Lunostudio. make_movie.py uses it two ways:

  1. Automatic fallback -- when a Lunostudio (Seedance) generation fails
     twice, that segment is re-routed here.
  2. Primary provider -- when film_config.VIDEO_PROVIDER == "openrouter",
     every segment is generated here from the start.

Same model family (ByteDance Seedance 2.0), different host. OpenRouter does
NOT use Lunostudio's @-tag prompt syntax; it takes structured references:
  - opening still / continuity frame -> frame_images (frame_type first_frame)
  - character & style reference imgs  -> input_references (type image_url)
  - voice reference audio             -> input_references (type audio_url)
  - previous-scene video              -> input_references (type video_url)
Image references may be public URLs OR base64 data URLs; video references
must be public URLs.

Copy this file into the project as openrouter_video.py (both template sets).

API docs: https://openrouter.ai/docs/api/api-reference/video-generation
"""

import base64
import json
import ssl
import time
import urllib.request
from pathlib import Path

import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Seedance 2 on OpenRouter -- same model family as Lunostudio's "seedance-2".
DEFAULT_MODEL = "bytedance/seedance-2.0"

TERMINAL_OK = {"completed"}
TERMINAL_FAIL = {"failed", "cancelled", "expired"}


def _request(method, path, api_key, body=None, raw=False):
    url = OPENROUTER_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
        content = resp.read()
    return content if raw else json.loads(content.decode())


def data_url_from_file(path, mime="image/jpeg"):
    """Encode a local image as a base64 data URL so it can be passed as a
    reference without hosting it anywhere (used for continuity frames)."""
    raw = Path(path).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _image_ref(url, frame_type=None):
    item = {"type": "image_url", "image_url": {"url": url}}
    if frame_type:
        item["frame_type"] = frame_type
    return item


def build_payload(spec):
    """Build the OpenRouter /api/v1/videos request body from a normalized
    spec dict produced by make_movie.py. Recognized keys:

      model, prompt, duration, resolution, aspect_ratio   -- core params
      first_frame_url  -- opening still or continuity frame (str | None)
      last_frame_url   -- locked closing frame for a continued segment (str | None)
      image_ref_urls   -- character/style reference image URLs (list)
      audio_urls       -- voice reference audio URLs, one per speaker (list)
      audio_url        -- single voice reference audio URL (str | None; back-compat)
      video_url        -- previous-scene video URL for continuity (str | None)
      generate_audio   -- bool (default True)
    """
    frame_images = []
    input_references = []

    if spec.get("first_frame_url"):
        frame_images.append(_image_ref(spec["first_frame_url"], "first_frame"))
    if spec.get("last_frame_url"):
        frame_images.append(_image_ref(spec["last_frame_url"], "last_frame"))
    for url in spec.get("image_ref_urls") or []:
        input_references.append(_image_ref(url))
    if spec.get("video_url"):
        input_references.append(
            {"type": "video_url", "video_url": {"url": spec["video_url"]}}
        )
    audios = spec.get("audio_urls") or ([spec["audio_url"]] if spec.get("audio_url") else [])
    for _audio in audios:
        input_references.append(
            {"type": "audio_url", "audio_url": {"url": _audio}}
        )

    payload = {
        "model": spec.get("model", DEFAULT_MODEL),
        "prompt": spec["prompt"],
        "duration": spec["duration"],
        "aspect_ratio": spec["aspect_ratio"],
        "resolution": spec["resolution"],
        "generate_audio": spec.get("generate_audio", True),
    }
    if frame_images:
        payload["frame_images"] = frame_images
    if input_references:
        payload["input_references"] = input_references
    return payload


def submit(spec, api_key):
    """Submit a job. Returns (job_id, raw_response)."""
    resp = _request("POST", "/videos", api_key, build_payload(spec))
    return resp.get("id"), resp


def poll(job_id, api_key):
    """Return (status, raw_response) for a job."""
    resp = _request("GET", f"/videos/{job_id}", api_key)
    return str(resp.get("status", "unknown")).lower(), resp


def download(job_id, api_key, dest, index=0):
    """Download the finished video bytes to dest. Returns True on success."""
    content = _request(
        "GET", f"/videos/{job_id}/content?index={index}", api_key, raw=True
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest.stat().st_size > 0


def generate(spec, api_key, dest, interval=20, timeout_min=45, log=print):
    """Full lifecycle: submit -> poll to a terminal state -> download.
    Returns True if a video file was saved to dest, else False."""
    try:
        job_id, _ = submit(spec, api_key)
    except Exception as e:
        log(f"  [OPENROUTER SUBMIT ERROR] {e}")
        return False
    if not job_id:
        log("  [OPENROUTER] no job id returned")
        return False
    log(f"  [OPENROUTER] job {job_id} submitted")

    deadline = time.time() + timeout_min * 60
    last = None
    while time.time() < deadline:
        try:
            status, resp = poll(job_id, api_key)
        except Exception as e:
            log(f"  [OPENROUTER POLL ERROR] {e}")
            time.sleep(interval)
            continue
        if status != last:
            log(f"  [OPENROUTER] job {job_id}: {status}")
            last = status
        if status in TERMINAL_OK:
            try:
                if download(job_id, api_key, dest):
                    log(f"  [OPENROUTER SAVED] {dest}")
                    return True
                log(f"  [OPENROUTER] empty download for job {job_id}")
                return False
            except Exception as e:
                log(f"  [OPENROUTER DOWNLOAD ERROR] {e}")
                return False
        if status in TERMINAL_FAIL:
            log(f"  [OPENROUTER FAILED] job {job_id}: {json.dumps(resp)[:300]}")
            return False
        time.sleep(interval)
    log(f"  [OPENROUTER TIMEOUT] job {job_id}")
    return False
