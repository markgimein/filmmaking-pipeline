#!/usr/bin/env python3
"""
FILM VOICE REFERENCE GENERATION TEMPLATE
=========================================
Creates the voice reference audio for recurring speakers. This is the LAST
step before generating the movie -- run it AFTER `make_movie.py prompts` and
AFTER the character images have been uploaded (so each character's frontal
face shot has a public CDN URL in GDRIVE_CHAR_REF_IDS).

WHO NEEDS A VOICE REFERENCE
  A character who SPEAKS in more than one segment needs a voice reference, so
  their voice stays consistent across shots that are generated independently.
  Every segment is an independent shot that attaches its own audio reference
  (continuity between continuous shots is carried by locked shared frames, not by
  chaining video), so every speaking segment counts. A character who speaks in
  only one segment does NOT need a reference.

HOW A REFERENCE IS MADE (Seedance audio -- Mark prefers it to dedicated TTS)
  1. list      Find the characters who qualify and write the list to
               audio/audio_references.txt (flagging any voice_desc that is
               missing or lacks the speaker's rough age).
  2. prompts   Write a prompt per character to audio/voice_ref_prompts.txt.
               Each clip is a 5-7 SECOND SECTION OF THE CHARACTER'S OWN
               DIALOGUE, typical of how they speak, performed IN THE CONTEXT
               OF ITS SCENE -- never "a person talking" in a void. The prompt
               carries the scene context, the character description, and an
               explicit VOICE description (rough age + GENERAL characteristics
               such as confident or careful -- general traits, never
               scene-specific moods like "worried"), and uses ONLY that
               character's frontal face shot as the single image reference
               (@image1). Distinct, well-described voices keep the cast from
               sounding alike.
  3. generate  Submit each clip to Seedance, ALWAYS at 480p (we only want the
               audio, so there is no reason to pay for more). The throwaway
               video is saved to extras/, then ONLY the audio is extracted to
               audio/<prefix>_voice_reference.mp3 -- that file is the voice
               reference.
  4. all       Run list, then prompts, then generate.

AFTER THIS RUNS
  python3 upload_images.py audio    # host the new audio refs on the CDN
  then paste the returned URLs into VOICE_REFS in film_config.py before
  generating the movie.

Usage:
  python3 generate_voices.py list
  python3 generate_voices.py prompts
  python3 generate_voices.py generate
  python3 generate_voices.py all

Before running:
  1. Copy film_config_template.py to film_config.py and fill it in.
  2. Generate + upload character images (GDRIVE_CHAR_REF_IDS populated).
  3. pip install certifi ; ffmpeg must be installed for audio extraction.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

from film_config import (
    NANOGPT_API_KEY, NANOGPT_MODEL, VIDEO_PROVIDER,
    SEEDANCE_MODEL, SEEDANCE_ASPECT, SEEDANCE_MODE,
    FILM_TITLE, CHARACTERS, LOCATIONS, SEGMENTS,
    AUDIO_DIR, EXTRAS_DIR, GDRIVE_CHAR_REF_IDS,
)

# Reuse the Seedance / Lunostudio API helpers from make_movie so this script
# stays in lock-step with the rest of the pipeline (same auth, polling, CDN).
from make_movie import (
    api_request, get_status, find_field, find_video_url, download, gdrive,
)
import nanogpt_video

# Voice reference clips are ALWAYS 480p: we keep only the audio, so higher
# resolution would be wasted spend. The clip runs 7 seconds so the character
# can deliver a 5-7 second section of their own dialogue -- a too-short clip
# reads as a screen test and the cast's voices come out too similar. These are
# intentionally fixed and do NOT read SEEDANCE_RESOLUTION.
VOICE_REF_RESOLUTION = "480p"
VOICE_REF_SECONDS = 7


# === WHO NEEDS A VOICE REFERENCE ===

def segment_speakers(seg):
    """Characters who SPEAK in a segment. Prefers an explicit "speakers" list;
    otherwise falls back to the single "voice_audio" speaker."""
    if seg.get("speakers"):
        return list(seg["speakers"])
    if seg.get("voice_audio"):
        return [seg["voice_audio"]]
    return []


def speaking_segments(prefix):
    """Segment ids in which this character speaks. Every segment is an
    independent shot that attaches its own audio reference."""
    return [s["id"] for s in SEGMENTS if prefix in segment_speakers(s)]


def characters_needing_voice_refs():
    """Characters who speak in MORE THAN ONE segment, in the order they first
    appear (each speaking shot attaches its own audio, so a recurring speaker
    needs a reference for consistency)."""
    counts = {}
    order = []
    for seg in SEGMENTS:
        for sp in segment_speakers(seg):
            if sp not in counts:
                order.append(sp)
            counts[sp] = counts.get(sp, 0) + 1
    return [p for p in order if counts[p] > 1]


# === PROMPT FOR THE VOICE CLIP ===

def voice_location_desc(prefix, seg=None):
    """Describe the location for the voice clip -- the setting of the segment
    the chosen line comes from (else the character's first speaking segment),
    else a neutral interior."""
    ordered = ([seg] if seg else []) + [
        s for s in SEGMENTS if prefix in segment_speakers(s)]
    for s in ordered:
        if s and s.get("location"):
            loc = LOCATIONS.get(s["location"], {})
            d = loc.get("desc") or loc.get("name")
            if d:
                return d.strip().rstrip(".")
    return "a quiet, neutral interior with soft, even lighting"


# Rough age can be a number ("about 45") or a decade/stage word; the check just
# catches descriptions that forgot age entirely.
_AGE_HINT = re.compile(
    r"\d{2}|\b(?:teens|twenties|thirties|forties|fifties|sixties|seventies|"
    r"eighties|young|elderly|middle-aged|old)\b", re.IGNORECASE)


def voice_description(prefix):
    """The voice description carried in the prompt. It must ALWAYS state the
    speaker's ROUGH AGE plus the voice's GENERAL characteristics (confident,
    careful, weary, brisk...) -- lasting traits of the person, never
    scene-specific moods like "worried". Set via "voice_desc" (or
    "temperament") in CHARACTERS. Distinct descriptions are what keep the
    cast's voices from sounding alike."""
    c = CHARACTERS.get(prefix, {})
    return (c.get("voice_desc") or c.get("temperament") or "").strip()


def voice_desc_warning(prefix):
    """Warning text if the voice description is missing or lacks a rough age
    -- surfaced by `list` and `prompts` so gaps are fixed BEFORE generating."""
    desc = voice_description(prefix)
    if not desc:
        return ("MISSING -- add 'voice_desc' to CHARACTERS: rough age + general "
                "characteristics (confident, careful, ...), not scene moods")
    if not _AGE_HINT.search(desc):
        return "voice_desc should state the speaker's rough age"
    return None


# Contraction/possessive apostrophes (don't, it's, K.'s) sit between word
# characters; masking them leaves only the apostrophes that delimit spoken
# dialogue, so an ACTUAL quote can be lifted cleanly from a segment's action.
_CONTRACTION_APOS = re.compile(r"(?<=[A-Za-z.])'(?=[A-Za-z])")


def _quotes_in(action):
    """Spoken quotes inside a segment's action, contractions preserved."""
    sentinel = "\x00"
    masked = _CONTRACTION_APOS.sub(sentinel, action or "")
    return [s.replace(sentinel, "'").strip()
            for s in re.findall(r"'([^']*)'", masked) if s.strip()]


def character_quote(prefix, min_words=10, max_words=18):
    """A 5-7 second SECTION of the character's actual dialogue, lifted from the
    screenplay -- a passage TYPICAL of how the character speaks, NOT a generic
    screen test. At a natural pace (~2.7 words/second) 5-7 seconds is roughly
    13-18 words, so this prefers the character's first line in that range; if
    every line is shorter or longer it takes the longest and trims to
    max_words. Override per character with "voice_ref_line" in CHARACTERS (also
    a real, typical passage from the piece). Returns (quote, segment) -- the
    segment the line comes from, so the prompt can carry the scene's context --
    or (None, None) if the character has no quoted dialogue at all."""
    candidates = []  # (quote, seg) in film order
    for seg in SEGMENTS:
        if prefix in segment_speakers(seg):
            for q in _quotes_in(seg.get("action", "")):
                candidates.append((q, seg))
    if not candidates:
        return None, None
    sized = [(q, s) for q, s in candidates
             if min_words <= len(q.split()) <= max_words]
    if sized:
        return sized[0]
    quote, seg = max(candidates, key=lambda qs: len(qs[0].split()))
    words = quote.split()
    if len(words) > max_words:
        quote = " ".join(words[:max_words]).rstrip(",;:-") + "..."
    return quote, seg


def _strip_dialogue(text):
    """Remove quoted dialogue (and its dangling speech verb) from an action
    description, so the scene-context line can't read as lines for other
    voices."""
    sentinel = "\x00"
    masked = _CONTRACTION_APOS.sub(sentinel, text or "")
    stripped = re.sub(
        r"(?:\b(?:and\s+)?(?:says?|said|saying|replie[sd]|answer(?:s|ed)?|"
        r"whisper(?:s|ed)?|shout(?:s|ed)?|add(?:s|ed)?|ask(?:s|ed)?|"
        r"finish(?:es|ed|ing)?|continu(?:es|ed|ing))[,:]?\s*)?'[^']*'[,.]?",
        "", masked)
    return re.sub(r"\s+", " ", stripped.replace(sentinel, "'")).strip()


def scene_context(seg, max_words=40):
    """A short description of the film moment the voice line comes from. The
    clip is performed IN the scene's context -- never just "a person talking"
    in a void -- which keeps the delivery in character."""
    if not seg:
        return None
    bits = []
    if seg.get("title"):
        bits.append(seg["title"].strip())
    moment = _strip_dialogue(seg.get("action") or seg.get("opening") or "")
    words = moment.split()
    if len(words) > max_words:
        moment = " ".join(words[:max_words]).rstrip(",;:-") + "..."
    if moment:
        bits.append(moment)
    return " -- ".join(bits) or None


def build_voice_prompt(prefix):
    """Prompt for a character's voice-reference clip: a 5-7 second section of
    the character's own dialogue, typical of them, performed IN CHARACTER and
    IN THE SCENE'S CONTEXT, with the voice explicitly described (rough age +
    general characteristics). Only the audio is kept."""
    c = CHARACTERS.get(prefix, {})
    name = c.get("name", prefix)
    desc = c.get("desc", "")
    voice = voice_description(prefix)

    # The line MUST be a real, TYPICAL passage from the piece. Priority: an
    # explicit "voice_ref_line" in CHARACTERS, else a section auto-pulled from
    # the screenplay, else (only with no dialogue at all) a fallback.
    quote, quote_seg = character_quote(prefix)
    override = c.get("voice_ref_line")
    if override:
        line = override
        # Find the segment the override comes from, for its scene context.
        quote_seg = next(
            (s for s in SEGMENTS if prefix in segment_speakers(s)
             and override[:24].lower() in (s.get("action") or "").lower()),
            quote_seg) or next(
            (s for s in SEGMENTS if prefix in segment_speakers(s)), None)
    else:
        line = quote or f"My name is {name}. Listen, and you will know my voice."
    location = voice_location_desc(prefix, quote_seg)
    context = scene_context(quote_seg)

    parts = [
        f"@image1 shows {name}. Match this exact face, hair, and clothing.",
        "",
        f"Setting: {location}. Quiet background, no music.",
    ]
    if context:
        parts.append(
            f"SCENE CONTEXT -- this is a moment from a film; {name} plays the "
            f"scene, not a screen test: {context}")
    parts += [
        "",
        f"CHARACTER (match this description exactly): {desc}",
    ]
    if voice:
        parts.append(
            f"VOICE (match this exactly -- the speaker's rough age and the "
            f"voice's general character): {voice}")
    parts += [
        "",
        f"In this moment of the film, speaking IN CHARACTER in {name}'s own "
        f"natural voice, {name} delivers this dialogue exactly as written: "
        f"'{line}'",
        "",
        "Exactly one person speaks -- no other voices, no narration. Deliver "
        "the dialogue as a natural performance of the scene, at a normal "
        "conversational pace, with the voice described above. The purpose is a "
        "clean recording of this single character's true speaking voice as "
        "heard in the film.",
    ]
    return "\n".join(parts)


# === GENERATION ===

def voice_payload(prefix, image_url):
    return {
        "model": SEEDANCE_MODEL,
        "prompt": build_voice_prompt(prefix),
        "duration": VOICE_REF_SECONDS,
        "aspect_ratio": SEEDANCE_ASPECT,
        "resolution": VOICE_REF_RESOLUTION,  # always 480p
        "mode": SEEDANCE_MODE,
        "reference_images": [image_url],
    }


def _luno_generate_voice(prefix, image_url, video_dest,
                         interval=20, timeout_min=45):
    """Generate the voice clip on Lunostudio Seedance. Returns True if saved."""
    try:
        resp = api_request("POST", "/api/v1/generate", voice_payload(prefix, image_url))
    except Exception as e:
        print(f"  [SUBMIT ERROR] {prefix}: {e}", flush=True)
        return False
    task_id = find_field(resp, {"task_id", "taskId", "id"})
    if not task_id:
        print(f"  [ERROR] {prefix}: no task id returned", flush=True)
        return False
    print(f"  [SUBMIT] {prefix} -> task {task_id}", flush=True)

    deadline = time.time() + timeout_min * 60
    last = None
    while time.time() < deadline:
        try:
            status, sresp = get_status(str(task_id))
        except Exception as e:
            print(f"  [POLL ERROR] {prefix}: {e}", flush=True)
            time.sleep(interval)
            continue
        if status != last:
            print(f"  [STATUS] {prefix}: {status}", flush=True)
            last = status
        if status == "success":
            url = find_video_url(sresp)
            if url and download(url, video_dest):
                return True
            print(f"  [WARN] {prefix}: success but no video URL", flush=True)
            return False
        if status == "failed":
            print(f"  [FAILED] {prefix}", flush=True)
            return False
        time.sleep(interval)
    print(f"  [TIMEOUT] {prefix}", flush=True)
    return False


def _nanogpt_generate_voice(prefix, image_url, video_dest):
    """Nano-GPT fallback (same Seedance 2 model family, different host)."""
    spec = {
        "model": NANOGPT_MODEL,
        "prompt": build_voice_prompt(prefix),
        "duration": VOICE_REF_SECONDS,
        "resolution": VOICE_REF_RESOLUTION,  # always 480p
        "aspect_ratio": SEEDANCE_ASPECT,
        "first_frame_url": image_url,
        "image_ref_urls": [image_url],
        "audio_url": None,
        "video_url": None,
        "generate_audio": True,
    }
    return nanogpt_video.generate(spec, NANOGPT_API_KEY, video_dest)


def extract_audio(video_file, audio_dest):
    """Export ONLY the audio track of the clip to an mp3 voice reference."""
    cmd = ["ffmpeg", "-y", "-i", str(video_file), "-vn",
           "-acodec", "libmp3lame", "-q:a", "2", str(audio_dest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and Path(audio_dest).exists():
        size = Path(audio_dest).stat().st_size
        print(f"  [AUDIO] {audio_dest.name} ({size / 1e3:.0f} KB)", flush=True)
        return True
    print(f"  [ERROR] could not extract audio from {Path(video_file).name}:\n"
          f"{result.stderr[-300:]}", flush=True)
    return False


def generate_voice(prefix):
    """Generate one character's voice reference: 480p clip -> extras/, then
    audio-only -> audio/<prefix>_voice_reference.mp3."""
    audio_dest = AUDIO_DIR / f"{prefix}_voice_reference.mp3"
    if audio_dest.exists():
        print(f"  [SKIP] {prefix}: voice reference already exists", flush=True)
        return True

    image_id = GDRIVE_CHAR_REF_IDS.get(f"{prefix}_front_full_face")
    if not image_id:
        print(f"  [SKIP] {prefix}: no uploaded frontal face reference "
              f"(run `python3 upload_images.py chars` first)", flush=True)
        return False
    image_url = gdrive(image_id)

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    EXTRAS_DIR.mkdir(parents=True, exist_ok=True)
    video_dest = EXTRAS_DIR / f"{prefix}_voice_ref_clip.mp4"

    ok = video_dest.exists()
    if not ok and VIDEO_PROVIDER not in ("nanogpt", "openrouter"):
        ok = _luno_generate_voice(prefix, image_url, video_dest)
        if not ok:
            print(f"  [FALLBACK] {prefix}: routing to Nano-GPT", flush=True)
    if not ok:
        ok = _nanogpt_generate_voice(prefix, image_url, video_dest)
    if not ok:
        print(f"  [FAIL] {prefix}: could not generate voice clip", flush=True)
        return False

    return extract_audio(video_dest, audio_dest)


# === MODES ===

def write_list():
    needed = characters_needing_voice_refs()
    lines = [
        f"{FILM_TITLE} -- VOICE REFERENCES NEEDED",
        "Characters who speak in MORE THAN ONE segment need a voice reference so",
        "their voice stays consistent across independently generated shots; a",
        "character who speaks only once needs no reference.",
        "=" * 70,
        "",
    ]
    if not needed:
        lines.append("(none -- no character speaks in more than one "
                     "segment, so no voice references are needed)")
    for prefix in needed:
        c = CHARACTERS.get(prefix, {})
        name = c.get("name", prefix)
        voice = voice_description(prefix) or "(missing)"
        warn = voice_desc_warning(prefix)
        lines.append(f"{prefix} -- {name}")
        lines.append(f"  speaks in segments: {speaking_segments(prefix)}")
        lines.append(f"  voice description: {voice}")
        if warn:
            lines.append(f"  !! {warn}")
        lines.append(f"  audio reference file: audio/{prefix}_voice_reference.mp3")
        lines.append("")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out = AUDIO_DIR / "audio_references.txt"
    out.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out}", flush=True)
    print(f"Characters needing a voice reference: {needed or 'none'}", flush=True)
    return needed


def write_prompts():
    needed = characters_needing_voice_refs()
    lines = [
        f"{FILM_TITLE} -- VOICE REFERENCE VIDEO PROMPTS",
        f"{VOICE_REF_SECONDS}-second in-scene clips (a 5-7s section of the "
        f"character's own dialogue, typical of them, played in the scene's "
        f"context), always {VOICE_REF_RESOLUTION} (only the audio is kept).",
        "Each clip uses ONLY the character's frontal face shot as @image1.",
        "=" * 70,
    ]
    for prefix in needed:
        img = GDRIVE_CHAR_REF_IDS.get(f"{prefix}_front_full_face")
        name = CHARACTERS.get(prefix, {}).get("name", prefix)
        lines.append("")
        lines.append(f"CHARACTER {prefix} -- {name}")
        warn = voice_desc_warning(prefix)
        if warn:
            lines.append(f"  !! {warn}")
        lines.append(f"  @image1 (frontal face): "
                     f"{gdrive(img) if img else '<upload the front_full_face ref first>'}")
        lines.append(f"  duration {VOICE_REF_SECONDS}s, resolution {VOICE_REF_RESOLUTION}")
        lines.append("")
        lines.append(build_voice_prompt(prefix))
        lines.append("-" * 70)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out = AUDIO_DIR / "voice_ref_prompts.txt"
    out.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out}", flush=True)


def run_generate():
    needed = characters_needing_voice_refs()
    if not needed:
        print("No voice references needed -- nothing to generate.", flush=True)
        return
    done = 0
    for prefix in needed:
        print(f"\n=== VOICE REFERENCE: {prefix} ===", flush=True)
        if generate_voice(prefix):
            done += 1
    print(f"\nVOICE REFERENCES DONE: {done}/{len(needed)}", flush=True)
    print("Next: `python3 upload_images.py audio`, then paste the URLs into "
          "VOICE_REFS in film_config.py.", flush=True)


def run_all():
    write_list()
    write_prompts()
    run_generate()


MODES = {
    "list": write_list,
    "prompts": write_prompts,
    "generate": run_generate,
    "all": run_all,
}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    if mode not in MODES:
        print(f"Usage: generate_voices.py {{{' | '.join(MODES)}}}")
        sys.exit(1)
    MODES[mode]()
