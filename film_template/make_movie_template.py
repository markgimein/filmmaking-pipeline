#!/usr/bin/env python3
"""
FILM VIDEO GENERATION TEMPLATE
================================
Generates video segments via the Seedance 2 model on Lunostudio.

This single template produces TWO versions of a film from the same config:

  STANDARD (default) -- every segment is an independent shot with its own
    locked opening still (@image1). All segments generate in parallel.

  CONTINUOUS (opt-in, append the word `continuous` to the command) -- continuous
    action or dialogue is held together with LOCKED SHARED FRAMES, not the
    Seedance extend feature (which drifts and degrades over a chain). A segment
    that flows on from the one before it is marked "continues_previous": True.
    Every segment -- continuation or not -- is still an independent shot with its
    own opening still (@image1). The trick: the continuation's opening still is
    ALSO used as the PREVIOUS segment's locked LAST frame (its "zclosing" image).
    So the previous shot is generated to END exactly on that frame, and the
    continuation is generated to BEGIN on the same frame -- the cut is invisible.
    No video is reused, nothing extends anything, and because each shot is a
    single fresh generation there is no accumulated drift. All segments still
    generate IN PARALLEL.

Whether a film is STANDARD or CONTINUOUS is decided UP FRONT, when the user
describes the film, and the screenplay is written accordingly (see README). A
standard film leaves "continues_previous" off every segment and is generated
with the plain commands; a continuous film flags its continuation segments and
is generated with the `continuous` commands. The continuous output is kept in
its own files (videos_continuous/, seedance_tasks_continuous.json,
seedance_prompts_continuous.txt) so it never clobbers a standard run.

OVERRIDE: If the user gives different specific rules for video creation in their
prompt, those rules win over the defaults here and in the README.

A "continues_previous" segment should keep the same location as the segment it
continues (and normally the same characters), so the shared frame is coherent;
this script reports any continuation that changes location.

For each segment, the request carries:
  - @image1  = the segment's opening still (locked first frame)
  - @image2+ = character reference images (faces must match)
  - @audio1  = voice reference audio (segments where a character speaks)
  - last_frame = the locked closing still (its "zclosing" image), attached ONLY
    to a segment that is FOLLOWED by a continuation, so it ends on the exact
    frame the next shot begins on.
All reference media must be publicly accessible URLs (Lunostudio CDN
upload URLs or any other public hosting).

Usage (append `continuous` to operate on the continuous version):
  python3 make_movie.py prompts              # write seedance_prompts.txt
  python3 make_movie.py prompts continuous   # write seedance_prompts_continuous.txt
  python3 make_movie.py generate             # generate the standard movie
  python3 make_movie.py generate continuous  # generate the continuous version
  python3 make_movie.py status [continuous]  # show status of all tasks
  python3 make_movie.py fetch  [continuous]  # poll/download / resume
  python3 make_movie.py renumber [continuous]# renumber segments to a clean 1..N (run before stitch)
  python3 make_movie.py stitch [continuous]  # concatenate downloaded segments with ffmpeg
  python3 make_movie.py palmier [continuous] # export a Palmier Pro import manifest (assemble in the editor instead of stitching)
  python3 make_movie.py report [continuous]  # print a generation report
  python3 make_movie.py list   [continuous]  # write numbered segment list to screenplay/
  python3 make_movie.py failures [continuous]# export failed prompts + assets

Before running:
  1. Copy film_config_template.py to this folder as film_config.py
  2. Fill in all [FILL IN] sections, including GDRIVE_STILL_IDS and
     GDRIVE_CHAR_REF_IDS (after generating and uploading images)
  3. pip install certifi

API docs: https://www.lunostudio.ai/developers/seedance-2
"""

import ast
import concurrent.futures
import json
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import certifi

# Import the film-specific configuration
from film_config import (
    LUNO_API_KEY, NANOGPT_API_KEY, NANOGPT_MODEL, VIDEO_PROVIDER,
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    UPSCALE_MODEL, UPSCALE_RESOLUTION,
    FILM_TITLE, FILM_SLUG,
    SEEDANCE_MODEL, SEEDANCE_RESOLUTION, SEEDANCE_ASPECT, SEEDANCE_MODE,
    BASE_DIR, VIDEOS_DIR, SCREENPLAY_DIR,
    CHARACTERS, VISUAL_STYLES, SEGMENTS, VOICE_REFS, PRONUNCIATIONS,
    GDRIVE_STILL_IDS, GDRIVE_CHAR_REF_IDS,
)

# Locked closing frames (continuous version). Maps a segment id -> the public URL
# of its "zclosing" still (a copy of the NEXT segment's opening still), used as
# that segment's last_frame. Optional so standard configs need not define it.
try:
    from film_config import GDRIVE_CLOSING_IDS
except ImportError:
    GDRIVE_CLOSING_IDS = {}

# Provider for KEYFRAME (first-and-last-frame) segments. In a continuous film, a
# segment that is FOLLOWED by a continuation carries a locked last_frame and must
# be generated in Seedance KEYFRAME mode: first frame + last frame + audio, and
# NO character reference images -- keyframe mode is mutually exclusive with
# reference images (attach any and Seedance ignores the last_frame, so the seam
# drifts). Only Nano-GPT reliably honors the locked last_frame (Lunostudio drops
# it; OpenRouter rejects photoreal faces), so these join segments are routed to
# Nano-GPT regardless of VIDEO_PROVIDER. Set KEYFRAME_PROVIDER = None in
# film_config.py to disable the routing (join segments then follow VIDEO_PROVIDER
# and the locked end frame is NOT honored).
try:
    from film_config import KEYFRAME_PROVIDER
except ImportError:
    KEYFRAME_PROVIDER = "nanogpt"

import nanogpt_video
# Legacy alternate host; only used when VIDEO_PROVIDER == "openrouter".
try:
    import openrouter_video
except ImportError:
    openrouter_video = None

# === PARALLEL SUBMISSION THROTTLE ===
# Every segment is an independent shot and generates in parallel (continuity is
# carried by locked shared frames, not by chaining videos). Submissions are
# spaced out so concurrent requests stay within Lunostudio's 25 req/min limit.
SUBMIT_SPACING = 3.0         # min seconds between Lunostudio API submissions
_SAVE_LOCK = threading.Lock()
_SUBMIT_LOCK = threading.Lock()
_LAST_SUBMIT = [0.0]


def _throttle_submit():
    """Block until at least SUBMIT_SPACING has elapsed since the last Lunostudio
    API call, so concurrent chains stay within the 25 req/min rate limit. Only
    the brief spacing wait is serialized; the API call itself runs unlocked."""
    with _SUBMIT_LOCK:
        wait = SUBMIT_SPACING - (time.time() - _LAST_SUBMIT[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_SUBMIT[0] = time.time()

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

BASE_URL = "https://www.lunostudio.ai"

# Number of Lunostudio attempts before a segment is re-routed to Nano-GPT.
MAX_LUNO_ATTEMPTS = 2
SEG_BY_ID = {s["id"]: s for s in SEGMENTS}

# === VERSION MODE (standard vs. continuous) ===
# Set in __main__ from the optional `continuous` command token. When True, the
# script operates on the continuous version: separate output paths and the
# locked-frame continuity handling (continues_previous / last_frame). Functions
# read this global; it defaults to the standard version.
CONTINUOUS = False

OPENING_RULE = (
    "@image1 is the locked OPENING FRAME of this shot. The video must BEGIN on "
    "@image1 -- the first frame identical to it -- and the action continues from "
    "that exact composition."
)

CONTINUE_RULE = (
    "CONTINUITY: this shot CONTINUES the previous scene with no cut. Its opening "
    "frame @image1 is identical to the frame the previous shot ended on, so match "
    "that framing, lighting, character positions, and camera exactly and carry "
    "the action straight on -- do not restart or recompose."
)

CLOSING_RULE = (
    "LOCKED CLOSING FRAME: a locked end frame is provided (last_frame). The video "
    "MUST arrive at and end on that EXACT composition by its final frame, so the "
    "next shot can continue seamlessly from it."
)

# Pacing is NOT injected as a standing prompt rule -- a generic "speak faster"
# instruction just confuses Seedance. Pacing comes from two things the segment
# already carries: (1) the target DURATION (`seconds`, sent as the clip length
# and named in the ACTION AND SOUND header) and (2) the ACTION text itself,
# which integrates speech and action in order (speech-then-action,
# action-then-speech, or dialogue-during-action). Given a target time and a
# clear sequence of what happens, Seedance paces the shot on its own. Budget
# `seconds` well (see the README's Pacing section) and write the action so the
# ordering is explicit.


# === VERSION-AWARE PATHS ===
# The continuous version writes to its own files so it never clobbers the
# standard version's videos, tasks, or prompts.

def videos_dir():
    return (BASE_DIR / "videos_continuous") if CONTINUOUS else VIDEOS_DIR


def tasks_file():
    name = "seedance_tasks_continuous.json" if CONTINUOUS else "seedance_tasks.json"
    return BASE_DIR / name


def prompts_file():
    name = "seedance_prompts_continuous.txt" if CONTINUOUS else "seedance_prompts.txt"
    return BASE_DIR / name


def video_path(seg_id):
    return videos_dir() / f"seg{seg_id:02d}.mp4"


# Segment ids that are FOLLOWED by a continues_previous segment -- they receive a
# locked last_frame (a copy of the next segment's opening still, the "zclosing"
# image). Computed from list order; gated by the CONTINUOUS flag at call time.
CONTINUED_IDS = {
    SEGMENTS[i - 1]["id"] for i in range(1, len(SEGMENTS))
    if SEGMENTS[i].get("continues_previous")
}


def continues_prev(seg):
    """True (continuous version only) for a segment that continues the previous
    one with no cut. It is still a normal independent shot: its opening still is
    the first frame, and that frame is identical to the previous segment's locked
    closing frame, so the join is seamless."""
    return CONTINUOUS and bool(seg.get("continues_previous"))


def is_continued(seg):
    """True (continuous version only) for a segment that is FOLLOWED by a
    continuation -- it gets a locked last_frame (its zclosing still) so the next
    shot picks up on the exact frame it ends on."""
    return CONTINUOUS and seg["id"] in CONTINUED_IDS


def gdrive(file_id):
    # Reference media lives on the Lunostudio CDN. Values that are already
    # public URLs (CDN upload URLs) are passed through unchanged; only bare
    # Google Drive file IDs are wrapped in a Drive download URL.
    if isinstance(file_id, str) and file_id.startswith("http"):
        return file_id
    return f"https://drive.google.com/uc?export=download&id={file_id}"


# Standing instruction added to every Seedance prompt so the video model
# says character names, place names, and unusual/foreign words correctly.
NAME_RULE = (
    "NAMES & PRONUNCIATION: Reproduce every proper name and unusual or "
    "foreign word in the spoken dialogue EXACTLY as written -- do not "
    "anglicize, translate, abbreviate, or drop syllables."
)


# Contraction/possessive apostrophes (don't, it's, K.'s) sit between word
# characters; dropping them leaves only the apostrophes that delimit spoken
# dialogue, so quoted speech can be extracted cleanly.
_CONTRACTION_APOS = re.compile(r"(?<=[A-Za-z.])'(?=[A-Za-z])")


def spoken_dialogue(seg):
    """Return only the SPOKEN dialogue from a segment's action -- the text
    inside single quotes. Names that appear only in stage directions are
    therefore not treated as spoken."""
    action = seg.get("action", "") or ""
    masked = _CONTRACTION_APOS.sub("", action)
    spans = re.findall(r"'([^']*)'", masked)
    return " ".join(spans)


# NOTE: there is no separate DIALOGUE DIRECTION section. Who the speaker is
# addressing must be stated IN THE SEGMENT'S ACTION TEXT itself (e.g. "Jane,
# facing Tom, says: '...'"), or Seedance defaults to having the character talk
# to the camera. Write it into `action` when filling in film_config.py.


def _respell_spoken(action):
    """Replace each glossary term (PRONUNCIATIONS) with its phonetic respelling
    INSIDE the spoken dialogue. Returns (new_action, used_terms).

    Why in the dialogue and not just a side note: Seedance voices the literal
    quoted dialogue text, and a standalone "pronounce X as Y" note does NOT
    reliably override how it says the actual words. Spelling the name
    phonetically in the words the character SAYS is far more reliable.

    The substitution happens only within the single-quoted dialogue, so stage
    directions and character labels keep the real, correctly-spelled name (the
    model still knows who is who visually); only the spoken words change.
    Contraction/possessive apostrophes are masked first so they are not mistaken
    for dialogue delimiters -- so "Hermia's" becomes "<respelling>'s"."""
    action = action or ""
    if not action:
        return action, []
    used = []
    sentinel = "\x00"
    masked = _CONTRACTION_APOS.sub(sentinel, action)

    def repl(m):
        span = m.group(1)
        for term, pron in PRONUNCIATIONS.items():
            span, n = re.subn(rf"\b{re.escape(term)}\b", pron, span,
                              flags=re.IGNORECASE)
            if n and term not in used:
                used.append(term)
        return "'" + span + "'"

    out = re.sub(r"'([^']*)'", repl, masked).replace(sentinel, "'")
    return out, used


def spoken_action(seg):
    """The segment's `action` with glossary names respelled phonetically inside
    the spoken dialogue (see _respell_spoken). Always emit THIS into a Seedance
    prompt's ACTION AND SOUND line -- never the raw seg['action']."""
    new, _ = _respell_spoken(seg.get("action", "") or "")
    return new


def name_note(seg):
    """Reinforcement block listing the phonetic respellings that were injected
    into THIS segment's spoken dialogue, so the model treats them as exact
    pronunciations to read verbatim. Returns [] when no glossary term is spoken.
    It lists only the respelled tokens (never the original spelling), so the
    model is not tempted to 'correct' the dialogue back to the standard form."""
    _, used = _respell_spoken(seg.get("action", "") or "")
    if not used:
        return []
    block = [NAME_RULE + " The dialogue spells these names PHONETICALLY on "
             "purpose; say each EXACTLY as written in the dialogue:"]
    for term in used:
        block.append(f"  - {PRONUNCIATIONS[term]}")
    return block


# === EXTEND ELIGIBILITY ===

def validate_continuations():
    """Inspect every segment flagged continues_previous. Returns (joins, problems)
    where joins is a list of (continuation_id, previous_id) pairs and problems is
    a list of (seg_id, reason) for continuations that look incoherent (the first
    segment, or a location change that would make the shared frame jump). These
    are warnings only -- the segment still generates as a normal shot."""
    joins, problems = [], []
    for i, seg in enumerate(SEGMENTS):
        if not seg.get("continues_previous"):
            continue
        if i == 0:
            problems.append((seg["id"], "first segment cannot continue a previous one"))
            continue
        prev = SEGMENTS[i - 1]
        if (seg.get("location") != prev.get("location")
                or seg.get("location_variant") != prev.get("location_variant")):
            problems.append((seg["id"],
                             f"location changes from segment {prev['id']} -- the shared "
                             f"frame may not be coherent"))
        joins.append((seg["id"], prev["id"]))
    return joins, problems


def print_continuation_report(joins, problems):
    print(f"\n{'=' * 70}")
    print(f"{FILM_TITLE} -- CONTINUOUS VERSION: LOCKED-FRAME CONTINUATIONS")
    print(f"{'=' * 70}")
    if joins:
        for cid, pid in joins:
            seg = SEG_BY_ID[cid]
            print(f"  JOIN  segment {cid:02d} continues {pid:02d} "
                  f"(seg {cid:02d}'s opening = seg {pid:02d}'s locked last frame) "
                  f"-- {seg['title']}")
    else:
        print("  (none -- no continuations; the continuous version matches "
              "the standard cut)")
    if problems:
        print("\n  Continuation warnings:")
        for sid, reason in problems:
            print(f"  - segment {sid:02d}: {reason}")
    print()


def save_continuation_list(joins, problems):
    """Persist the list of locked-frame continuations so the user has a record of
    exactly which cuts the continuous version joins seamlessly."""
    lines = [
        f"{FILM_TITLE} -- continuous version: locked-frame continuations",
        "",
        "Each continuation below is a normal independent shot whose OPENING still",
        "is also used as the PREVIOUS segment's locked LAST frame (its zclosing",
        "image). The previous shot ends on that exact frame and the continuation",
        "begins on it, so the cut is seamless -- no video extension, no drift.",
        "=" * 70,
        "",
    ]
    if joins:
        for cid, pid in joins:
            seg = SEG_BY_ID[cid]
            lines.append(f"segment {cid:02d}  continues {pid:02d}  -- {seg['title']}")
    else:
        lines.append("(no continuations)")
    if problems:
        lines += ["", "Continuation warnings:"]
        for sid, reason in problems:
            lines.append(f"segment {sid:02d}: {reason}")
    out = BASE_DIR / "continuous_segments.txt"
    out.write_text("\n".join(lines) + "\n")
    print(f"Saved continuation list -> {out}", flush=True)


# === PROMPT BUILDER ===

# Character references attached to a segment's video prompt. The frontal FACE
# (front_full_face) is ALWAYS attached; beyond that, attach a body shot only
# where it adds information the opening still (@image1) does not already show.
# Default: face + a head-and-shoulders (three_quarter) view -- "most scenes use
# face + head-and-shoulders or face + full body." Use all three only where
# necessary, and if the opening still is itself a head-and-shoulders or full-body
# shot, drop that framing here so it is not duplicated. Override per character
# with a segment's "char_ref_angles" map, e.g.
#   "char_ref_angles": {"jane": ["front_full_face", "full_body"]}
# or just ["front_full_face"] when the still already carries the body.
DEFAULT_CHAR_REF_ANGLES = ["front_full_face", "three_quarter"]


def char_ref_angles_for(key, angle_map):
    """The character reference angles to attach for `key`, always including the
    frontal face. `angle_map` is a segment's optional char_ref_angles dict
    (character prefix -> list of angle names)."""
    angles = list(angle_map.get(key, DEFAULT_CHAR_REF_ANGLES))
    if "front_full_face" not in angles:
        angles.insert(0, "front_full_face")
    return angles


# === AUDIO / VOICE REFERENCES (one OR MORE per segment) ===
# A segment may attach the voice reference of more than one speaker. List the
# speakers, in order, in `voice_audios` (a list of character keys); each maps to
# its own @audio1, @audio2, ... A single speaker can still use `voice_audio`
# (a string). Only keys that actually have a VOICE_REFS entry are attached, and
# order is preserved so the @audioN indices are stable.

def audio_keys_for(seg):
    """Ordered character keys whose voice reference audio attaches to this
    segment (each -> its own @audioN). Uses `voice_audios` (list) if present,
    else `voice_audio` (single); keeps only keys present in VOICE_REFS."""
    raw = seg.get("voice_audios")
    if raw is None:
        single = seg.get("voice_audio")
        raw = [single] if single else []
    keys = []
    for k in raw:
        if k and k in VOICE_REFS and k not in keys:
            keys.append(k)
    return keys


def audio_urls_for(seg):
    """Public CDN URLs for this segment's voice references, in @audioN order."""
    return [gdrive(VOICE_REFS[k]) for k in audio_keys_for(seg)]


def audio_index_map(seg):
    """character key -> its 1-based @audioN index for this segment."""
    return {k: i for i, k in enumerate(audio_keys_for(seg), 1)}


def voice_reference_note(seg, ref=lambda i: f"@audio{i}"):
    """Prompt lines mapping each voice reference to the character who speaks it,
    so a multi-speaker shot keeps the voices straight. `ref(i)` formats the
    reference label (Lunostudio @audioN by default; pass a plain-text formatter
    for the backup providers). Empty list when there is no voice reference."""
    keys = audio_keys_for(seg)
    if not keys:
        return []
    if len(keys) == 1:
        name = CHARACTERS[keys[0]]["name"]
        return [f"VOICE REFERENCE: {ref(1)} is {name}'s voice -- match it for "
                f"{name}'s spoken lines."]
    lines = ["VOICE REFERENCES (each character speaks in their OWN voice):"]
    for i, k in enumerate(keys, 1):
        name = CHARACTERS[k]["name"]
        lines.append(f"- {ref(i)} is {name}'s voice -- use it for {name}'s lines only.")
    return lines


def build_prompt(seg):
    """Build the Seedance API payload for a segment. Every segment is an
    independent shot keyed on its opening still (@image1). A segment that is
    FOLLOWED by a continuation also carries a locked last_frame (its zclosing
    still) so it ends on the exact frame the next shot begins on."""
    seg_id = seg["id"]
    _audio_idx = audio_index_map(seg)

    closing_url = None
    if is_continued(seg):
        closing_id = GDRIVE_CLOSING_IDS.get(seg_id)
        if closing_id:
            closing_url = gdrive(closing_id)
        else:
            print(f"  [WARN] No closing (zclosing) still for continued segment "
                  f"{seg_id}", flush=True)

    still_id = GDRIVE_STILL_IDS.get(seg_id)
    if not still_id:
        print(f"  [WARN] No still ID for segment {seg_id}", flush=True)
        refs = []
    else:
        refs = [gdrive(still_id)]

    style = VISUAL_STYLES.get(seg["style"], seg["style"])

    # If this segment spans two compositions to avoid interrupting
    # dialogue, attach the mid-cut reference still as @image2.
    mid_cut = seg.get("mid_cut_ref")
    n = 2
    if mid_cut:
        mid_id = GDRIVE_STILL_IDS.get(mid_cut)
        if mid_id:
            refs.append(gdrive(mid_id))
            n = 3

    parts = [OPENING_RULE, ""]
    if continues_prev(seg):
        parts.append(CONTINUE_RULE)
        parts.append("")
    if mid_cut:
        parts.append(
            f"@image2 is a reference for the second composition within this "
            f"shot. The camera transitions to this framing mid-segment to "
            f"avoid interrupting dialogue."
        )
        parts.append("")

    parts.append(f"VISUAL STYLE: {style}")
    parts.append("")

    if seg["characters"]:
        char_lines = ["CHARACTERS (match reference images exactly):"]
        angle_map = seg.get("char_ref_angles", {})
        for key in seg["characters"]:
            c = CHARACTERS[key]
            tags = []
            for angle in char_ref_angles_for(key, angle_map):
                gkey = f"{key}_{angle}"
                if gkey in GDRIVE_CHAR_REF_IDS:
                    refs.append(gdrive(GDRIVE_CHAR_REF_IDS[gkey]))
                    tags.append(f"@image{n}")
                    n += 1

            voice = c.get("voice_desc", "")
            voice_line = ""
            aidx = _audio_idx.get(key)
            if aidx:
                voice_line = (
                    f" His/her voice must match the reference audio @audio{aidx} -- "
                    f"{voice}"
                )
            # Video prompts use the SHORT description (video_desc): only what is
            # essential and not already visible in the reference images. The full
            # `desc` exists to GENERATE the refs and is not repeated here.
            desc = (c.get("video_desc") or c["desc"]) + voice_line
            label = f" ({', '.join(tags)})" if tags else ""
            char_lines.append(f"- {c['name']}{label}: {desc}")
        parts.extend(char_lines)
        parts.append("")

    for sid in seg.get("extra_still_refs", []):
        extra_id = GDRIVE_STILL_IDS.get(sid)
        if extra_id:
            refs.append(gdrive(extra_id))
            parts.append(
                f"@image{len(refs)} shows the same character and setting at "
                f"another moment of the same scene -- match the face and "
                f"details exactly."
            )
            parts.append("")

    parts.append(f"ACTION AND SOUND ({seg['seconds']} seconds): {spoken_action(seg)}")
    _vn = voice_reference_note(seg)
    if _vn:
        parts.append("")
        parts.extend(_vn)
    _nn = name_note(seg)
    if _nn:
        parts.append("")
        parts.extend(_nn)
    if closing_url:
        parts.append(CLOSING_RULE)
    parts.append("")
    parts.append(f"CAMERA: {seg['camera']}")

    payload = {
        "model": SEEDANCE_MODEL,
        "prompt": "\n".join(parts),
        "duration": seg["seconds"],
        "aspect_ratio": SEEDANCE_ASPECT,
        "resolution": SEEDANCE_RESOLUTION,
        "mode": SEEDANCE_MODE,
        "reference_images": refs,
    }

    audio_urls = audio_urls_for(seg)
    if audio_urls:
        payload["reference_audio"] = audio_urls

    if closing_url:
        payload["last_frame"] = closing_url

    return payload


# === BACKUP-PROVIDER PROMPT BUILDER ===
# Nano-GPT and OpenRouter Seedance take a plain prompt plus reference URLs
# rather than Lunostudio's @-tags, so this prompt drops the @image/@audio tag
# language but keeps all of the same descriptive content (style, characters,
# action, names, camera). Shared by both backup providers.

def build_openrouter_prompt(seg, style):
    parts = []
    _audio_idx = audio_index_map(seg)
    if continues_prev(seg):
        parts.append(
            "CONTINUITY: this shot continues the previous scene with no cut. Its "
            "first frame is identical to the frame the previous shot ended on -- "
            "match that framing, lighting, character positions, and camera exactly "
            "and carry the action straight on; do not restart or recompose."
        )
        parts.append("")
    parts.append(f"VISUAL STYLE: {style}")
    parts.append("")
    if seg["characters"]:
        parts.append("CHARACTERS (match the reference images exactly):")
        for key in seg["characters"]:
            c = CHARACTERS[key]
            voice_line = ""
            if key in _audio_idx:
                voice_line = (f" The voice must match this character's reference "
                              f"audio -- {c.get('voice_desc', '')}")
            parts.append(f"- {c['name']}: {c.get('video_desc') or c['desc']}{voice_line}")
        parts.append("")
    parts.append(f"ACTION AND SOUND ({seg['seconds']} seconds): {spoken_action(seg)}")
    _vn = voice_reference_note(seg, ref=lambda i: f"reference audio {i}")
    if _vn:
        parts.append("")
        parts.extend(_vn)
    _nn = name_note(seg)
    if _nn:
        parts.append("")
        parts.extend(_nn)
    if is_continued(seg):
        parts.append("")
        parts.append(
            "LOCKED CLOSING FRAME: a locked end frame is provided -- the video "
            "must arrive at and end on that exact composition by its final frame."
        )
    parts.append("")
    parts.append(f"CAMERA: {seg['camera']}")
    return "\n".join(parts)


def openrouter_spec(seg, first_frame_url=None, video_url=None):
    """Normalized spec for openrouter_video.generate(). Resolves the same
    reference media as build_prompt into OpenRouter's structured form, including
    a locked last_frame_url for a segment that is followed by a continuation."""
    style = VISUAL_STYLES.get(seg["style"], seg["style"])

    # A locked last_frame (this segment is FOLLOWED by a continuation) puts the
    # shot in Seedance KEYFRAME mode -- first frame + last frame. Keyframe mode is
    # MUTUALLY EXCLUSIVE with character reference images: attach any and Seedance
    # ignores the last_frame, so the join drifts (verified -- the seam ends on a
    # different, sometimes mirror-flipped, composition). So when a last_frame is
    # present we attach NO character/mid-cut/extra reference images. The locked
    # first and last frames already pin the faces and composition, and the audio
    # reference (added below) still carries the voice -- audio is compatible with
    # keyframe mode.
    last_frame_url = None
    if is_continued(seg) and GDRIVE_CLOSING_IDS.get(seg["id"]):
        last_frame_url = gdrive(GDRIVE_CLOSING_IDS[seg["id"]])

    image_refs = []
    if not last_frame_url:
        angle_map = seg.get("char_ref_angles", {})
        for key in seg["characters"]:
            for angle in char_ref_angles_for(key, angle_map):
                gkey = f"{key}_{angle}"
                if gkey in GDRIVE_CHAR_REF_IDS:
                    image_refs.append(gdrive(GDRIVE_CHAR_REF_IDS[gkey]))
        mid_cut = seg.get("mid_cut_ref")
        if mid_cut and GDRIVE_STILL_IDS.get(mid_cut):
            image_refs.append(gdrive(GDRIVE_STILL_IDS[mid_cut]))
        for sid in seg.get("extra_still_refs", []):
            if GDRIVE_STILL_IDS.get(sid):
                image_refs.append(gdrive(GDRIVE_STILL_IDS[sid]))

    if first_frame_url is None and GDRIVE_STILL_IDS.get(seg["id"]):
        first_frame_url = gdrive(GDRIVE_STILL_IDS[seg["id"]])

    audio_urls = audio_urls_for(seg)

    return {
        "model": OPENROUTER_MODEL,
        "prompt": build_openrouter_prompt(seg, style),
        "duration": seg["seconds"],
        "resolution": SEEDANCE_RESOLUTION,
        "aspect_ratio": SEEDANCE_ASPECT,
        "first_frame_url": first_frame_url,
        "last_frame_url": last_frame_url,
        "image_ref_urls": image_refs,
        "audio_url": audio_urls[0] if audio_urls else None,  # back-compat (single)
        "audio_urls": audio_urls,                            # full list (multi-speaker)
        "video_url": None,
    }


def nanogpt_spec(seg, first_frame_url=None, video_url=None):
    """Same normalized spec as openrouter_spec (identical reference resolution
    and prompt) but tagged with the Nano-GPT Seedance model id. nanogpt_video
    maps first_frame_url->imageUrl, last_frame_url->last_image, image_ref_urls->
    reference_images, audio_url->reference_audios, video_url->reference_videos."""
    spec = openrouter_spec(seg, first_frame_url=first_frame_url, video_url=video_url)
    spec["model"] = NANOGPT_MODEL
    return spec


# === API HELPERS ===

def api_request(method, path, body=None):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {LUNO_API_KEY}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode())


def cdn_upload(filepath):
    """Upload a local file to the Lunostudio CDN and return its public URL."""
    filepath = Path(filepath)
    data_bytes = filepath.read_bytes()
    boundary = uuid.uuid4().hex
    mime = {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    }.get(filepath.suffix.lower(), "application/octet-stream")
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"\r\n'
        f"Content-Type: {mime}\r\n"
        f"\r\n"
    ).encode() + data_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE_URL + "/api/v1/upload", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {LUNO_API_KEY}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=600, context=SSL_CONTEXT) as resp:
        result = json.loads(resp.read().decode())
    url = result.get("url")
    if not url:
        raise RuntimeError(f"upload returned no url: {json.dumps(result)[:200]}")
    return url


def find_field(obj, names):
    """Recursively find the first value whose key is in names."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and isinstance(v, (str, int)):
                return v
        for v in obj.values():
            r = find_field(v, names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_field(v, names)
            if r is not None:
                return r
    return None


def find_video_url(obj):
    """Recursively find an http(s) URL that looks like a video."""
    if isinstance(obj, str):
        if obj.startswith("http") and (".mp4" in obj or "video" in obj):
            return obj
    elif isinstance(obj, dict):
        for v in obj.values():
            r = find_video_url(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_video_url(v)
            if r:
                return r
    return None


def load_tasks():
    f = tasks_file()
    if f.exists():
        return json.loads(f.read_text())
    return {}


def save_tasks(tasks):
    # Thread-safe: parallel chains may mutate `tasks` (distinct keys) while this
    # serializes. Snapshot under a lock, retrying if a concurrent insert resizes
    # the dict mid-iteration, then write the file.
    with _SAVE_LOCK:
        for _ in range(8):
            try:
                data = json.dumps(dict(tasks), indent=2)
                break
            except RuntimeError:
                time.sleep(0.05)
        else:
            return
        tasks_file().write_text(data)


def submit(seg):
    payload = build_prompt(seg)
    resp = api_request("POST", "/api/v1/generate", payload)
    task_id = find_field(resp, {"task_id", "taskId", "id"})
    tag = " [CONTINUES]" if continues_prev(seg) else ""
    print(f"[SUBMIT] segment {seg['id']:02d}{tag} -> task {task_id}", flush=True)
    return str(task_id), resp


def get_status(task_id):
    resp = api_request("GET", f"/api/v1/status?task_id={task_id}")
    status = find_field(resp, {"status", "state"})
    return (str(status).lower() if status else "unknown"), resp


def download(url, dest):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=600, context=SSL_CONTEXT) as resp, \
            open(dest, "wb") as f:
        f.write(resp.read())
    size = Path(dest).stat().st_size
    print(f"  [SAVED] {dest} ({size / 1e6:.1f} MB)", flush=True)
    return size > 0


def poll_and_download(tasks, seg_ids, interval=20, timeout_min=45):
    """Poll the given segments until all reach a terminal state."""
    deadline = time.time() + timeout_min * 60
    pending = {
        str(s) for s in seg_ids
        if tasks.get(str(s), {}).get("status") not in ("success", "failed")
        and not video_path(int(s)).exists()
    }
    while pending and time.time() < deadline:
        for s in sorted(pending, key=int):
            info = tasks.get(s)
            if not info or not info.get("task_id"):
                pending.discard(s)
                continue
            try:
                status, resp = get_status(info["task_id"])
            except Exception as e:
                print(f"  [POLL ERROR] seg {s}: {e}", flush=True)
                continue
            if status != info.get("status"):
                print(f"[STATUS] segment {s}: {status}", flush=True)
            info["status"] = status
            if status == "success":
                url = find_video_url(resp)
                info["video_url"] = url
                if url:
                    try:
                        download(url, video_path(int(s)))
                    except Exception as e:
                        print(f"  [DOWNLOAD ERROR] seg {s}: {e}", flush=True)
                else:
                    print(
                        f"  [WARN] seg {s} success but no video URL; "
                        f"raw: {json.dumps(resp)[:400]}",
                        flush=True,
                    )
                pending.discard(s)
            elif status == "failed":
                print(
                    f"  [FAILED] seg {s}; raw: {json.dumps(resp)[:400]}",
                    flush=True,
                )
                pending.discard(s)
            save_tasks(tasks)
            time.sleep(1)
        if pending:
            time.sleep(interval)
    if pending:
        print(f"[TIMEOUT] still pending: {sorted(pending, key=int)}", flush=True)


def poll_single(tasks, seg_id, interval=20, timeout_min=60):
    """Poll one segment until it reaches a terminal state and download it.
    Returns True if the video is on disk. Used for sequential extend segments."""
    s = str(seg_id)
    info = tasks.get(s, {})
    if not info.get("task_id"):
        return False
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        try:
            status, resp = get_status(info["task_id"])
        except Exception as e:
            print(f"  [POLL ERROR] seg {s}: {e}", flush=True)
            time.sleep(interval)
            continue
        if status != info.get("status"):
            print(f"[STATUS] segment {s}: {status}", flush=True)
        info["status"] = status
        if status == "success":
            url = find_video_url(resp)
            info["video_url"] = url
            save_tasks(tasks)
            if url and not video_path(seg_id).exists():
                try:
                    download(url, video_path(seg_id))
                except Exception as e:
                    print(f"  [DOWNLOAD ERROR] seg {s}: {e}", flush=True)
            return video_path(seg_id).exists()
        elif status == "failed":
            print(f"  [FAILED] seg {s}; raw: {json.dumps(resp)[:300]}", flush=True)
            save_tasks(tasks)
            return False
        save_tasks(tasks)
        time.sleep(interval)
    print(f"[TIMEOUT] segment {s}", flush=True)
    return False


def last_frame_ref_url(video_file):
    """Extract the final frame of a local video and host it on litterbox,
    returning a temporary public URL. Used to seed a Nano-GPT continuity/extend
    segment from a previous clip that only exists locally (Nano-GPT references
    must be real URLs, not base64 data URLs)."""
    tmp = videos_dir() / "_lastframe.jpg"
    cmd = ["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(video_file),
           "-frames:v", "1", "-q:v", "2", str(tmp)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not tmp.exists():
        print(f"  [WARN] could not extract last frame from {video_file.name}",
              flush=True)
        return None
    url = nanogpt_video.public_url_from_file(tmp)
    tmp.unlink(missing_ok=True)
    return url


# === STANDARD / BASE GENERATION ===

def _run_nanogpt(seg, tasks, reason="fallback"):
    """Generate one independent segment via Nano-GPT. Returns True if saved."""
    s = str(seg["id"])
    info = tasks.setdefault(s, {})
    info["provider"] = "nanogpt"
    info["status"] = "nanogpt_submitted"
    save_tasks(tasks)
    label = {
        "primary": "primary provider",
        "keyframe": "keyframe mode -- first+last frame + audio, no char-ref images",
    }.get(reason, f"fallback after {MAX_LUNO_ATTEMPTS} Lunostudio failures")
    print(f"[NANOGPT] segment {s} ({label})", flush=True)
    ok = nanogpt_video.generate(
        nanogpt_spec(seg), NANOGPT_API_KEY, video_path(seg["id"])
    )
    info["status"] = "success" if ok else "failed"
    save_tasks(tasks)
    return ok


def _run_openrouter(seg, tasks, reason="fallback"):
    """Legacy: generate one independent segment via OpenRouter (only used when
    VIDEO_PROVIDER == 'openrouter'). Returns True if saved."""
    if openrouter_video is None:
        print("  [OPENROUTER] openrouter_video.py not available", flush=True)
        return False
    s = str(seg["id"])
    info = tasks.setdefault(s, {})
    info["provider"] = "openrouter"
    info["status"] = "openrouter_submitted"
    save_tasks(tasks)
    label = ("primary provider" if reason == "primary"
             else f"fallback after {MAX_LUNO_ATTEMPTS} Lunostudio failures")
    print(f"[OPENROUTER] segment {s} ({label})", flush=True)
    ok = openrouter_video.generate(
        openrouter_spec(seg), OPENROUTER_API_KEY, video_path(seg["id"])
    )
    info["status"] = "success" if ok else "failed"
    save_tasks(tasks)
    return ok


def _luno_round(seg_ids, tasks, attempt):
    """Submit the given independent segments to Lunostudio (fresh tasks) and
    poll once until each reaches a terminal state."""
    print(f"=== Lunostudio attempt {attempt}/{MAX_LUNO_ATTEMPTS} "
          f"-- {len(seg_ids)} segment(s) ===", flush=True)
    for sid in seg_ids:
        s = str(sid)
        try:
            task_id, _ = submit(SEG_BY_ID[sid])
            tasks[s] = {"task_id": task_id, "status": "submitted",
                        "provider": "lunostudio", "luno_attempts": attempt}
        except Exception as e:
            print(f"[SUBMIT ERROR] segment {s}: {e}", flush=True)
            tasks[s] = {"task_id": None, "status": "submit_error",
                        "provider": "lunostudio", "luno_attempts": attempt,
                        "error": str(e)}
        save_tasks(tasks)
        time.sleep(3)
    poll_and_download(tasks, seg_ids, interval=30, timeout_min=60)


def _run_backup(seg, tasks, reason="fallback"):
    """Route a single segment to the configured backup provider (Nano-GPT by
    default; OpenRouter only when VIDEO_PROVIDER == 'openrouter')."""
    if VIDEO_PROVIDER == "openrouter":
        return _run_openrouter(seg, tasks, reason=reason)
    return _run_nanogpt(seg, tasks, reason=reason)


def _generate(seg_ids, tasks):
    """Generate the given segments: Lunostudio with up to MAX_LUNO_ATTEMPTS
    attempts, then Nano-GPT fallback -- or the backup provider from the start
    when VIDEO_PROVIDER is 'nanogpt' (or legacy 'openrouter'). Segments already
    on disk are skipped, so this resumes cleanly. All segments are independent,
    so they generate in parallel within _luno_round; locked shared frames
    (last_frame) carry any continuity."""
    # KEYFRAME (first-and-last-frame) join segments first. A segment FOLLOWED by
    # a continuation carries a locked last_frame and must be generated in Seedance
    # keyframe mode (first+last frame + audio, no character-ref images -- handled
    # in openrouter_spec). Only Nano-GPT honors the locked last_frame, so route
    # these to it regardless of VIDEO_PROVIDER. (In a standard film is_continued
    # is always False, so this is a no-op there.) KEYFRAME_PROVIDER = None opts
    # out -- the seams then drift on Lunostudio.
    if KEYFRAME_PROVIDER:
        keyframe_ids = [sid for sid in seg_ids if is_continued(SEG_BY_ID[sid])]
        for sid in keyframe_ids:
            if not video_path(sid).exists():
                _run_nanogpt(SEG_BY_ID[sid], tasks, reason="keyframe")
        seg_ids = [sid for sid in seg_ids if sid not in set(keyframe_ids)]
        if not seg_ids:
            return

    if VIDEO_PROVIDER in ("nanogpt", "openrouter"):
        for sid in seg_ids:
            if not video_path(sid).exists():
                _run_backup(SEG_BY_ID[sid], tasks, reason="primary")
        return

    for attempt in range(1, MAX_LUNO_ATTEMPTS + 1):
        pending = [sid for sid in seg_ids if not video_path(sid).exists()]
        if not pending:
            break
        _luno_round(pending, tasks, attempt)

    for sid in seg_ids:
        if not video_path(sid).exists():
            _run_backup(SEG_BY_ID[sid], tasks)


def _luno_generate_to_dest(seg, dest, interval=30, timeout_min=60):
    """Generate one segment on Lunostudio straight to an arbitrary dest path
    (used by `regenerate`, which must NOT write over the main film). Returns
    True if dest was written. Mirrors poll_single's terminal logic."""
    try:
        task_id, _ = submit(seg)
    except Exception as e:
        print(f"  [SUBMIT ERROR] seg {seg['id']}: {e}", flush=True)
        return False
    deadline = time.time() + timeout_min * 60
    last = None
    while time.time() < deadline:
        try:
            status, resp = get_status(task_id)
        except Exception as e:
            print(f"  [POLL ERROR] seg {seg['id']}: {e}", flush=True)
            time.sleep(interval)
            continue
        if status != last:
            print(f"[STATUS] segment {seg['id']}: {status}", flush=True)
            last = status
        if status == "success":
            url = find_video_url(resp)
            if not url:
                print(f"  [WARN] seg {seg['id']} success but no video URL",
                      flush=True)
                return False
            try:
                return download(url, dest)
            except Exception as e:
                print(f"  [DOWNLOAD ERROR] seg {seg['id']}: {e}", flush=True)
                return False
        if status == "failed":
            print(f"  [FAILED] seg {seg['id']}; raw: {json.dumps(resp)[:300]}",
                  flush=True)
            return False
        time.sleep(interval)
    print(f"[TIMEOUT] segment {seg['id']}", flush=True)
    return False


def _generate_one_to_dest(seg, dest):
    """Generate a single fresh segment to dest using the configured provider
    chain (Lunostudio with Nano-GPT fallback, or the backup provider directly
    when VIDEO_PROVIDER is 'nanogpt'/'openrouter'). Returns True on success."""
    if VIDEO_PROVIDER == "openrouter":
        return openrouter_video.generate(openrouter_spec(seg),
                                         OPENROUTER_API_KEY, dest)
    if VIDEO_PROVIDER == "nanogpt":
        return nanogpt_video.generate(nanogpt_spec(seg), NANOGPT_API_KEY, dest)

    for attempt in range(1, MAX_LUNO_ATTEMPTS + 1):
        print(f"=== Lunostudio attempt {attempt}/{MAX_LUNO_ATTEMPTS} "
              f"(regenerate seg {seg['id']}) ===", flush=True)
        if _luno_generate_to_dest(seg, dest):
            return True
    print(f"[FALLBACK] seg {seg['id']}: routing to Nano-GPT", flush=True)
    return nanogpt_video.generate(nanogpt_spec(seg), NANOGPT_API_KEY, dest)


# === MODES ===

def write_prompts():
    label = "continuous version" if CONTINUOUS else "standard"
    lines = [
        f"{FILM_TITLE} -- Seedance 2 prompts ({label})",
        f"resolution {SEEDANCE_RESOLUTION}, aspect {SEEDANCE_ASPECT}, "
        f"model {SEEDANCE_MODEL}",
        "=" * 70,
    ]
    for seg in SEGMENTS:
        p = build_prompt(seg)
        tag = ""
        if continues_prev(seg):
            tag += " [CONTINUES PREVIOUS]"
        if "last_frame" in p:
            tag += " [LOCKED LAST FRAME]"
        header = (
            f"SEGMENT {seg['id']:02d} -- duration {seg['seconds']}s"
            f" -- {len(p['reference_images'])} ref image(s)"
        )
        if "reference_audio" in p:
            header += ", voice audio"
        if "last_frame" in p:
            header += ", last_frame"
        lines.append("")
        lines.append(header + tag)
        for i, u in enumerate(p["reference_images"], 1):
            lines.append(f"  @image{i}: {u}")
        if "reference_audio" in p:
            for i, u in enumerate(p["reference_audio"], 1):
                lines.append(f"  @audio{i}: {u}")
        if "last_frame" in p:
            lines.append(f"  CLOSING FRAME [last_frame] (locked end frame -- the "
                         f"next shot opens on this exact frame): {p['last_frame']}")
        lines.append("")
        lines.append(p["prompt"])
        lines.append("-" * 70)
    prompts_file().write_text("\n".join(lines) + "\n")
    print(f"Wrote {prompts_file()}", flush=True)


def run_generate():
    """Generate the whole movie at once. Every segment is an independent shot
    (continuity is carried by locked shared frames), so all segments generate in
    parallel -- standard and continuous alike. Segments already on disk are
    skipped, so this also resumes an interrupted run."""
    videos_dir().mkdir(parents=True, exist_ok=True)
    tasks = load_tasks()
    _generate([seg["id"] for seg in SEGMENTS], tasks)
    done = sorted(p.name for p in videos_dir().glob("seg*.mp4"))
    print(f"\nVIDEOS ON DISK: {len(done)}/{len(SEGMENTS)} "
          f"({videos_dir().name}/)", flush=True)


def show_status():
    tasks = load_tasks()
    success = failed = pending = 0
    for seg in SEGMENTS:
        s = str(seg["id"])
        info = tasks.get(s, {})
        status = info.get("status", "not submitted")
        on_disk = "video saved" if video_path(seg["id"]).exists() else ""
        ext = " [cont]" if continues_prev(seg) else ""
        if video_path(seg["id"]).exists():
            success += 1
        elif status in ("failed", "submit_error", "skipped"):
            failed += 1
        else:
            pending += 1
        provider = info.get("provider", "lunostudio")
        print(f"segment {s:>2}{ext}: {status:<16} via {provider:<10} "
              f"task={info.get('task_id', '-')} {on_disk}")
    label = "continuous" if CONTINUOUS else "standard"
    print(f"\n{label} version | Total: {len(SEGMENTS)} segments | "
          f"{success} success | {failed} failed | {pending} pending")


def run_fetch():
    videos_dir().mkdir(parents=True, exist_ok=True)
    tasks = load_tasks()
    poll_and_download(tasks, [seg["id"] for seg in SEGMENTS],
                      interval=30, timeout_min=60)


# === RENUMBER (clean 1..N, run BEFORE stitching) ===

def _renumber_map():
    """Map each segment's current id -> its 1-based position in FILM (list)
    order. List order is preserved, so extend eligibility is unaffected.
    Returns (mapping, already_sequential)."""
    old = [seg["id"] for seg in SEGMENTS]
    new = list(range(1, len(SEGMENTS) + 1))
    return dict(zip(old, new)), old == new


def _renumber_prefix(directory, mapping):
    """Two-phase rename of EVERY seg file in a directory -- base, versioned takes,
    and opening/zclosing/versioned stills -- from its old id to its new id while
    preserving the rest of the name (seg14.mp4 -> seg03.mp4, seg14_v2.mp4 ->
    seg03_v2.mp4, seg14_opening_v2.jpg -> seg03_opening_v2.jpg). The per-segment
    id is matched exactly (so seg140 is not caught by seg14). Returns count moved."""
    if not directory.exists():
        return 0
    staged = []
    for old, new in mapping.items():
        if old == new:
            continue
        for src in directory.glob(f"seg{old:02d}*"):
            if _seg_id_from_name(src.name) != old:  # guard: seg14 must not catch seg140
                continue
            newname = re.sub(rf"^seg0*{old}\b", f"seg{new:02d}", src.name)
            if newname == src.name:
                newname = re.sub(rf"^seg0*{old}", f"seg{new:02d}", src.name)
            tmp = directory / (".renum_" + newname)
            src.rename(tmp)
            staged.append((tmp, directory / newname))
    for tmp, dst in staged:
        tmp.rename(dst)
    return len(staged)


def _renumber_manifest(path, mapping):
    """Rewrite a manifest's filenames to the new ids, preserving each chosen take.
    Pretty per-segment comments are dropped (the next `manifest` / `stillmanifest`
    refresh restores them)."""
    if not path.exists():
        return
    sel = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        old = _seg_id_from_name(s)
        if old is None:
            continue
        new = mapping.get(old, old)
        sel[new] = re.sub(rf"^seg0*{old}", f"seg{new:02d}", s)
    out = ["# manifest remapped by renumber -- run `make_movie.py manifest` (videos)",
           "# or `generate_images.py stillmanifest` (stills) to restore full comments.",
           "#"]
    out.extend(sel[k] for k in sorted(sel))
    path.write_text("\n".join(out) + "\n")


def _renumber_json_keys(path, mapping, subkey=None):
    """Remap integer-id dict keys (still ids, task ids) in a JSON sidecar.
    Non-integer keys (character-ref / audio names) are left untouched."""
    if not path.exists():
        return
    data = json.loads(path.read_text())
    target = data.get(subkey, {}) if subkey else data
    remapped = {}
    for k, v in target.items():
        try:
            nk = str(mapping.get(int(k), int(k)))
        except (ValueError, TypeError):
            nk = k
        remapped[nk] = v
    if subkey:
        data[subkey] = remapped
    else:
        data = remapped
    path.write_text(json.dumps(data, indent=2))


def _emit_segments(segs):
    """Emit a SEGMENTS list literal, one field per line, preserving key order."""
    out = ["["]
    for d in segs:
        out.append("    {")
        for k, v in d.items():
            out.append(f"        {k!r}: {v!r},")
        out.append("    },")
    out.append("]")
    return "\n".join(out)


def _rewrite_config_ids(mapping):
    """Rewrite the 'id' (and any mid_cut_ref / extra_still_refs) values inside the
    SEGMENTS list of film_config.py. Uses ast to find the exact list span, so the
    rest of the file (other sections, comments) is left intact."""
    cfg = BASE_DIR / "film_config.py"
    src = cfg.read_text()
    node = None
    for stmt in ast.parse(src).body:
        if (isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "SEGMENTS"
                        for t in stmt.targets)):
            node = stmt.value
            break
    if node is None:
        raise SystemExit("[RENUMBER] could not find SEGMENTS in film_config.py")
    new_segs = []
    for seg in SEGMENTS:
        d = dict(seg)
        d["id"] = mapping[d["id"]]
        if isinstance(d.get("mid_cut_ref"), int):
            d["mid_cut_ref"] = mapping.get(d["mid_cut_ref"], d["mid_cut_ref"])
        if "extra_still_refs" in d:
            d["extra_still_refs"] = [mapping.get(x, x)
                                     for x in d["extra_still_refs"]]
        new_segs.append(d)
    lens = [len(line) for line in src.splitlines(keepends=True)]
    start = sum(lens[:node.lineno - 1]) + node.col_offset
    end = sum(lens[:node.end_lineno - 1]) + node.end_col_offset
    cfg.write_text(src[:start] + _emit_segments(new_segs) + src[end:])


def run_renumber():
    """Renumber segments to a clean 1..N in FILM (list) order BEFORE stitching,
    so the final film and the segment list read in order and out-of-place or
    3-digit ids are gone. Renames the video + still files, remaps the JSON
    sidecars (upload_urls.json stills, the task file, stills_progress.json), and
    rewrites the ids in film_config.py. List order -- hence extend eligibility --
    is unchanged. Idempotent. Run `renumber` then `stitch`."""
    mapping, already = _renumber_map()
    if already:
        print("[RENUMBER] segments already numbered 1..N in order; nothing to "
              "do.", flush=True)
        return
    changed = sum(1 for o, n in mapping.items() if o != n)
    print(f"[RENUMBER] {changed} id(s) change -> clean 1..{len(SEGMENTS)} "
          f"sequence (film order preserved).", flush=True)
    backup = BASE_DIR / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE_DIR / "film_config.py",
                 backup / "film_config_pre_renumber.py")
    n_v = _renumber_prefix(videos_dir(), mapping)
    n_s = _renumber_prefix(BASE_DIR / "stills", mapping)
    print(f"[RENUMBER] renamed {n_v} video file(s), {n_s} still file(s) "
          f"(base + versioned takes).", flush=True)
    _renumber_json_keys(BASE_DIR / "upload_urls.json", mapping, subkey="stills")
    _renumber_json_keys(tasks_file(), mapping)
    _renumber_json_keys(BASE_DIR / "stills_progress.json", mapping)
    _renumber_manifest(manifest_path(), mapping)
    _renumber_manifest(BASE_DIR / "stills" / "manifest.txt", mapping)
    _rewrite_config_ids(mapping)
    print("[RENUMBER] film_config.py ids rewritten (backup in backup/). "
          "Now assemble: `make_movie.py stitch` for a flat file, or "
          "`make_movie.py palmier` to import into the Palmier Pro editor "
          "(append `continuous` if applicable).",
          flush=True)


def ordered_segment_files():
    """The CHOSEN take of each segment, in SEGMENTS (film) order, for segments
    whose chosen file exists. Driven by the videos-folder manifest (built on
    first use), so stitch / palmier / upscale / list all assemble exactly the
    lineup the manifest declares -- and an external editor gets the same lineup.
    Film order, NOT lexical filename order (which would misplace 3-digit ids or
    a 'segNN_v2' next to 'segNN'). Returns (segment, Path) pairs."""
    sel = read_manifest() or refresh_manifest()
    out = []
    for seg in SEGMENTS:
        fname = sel.get(seg["id"]) or default_take(seg["id"])
        path = videos_dir() / fname
        if path.exists():
            out.append((seg, path))
    return out


def upscaled_dir():
    """Sibling of videos_dir() that holds the upscaled segments, e.g.
    videos_upscaled/ (or videos_continuous_upscaled/ for the continuous cut)."""
    vdir = videos_dir()
    return vdir.parent / f"{vdir.name}_upscaled"


# === SEGMENT VERSIONS + MANIFEST ===
# Regenerations never overwrite and never move to a side folder. A re-rolled
# segment is written into the SAME videos folder as a NEW versioned file:
# seg05.mp4 is the original ("v1"); seg05_v2.mp4, seg05_v3.mp4, ... are re-rolls.
# Nothing is renamed or replaced, so every take is kept and file names are never
# reused. A manifest.txt IN the videos folder is the single source of truth for
# which take is in the film -- the ordered lineup that `stitch` / `palmier` (and
# an external editor such as Palmier or CapCut) read. Regenerating does NOT
# change the manifest; you pick a take explicitly with
#   make_movie.py use <id> <version>
# (or by editing the filename on its line in manifest.txt). The continuous cut
# keeps its own folder (videos_continuous/) and therefore its own manifest.

def manifest_path():
    return videos_dir() / "manifest.txt"


def _seg_id_from_name(name):
    m = re.match(r"seg0*(\d+)", Path(name).name)
    return int(m.group(1)) if m else None


def base_video_name(seg_id):
    return f"seg{seg_id:02d}.mp4"


def version_video_name(seg_id, ver):
    """Filename for a version label: 1/'base'/'v1' -> segNN.mp4; k>=2 (or 'vK')
    -> segNN_v{k}.mp4."""
    s = str(ver).strip().lower().lstrip("v")
    if s in ("", "1", "base"):
        return base_video_name(seg_id)
    return f"seg{seg_id:02d}_v{int(s)}.mp4"


def segment_versions(seg_id):
    """Every take of a segment present in the videos folder, ordered: base
    (segNN.mp4) first if present, then segNN_v2, _v3, ... by number."""
    vdir = videos_dir()
    out = []
    base = vdir / base_video_name(seg_id)
    if base.exists():
        out.append(base)
    vers = []
    for p in vdir.glob(f"seg{seg_id:02d}_v*.mp4"):
        m = re.fullmatch(rf"seg0*{seg_id}_v(\d+)", p.stem)
        if m:
            vers.append((int(m.group(1)), p))
    out.extend(p for _, p in sorted(vers))
    return out


def next_video_version(seg_id):
    """Next free re-roll version NUMBER (>=2; the base segNN.mp4 is v1)."""
    n = 2
    while (videos_dir() / f"seg{seg_id:02d}_v{n}.mp4").exists():
        n += 1
    return n


def default_take(seg_id):
    """The filename to list for a segment when the manifest has no explicit
    choice: the base file if present, else the lowest-numbered take on disk,
    else the base name (so the slot is still listed even if nothing exists)."""
    takes = segment_versions(seg_id)
    return takes[0].name if takes else base_video_name(seg_id)


def read_manifest():
    """Parse the videos-folder manifest -> {seg_id: filename}. {} if none yet."""
    path = manifest_path()
    sel = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sid = _seg_id_from_name(line)
            if sid is not None:
                sel[sid] = line
    return sel


def write_manifest(selections):
    """Write the manifest in SEGMENTS (film) order: a header, then for each
    segment a comment line (id / title / duration) and the chosen filename on
    its own line. `selections` is {seg_id: filename}; gaps default to base."""
    cut = "continuous" if CONTINUOUS else "standard"
    lines = [
        f"# {FILM_TITLE} -- VIDEO MANIFEST ({cut} cut)",
        "# The lineup of the latest complete film, in film order -- one filename per line.",
        "# All takes live in THIS folder; regenerating a segment adds seg<NN>_v<N>.mp4",
        "# but does NOT change this list. Choose a take with:",
        "#   python3 make_movie.py use <id> <version>     (e.g. use 7 2  |  use 7 base)",
        "# or edit the filename on its line below. Lines starting with # are ignored.",
        "#",
    ]
    for seg in SEGMENTS:
        sid = seg["id"]
        fname = selections.get(sid) or default_take(sid)
        flag = "" if (videos_dir() / fname).exists() else "   <-- MISSING"
        lines.append(f"#  seg {sid:>3}  {seg['title']}  ({seg['seconds']}s){flag}")
        lines.append(fname)
    manifest_path().parent.mkdir(parents=True, exist_ok=True)
    manifest_path().write_text("\n".join(lines) + "\n")


def refresh_manifest():
    """Build the manifest if absent, or refresh it in place: keep every explicit
    choice whose file still exists, default the rest to their base/earliest take,
    and rewrite every segment slot in film order. Returns {seg_id: filename}."""
    existing = read_manifest()
    selections = {}
    for seg in SEGMENTS:
        sid = seg["id"]
        chosen = existing.get(sid)
        selections[sid] = chosen if (chosen and (videos_dir() / chosen).exists()) \
            else default_take(sid)
    write_manifest(selections)
    return selections


def run_upscale():
    """Upscale the finished segment clips with Nano-GPT's SeedVR2 (the default
    upscaler), writing enlarged copies to <videos>_upscaled/segNN.mp4. Each
    local clip is staged on litterbox to get a public URL, then enlarged to
    UPSCALE_RESOLUTION (720p by default). Segments already upscaled are skipped,
    so this resumes cleanly. Audio is preserved by the upscaler.

    Usage: make_movie.py upscale [continuous]"""
    seg_files = ordered_segment_files()
    if not seg_files:
        print("No segment videos found to upscale.", flush=True)
        return
    out_dir = upscaled_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Upscaling {len(seg_files)} segment(s) to {UPSCALE_RESOLUTION} "
          f"with {UPSCALE_MODEL} -> {out_dir}/", flush=True)

    done, failed = [], []
    for seg, src in seg_files:
        dest = out_dir / src.name
        if dest.exists():
            print(f"  [SKIP] {dest.name} already upscaled", flush=True)
            done.append(seg["id"])
            continue
        print(f"[UPSCALE] segment {seg['id']}: {src.name}", flush=True)
        ok = nanogpt_video.upscale(
            src, dest, NANOGPT_API_KEY,
            resolution=UPSCALE_RESOLUTION, model=UPSCALE_MODEL,
        )
        (done if ok else failed).append(seg["id"])

    print(f"\n[DONE] upscaled {len(done)}/{len(seg_files)} segment(s) "
          f"-> {out_dir}/", flush=True)
    if failed:
        print(f"Failed segments: {failed}", flush=True)


def _int_args():
    """Integer command arguments (e.g. a segment id, a revision number),
    ignoring the `continuous` cut token."""
    out = []
    for a in sys.argv[2:]:
        if a.lower() in ("continuous", "cont", "--continuous"):
            continue
        try:
            out.append(int(a))
        except ValueError:
            pass
    return out


def run_regenerate():
    """Re-roll ONE segment as a NEW versioned take, written into the SAME videos
    folder (seg<NN>_v<N>.mp4). The original and every prior take are kept; nothing
    is overwritten and no file name is reused. The manifest is NOT changed -- so
    the film is untouched until you review the take and select it with
    `make_movie.py use <id> <version>`.

    Usage: make_movie.py regenerate <segment_id> [continuous]"""
    ids = _int_args()
    if not ids:
        print("Usage: make_movie.py regenerate <segment_id> [continuous]",
              flush=True)
        return
    seg_id = ids[0]
    if seg_id not in SEG_BY_ID:
        print(f"Unknown segment id {seg_id}. Known: "
              f"{[s['id'] for s in SEGMENTS]}", flush=True)
        return

    vdir = videos_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    ver = next_video_version(seg_id)
    dest = vdir / f"seg{seg_id:02d}_v{ver}.mp4"
    cut = "continuous" if CONTINUOUS else "standard"
    print(f"[REGENERATE] segment {seg_id} ({cut}) -> {dest.name}", flush=True)
    if _generate_one_to_dest(SEG_BY_ID[seg_id], dest):
        print(f"\n[DONE] saved {dest}", flush=True)
        print(f"The film is unchanged. Review the take, then put it in the cut with:\n"
              f"  python3 make_movie.py use {seg_id} {ver}"
              f"{' continuous' if CONTINUOUS else ''}", flush=True)
    else:
        print(f"\n[FAIL] could not regenerate segment {seg_id}", flush=True)


def run_use():
    """Select which take of a segment is in the film by setting its manifest
    entry (nothing is moved or renamed -- only the manifest changes).

    Usage: make_movie.py use <segment_id> <version> [continuous]
      version: a number (2, 3, ...), 'base'/'1' (the original segNN.mp4), 'vN',
      or an explicit filename. The chosen file must exist in the videos folder."""
    args = [a for a in sys.argv[2:]
            if a.lower() not in ("continuous", "cont", "--continuous")]
    if len(args) < 2:
        print("Usage: make_movie.py use <segment_id> <version> [continuous]\n"
              "  e.g. use 7 2   (seg07_v2.mp4)  |  use 7 base   (seg07.mp4)",
              flush=True)
        return
    try:
        seg_id = int(args[0])
    except ValueError:
        print(f"Bad segment id: {args[0]}", flush=True)
        return
    if seg_id not in SEG_BY_ID:
        print(f"Unknown segment id {seg_id}. Known: "
              f"{[s['id'] for s in SEGMENTS]}", flush=True)
        return
    ver = args[1]
    fname = Path(ver).name if ver.endswith(".mp4") else version_video_name(seg_id, ver)
    if not (videos_dir() / fname).exists():
        avail = [p.name for p in segment_versions(seg_id)]
        print(f"{fname} not found in {videos_dir().name}/. Available takes: "
              f"{avail or '(none -- regenerate first)'}", flush=True)
        return
    selections = read_manifest()
    for seg in SEGMENTS:                 # keep the manifest complete
        selections.setdefault(seg["id"], default_take(seg["id"]))
    selections[seg_id] = fname
    write_manifest(selections)
    print(f"[MANIFEST] segment {seg_id} -> {fname}", flush=True)
    print(f"  {manifest_path()}", flush=True)


def run_manifest():
    """Build or refresh the videos-folder manifest -- the ordered lineup of the
    current take of every segment -- and print it. Existing explicit choices are
    kept (if their file still exists); other slots default to the base/earliest
    take on disk. Run this before assembling, or any time to see the lineup.

    Usage: make_movie.py manifest [continuous]"""
    sel = refresh_manifest()
    cut = "continuous" if CONTINUOUS else "standard"
    print(f"[MANIFEST] {manifest_path()} ({cut} cut):", flush=True)
    for seg in SEGMENTS:
        fname = sel.get(seg["id"])
        flag = "" if (videos_dir() / fname).exists() else "   <-- MISSING"
        print(f"  seg {seg['id']:>3}  {fname}{flag}   {seg['title']}", flush=True)


def run_stitch():
    """Concatenate all segment videos into one file using ffmpeg. (For an
    editable assembly instead, use `palmier` to import the segments into the
    Palmier Pro AI editor.)"""
    vdir = videos_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    seg_files = [path for _, path in ordered_segment_files()]
    if not seg_files:
        print("No segment videos found to stitch.", flush=True)
        return

    print(f"Found {len(seg_files)} segment(s) to stitch:", flush=True)
    for sf in seg_files:
        print(f"  {sf.name}", flush=True)
    answer = input("\nStitch these into a single video? (y/n): ").strip().lower()
    if answer not in ("y", "yes"):
        print("Stitch cancelled.", flush=True)
        return

    concat_list = vdir / "_concat_list.txt"
    with open(concat_list, "w") as f:
        for sf in seg_files:
            f.write(f"file '{sf.resolve()}'\n")

    suffix = "_continuous" if CONTINUOUS else ""
    output = BASE_DIR / f"{FILM_SLUG}{suffix}_full.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy", str(output),
    ]
    print(f"Stitching {len(seg_files)} segments -> {output.name}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        size = output.stat().st_size / 1e6
        print(f"[DONE] {output} ({size:.1f} MB)", flush=True)
    else:
        print(f"[ERROR] ffmpeg failed:\n{result.stderr[-500:]}", flush=True)
    concat_list.unlink(missing_ok=True)

    # The film is assembled -- write the numbered segment list to the
    # screenplay folder so the user can review it and flag any to regenerate.
    if result.returncode == 0:
        write_segment_list()


def run_palmier():
    """Prepare the finished segments for the Palmier Pro AI editor instead of
    stitching them into a flat file. Writes palmier_import.txt -- every segment
    video in FILM ORDER, with its absolute path, title, and duration -- a
    manifest Claude reads and imports into Palmier Pro through that server's MCP
    tools (import_media with each path, then add_clips to lay the clips end to
    end on one video track). Nothing is concatenated here: the cut is assembled
    and finished inside Palmier, where the user can trim, reorder, add music,
    titles, transitions, and effects. Run after `renumber`, same as `stitch`."""
    seg_files = ordered_segment_files()
    if not seg_files:
        print("No segment videos found to import.", flush=True)
        return

    suffix = "_continuous" if CONTINUOUS else ""
    label = "continuous version" if CONTINUOUS else "standard cut"
    total_seconds = sum(seg["seconds"] for seg, _ in seg_files)
    lines = [
        f"{FILM_TITLE} -- PALMIER PRO IMPORT MANIFEST ({label})",
        f"{len(seg_files)} segment(s) in film order -- total ~{total_seconds}s "
        f"(~{total_seconds / 60:.1f} min)",
        "Import each absolute path below into Palmier Pro (import_media with",
        "source.path), then place the clips end to end IN THIS ORDER on one",
        "video track (add_clips). Assemble and finish the cut inside Palmier --",
        "nothing is concatenated by this script.",
        "=" * 70,
        "",
    ]
    print(f"Found {len(seg_files)} segment(s) to import (film order):", flush=True)
    for i, (seg, path) in enumerate(seg_files, 1):
        lines.append(f"{i:>3}. {seg['title']}  ({seg['seconds']}s)")
        lines.append(f"     {path.resolve()}")
        lines.append("")
        print(f"  {i:>3}. {path.name}  -- {seg['title']}", flush=True)
    out = BASE_DIR / f"palmier_import{suffix}.txt"
    out.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {out}", flush=True)
    print("Ask Claude to import these into Palmier Pro and assemble the cut on "
          "the timeline.", flush=True)


def generate_report():
    """Print a report of all segments and their generation status."""
    tasks = load_tasks()
    label = "continuous version" if CONTINUOUS else "standard"
    print(f"\n{'=' * 70}")
    print(f"{FILM_TITLE} -- SEGMENT GENERATION REPORT ({label})")
    print(f"{'=' * 70}\n")

    succeeded = []
    failed_segs = []
    missing = []

    for seg in SEGMENTS:
        s = str(seg["id"])
        on_disk = video_path(seg["id"]).exists()
        info = tasks.get(s, {})
        status = info.get("status", "not submitted")
        ext = " [continues previous]" if continues_prev(seg) else ""

        if on_disk:
            succeeded.append(seg["id"])
            mark = "OK"
        elif status in ("failed", "submit_error", "skipped"):
            failed_segs.append(seg["id"])
            mark = "FAILED"
        else:
            missing.append(seg["id"])
            mark = status.upper()

        print(f"  [{mark:>12}] Segment {seg['id']:02d} -- {seg['title']}{ext}")

    print(f"\n{'- ' * 35}")
    print(f"  Succeeded:  {len(succeeded)}/{len(SEGMENTS)}")
    if failed_segs:
        print(f"  Failed:     {failed_segs}")
    if missing:
        print(f"  Missing:    {missing}")
    print()


def write_segment_list():
    """Write a numbered list of every segment to the screenplay folder (next to
    the screenplay/segments scripts) so the finished film can be reviewed shot
    by shot. Each entry shows the segment's status, so the user can point at any
    segment to regenerate. Run automatically after a successful stitch, and
    available on its own as `make_movie.py list [continuous]`."""
    tasks = load_tasks()
    suffix = "_continuous" if CONTINUOUS else ""
    label = "continuous version" if CONTINUOUS else "standard cut"
    total_seconds = sum(s["seconds"] for s in SEGMENTS)
    lines = [
        f"{FILM_TITLE} -- SEGMENT LIST ({label})",
        f"{len(SEGMENTS)} segments -- total ~{total_seconds}s "
        f"(~{total_seconds / 60:.1f} min)",
        "Review each segment below; tell Claude the numbers of any to regenerate.",
        "=" * 70,
        "",
    ]
    for seg in SEGMENTS:
        info = tasks.get(str(seg["id"]), {})
        on_disk = video_path(seg["id"]).exists()
        if on_disk:
            provider = info.get("provider")
            status = f"ready{f' via {provider}' if provider else ''}"
        else:
            status = info.get("status", "not generated")
        ext = "  [CONTINUES PREVIOUS]" if continues_prev(seg) else ""
        lines.append(f"{seg['id']:>3}. {seg['title']}  ({seg['seconds']}s)  "
                     f"[{status}]{ext}")
        chars = ", ".join(seg["characters"]) if seg["characters"] else "none"
        lines.append(f"     characters: {chars}")
        desc = seg.get("action") or seg.get("opening")
        if desc:
            lines.append(f"     {desc}")
        lines.append("")
    out = SCREENPLAY_DIR / f"segment_list{suffix}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out}", flush=True)
    print("Review the segment list and tell me if any segments should be "
          "regenerated.", flush=True)


def export_failures():
    """Create a failed_segments/ folder with prompts and assets for manual regen."""
    tasks = load_tasks()
    failed = []
    for seg in SEGMENTS:
        s = str(seg["id"])
        info = tasks.get(s, {})
        if (info.get("status") in ("failed", "submit_error")
                and not video_path(seg["id"]).exists()):
            failed.append(seg)

    if not failed:
        print("No failed segments to export.", flush=True)
        return

    suffix = "_continuous" if CONTINUOUS else ""
    out_dir = BASE_DIR / f"failed_segments{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"{FILM_TITLE} -- FAILED SEGMENT REGENERATION REPORT"
        f"{' (continuous version)' if CONTINUOUS else ''}",
        f"{'=' * 70}",
        "",
    ]

    for seg in failed:
        seg_id = seg["id"]
        info = tasks.get(str(seg_id), {})
        payload = build_prompt(seg)
        lines.append(f"SEGMENT {seg_id:02d} -- {seg['title']}"
                     + (" [CONTINUES PREVIOUS]" if continues_prev(seg) else ""))
        lines.append(f"  duration: {seg['seconds']}s")
        lines.append(f"  model: {SEEDANCE_MODEL}")
        lines.append(f"  resolution: {SEEDANCE_RESOLUTION}")
        lines.append(f"  aspect_ratio: {SEEDANCE_ASPECT}")
        lines.append(f"  mode: {SEEDANCE_MODE}")
        lines.append("")
        if payload["reference_images"]:
            lines.append("  REFERENCE IMAGES:")
            for i, url in enumerate(payload["reference_images"], 1):
                lines.append(f"    @image{i}: {url}")
        if payload.get("last_frame"):
            lines.append("  LOCKED LAST FRAME:")
            lines.append(f"    last_frame: {payload['last_frame']}")
        if "reference_audio" in payload:
            lines.append("  REFERENCE AUDIO:")
            for i, url in enumerate(payload["reference_audio"], 1):
                lines.append(f"    @audio{i}: {url}")
        lines.append("")
        lines.append("  PROMPT:")
        for pline in payload["prompt"].split("\n"):
            lines.append(f"    {pline}")
        lines.append("")
        lines.append("-" * 70)
        lines.append("")

        for i, url in enumerate(payload["reference_images"], 1):
            fname = (f"seg{seg_id:02d}_opening.jpg" if i == 1
                     else f"seg{seg_id:02d}_ref_image{i}.jpg")
            dest = out_dir / fname
            if not dest.exists() and url.startswith("http"):
                try:
                    print(f"  Downloading {fname}...", end="", flush=True)
                    download(url, dest)
                except Exception as e:
                    print(f" ERROR: {e}", flush=True)

        if "reference_audio" in payload:
            multi = len(payload["reference_audio"]) > 1
            for i, url in enumerate(payload["reference_audio"], 1):
                name = (f"seg{seg_id:02d}_audio_ref{i}.mp3" if multi
                        else f"seg{seg_id:02d}_audio_ref.mp3")
                dest = out_dir / name
                if not dest.exists() and url.startswith("http"):
                    try:
                        print(f"  Downloading {dest.name}...", end="", flush=True)
                        download(url, dest)
                    except Exception as e:
                        print(f" ERROR: {e}", flush=True)

    report_path = out_dir / "report.txt"
    report_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {report_path}", flush=True)
    print(f"Assets saved to {out_dir}/", flush=True)
    print(f"Failed segments: {[s['id'] for s in failed]}", flush=True)


MODES = {
    "prompts": write_prompts,
    "generate": run_generate,
    "rest": run_generate,  # alias
    "status": show_status,
    "fetch": run_fetch,
    "renumber": run_renumber,
    "upscale": run_upscale,
    "regenerate": run_regenerate,
    "regen": run_regenerate,  # alias
    "use": run_use,
    "select": run_use,  # alias
    "manifest": run_manifest,
    "lineup": run_manifest,  # alias
    "stitch": run_stitch,
    "palmier": run_palmier,
    "editor": run_palmier,  # alias
    "import": run_palmier,  # alias
    "report": generate_report,
    "list": write_segment_list,
    "segments": write_segment_list,  # alias
    "failures": export_failures,
}


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    mode = args[0] if args else "prompts"
    CONTINUOUS = any(a in ("continuous", "cont", "--continuous") for a in args[1:])

    if mode not in MODES:
        print(f"Usage: make_movie.py {{{' | '.join(MODES)}}} [continuous]")
        sys.exit(1)

    if CONTINUOUS and mode in ("prompts", "generate", "rest"):
        # Building prompts or generating is the moment to surface and record
        # exactly which cuts the locked-frame continuity joins.
        joins, problems = validate_continuations()
        print_continuation_report(joins, problems)
        save_continuation_list(joins, problems)

    MODES[mode]()
