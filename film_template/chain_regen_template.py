#!/usr/bin/env python3
"""Forward frame-chaining for continuous scenes -- the PREFERRED method.

Continuity is built by carrying a real frame forward, not by locking both ends:

  Generate a shot from an OPENING image + character/audio refs, with **NO**
  locked closing frame. Let Seedance reach its own ending. Then EXTRACT that
  final frame and feed it as the OPENING image of the next shot. The next shot
  begins on the exact frame the previous one ended on, so the cut is seamless --
  and because every shot is an ordinary image-to-video generation (full
  character + audio references, no keyframe-mode restriction), nothing is lost.

Why this is preferred over the locked first-and-last-frame ("keyframe") joins:
  - Each shot keeps its CHARACTER reference images (keyframe mode forbids them).
  - The seam is the previous shot's ACTUAL last frame, so it always matches --
    no provider has to "honor" a locked end frame, nothing drifts or mirror-flips.
  - It runs on the normal generator: **Lunostudio is primary**; if a shot fails
    or returns nothing within LUNO_TIMEOUT_MIN minutes, it falls back to
    **Nano-GPT** (doubao-seedance-2-0). Same provider policy as the rest of the film.
  The tradeoff: a chain is SEQUENTIAL (each shot needs the previous shot's output),
  whereas locked-frame joins generate in parallel. For continuous dialogue/action
  the exact, drift-free seam is worth the sequential cost.

Output goes into the continuous videos folder (videos_continuous/) as a NEW
versioned take (seg<NN>_v<N>.mp4) -- the original seg<NN>.mp4 and every prior
take are kept, nothing is overwritten, and no file name is reused. The film is
unchanged: the manifest is NOT touched. Review the take, then select it with
`make_movie.py use <id> <version> continuous`.

Usage:
  python3 chain_regen.py regen <id> still                 # open from the config still
  python3 chain_regen.py regen <id> frame <prev_video>    # open from prev_video's last frame
  python3 chain_regen.py chainstill <id1> <id2> ...       # id1 from its still, rest chained forward
  python3 chain_regen.py chainfrom <prev_video> <id1> ... # id1 from prev_video's last frame, rest chained
  python3 chain_regen.py chain <a> <b>                    # a from its still, then b chained off a

Optional per-run env (comma-separated segment ids):
  CHAIN_LOCREF=3,4,5       also attach each seg's location reference still (locations/<location>_default.jpg)
  CHAIN_MULTIAUDIO=17,18   attach voice refs for ALL the seg's speakers, not just voice_audio
"""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import certifi

import make_movie as mm

mm.CONTINUOUS = True  # continuous cut: write into videos_continuous/, add CONTINUE_RULE

HERE = Path(__file__).parent
# Re-rolled takes land in the continuous videos folder as versioned files
# (never overwriting); intermediate chained opening frames go to a working dir.
WORK = HERE / "chain_frames"
WORK.mkdir(exist_ok=True)

LUNO_TIMEOUT_MIN = 10   # primary: Lunostudio for this long, then fall back to Nano-GPT
NANO_TIMEOUT_MIN = 40

# Segment ids that also attach their location reference still as an extra @imageN.
# Override per-run with CHAIN_LOCREF, e.g. CHAIN_LOCREF=3,4,5
INCLUDE_LOCATION_REF = {
    int(x) for x in os.environ.get("CHAIN_LOCREF", "").split(",") if x.strip()
}
# Segment ids that attach voice refs for ALL speakers (not just voice_audio).
# Override per-run with CHAIN_MULTIAUDIO, e.g. CHAIN_MULTIAUDIO=17,18
MULTI_AUDIO = {
    int(x) for x in os.environ.get("CHAIN_MULTIAUDIO", "").split(",") if x.strip()
}
_loc_url_cache = {}

# === Minimal Nano-GPT fallback client (self-contained; key/model from film_config
# via make_movie). Submits doubao-seedance-2-0 image-to-video jobs. ===
_SSL = ssl.create_default_context(cafile=certifi.where())
_NANO_BASE = "https://nano-gpt.com"


def _nano_req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(_NANO_BASE + path, data=data, method=method)
    r.add_header("x-api-key", mm.NANOGPT_API_KEY)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=120, context=_SSL) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_httperror": e.code, "_body": e.read().decode()[:800]}


def _nano_submit(body):
    resp = _nano_req("POST", "/api/generate-video", body)
    return (resp.get("runId") or resp.get("id")), resp


def _nano_status(run_id):
    return _nano_req("GET", f"/api/video/status?requestId={run_id}")


def _nano_find_url(obj):
    if isinstance(obj, str):
        return obj if obj.startswith("http") and ".mp4" in obj else None
    if isinstance(obj, dict):
        for v in obj.values():
            u = _nano_find_url(v)
            if u:
                return u
    if isinstance(obj, list):
        for v in obj:
            u = _nano_find_url(v)
            if u:
                return u
    return None


def location_ref_url(seg):
    """Upload (once) the segment's location reference still and return its CDN URL."""
    loc = seg.get("location")
    if not loc:
        return None
    if loc in _loc_url_cache:
        return _loc_url_cache[loc]
    path = HERE / "locations" / f"{loc}_default.jpg"
    if not path.exists():
        print(f"  [WARN] no location image {path.name}", flush=True)
        return None
    url = mm.cdn_upload(path)
    _loc_url_cache[loc] = url
    print(f"  [UPLOAD] location ref {loc} -> {url}", flush=True)
    return url


def multi_audio_for(seg):
    """Ordered (char_key, voice_url) for every speaker in seg that has a voice
    reference -- the voice_audio character FIRST (so it stays @audio1, matching
    build_prompt's voice line), then the other cast members in order."""
    primary = seg.get("voice_audio")
    order = []
    if primary and primary in mm.VOICE_REFS:
        order.append(primary)
    for key in seg["characters"]:
        if key != primary and key in mm.VOICE_REFS and key not in order:
            order.append(key)
    return [(k, mm.gdrive(mm.VOICE_REFS[k])) for k in order]


def seg_by_id(sid):
    return next(s for s in mm.SEGMENTS if s["id"] == int(sid))


def next_rev_path(sid):
    """Next free videos_continuous/seg<NN>_v<N>.mp4 -- a new versioned take in the
    cut's videos folder (the base seg<NN>.mp4 is v1). Never overwrites."""
    ver = mm.next_video_version(int(sid))
    return mm.videos_dir() / f"seg{int(sid):02d}_v{ver}.mp4"


def extract_last_frame(video_path, out_path):
    """Grab the final frame of a clip as a JPEG (ffmpeg required)."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    cmd = ["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(video_path),
           "-frames:v", "1", "-q:v", "2", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"frame extract failed: {r.stderr[-400:]}")
    print(f"  [FRAME] extracted last frame of {video_path.name} -> {Path(out_path).name}", flush=True)
    return out_path


def opening_url_for(sid, source, prev_video=None):
    """Return a public CDN URL for the segment's OPENING image.
    source='still' -> the config opening still; source='frame' -> the last frame
    of prev_video, extracted and uploaded to the Lunostudio CDN."""
    if source == "still":
        return mm.gdrive(mm.GDRIVE_STILL_IDS[int(sid)])
    if source == "frame":
        frame_path = WORK / f"seg{int(sid):02d}_opening_chained.jpg"
        extract_last_frame(prev_video, frame_path)
        url = mm.cdn_upload(frame_path)
        print(f"  [UPLOAD] opening frame -> {url}", flush=True)
        return url
    raise ValueError(source)


def build_luno_payload(seg, opening_url):
    """build_prompt(), then strip the locked closing frame (chaining sends NO
    closing frame) and point @image1 at the chained opening URL."""
    payload = mm.build_prompt(seg)
    payload.pop("last_frame", None)
    # remove the CLOSING_RULE block from the prompt text (no last_frame is sent)
    p = payload["prompt"]
    p = p.replace(mm.CLOSING_RULE + "\n\n", "").replace(mm.CLOSING_RULE + "\n", "").replace(mm.CLOSING_RULE, "")
    payload["prompt"] = p
    # refs[0] is the opening still (@image1) -> override with the chained frame
    refs = payload.get("reference_images") or []
    if refs:
        refs[0] = opening_url
    else:
        refs = [opening_url]
    # optionally attach the scene's location reference image as an extra @imageN
    if seg["id"] in INCLUDE_LOCATION_REF:
        lurl = location_ref_url(seg)
        if lurl:
            refs.append(lurl)
            idx = len(refs)
            payload["prompt"] += (
                f"\n\nLOCATION REFERENCE: @image{idx} shows this scene's setting -- "
                f"match the environment, set dressing, and lighting exactly. It "
                f"contains no characters."
            )
    # optionally attach voice refs for all speakers (voice_audio char stays @audio1)
    if seg["id"] in MULTI_AUDIO:
        pairs = multi_audio_for(seg)
        if pairs:
            payload["reference_audio"] = [u for _, u in pairs]
            notes = "\n".join(
                f"@audio{i} is {mm.CHARACTERS[k]['name']}'s voice -- match it for "
                f"{mm.CHARACTERS[k]['name']}'s lines only."
                for i, (k, _) in enumerate(pairs, 1)
            )
            payload["prompt"] += (
                "\n\nVOICE REFERENCES (each character speaks in their OWN voice):\n" + notes
            )
    payload["reference_images"] = refs
    return payload


def run_luno(seg, payload):
    """PRIMARY generator. Returns the finished video URL, or None to fall back."""
    try:
        resp = mm.api_request("POST", "/api/v1/generate", payload)
    except Exception as e:
        print(f"[LUNO] seg{seg['id']} submit error: {e}", flush=True)
        return None
    task_id = mm.find_field(resp, {"task_id", "taskId", "id"})
    if not task_id:
        print(f"[LUNO] seg{seg['id']} no task id: {json.dumps(resp)[:300]}", flush=True)
        return None
    print(f"[LUNO] seg{seg['id']} submitted task {task_id}", flush=True)
    deadline = time.time() + LUNO_TIMEOUT_MIN * 60
    last = None
    while time.time() < deadline:
        try:
            status, sresp = mm.get_status(str(task_id))
        except Exception as e:
            print(f"[LUNO] seg{seg['id']} poll err: {e}", flush=True)
            time.sleep(15)
            continue
        if status != last:
            print(f"[LUNO] seg{seg['id']} status={status}", flush=True)
            last = status
        if status == "success":
            return mm.find_video_url(sresp)
        if status == "failed":
            print(f"[LUNO] seg{seg['id']} FAILED: {json.dumps(sresp)[:300]}", flush=True)
            return None
        time.sleep(20)
    print(f"[LUNO] seg{seg['id']} no result in {LUNO_TIMEOUT_MIN} min -> Nano-GPT fallback", flush=True)
    return None


def run_nano(seg, opening_url):
    """BACKUP generator: Nano-GPT image-to-video (opening frame + char refs +
    audio, NO last_image). Same chaining inputs as Lunostudio."""
    style = mm.VISUAL_STYLES.get(seg["style"], seg["style"])
    refs = []
    angle_map = seg.get("char_ref_angles", {})
    for key in seg["characters"]:
        for angle in mm.char_ref_angles_for(key, angle_map):
            gk = f"{key}_{angle}"
            if gk in mm.GDRIVE_CHAR_REF_IDS:
                refs.append(mm.gdrive(mm.GDRIVE_CHAR_REF_IDS[gk]))
    prompt = mm.build_openrouter_prompt(seg, style)
    if seg["id"] in MULTI_AUDIO and multi_audio_for(seg):
        pairs = multi_audio_for(seg)
        audios = [u for _, u in pairs]
        prompt += "\n\nVOICE REFERENCES: " + " ".join(
            f"reference audio {i} is {mm.CHARACTERS[k]['name']}'s voice -- use it for "
            f"{mm.CHARACTERS[k]['name']}'s lines only."
            for i, (k, _) in enumerate(pairs, 1))
    else:
        vc = seg.get("voice_audio")
        audios = [mm.gdrive(mm.VOICE_REFS[vc])] if vc and vc in mm.VOICE_REFS else []
    if seg["id"] in INCLUDE_LOCATION_REF:
        lurl = location_ref_url(seg)
        if lurl:
            refs.append(lurl)
            prompt += (
                "\n\nLOCATION REFERENCE: one of the provided reference images shows "
                "this scene's setting (no characters) -- match its environment, set "
                "dressing, and lighting exactly."
            )
    body = {
        "model": getattr(mm, "NANOGPT_MODEL", "doubao-seedance-2-0"),
        "prompt": prompt,
        "imageUrl": opening_url,            # chained opening frame (image-to-video)
        "duration": seg["seconds"],
        "resolution": mm.SEEDANCE_RESOLUTION,
        "aspect_ratio": getattr(mm, "SEEDANCE_ASPECT", "16:9"),
    }
    if audios:
        body["reference_audios"] = audios
    if refs:
        body["reference_images"] = refs
    run_id, resp = _nano_submit(body)
    if not run_id:
        print(f"[NANO] seg{seg['id']} submit failed: {json.dumps(resp)[:300]}", flush=True)
        return None
    print(f"[NANO] seg{seg['id']} run {run_id} (${resp.get('cost')})", flush=True)
    deadline = time.time() + NANO_TIMEOUT_MIN * 60
    while time.time() < deadline:
        st = _nano_status(run_id)
        s = json.dumps(st).lower()
        if "completed" in s or "succeeded" in s:
            return _nano_find_url(st)
        if "failed" in s or "error" in s:
            print(f"[NANO] seg{seg['id']} FAILED: {json.dumps(st)[:300]}", flush=True)
            return None
        time.sleep(20)
    print(f"[NANO] seg{seg['id']} timeout", flush=True)
    return None


def regen(sid, source, prev_video=None):
    """Regenerate one shot: Lunostudio (primary) -> Nano-GPT (backup). Returns the
    saved revision path, or None on failure."""
    seg = seg_by_id(sid)
    print(f"\n===== REGEN seg{int(sid):02d}  ({seg['title']})  src={source} =====", flush=True)
    opening_url = opening_url_for(sid, source, prev_video)
    payload = build_luno_payload(seg, opening_url)
    url = run_luno(seg, payload)
    provider = "lunostudio"
    if not url:
        url = run_nano(seg, opening_url)
        provider = "nano-gpt"
    if not url:
        print(f"[DONE] seg{int(sid):02d} FAILED on both providers", flush=True)
        return None
    out = next_rev_path(sid)
    mm.download(url, out)
    ver = out.stem.rsplit("_v", 1)[-1]
    print(f"[DONE] seg{int(sid):02d} via {provider} -> {out.name}", flush=True)
    print(f"       film unchanged; select this take with: "
          f"python3 make_movie.py use {int(sid)} {ver} continuous", flush=True)
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "regen":
        sid = sys.argv[2]
        source = sys.argv[3]
        prev = sys.argv[4] if len(sys.argv) > 4 else None
        regen(sid, source, prev)
    elif cmd == "chain":
        a, b = sys.argv[2], sys.argv[3]
        out_a = regen(a, "still")
        if not out_a:
            print(f"[CHAIN] seg{a} failed; not chaining seg{b}", flush=True)
            return
        regen(b, "frame", str(out_a))
    elif cmd == "chainstill":
        # id1 from its config still, then each subsequent id from the previous output's last frame
        ids = sys.argv[2:]
        out = regen(ids[0], "still")
        if not out:
            print(f"[CHAINSTILL] seg{ids[0]} failed; stopping chain", flush=True)
            return
        cur_prev = out
        for sid in ids[1:]:
            out = regen(sid, "frame", str(cur_prev))
            if not out:
                print(f"[CHAINSTILL] seg{sid} failed; stopping chain", flush=True)
                return
            cur_prev = out
    elif cmd == "chainfrom":
        # id1 from prev_video's last frame, then each subsequent id from the previous output's last frame
        prev = sys.argv[2]
        ids = sys.argv[3:]
        cur_prev = prev
        for sid in ids:
            out = regen(sid, "frame", str(cur_prev))
            if not out:
                print(f"[CHAINFROM] seg{sid} failed; stopping chain", flush=True)
                return
            cur_prev = out
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
