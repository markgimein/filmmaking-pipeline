#!/usr/bin/env python3
"""
FILM REFERENCE IMAGE UPLOAD TEMPLATE
======================================
Uploads opening stills and character reference images to Lunostudio's CDN
via their /api/v1/upload endpoint. Returns publicly accessible URLs that
can be used directly as reference_images in the Seedance 2 API.

Usage:
  python3 upload_images.py            # upload all stills + character refs
  python3 upload_images.py stills     # upload opening stills + closing (zclosing) frames
  python3 upload_images.py chars      # upload character refs only
  python3 upload_images.py audio      # upload voice reference audio only
  python3 upload_images.py all        # upload stills+closings, chars, and audio

Outputs upload_urls.json with all public URL mappings.

After running, copy the URLs into film_config.py:
  - GDRIVE_STILL_IDS (segment opening stills)
  - GDRIVE_CLOSING_IDS (continuous version: locked closing "zclosing" frames,
    keyed by the segment id that ends on them)
  - GDRIVE_CHAR_REF_IDS (every character reference angle, keyed "prefix_angle")
  - VOICE_REFS (voice reference audio)

Before running:
  1. Generate images with generate_images.py
  2. Fill in LUNO_API_KEY in film_config.py
"""

import json
import re
import ssl
import sys
import time
import uuid
from pathlib import Path
import urllib.request

import certifi

from film_config import (
    LUNO_API_KEY, BASE_DIR, STILLS_DIR, CHARACTERS_DIR, AUDIO_DIR,
    CHARACTERS, SEGMENTS, VOICE_REFS,
)

BASE_URL = "https://www.lunostudio.ai"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
OUTPUT_FILE = BASE_DIR / "upload_urls.json"


def upload_file(filepath):
    """Upload a file to Lunostudio CDN and return the public URL."""
    filepath = Path(filepath)
    with open(filepath, "rb") as f:
        file_data = f.read()

    boundary = uuid.uuid4().hex
    suffix = filepath.suffix.lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    }.get(suffix, "application/octet-stream")

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"\r\n'
        f"Content-Type: {mime}\r\n"
        f"\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        BASE_URL + "/api/v1/upload",
        data=body,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {LUNO_API_KEY}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
        result = json.loads(resp.read().decode())

    return result.get("url")


def load_urls():
    if OUTPUT_FILE.exists():
        return json.loads(OUTPUT_FILE.read_text())
    return {"stills": {}, "closings": {}, "chars": {}, "audio": {}}


def save_urls(urls):
    OUTPUT_FILE.write_text(json.dumps(urls, indent=2))


def _chosen_stills():
    """Chosen opening-still filename per segment id, from stills/manifest.txt
    (mirrors generate_images.py's manifest format). Falls back to the base
    seg<NN>_opening.jpg for any segment not listed."""
    sel = {}
    path = STILLS_DIR / "manifest.txt"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"seg0*(\d+)_opening", line)
            if m:
                sel[int(m.group(1))] = line
    return sel


def upload_stills(urls):
    print("=== UPLOADING OPENING STILLS (chosen take per stills manifest) ===")
    chosen = _chosen_stills()
    uploaded = urls.setdefault("stills_files", {})  # seg id -> filename last uploaded
    for seg in SEGMENTS:
        key = str(seg["id"])
        fname = chosen.get(seg["id"], f"seg{seg['id']:02d}_opening.jpg")
        # Skip only if THIS exact file is already on the CDN; re-upload when the
        # manifest now points at a different (re-stilled) version.
        if urls.get("stills", {}).get(key) and uploaded.get(key) == fname:
            print(f"  [SKIP] {fname} (already uploaded)")
            continue

        path = STILLS_DIR / fname
        if not path.exists():
            print(f"  [MISS] {path.name}")
            continue

        print(f"  Uploading {path.name}...", end="", flush=True)
        try:
            url = upload_file(path)
            urls.setdefault("stills", {})[key] = url
            uploaded[key] = fname
            print(" OK")
        except Exception as e:
            print(f" ERROR: {e}")

        save_urls(urls)
        time.sleep(0.3)


def upload_closings(urls):
    """Upload the locked CLOSING frames (continuous version). Each
    seg<id>_zclosing.jpg is a copy of the NEXT segment's opening still and is
    used as that segment's last_frame, so the shot ends on the exact frame the
    next shot begins on. Keyed by the segment id that OWNS the closing frame ->
    paste these into GDRIVE_CLOSING_IDS in film_config.py."""
    closing_files = sorted(STILLS_DIR.glob("seg*_zclosing.jpg"))
    if not closing_files:
        return
    print("\n=== UPLOADING CLOSING (zclosing) FRAMES ===")
    for path in closing_files:
        key = path.stem.split("_")[0].replace("seg", "").lstrip("0") or "0"
        if urls.get("closings", {}).get(key):
            print(f"  [SKIP] {path.name}")
            continue
        print(f"  Uploading {path.name}...", end="", flush=True)
        try:
            url = upload_file(path)
            urls.setdefault("closings", {})[key] = url
            print(" OK")
        except Exception as e:
            print(f" ERROR: {e}")
        save_urls(urls)
        time.sleep(0.3)


def upload_chars(urls):
    print("\n=== UPLOADING CHARACTER REFERENCES ===")
    # Upload EVERY character reference angle (face, three_quarter, full_body,
    # plus any variants) so the video prompts can attach whichever ones a shot
    # needs. The key is the file stem, e.g. "jane_front_full_face",
    # "jane_full_body" -- matching the "charprefix_angle" keys in
    # GDRIVE_CHAR_REF_IDS. The frontal face is always needed; the body shots are
    # used only where a scene calls for them.
    char_files = sorted(f for f in CHARACTERS_DIR.glob("*.jpg")
                        if not f.name.startswith("."))
    if not char_files:
        print("  [MISS] no character reference images found")
        return
    for path in char_files:
        key = path.stem
        if urls.get("chars", {}).get(key):
            print(f"  [SKIP] {key}")
            continue

        print(f"  Uploading {path.name}...", end="", flush=True)
        try:
            url = upload_file(path)
            urls.setdefault("chars", {})[key] = url
            print(" OK")
        except Exception as e:
            print(f" ERROR: {e}")

        save_urls(urls)
        time.sleep(0.3)


def upload_audio(urls):
    print("\n=== UPLOADING VOICE REFERENCES ===")
    audio_files = list(AUDIO_DIR.glob("*_voice_reference.*"))
    if not audio_files:
        audio_files = [f for f in AUDIO_DIR.iterdir()
                       if f.suffix.lower() in (".mp3", ".wav", ".m4a")
                       and not f.name.startswith(".")]

    for path in audio_files:
        key = path.stem
        if urls.get("audio", {}).get(key):
            print(f"  [SKIP] {path.name}")
            continue

        print(f"  Uploading {path.name}...", end="", flush=True)
        try:
            url = upload_file(path)
            urls.setdefault("audio", {})[key] = url
            print(" OK")
        except Exception as e:
            print(f" ERROR: {e}")

        save_urls(urls)
        time.sleep(0.3)


def main():
    urls = load_urls()
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("stills", "closings", "all"):
        upload_stills(urls)
        upload_closings(urls)
    if mode in ("chars", "all"):
        upload_chars(urls)
    if mode in ("audio", "all"):
        upload_audio(urls)

    still_count = sum(1 for v in urls.get("stills", {}).values() if v)
    closing_count = sum(1 for v in urls.get("closings", {}).values() if v)
    char_count = sum(1 for v in urls.get("chars", {}).values() if v)
    audio_count = sum(1 for v in urls.get("audio", {}).values() if v)
    print(f"\n=== SUMMARY ===")
    print(f"Stills: {still_count}/{len(SEGMENTS)}")
    print(f"Closing (zclosing) frames: {closing_count}")
    print(f"Character ref images: {char_count} (all angles across {len(CHARACTERS)} characters)")
    print(f"Audio refs: {audio_count}")
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
