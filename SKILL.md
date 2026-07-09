---
name: filmmaking
description: AI film production pipeline for creating short films. Covers the full workflow from screenplay to final film using OpenAI image generation (gpt-image-2) and Lunostudio Seedance 2 video generation. Trigger when the user asks to make a movie, film, video project, or anything related to the film production pipeline.
---

# AI Filmmaking Pipeline

Mark has a complete AI film production pipeline with templates and Python scripts.

**Precedence:** the README is the authority — where this skill and the README
are contradictory, follow the README. Mark's specific instructions for the film
at hand always override both this skill and the README.

## Before Starting Any Film Project

1. **Read the full README** at `/Users/markgimein/Desktop/film_template/README.md` — it contains the complete pipeline, API reference, prompt structure, and all workflow rules.
2. **Read the API keys** from `/Users/markgimein/Desktop/film_template/api_keys.txt`
3. **Review the template files** in `/Users/markgimein/Desktop/film_template/`

## Template Location

All templates live in `/Users/markgimein/Desktop/film_template/`:

| File | Purpose |
|------|---------|
| `film_config_template.py` | Configuration — copy as `film_config.py` per project |
| `generate_images_template.py` | Character refs, location refs, segment stills |
| `generate_voices_template.py` | Voice reference pipeline (Seedance audio) |
| `make_movie_template.py` | Video generation (standard + continuous), upscaling (SeedVR2), and final assembly (ffmpeg stitch or Palmier Pro import) |
| `chain_regen_template.py` | **Forward frame-chaining** for continuous scenes (preferred) — shots open on the previous shot's extracted last frame; Lunostudio primary, Nano-GPT backup |
| `upload_images_template.py` | Upload media to Lunostudio CDN |
| `nanogpt_video_template.py` | Nano-GPT backup Seedance provider + SeedVR2 upscaler (stages local files on litterbox) |
| `openrouter_video_template.py` | Legacy OpenRouter alternate provider (optional; no longer the default backup) |
| `workflow_prompt.txt` | Full prompt for starting a new film |

## Pipeline Overview

```
Screenplay -> Segments -> Characters/Locations -> Still Images -> Prompts -> Voice References -> Videos -> Final Film
```

**Every film is a STANDARD film** (independent segments) unless Mark explicitly
asks for continuous scenes or chained segments — never chain on your own
initiative, and no need to ask about it up front.

**Segmentation preferences** (full rules in the README):
- **No preference for longer segments** — use the length (4–15s) the action naturally needs; shorter is fine.
- **A segment may contain multiple shots** — it can cut within itself (e.g. to a closeup), described in the prompt. Split on scene/speaker changes, not shot changes.
- **Every segment has its own opening image.** (Chained segments, which open on the previous shot's extracted last frame, only when Mark asks.)
- One speaker per segment; no conversation over more than two segments.

## Preferences

- **Video generation:** Lunostudio Seedance 2 API (preferred). **Nano-GPT** (Seedance 2.0, `doubao-seedance-2-0`) is the automatic backup — a segment that fails twice on Lunostudio re-routes to Nano-GPT, or set `VIDEO_PROVIDER = "nanogpt"` to use it from the start. (OpenRouter is a legacy alternate, `VIDEO_PROVIDER = "openrouter"`.)
- **Generate in parallel, always:** images and videos are submitted simultaneously and polled together, never one at a time — this applies to the pipeline scripts (already concurrent) AND to any manual/ad-hoc regeneration batch. The one sequential exception is a frame chain (used only on request).
- **Video prompts are lean (three standing rules):**
  1. **Short character descriptions** — each character gets one essential line in the video prompt (`video_desc` in `CHARACTERS`: who they are / role), **never repeating appearance the reference images already show**. The detailed `desc` exists only to *generate* the reference images.
  2. **No style reminder** — the visual style is stated once at the top of each prompt; nothing is repeated at the end.
  3. **Who speaks to whom is in the action** — the segment's `action` text itself states clearly who the speaker addresses (e.g. "Jane, facing Tom, says: '...'"); there is no separate DIALOGUE DIRECTION section (the old `speaking_to` key is gone). Without it Seedance has the character talk to the camera; make soliloquy/voice-over explicit in the action too.
- **Pacing — scenes must not drag, but must still breathe:** there is **no injected `PACING` prompt rule** (a generic "speak faster" note just confuses Seedance). Pacing emerges from two things you set: (1) the segment's **target `seconds`** (the model fits delivery to the time given), and (2) an **`action` text that integrates speech and action in order** — what happens before a line, whether a line is spoken *during* an action, what follows — so Seedance places the beats itself. Budget `seconds` as `(words ÷ speech-rate) + beat/action time`, **not** `words ÷ 2.5` alone: normal speech ~2.5 wps (faster for urgency, slower for weighty or heightened/verse lines like Shakespeare), plus ~1.5–2s (more with staged action or a held pause) for an opening beat, natural pauses, and action. Spoken words should fill ~60–75% of the runtime, not all of it; don't over-pad either. See the README's Pacing section.
- **Script-risk review — flag, don't sand down:** before writing prompts, review the segments for things likely to trouble Seedance (too many characters in one shot, complex action, prop handling, on-screen text, crowds). But be **conservative about it** — the point is to *surface* risks for Mark to decide on, **not** to quietly cut, simplify, or rewrite anything that *might* confuse the generator (that strips his creative intent). Most flags are **borderline**: describe the risk, give the trade-off, and let Mark choose — borderline shots often come out fine and a re-roll is cheap. Only a severe, near-certain problem warrants strongly recommending a change, and even then you propose, never impose. See the README's *Review the script for generation risks* step.
- **Voice references (distinct voices are the point):** each is a 7s clip of the character delivering a **5–7 second section of their own dialogue, typical of them**, played **in the context of its scene** (never "a person talking"); `voice_desc` must always give the speaker's **rough age + general characteristics** (confident, careful, weary — lasting traits, never scene moods like "worried"). Audio-only is extracted as before; `generate_voices.py list`/`prompts` flag missing/age-less descriptions.
- **Upscaling:** **Nano-GPT SeedVR2** (`seedvr2-video-upscaler`) is the default. Run `make_movie.py upscale [continuous]` to enlarge finished segments to 720p (configurable) into `videos_upscaled/`. Local clips are staged on **litterbox** (auto-expiring public URLs) before upscaling; audio is preserved.
- **File storage for Nano-GPT:** Nano-GPT needs real public URLs. Project assets already live on the Lunostudio CDN; anything local (continuity frames, clips to upscale) is uploaded to litterbox.
- **Image generation:** OpenAI API with gpt-image-2 (latest 2.0 model)
- **New projects go in:** `~/Desktop/<Film_Name>/` — a folder on the Desktop named for the film, created with all the standard subfolders. The same folder doubles as a Film Studio project (next section).

## Film Studio integration (one folder, two systems)

The film folder is **also a Film Studio project**: the pipeline remains the
main means of generation, and its output is synced into the app's labs
(`script_lab/`, `asset_lab/`, `generated_videos/`, `audio_lab/`, `timeline/`)
so Mark can review takes, pick active revisions, preview the timeline, and
export in the app (`~/Desktop/film_studio`).

Run after **every** pipeline stage (incremental + idempotent — a ledger,
`pipeline_sync.json`, tracks what's synced):

```bash
node ~/Desktop/film_studio/src/tools/sync_pipeline.js ~/Desktop/<Film_Name>
```

- Run it once at project creation (it initializes the labs), then after images, voices, video generation, re-rolls, `use`/`usestill` selections, and script revisions.
- New versioned takes become new revisions; the `manifest.txt`-chosen take becomes the item's **active** revision; every video's active take auto-joins the timeline in segment order (app-side removals respected).
- It also refreshes **`browse/`** in the film folder — Finder-friendly hard-link views (real thumbnails/QuickLook, zero disk cost): `browse/film/` = the current cut in film order, `browse/stills/` = chosen opening stills, `browse/newest/` = exactly what the last generation batch produced. Auto-regenerated every sync; safe to delete, never hand-edit.
- Flow is one-way pipeline→labs; app-side work (trims, clips, imports) is not written back to pipeline folders.
- Skipped on purpose: `extras/`, `chain_frames/`, `backup/`, `videos_upscaled/`, code/progress files. `--continuous` reads the continuous cut.
- **Renumber before the first sync** (`make_movie.py renumber`) — the ledger is keyed by filename.
- Full details in the README's *Film Studio integration* section. (Legacy one-time importer for old films: `film_studio/src/tools/import_pipeline.js`, which makes a separate `<film>_Studio` copy. A studio-only project gets its `browse/` views from `film_studio/src/tools/browse_studio.js <studioProject>` — built from active takes; re-run after changing them in the app.)

## Continuous films (ONLY when Mark asks): forward frame-chaining (preferred)

**The preferred way to build a continuous scene** is forward frame-chaining
(`chain_regen.py`, copied from `chain_regen_template.py`). Each shot is generated
from an OPENING image + character/audio refs with **no** locked closing frame;
let Seedance reach its own ending; then **extract that final frame and use it as
the opening image of the next shot.** The next shot starts on the previous shot's
exact last frame, so the seam is perfect — and because each shot is an ordinary
image-to-video generation it **keeps its character reference images** and runs on
**Lunostudio (primary) with Nano-GPT fallback** (no keyframe mode). The only cost
is that a chain is **sequential**. Full detail + the comparison table are in the
README's *Forward frame-chaining (preferred for continuous scenes)* section.

```bash
python3 chain_regen.py regen <id> still                # one shot from its still
python3 chain_regen.py regen <id> frame <prev.mp4>     # one shot from a clip's last frame
python3 chain_regen.py chainstill <id1> <id2> ...      # id1 from its still, rest chained forward
python3 chain_regen.py chainfrom <prev.mp4> <id1> ...  # id1 from an existing clip, rest chained
```

Output goes into `videos_continuous/` as a new **versioned take**
(`seg<NN>_v<N>.mp4`; the original is its "v1") — nothing is overwritten and the
**manifest is not touched, so the film is unchanged.** Select a take with
`make_movie.py use <id> <version> continuous` (see *Regenerating segments and
stills* below). Optional per-run env: `CHAIN_LOCREF=3,4,5` also attaches each
shot's location reference still; `CHAIN_MULTIAUDIO=17,18` attaches voice refs for
**all** of a shot's speakers (each mapped to its own `@audioN`). Multiple voice
refs are now also a config option — set `voice_audios` (a list of character keys)
on the segment — so the env toggle is just for ad-hoc runs.

## Continuous films (ONLY when Mark asks): first-and-last-frame (keyframe) join segments (alternative)

*(Alternative to forward frame-chaining above — prefer chaining for new
continuous scenes.)*

In a **continuous** film, the seamless join depends on the **previous** shot
ending on the exact frame the next shot opens on — a **first-and-last-frame**
generation that Seedance runs in **keyframe mode**. Two hard rules (now automated
in `make_movie.py`; full detail in the README's
*First-and-last-frame (keyframe) join segments* section):

- **Keyframe mode is mutually exclusive with character reference images.** A join
  segment (one carrying a locked `last_frame`) must be built with **first frame +
  last frame + audio only, NO character-ref images** — send char refs alongside a
  `last_frame` and Seedance silently ignores the end frame and the seam drifts
  (often mirror-flipped). The locked frames already pin faces/composition; the
  character *descriptions* still go in the prompt text.
- **Only Nano-GPT honors the locked `last_frame`.** Lunostudio drops it (seams
  drift / times out on frames-only jobs); OpenRouter rejects photoreal faces
  (moderation). Nano-GPT (`doubao-seedance-2-0`) honors first+last frame **and**
  the audio reference, and accepts faces.

So `KEYFRAME_PROVIDER = "nanogpt"` in `film_config.py` routes join segments to
Nano-GPT in keyframe mode **regardless of `VIDEO_PROVIDER`** — the rest of the
film can still run on Lunostudio. `make_movie.py generate continuous` does this
automatically. To re-do only the seams, delete those `videos_continuous/` clips
and re-run `generate continuous`.

## Regenerating segments and stills (versioned in place + a manifest)

Once a film is generated, individual shots and stills often need to be re-rolled.
**The rule: a re-roll is written back into the SAME folder as a NEW versioned
file, and nothing is ever overwritten or renamed.** There is **no
`regenerated_segments/` folder** anymore.

- Original take: `videos/seg07.mp4` (its "v1"). Re-rolls: `videos/seg07_v2.mp4`,
  `seg07_v3.mp4`, … (continuous cut: `videos_continuous/`). Stills the same:
  `stills/seg07_opening.jpg` → `stills/seg07_opening_v2.jpg`.
- A **`manifest.txt` in each folder** (`videos/`, `videos_continuous/`, `stills/`)
  is the single source of truth for which take is in the film — the ordered
  lineup an external editor (Palmier, CapCut) reads. **Regenerating does NOT
  change the manifest**; the film is untouched until the user picks a take.

```bash
# Re-roll -> videos/seg07_v2.mp4 (next version; film unchanged). Manifest untouched.
python3 make_movie.py regenerate <id> [continuous]

# Put a reviewed take in the film by selecting it in the manifest:
python3 make_movie.py use <id> <version> [continuous]    # e.g. use 7 2  |  use 7 base
python3 make_movie.py manifest [continuous]              # show / rebuild the lineup
```

**Stills work the same** (a still is the locked opening frame, so changing it is
how you change a shot's pose/composition). Regenerating a still requires
re-uploading the chosen still so video generation opens on it:

```bash
python3 generate_images.py restill <id>          # -> stills/seg<NN>_opening_v2.jpg (original kept)
python3 generate_images.py usestill <id> <ver>   # select it in stills/manifest.txt
python3 upload_images.py stills                   # uploads the CHOSEN still per segment
# then paste the new URL into GDRIVE_STILL_IDS[<id>] before regenerating the video
```

Per the user's standing preference: regeneration **selection is manual** — after
re-rolling, do NOT auto-switch the film to the new take; present it for review and
only run `use`/`usestill` (or edit the manifest) once the user approves. If doing
it by hand, follow the same rule: write the new clip/still into the same folder
with a `_v<N>` suffix (never overwrite) and update the folder's `manifest.txt` to
select it.

## Editing an already-generated film in Palmier Pro

When the user wants to **edit or assemble an existing, already-generated film** in
Palmier Pro (rather than make a new one), the finished segments are already on
disk in the project folder — nothing needs to be regenerated. Example project:
`/Users/markgimein/Desktop/Other_Films/Midsummer_Madness`.

**1. Find the segments.** A completed project keeps each shot as its own file,
plus versioned re-rolls:
- Standard cut: `videos/seg01.mp4, seg02.mp4, … segNN.mp4` (and `seg<NN>_v<N>.mp4`)
- Continuous cut: `videos_continuous/…`
- A stitched flat film, if one was made, sits in the project root as
  `<film_slug>_full.mp4` (e.g. `midsummer_madness_full.mp4`). Import this only if
  the user wants the pre-stitched movie as a single clip rather than editable
  segments.

**The `manifest.txt` in the videos folder is the authoritative lineup** — it names
the chosen take of each segment, already in film order, so you don't have to guess
which `_v<N>` take to use or worry about lexical sorting (3-digit ids like
`seg100` sort before `seg14`). If a folder has no manifest yet, run
`make_movie.py manifest [continuous]` to build one (it defaults each segment to
its base/earliest take); the order otherwise follows the `SEGMENTS` list in
`film_config.py` (also in `screenplay/segment_list.txt`).

**2. Build the ordered manifest.** From the project directory:
```bash
python3 make_movie.py palmier             # standard cut  -> palmier_import.txt
python3 make_movie.py palmier continuous  # continuous cut
```
This writes `palmier_import.txt`: every segment's absolute path in film order,
with title and duration. (If the project's `make_movie.py` predates the `palmier`
mode, copy the current `make_movie_template.py` over it, or just read the ordered
absolute paths straight from `videos/` following the `SEGMENTS` order.)

**3. Import into Palmier.** Call `get_timeline` first (fps, tracks, canGenerate).
Then, in manifest order, `import_media` each segment with `source.path` set to its
absolute path, and `add_clips` to lay the clips end to end on one video track. A
segment that carries audio gets its linked audio clip created automatically. From
there the user edits in Palmier.

**Files import in place — they are NOT copied.** A `source.path` import makes
Palmier *reference the file where it sits*; only `source.url` imports are
downloaded into Palmier. So leave the project's `videos/` files where they are —
do **not** move, rename, or renumber them after importing, or Palmier's
references break. (If you need clean `1..N` numbering, run `make_movie.py
renumber` *before* importing, never after.)

## Previous Films (Reference Implementations)

- **The Ashkenazi Nebula v1:** `/Users/markgimein/Desktop/Other_Films/Ashkenazi_Nebula/`
- **The Ashkenazi Nebula v2:** `/Users/markgimein/Desktop/Other_Films/Ashkenazi_Nebula_v2/`
