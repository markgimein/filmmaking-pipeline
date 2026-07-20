#!/usr/bin/env python3
"""
FILM IMAGE GENERATION TEMPLATE
================================
Generates reference images for characters, locations, and segment opening
stills using OpenAI gpt-image-2 (ChatGPT Images 2.0).

Usage:
  python3 generate_images.py characters     # generate character reference images
  python3 generate_images.py locations      # generate location reference images
  python3 generate_images.py stills         # generate segment opening frames
  python3 generate_images.py descriptions   # write frame_descriptions.txt
  python3 generate_images.py all            # run characters, locations, then stills

Before running:
  1. Copy film_config_template.py to this folder as film_config.py
  2. Fill in all [FILL IN] sections in film_config.py
  3. pip install openai

The program skips images that already exist on disk, so it's safe to re-run
after fixing errors or adding new segments.
"""

import base64
import concurrent.futures
import json
import re
import shutil
import sys
import time
from pathlib import Path

from openai import OpenAI

# Import the film-specific configuration
from film_config import (
    OPENAI_API_KEY, OPENAI_MODEL, IMAGE_SIZE,
    BASE_DIR, CHARACTERS_DIR, LOCATIONS_DIR, STILLS_DIR, SCREENPLAY_DIR,
    FILM_TITLE, STILL_PREAMBLE, CHAR_PREAMBLE, LOCATION_PREAMBLE,
    CHARACTERS, ANGLES, LOCATIONS, VISUAL_STYLES, SEGMENTS,
)

client = OpenAI(api_key=OPENAI_API_KEY)

PROGRESS_FILE = BASE_DIR / "stills_progress.json"


# === PROGRESS TRACKING ===

def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# === CORE GENERATION ===

def _save_b64(result, output_path):
    image_bytes = base64.b64decode(result.data[0].b64_json)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(image_bytes)


def generate_image(prompt, reference_paths, output_path, retries=3):
    """Generate one image with gpt-image-2; uses edit() when references exist."""
    output_path = Path(output_path)
    if output_path.exists():
        print(f"  [SKIP] exists: {output_path.name}", flush=True)
        return True

    for attempt in range(1, retries + 1):
        file_handles = []
        try:
            if reference_paths:
                for rp in reference_paths:
                    file_handles.append(open(rp, "rb"))
                result = client.images.edit(
                    model=OPENAI_MODEL,
                    image=file_handles,
                    prompt=prompt,
                    size=IMAGE_SIZE,
                )
            else:
                result = client.images.generate(
                    model=OPENAI_MODEL,
                    prompt=prompt,
                    size=IMAGE_SIZE,
                )
            _save_b64(result, output_path)
            print(f"  [OK] {output_path.name}", flush=True)
            return True
        except Exception as e:
            print(f"  [ERROR] {output_path.name} (attempt {attempt}/{retries}): {e}", flush=True)
            time.sleep(5 * attempt)
        finally:
            for fh in file_handles:
                fh.close()
    return False


# === CHARACTER REFERENCES ===

# Default reference angles per character: a front portrait, a three-quarter
# view, and a full-body shot. (Strict side profile is omitted -- front +
# three-quarter triangulate the face well enough, and full body anchors the
# costume.) Override per character with "ref_angles" in CHARACTERS.
DEFAULT_REF_ANGLES = ("front_full_face", "three_quarter", "full_body")


def char_ref_path(prefix, angle, variant=None):
    suffix = f"_{variant}" if variant else ""
    return CHARACTERS_DIR / f"{prefix}{suffix}_{angle}.jpg"


def char_refs(prefix, angles=DEFAULT_REF_ANGLES, variant=None):
    """Return existing reference image paths for a character."""
    paths = []
    for angle in angles:
        p = char_ref_path(prefix, angle, variant)
        if p.exists():
            paths.append(p)
    return paths


def generate_character(prefix, desc, variant_name=None, variant_extra=""):
    """Generate the reference images for one character (or variant).

    The front portrait is generated first; the remaining angles use it as a
    reference so every view shows the same person. By default three views are
    made (front, three-quarter, full body); override with "ref_angles".
    """
    char = CHARACTERS[prefix]
    angles_to_use = char.get("ref_angles", DEFAULT_REF_ANGLES)
    full_desc = desc + (" " + variant_extra if variant_extra else "")

    front_path = char_ref_path(prefix, "front_full_face", variant_name)
    # A variant's front portrait is generated FROM the base front portrait, so
    # the face never drifts between wardrobe variants (2026-07-10).
    front_refs = []
    if variant_name:
        base_front = char_ref_path(prefix, "front_full_face")
        if base_front.exists():
            front_refs = [base_front]
    if front_refs:
        front_prompt = (
            f"{CHAR_PREAMBLE}\n\nCHARACTER: {full_desc}\n\n"
            f"The attached image shows this exact character. Match the face "
            f"and hair EXACTLY -- same person; only the wardrobe and styling "
            f"change as described.\n\nSHOT: {ANGLES['front_full_face']}"
        )
    else:
        front_prompt = f"{CHAR_PREAMBLE}\n\nCHARACTER: {full_desc}\n\nSHOT: {ANGLES['front_full_face']}"
    ok = generate_image(front_prompt, front_refs, front_path)
    if not ok:
        print(f"  [FAIL] {prefix}: front portrait failed, skipping other angles", flush=True)
        return 0

    count = 1
    for angle in angles_to_use:
        if angle == "front_full_face":
            continue
        out = char_ref_path(prefix, angle, variant_name)
        prompt = (
            f"{CHAR_PREAMBLE}\n\nCHARACTER: {full_desc}\n\n"
            f"The attached image shows this exact character. Match the face, "
            f"hair and clothing exactly.\n\nSHOT: {ANGLES[angle]}"
        )
        if generate_image(prompt, [front_path], out):
            count += 1
    return count


def run_characters():
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    tasks = []

    for prefix, char in CHARACTERS.items():
        tasks.append((prefix, char["desc"], None, ""))
        for vname, vdesc in char.get("variants", {}).items():
            tasks.append((prefix, char["desc"], vname, vdesc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(generate_character, p, d, vn, ve): f"{p}{'_'+vn if vn else ''}"
            for p, d, vn, ve in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            label = futures[fut]
            n = fut.result()
            total += n
            print(f"[CHARACTER DONE] {label}: {n} images", flush=True)

    expected = sum(
        len(c.get("ref_angles", ANGLES)) * (1 + len(c.get("variants", {})))
        for c in CHARACTERS.values()
    )
    print(f"\nCHARACTERS DONE: {total}/{expected} images.", flush=True)


# === LOCATION REFERENCES ===

def location_ref_path(prefix, variant=None):
    suffix = f"_{variant}" if variant else "default"
    return LOCATIONS_DIR / f"{prefix}_{suffix}.jpg"


def generate_location(prefix):
    loc = LOCATIONS[prefix]
    count = 0
    variants = loc.get("variants", {})
    if not variants:
        variants = {"default": ""}

    for vname, vdesc in variants.items():
        out = location_ref_path(prefix, vname if vname != "default" else None)
        extra = f" {vdesc}" if vdesc else ""
        prompt = f"{LOCATION_PREAMBLE}\n\nLOCATION: {loc['desc']}{extra}"
        if generate_image(prompt, [], out):
            count += 1
    return count


def run_locations():
    LOCATIONS_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(generate_location, p): p for p in LOCATIONS}
        for fut in concurrent.futures.as_completed(futures):
            prefix = futures[fut]
            n = fut.result()
            total += n
            print(f"[LOCATION DONE] {prefix}: {n} images", flush=True)

    expected = sum(max(1, len(loc.get("variants", {}))) for loc in LOCATIONS.values())
    print(f"\nLOCATIONS DONE: {total}/{expected} images.", flush=True)


# === SEGMENT STILLS ===

def frame_path(seg_id, moment):
    return STILLS_DIR / f"seg{seg_id:02d}_{moment}.jpg"


def build_still_prompt(seg, moment, desc):
    style = VISUAL_STYLES.get(seg["style"], seg["style"])
    return (
        f"{STILL_PREAMBLE}\n\n"
        f"VISUAL STYLE: {style}\n\n"
        f"{moment.upper()} FRAME of segment {seg['id']} ({seg['title']}):\n{desc}"
    )


def refs_for_segment(seg, max_refs=9):
    """Reference images attached when generating a segment's opening still:
    the segment's location image (if it names one via "location") first, then
    each character's reference views. Capped so multi-character shots don't
    overload the edit call.

    Two optional per-segment overrides:
      - extra_ref_images: a list of extra look-reference paths (relative to
        BASE_DIR) the user wants matched -- e.g. a specific costume or actor
        reference. Placed early so the cap never drops them.
      - char_ref_variant: a {character_prefix: variant} map to pull a different
        reference set for a character, e.g. {"hero": "pajamas"} to use the
        hero_pajamas_*.jpg refs instead of the default ones.
    """
    refs = []
    loc_key = seg.get("location")
    if loc_key:
        loc_path = location_ref_path(loc_key, seg.get("location_variant"))
        if loc_path.exists():
            refs.append(loc_path)
    for extra in seg.get("extra_ref_images", []):
        p = BASE_DIR / extra
        if p.exists():
            refs.append(p)
        else:
            print(f"  [WARN] seg {seg['id']}: extra ref not found: {p}", flush=True)
    variant_map = seg.get("char_ref_variant", {})
    for prefix in seg["characters"]:
        refs.extend(char_refs(prefix, variant=variant_map.get(prefix)))
    return refs[:max_refs]


# === STILL VERSIONS + MANIFEST ===
# Re-stilling a segment never overwrites: the new opening frame is written into
# THIS stills/ folder as a versioned file (seg07_opening.jpg is the original,
# seg07_opening_v2.jpg, _v3.jpg, ... are re-rolls). A manifest.txt in stills/
# records which opening still each segment uses. `upload_images.py stills` uploads
# exactly the chosen stills, so video generation opens on the frames you picked.
# Re-stilling does NOT change the manifest; select with
#   generate_images.py usestill <id> <version>

def still_manifest_path():
    return STILLS_DIR / "manifest.txt"


def _still_seg_id(name):
    m = re.match(r"seg0*(\d+)_opening", Path(name).name)
    return int(m.group(1)) if m else None


def base_still_name(seg_id):
    return f"seg{seg_id:02d}_opening.jpg"


def still_version_name(seg_id, ver):
    """1/'base'/'v1' -> seg NN_opening.jpg; k>=2 (or 'vK') -> segNN_opening_v{k}.jpg."""
    s = str(ver).strip().lower().lstrip("v")
    if s in ("", "1", "base"):
        return base_still_name(seg_id)
    return f"seg{seg_id:02d}_opening_v{int(s)}.jpg"


def still_versions(seg_id):
    """Every opening-still take for a segment: base first, then _v2, _v3, ..."""
    out = []
    base = STILLS_DIR / base_still_name(seg_id)
    if base.exists():
        out.append(base)
    vers = []
    for p in STILLS_DIR.glob(f"seg{seg_id:02d}_opening_v*.jpg"):
        m = re.fullmatch(rf"seg0*{seg_id}_opening_v(\d+)", p.stem)
        if m:
            vers.append((int(m.group(1)), p))
    out.extend(p for _, p in sorted(vers))
    return out


def next_still_version(seg_id):
    n = 2
    while (STILLS_DIR / f"seg{seg_id:02d}_opening_v{n}.jpg").exists():
        n += 1
    return n


def default_still(seg_id):
    takes = still_versions(seg_id)
    return takes[0].name if takes else base_still_name(seg_id)


def read_still_manifest():
    path = still_manifest_path()
    sel = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sid = _still_seg_id(line)
            if sid is not None:
                sel[sid] = line
    return sel


def write_still_manifest(selections):
    lines = [
        f"# {FILM_TITLE} -- STILL MANIFEST (opening frames)",
        "# The chosen opening still for each segment -- one filename per line, in",
        "# film order. Re-stilling adds seg<NN>_opening_v<N>.jpg but does NOT change",
        "# this list. Choose a still with:",
        "#   python3 generate_images.py usestill <id> <version>   (e.g. usestill 7 2)",
        "# or edit the filename below, then run `upload_images.py stills` so video",
        "# generation uses these exact frames. Lines starting with # are ignored.",
        "#",
    ]
    for seg in SEGMENTS:
        sid = seg["id"]
        fname = selections.get(sid) or default_still(sid)
        flag = "" if (STILLS_DIR / fname).exists() else "   <-- MISSING"
        lines.append(f"#  seg {sid:>3}  {seg['title']}{flag}")
        lines.append(fname)
    still_manifest_path().parent.mkdir(parents=True, exist_ok=True)
    still_manifest_path().write_text("\n".join(lines) + "\n")


def refresh_still_manifest():
    existing = read_still_manifest()
    sel = {}
    for seg in SEGMENTS:
        sid = seg["id"]
        chosen = existing.get(sid)
        sel[sid] = chosen if (chosen and (STILLS_DIR / chosen).exists()) \
            else default_still(sid)
    write_still_manifest(sel)
    return sel


def current_still_path(seg_id):
    """The chosen opening still Path for a segment (manifest-driven, built if
    absent)."""
    sel = read_still_manifest() or refresh_still_manifest()
    return STILLS_DIR / (sel.get(seg_id) or default_still(seg_id))


def process_segment(seg):
    """Generate the opening frame for this segment."""
    opening = frame_path(seg["id"], "opening")

    generate_image(
        build_still_prompt(seg, "opening", seg["opening"]),
        refs_for_segment(seg),
        opening,
    )
    return str(opening)


def copy_closing_frames():
    """Continuous version: a continuation's OPENING still is also the PREVIOUS
    segment's locked CLOSING frame. For every segment flagged continues_previous,
    copy its opening still onto the previous segment as that segment's "zclosing"
    image (seg<prev>_zclosing.jpg). The previous shot is then generated to END on
    that exact frame and the continuation BEGINS on it, so the cut is seamless.
    Only segments that are followed by a continuation get a closing image."""
    made = 0
    for i, seg in enumerate(SEGMENTS):
        if i == 0 or not seg.get("continues_previous"):
            continue
        prev = SEGMENTS[i - 1]
        src = current_still_path(seg["id"])  # the continuation's CHOSEN opening still
        dst = frame_path(prev["id"], "zclosing")
        if not src.exists():
            print(f"  [WARN] seg {seg['id']:02d} opening missing; cannot make the "
                  f"closing frame for seg {prev['id']:02d}", flush=True)
            continue
        shutil.copy2(src, dst)
        made += 1
        print(f"  [CLOSING] seg {prev['id']:02d} zclosing <- seg {seg['id']:02d} "
              f"opening  ({dst.name})", flush=True)
    if made:
        print(f"Copied {made} closing (zclosing) frame(s) for continuations.",
              flush=True)
    return made


def run_stills():
    STILLS_DIR.mkdir(parents=True, exist_ok=True)

    progress = load_progress()

    def process_one(seg):
        print(f"\n=== SEGMENT {seg['id']:02d} -- {seg['title']} ===", flush=True)
        result = process_segment(seg)
        progress[str(seg["id"])] = {
            "opening": str(frame_path(seg["id"], "opening")),
        }
        save_progress(progress)

    # Every segment is an independent shot, so every segment gets an opening
    # still (its locked first frame). Already-generated stills are skipped.
    # 8 concurrent workers (raised from 3, 2026-07-09): generate_image()'s
    # retry-with-backoff absorbs any 429s if the account tier throttles us.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(process_one, SEGMENTS))

    # Continuous version: copy each continuation's opening onto the previous
    # segment as its locked closing (zclosing) frame.
    copy_closing_frames()

    # Record the chosen opening still for every segment (defaults to the base
    # frame just generated). Re-stilling later adds versions without touching it.
    refresh_still_manifest()

    done = len(list(STILLS_DIR.glob("seg*_opening.jpg")))
    print(f"\nSTILLS DONE: {done}/{len(SEGMENTS)} opening frames on disk. "
          f"Manifest: {still_manifest_path()}", flush=True)


# === RE-STILL (versioned) + SELECTION ===

def run_restill():
    """Re-roll the opening still of one or more segments as NEW versioned files
    (seg<NN>_opening_v<N>.jpg) -- the original and prior versions are kept and
    nothing is overwritten. The manifest is NOT changed; choose a still with
    `generate_images.py usestill <id> <version>`.

    Usage: generate_images.py restill <id> [<id> ...]"""
    ids = [int(a) for a in sys.argv[2:] if a.isdigit()]
    if not ids:
        print("Usage: generate_images.py restill <id> [<id> ...]", flush=True)
        return
    by_id = {s["id"]: s for s in SEGMENTS}
    for sid in ids:
        if sid not in by_id:
            print(f"  [SKIP] unknown segment id {sid}", flush=True)
            continue
        seg = by_id[sid]
        ver = next_still_version(sid)
        dest = STILLS_DIR / f"seg{sid:02d}_opening_v{ver}.jpg"
        print(f"\n=== RESTILL seg{sid:02d} -- {seg['title']} -> {dest.name} ===",
              flush=True)
        generate_image(build_still_prompt(seg, "opening", seg["opening"]),
                       refs_for_segment(seg), dest)
        if dest.exists():
            print(f"  [DONE] {dest.name}. The film is unchanged. Select it with:\n"
                  f"    python3 generate_images.py usestill {sid} {ver}\n"
                  f"  then re-upload so video gen uses it: "
                  f"python3 upload_images.py stills", flush=True)


def run_usestill():
    """Select which opening still a segment uses, by setting its manifest entry
    (nothing is moved or renamed). After selecting, run `upload_images.py stills`
    so video generation opens on the chosen frame.

    Usage: generate_images.py usestill <id> <version>
      version: a number (2, 3, ...), 'base'/'1', 'vN', or an explicit filename."""
    args = sys.argv[2:]
    if len(args) < 2:
        print("Usage: generate_images.py usestill <id> <version>  "
              "(e.g. usestill 7 2  |  usestill 7 base)", flush=True)
        return
    try:
        sid = int(args[0])
    except ValueError:
        print(f"Bad segment id: {args[0]}", flush=True)
        return
    if sid not in {s["id"] for s in SEGMENTS}:
        print(f"Unknown segment id {sid}.", flush=True)
        return
    ver = args[1]
    fname = Path(ver).name if ver.endswith(".jpg") else still_version_name(sid, ver)
    if not (STILLS_DIR / fname).exists():
        avail = [p.name for p in still_versions(sid)]
        print(f"{fname} not found in stills/. Available: "
              f"{avail or '(none -- restill first)'}", flush=True)
        return
    sel = read_still_manifest()
    for seg in SEGMENTS:
        sel.setdefault(seg["id"], default_still(seg["id"]))
    sel[sid] = fname
    write_still_manifest(sel)
    print(f"[STILL MANIFEST] segment {sid} -> {fname}", flush=True)
    print("  Now re-upload so video generation uses it: "
          "python3 upload_images.py stills", flush=True)


def run_still_manifest():
    """Build or refresh the stills manifest and print it.
    Usage: generate_images.py stillmanifest"""
    sel = refresh_still_manifest()
    print(f"[STILL MANIFEST] {still_manifest_path()}:", flush=True)
    for seg in SEGMENTS:
        fname = sel.get(seg["id"])
        flag = "" if (STILLS_DIR / fname).exists() else "   <-- MISSING"
        print(f"  seg {seg['id']:>3}  {fname}{flag}   {seg['title']}", flush=True)


# === FRAME DESCRIPTIONS EXPORT ===

def write_descriptions():
    lines = [
        FILM_TITLE,
        "Opening frame descriptions for each segment",
        "",
        f"Frames are generated at {IMAGE_SIZE} with OpenAI {OPENAI_MODEL}",
        "(ChatGPT Images 2.0).",
        "",
        "=" * 70,
    ]
    for seg in SEGMENTS:
        lines.append("")
        lines.append(f"SEGMENT {seg['id']} -- {seg['title']} ({seg['seconds']} sec)")
        chars = ", ".join(seg["characters"]) if seg["characters"] else "none"
        lines.append(f"Characters in frame: {chars}")
        lines.append(f"OPENING FRAME: {seg['opening']}")
        lines.append("-" * 70)
    out = SCREENPLAY_DIR / "frame_descriptions.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out}", flush=True)


# === MAIN ===

MODES = {
    "characters": run_characters,
    "locations": run_locations,
    "stills": run_stills,
    "restill": run_restill,
    "usestill": run_usestill,
    "stillmanifest": run_still_manifest,
    "descriptions": write_descriptions,
}


def run_all():
    run_characters()
    run_locations()
    run_stills()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "descriptions"
    if mode == "all":
        run_all()
    elif mode in MODES:
        MODES[mode]()
    else:
        print(f"Unknown mode: {mode}. Use: {' | '.join(MODES)} | all")
        sys.exit(1)
