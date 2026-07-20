# AI Film Production Template

A reusable workflow and set of Python programs for producing AI-generated short films using OpenAI's image generation API (gpt-image-2) and Lunostudio's Seedance 2 video generation API.

## Overview

This template system produces films through a pipeline:

```
Screenplay -> Segments -> Characters/Locations -> Still Images -> Prompts -> Voice References -> Videos -> Final Film
```

Every film is a **standard film** (independent segments, each with its own
opening image) unless the user explicitly asks for continuous/chained scenes
(see [Standard by default](#standard-by-default-chained-segments-only-on-request)).

Each stage has a corresponding template program. All film-specific data lives in a single configuration file (`film_config.py`) that you fill in for each new project.

The project folder is also a **Film Studio project**: the pipeline remains the
main means of generation, and its output is synced into the Film Studio app's
labs so it can be reviewed and manipulated there. See
[Film Studio integration](#film-studio-integration).

## Which instructions win

When instructions conflict, the order of authority is:

1. **The user's specific instructions** for the film at hand -- these always
   override everything, including this README and the filmmaking skill.
2. **This README.**
3. **The `filmmaking` skill.** Where the skill and this README are
   contradictory, prefer the README.

## Standard by default; chained segments only on request

There is a **single** set of templates. Every film is a **standard film**
unless the user asks for continuous scenes or chained segments -- **or unless
you propose them and the user approves**. The continuous techniques may be
suggested somewhat more freely than they used to be (2026-07-14), especially
where a dialogue scene's segment breaks would risk continuity -- but **never
generate continuous or chained clips without flagging the plan to the user
first**. (The preferred method is forward frame-chaining, below.)

**If the user gives their own specific rules for video creation in the prompt,
those rules override the defaults below (and everywhere else in this README).**

### Standard film (the default)

Break the screenplay into independent 4-15 second segments, each generated on
its own and all able to run in parallel. Screenplay rules:

- **No preference for longer segments.** Use whatever length the action
  naturally needs within the 4-15s range; shorter segments are fine.
- **In dialogue scenes, avoid segment breaks that would create continuity
  problems.** One 15-second segment is usually preferable to two 8-second
  ones. The most frequent continuity failure is characters changing their
  relative positions from one segment to the next in mid-conversation; fewer
  segment switches avoid it. Keeping the exchange in a single two-speaker
  segment (next bullet) often solves it -- and where a dialogue scene genuinely
  won't fit, propose a continuous treatment (flagged before generating).
- **A segment may contain multiple shots.** A single segment can cut within
  itself -- e.g. to a closeup -- with the cut described in the prompt. Don't
  split a segment only because the shot changes; split where the *scene*
  changes or a different speaker takes over.
- **No more than two people speak** on screen in any segment. A two-person
  exchange may share one segment -- often better than a continuity-breaking
  split (previous bullet). For a two-speaker segment set `voice_audios` (each
  speaker's reference becomes its own `@audioN`), and the `action` text must
  attribute **every line to its named speaker** (e.g. "Jane, facing Tom, says:
  '...' Tom replies: '...'") -- see
  [Dialogue direction](#dialogue-direction-who-a-character-speaks-to).
- **No conversation** stretches over **more than two segments**. This keeps
  conversations in a standard film very short -- that is **by design**; for
  longer exchanges, use a continuous film. (Voice-overs and narration are not
  conversations and can run as long as needed.)
- **Every segment begins with an opening image.**

Generate it with the plain commands (`make_movie.py generate`, etc.). Leave
`"continues_previous"` off every segment.

### Continuous film (on the user's request or sign-off)

There are two ways to build a continuous scene, and the **preferred** one is
**forward frame-chaining**:

**Forward frame-chaining (preferred).** Generate each shot from an OPENING image
+ character/audio refs with **no** locked closing frame; let Seedance reach its
own ending; then **extract that final frame and feed it as the opening image of
the next shot.** The next shot begins on the exact frame the previous one ended
on, so the cut is seamless. Because every shot is an ordinary image-to-video
generation it keeps its **character reference images** and runs on the normal
provider chain (**Lunostudio primary, Nano-GPT backup**) -- no keyframe mode, no
provider has to "honor" a locked end frame, nothing drifts or mirror-flips. The
only cost is that a chain is **sequential** (each shot needs the previous shot's
output). Implemented in `chain_regen.py`; see
[Forward frame-chaining](#forward-frame-chaining-preferred-for-continuous-scenes).

**Locked shared frames (alternative).** The older method holds a join with
**locked shared frames**, **not** the Seedance extend feature. (The extend
feature chains video off video, and the quality drifts and degrades fast over a
take -- so we don't use it.)

The key idea: **every** segment -- continuation or not -- is still an
independent shot with its own **opening image** (`@image1`, its locked first
frame). A segment that flows on from the one before it with no cut is flagged
`"continues_previous": True`. That continuation's opening still is **also copied
onto the previous segment as its locked LAST frame** (its `zclosing` image). So:

- the **previous** shot is generated to **end exactly on that frame**
  (passed to Seedance as `last_frame`), and
- the **continuation** is generated to **begin on the same frame** (`@image1`).

The two shots meet on one identical frame, so the cut is invisible -- and
because each shot is a single fresh generation (no video chained off another
video), **nothing drifts**. All segments generate **in parallel**, exactly like
a standard film; the only extra is the `last_frame` carried by any segment that
is followed by a continuation.

**This only works in Seedance KEYFRAME mode, on Nano-GPT.** A segment carrying a
`last_frame` is a **first-and-last-frame** shot, and Seedance treats first/last
frame control (**keyframe mode**) as **mutually exclusive** with character
reference images (**reference mode**): attach any character refs and Seedance
silently ignores the `last_frame`, so the seam drifts (the previous shot ends on
a different, sometimes mirror-flipped composition). So a join segment is built
with **first frame + last frame + audio reference and NO character-reference
images** -- the locked frames already pin the faces and composition, and audio
is compatible with keyframe mode. And only **Nano-GPT** honors the locked
`last_frame` (Lunostudio drops it; OpenRouter rejects photoreal faces), so these
join segments are **routed to Nano-GPT automatically** (`KEYFRAME_PROVIDER`),
even when the rest of the film generates on Lunostudio. See
[First-and-last-frame (keyframe) join segments](#first-and-last-frame-keyframe-join-segments).

**Coherence:** a `continues_previous` segment should keep the **same location**
(and normally the **same characters**) as the segment it continues, so the
shared frame makes sense. `make_movie.py` reports any continuation that changes
location. The **two-speaker ceiling** applies here too -- and since the
seamlessness comes from the shared frame, there is no need to pack a whole
exchange into one shot.

`make_movie.py prompts continuous` lists every continuation (and any warnings)
and saves the list to `continuous_segments.txt`.

Generate a continuous film with the `continuous` commands
(`make_movie.py generate continuous`, etc.). All segments generate concurrently
(submissions throttled `SUBMIT_SPACING` seconds apart to respect the rate
limit). The continuous run keeps its own files (`videos_continuous/`,
`seedance_prompts_continuous.txt`, `seedance_tasks_continuous.json`,
`<film>_continuous_full.mp4`), so it never clobbers a standard run from the same
config.

## Files in this template

| File | Purpose |
|------|---------|
| `film_config_template.py` | Configuration template -- copy to your project as `film_config.py` (drives both the standard and continuous versions) |
| `generate_images_template.py` | Generates character refs, location refs, and segment opening stills |
| `generate_voices_template.py` | Voice pipeline: makes a 7s 480p Seedance clip per recurring speaker -- each delivering an actual line from the piece in character -- and extracts audio-only voice references |
| `make_movie_template.py` | Generates video segments via Seedance 2 (standard and continuous versions), upscales them (SeedVR2), and stitches the final film |
| `chain_regen_template.py` | **Forward frame-chaining** for continuous scenes (preferred) -- builds/regenerates shots that open on the previous shot's extracted last frame; Lunostudio primary, Nano-GPT backup |
| `upload_images_template.py` | Uploads images/audio/video to the Lunostudio CDN for public URLs |
| `nanogpt_video_template.py` | Nano-GPT video client -- backup Seedance provider **and** the SeedVR2 upscaler; stages local files on litterbox |
| `openrouter_video_template.py` | Legacy OpenRouter video client -- optional alternate Seedance provider (no longer the default backup) |
| `workflow_prompt.txt` | The full prompt to give Claude when starting a new film |
| `api_keys.txt` | OpenAI, Lunostudio, Nano-GPT (and legacy OpenRouter) API keys |
| `README.md` | This file |

## Quick start for a new film

### 1. Create the project

A new film gets its own folder **on the Desktop, named for the film**, with all
the standard subfolders:

```
mkdir -p ~/Desktop/My_Film/{screenplay,characters,locations,stills,videos,audio,backup,extras}
```

Then initialize the Film Studio labs **in the same folder** (this creates
`script_lab/`, `asset_lab/`, `generated_videos/`, `audio_lab/`, `timeline/`
and `project.json`, making the film folder a Film Studio project the app can
open directly):

```
node ~/Desktop/film_studio/src/tools/sync_pipeline.js ~/Desktop/My_Film
```

Re-run that same command after each pipeline stage to keep the app's view
current -- it is incremental and only picks up what's new. See
[Film Studio integration](#film-studio-integration).

(There is no separate folder for re-rolls: a regenerated segment or still is
written back into `videos/` or `stills/` as a new *versioned* file -- see
[Regenerating segments and stills](#regenerating-segments-and-stills-versioned-in-place--a-manifest).)

The **`extras/`** folder is a catch-all in every project for assets, backups,
notes, or anything else you want to keep alongside the film. It is also where a
backup of the scripts and prompts goes when you make a major revision (see
[Backups](#backups)), and where the voice pipeline drops its throwaway
voice-reference clips (only the extracted audio is kept as the actual reference).

### 2. Copy the templates

One set of templates covers every film:
```
cp film_config_template.py ~/Desktop/My_Film/film_config.py
cp generate_images_template.py ~/Desktop/My_Film/generate_images.py
cp generate_voices_template.py ~/Desktop/My_Film/generate_voices.py
cp upload_images_template.py ~/Desktop/My_Film/upload_images.py
cp make_movie_template.py ~/Desktop/My_Film/make_movie.py
cp chain_regen_template.py ~/Desktop/My_Film/chain_regen.py  # forward frame-chaining (only with the user's sign-off on chained scenes)
cp nanogpt_video_template.py ~/Desktop/My_Film/nanogpt_video.py
cp openrouter_video_template.py ~/Desktop/My_Film/openrouter_video.py  # optional/legacy
```

Reference media (stills, character refs, voice audio, and -- for the continuous
version -- segment videos) is stored on the Lunostudio CDN. Run
`upload_images.py` to upload it and get public URLs, then paste those URLs into
`film_config.py`.

### 3. Write the screenplay and fill in the config

Write the screenplay with the **standard** segmentation rules (see
[Standard by default](#standard-by-default-chained-segments-only-on-request))
unless the user has asked for continuous/chained scenes. If they have, set
`"continues_previous": True` on each continuation segment as you fill in
`SEGMENTS`. (If the user gave their own video-creation rules, follow those
instead.)

Edit `film_config.py` and fill in:
- `FILM_TITLE`, `FILM_SLUG`
- `OPENAI_API_KEY`, `LUNO_API_KEY`, `NANOGPT_API_KEY` (and legacy `OPENROUTER_API_KEY`, optional)
- `VIDEO_PROVIDER` (`"lunostudio"` default, or `"nanogpt"` to generate on Nano-GPT from the start; legacy `"openrouter"` still works)
- `NANOGPT_MODEL` (backup Seedance model id, default `doubao-seedance-2-0`)
- `KEYFRAME_PROVIDER` (continuous films -- provider for first-and-last-frame join segments, default `"nanogpt"`; `None` disables the keyframe routing. See [First-and-last-frame (keyframe) join segments](#first-and-last-frame-keyframe-join-segments))
- `UPSCALE_MODEL` / `UPSCALE_RESOLUTION` (default `seedvr2-video-upscaler` at `720p`)
- `CHARACTERS` dict with all character descriptions: `desc` is the **detailed**
  physical description used to *generate* the reference images; `video_desc` is
  the **short, essential-only** line used in the *video prompts* (who they are /
  role -- never repeating appearance the reference images already show). Add
  `temperament` / `voice_desc` for recurring speakers -- the voice pipeline
  uses them
- `LOCATIONS` dict with all location descriptions
- `VISUAL_STYLES` dict with style strings
- `PRONUNCIATIONS` glossary for unusual names/words (see below)
- `SEGMENTS` list with all segment data (for a continuous film, flag
  continuation segments with `"continues_previous": True`)
- `GDRIVE_CLOSING_IDS` is filled later (continuous film) -- the locked closing
  (`zclosing`) frame URLs, after stills are generated and uploaded
- `VOICE_REFS` is filled later, after the voice pipeline generates and you
  upload the audio references

### 4. Generate images

```bash
# Generate character reference images (3 angles per character by default)
python3 generate_images.py characters

# Generate location reference images
python3 generate_images.py locations

# Generate segment opening stills (one per segment). In a continuous film this
# also copies each continuation's opening onto the previous segment as its
# locked closing "zclosing" frame.
python3 generate_images.py stills

# Or run all three in sequence
python3 generate_images.py all

# Export frame descriptions text file
python3 generate_images.py descriptions
```

All three commands generate their images **concurrently** (worker pools) --
never generate images one at a time (see
[Generate in parallel, always](#generate-in-parallel-always)). After the
images are in, sync them into the app:
`node ~/Desktop/film_studio/src/tools/sync_pipeline.js <project>`.

An opening still is generated for **every** segment -- the locked first frame (`@image1`) for video generation. In a **continuous** film, `generate_images.py stills` then copies each `continues_previous` segment's opening still onto the **previous** segment as that segment's locked closing frame (`seg<prev>_zclosing.jpg`), so the previous shot can be generated to end on the exact frame the continuation begins on.

### 5. Review images with the user

**IMPORTANT:** Before proceeding to upload and video generation, present the generated images to the user for review. Show key character references, location images, and representative opening stills. Ask the user if they are satisfied with the images or if any need revisions. Do not proceed to video generation until the user confirms the images are acceptable.

### 6. Upload reference images

Upload opening stills, character refs, and voice audio to Lunostudio's CDN to get publicly accessible URLs. The Seedance API requires all reference media (images and audio) to be at publicly accessible URLs. Lunostudio provides a built-in CDN via their `/api/v1/upload` endpoint -- this is the fastest option since it's a single API call per file using the same API key you already have.

```bash
# Upload everything (stills, character refs, voice audio)
python3 upload_images.py all

# Or upload selectively
python3 upload_images.py stills
python3 upload_images.py chars
python3 upload_images.py audio
```

The script outputs `upload_urls.json` with all public CDN URLs. Copy these into `film_config.py`:
- `GDRIVE_STILL_IDS` -- maps segment ID to public CDN URL for the opening still
- `GDRIVE_CLOSING_IDS` -- (continuous film) maps segment ID to public CDN URL for
  its locked closing (`zclosing`) frame; only segments followed by a continuation
  appear here. `stills` uploads these alongside the openings
- `GDRIVE_CHAR_REF_IDS` -- maps "charname_angle" to a public CDN URL for each
  character reference. `chars` uploads **every** generated angle (face,
  three-quarter, full body, and any variants); the video prompts then attach the
  face always and a body shot only where a scene needs it
- `VOICE_REFS` -- maps character/narrator key to public CDN URL for the audio reference

Despite the `GDRIVE_` prefix in the variable names, these are Lunostudio CDN URLs (not Google Drive). Any publicly accessible URL will work.

At this stage upload only **stills** and **character refs** (`stills`, `chars`).
The voice audio is created in the next step and uploaded after.

### 7. Review the script for generation risks

**Before generating prompts**, review every segment of the script and flag any
sections that are likely to cause problems for the AI video generator. Common
risks include:

- **Too many characters** in a single segment (Seedance handles 1-2 characters
  well; 3+ often produces artifacts or ignores some characters)
- **Complex physical actions** (acrobatics, precise hand interactions, objects
  being passed between people) that AI video models struggle with
- **Rapid scene changes within a segment** that the model may not execute
  clearly
- **Characters interacting with specific props** in ways that require precise
  spatial reasoning
- **Text on screen** or signage that needs to be readable
- **Crowds or groups** where individual faces matter
- **Any other element** that could confuse the video generator based on known
  limitations

**Flag; do not silently rewrite.** The job of this pass is to *surface* risks
for the user to decide on -- **not** to sand the script down on your own. Do
**not** quietly cut, simplify, tone down, or rewrite a line, action, or image
just because it *might* trouble the generator: that strips the user's creative
intent and often removes exactly what makes a shot interesting. Err toward
leaving the script as written and raising a flag, not toward pre-emptively
"fixing" it. (The one thing you may correct without asking is an outright error
already covered elsewhere -- a garbled name or wrong date -- which you clarify
with the user anyway.)

**Separate the clear-cut from the borderline.** Only a genuinely severe,
near-certain problem (e.g. six named characters all speaking in one 5-second
shot) is worth strongly recommending a change for -- and even then you propose,
you don't impose. Most flags are **borderline**: a moderately complex action, a
prop interaction, a bit of on-screen text. For those, describe the risk and
**discuss it with the user** -- offer the trade-off (keep it as written and
accept some risk, or adjust) and let them choose. Do not treat "could possibly
confuse Seedance" as sufficient reason to eliminate something; many borderline
shots come out fine, and a re-roll is cheap if one doesn't.

Present the flagged sections to the user with a brief explanation of the risk
for each, mark which (if any) you think are serious versus borderline, and ask
how they want to handle each before proceeding to prompt generation. This is
much cheaper to fix in the script than to discover after generating video --
but it is the user's call, not yours to make silently.

### 8. Generate the video prompts

```bash
# Standard film
python3 make_movie.py prompts

# Continuous film (prints which segments continue the previous one via a locked
# shared frame, saves continuous_segments.txt, writes seedance_prompts_continuous.txt)
python3 make_movie.py prompts continuous
```

For a continuous film, **report the continuation list back to the user** so
they can confirm which cuts flow together.

### 8b. Create voice references (the last step before making the movie)

Voice references keep a recurring speaker's voice consistent across shots that
are generated independently. **Who needs one:** any character who speaks in
**more than one segment.** Every segment is an independent shot that attaches its
own audio reference (continuity between continuous shots is carried by locked
shared frames, not by chaining video), so every speaking segment counts. A
character who speaks in only one segment needs no reference.

The voice pipeline (Seedance audio, per preference -- not a dedicated TTS API):

```bash
# 1. List the characters who need a voice reference -> audio/audio_references.txt
#    (flags any voice_desc that is missing or lacks the speaker's rough age)
python3 generate_voices.py list

# 2. Write an in-scene prompt for each: a 5-7 SECOND SECTION of the character's
#    own dialogue, typical of them, played in its scene's context, with the
#    voice explicitly described -> audio/voice_ref_prompts.txt
python3 generate_voices.py prompts

# 3. Generate: a 7s clip per character (ALWAYS 480p -- only the audio matters),
#    saved to extras/, then audio-only extracted to audio/<char>_voice_reference.mp3
python3 generate_voices.py generate

# Or all three in sequence
python3 generate_voices.py all
```

The point of the recipe is **distinct voices** -- generic clips make the whole
cast sound alike. Each clip:

- uses **only** the character's frontal face shot (`@image1`) as its one image
  reference;
- has the character deliver a **5-7 second section of their own dialogue,
  typical of how they speak** (roughly 13-18 words) -- never a generic "let me
  say a few words" screen test. The passage is auto-picked from the character's
  dialogue in `SEGMENTS` (override with `voice_ref_line` in `CHARACTERS` --
  also a real, typical passage from the piece);
- is played **in the context of its scene** -- the prompt carries the scene's
  setting and a short description of the moment the line comes from, so the
  character performs the scene rather than reciting into a void;
- carries an explicit **VOICE description** (`voice_desc` in `CHARACTERS`) that
  must **always state the speaker's rough age** plus the voice's **general
  characteristics** -- confident, careful, weary, brisk... These are lasting
  traits of the person, **never scene-specific moods** (not "worried"). `list`
  and `prompts` flag any voice_desc that is missing or lacks an age.

The throwaway video goes to `extras/`; **only the audio** is kept, in `audio/`,
as the reference (extracted with ffmpeg, as before).

Then host the new audio and wire it up:

```bash
python3 upload_images.py audio   # upload audio/*_voice_reference.mp3 to the CDN
```

Paste the returned URLs into `VOICE_REFS` in `film_config.py`.

### 9. Generate videos

**Before you start generating, get the user's final OK and double-check two
things with them:**
- **Length** -- the total film duration and number of segments.
- **Resolution** -- `SEEDANCE_RESOLUTION` in `film_config.py` (`480p`, `720p`, or `1080p`).

Generation is the slow, costly step, so confirm both explicitly first. There is
no need to test a single segment -- generate the whole movie at once:

```bash
# Preview all prompts (no API calls)
python3 make_movie.py prompts

# Generate the whole movie at once (all segments)
python3 make_movie.py generate

# Check status of all tasks (shows which provider produced each segment)
python3 make_movie.py status

# Poll and download any unfinished tasks
python3 make_movie.py fetch

# Print a generation report
python3 make_movie.py report

# Export failed segments with prompts and assets for manual regen
python3 make_movie.py failures

# Re-roll one segment -> videos/seg07_v2.mp4 (a new versioned take; the film is
# untouched until you select it). See "Regenerating segments and stills".
python3 make_movie.py regenerate 7

# Put a reviewed take in the film by selecting it in the manifest
python3 make_movie.py use 7 2          # or: use 7 base
python3 make_movie.py manifest         # show / rebuild the current lineup

# Upscale the finished segments to 720p with SeedVR2 -> videos_upscaled/
python3 make_movie.py upscale
```

`generate` submits **all segments in parallel** (throttled for the rate limit)
and skips segments already on disk, so re-running it resumes an interrupted
generation. Any manual regeneration batch must be submitted in parallel too
(see [Generate in parallel, always](#generate-in-parallel-always)).

When the videos are down, sync them into Film Studio:
`node ~/Desktop/film_studio/src/tools/sync_pipeline.js <project>`
-- and again after re-rolls and `use` selections, so the app always shows the
current takes and lineup.

**Continuous film (chosen up front).** Append the word `continuous` to every
command so it operates on the continuous film. These commands use their own
files throughout, so a standard run from the same config is never overwritten:

```bash
# Preview the continuous prompts + save the continuation list (no API calls)
python3 make_movie.py prompts continuous

# Generate the continuous film: all segments in parallel (locked shared frames)
python3 make_movie.py generate continuous

# Status / resume / report / assemble
python3 make_movie.py status continuous
python3 make_movie.py fetch continuous
python3 make_movie.py report continuous
python3 make_movie.py upscale continuous   # SeedVR2 -> videos_continuous_upscaled/
python3 make_movie.py stitch continuous   # flat ffmpeg concatenation
python3 make_movie.py palmier continuous  # or import into the Palmier Pro editor
```

`generate continuous` generates every segment independently and in parallel,
into `videos_continuous/`. There is no base-then-extend ordering -- continuity is
baked into the stills (each continuation's opening is the previous segment's
locked `last_frame`), so no segment waits on another. It skips work already on
disk, so it resumes cleanly.

**Provider fallback is automatic.** Each segment is attempted on Lunostudio
Seedance up to twice; if both attempts fail, `make_movie.py` re-routes that
segment to **Nano-GPT** (same Seedance 2 model family, different host) with no
manual step. To generate everything on Nano-GPT from the start, set
`VIDEO_PROVIDER = "nanogpt"` in `film_config.py`. See
[Backup provider and upscaling (Nano-GPT)](#backup-provider-and-upscaling-nano-gpt)
below. (The legacy OpenRouter path is still available with
`VIDEO_PROVIDER = "openrouter"`.)

**Continuous films: join segments always go to Nano-GPT (keyframe mode).**
Independently of `VIDEO_PROVIDER`, any segment that carries a locked `last_frame`
(a first-and-last-frame join shot) is generated on Nano-GPT in keyframe mode --
first frame + last frame + audio, no character refs -- because that is the only
provider/mode that actually honors the locked end frame. This happens
automatically; see
[First-and-last-frame (keyframe) join segments](#first-and-last-frame-keyframe-join-segments).

### 10. Handle failed generations

A segment only ends up "failed" after it has failed twice on Lunostudio **and**
failed on Nano-GPT. For anything still failed after that, create a
`failed_segments/` folder in the project directory and generate a report to make
manual regeneration easier:

1. **Create `failed_segments/report.txt`** containing the complete Seedance API prompt for each failed segment, including all parameters (model, duration, resolution, aspect ratio, mode) and the full prompt text.
2. **Download all referenced assets** into `failed_segments/` -- opening stills, character reference images, and audio references used by the failed prompts. Name them clearly (e.g., `seg06_opening.jpg`, `seg06_lysander_ref.jpg`, `seg06_audio_ref.mp3`).
3. **List the reference URLs** in the report so they can be pasted directly into another service's UI.

This gives everything needed to regenerate those segments manually through a different provider or the Lunostudio web UI.

### 10b. Regenerating segments and stills (versioned in place + a manifest)

After a film is generated, individual shots and stills usually need to be
re-rolled. The rule is simple and never loses or confuses a file:

> **A re-roll is written back into the SAME folder as a NEW versioned file, and
> nothing is ever overwritten or renamed.** The original is `seg07.mp4` (its
> "v1"); re-rolls are `seg07_v2.mp4`, `seg07_v3.mp4`, … in `videos/` (or
> `videos_continuous/`). A `manifest.txt` IN that folder lists the take of each
> segment that is currently in the film. **Re-rolling does NOT change the
> manifest** -- the film is untouched until you explicitly pick a take.

This is what an external editor (Palmier, CapCut) reads: hand it the `videos/`
folder and its `manifest.txt` -- the manifest is the ordered lineup of the latest
complete film, and to swap a take you change one line (or run one command), never
a filename.

```bash
# Re-roll one segment -> videos/seg07_v2.mp4 (next free version; original kept).
# The manifest is NOT touched, so the film is unchanged.
python3 make_movie.py regenerate 7
python3 make_movie.py regenerate 7              # again -> seg07_v3.mp4 (every take kept)
python3 make_movie.py regenerate 7 continuous   # continuous cut -> videos_continuous/seg07_v2.mp4

# Review the takes, then PUT ONE IN THE FILM by selecting it in the manifest:
python3 make_movie.py use 7 2                    # seg 7 now uses seg07_v2.mp4
python3 make_movie.py use 7 base                 # back to the original seg07.mp4
python3 make_movie.py use 7 2 continuous         # continuous cut

# See / (re)build the lineup at any time:
python3 make_movie.py manifest [continuous]
```

**How it works:**
- **`regenerate <id>`** generates one fresh take through the normal provider
  chain (Lunostudio with Nano-GPT fallback, or the backup provider directly when
  `VIDEO_PROVIDER` is `nanogpt`/`openrouter`) and writes it into the cut's videos
  folder as the next free `seg<NN>_v<N>.mp4`. **Every take is kept; nothing is
  overwritten and no name is reused.** The manifest is left alone.
- **`use <id> <version>`** sets that segment's line in the manifest -- `2`/`v2`
  for `seg07_v2.mp4`, `base`/`1` for the original, or an explicit filename. The
  chosen take must exist. This is the only thing that changes which take is in the
  film; you can also just edit `manifest.txt` by hand.
- **`manifest`** builds the manifest if absent or refreshes it in place: it
  **keeps every explicit choice** whose file still exists and defaults the rest to
  their base/earliest take, writing every segment slot in film order. `stitch`,
  `palmier`, `upscale`, and `list` all read the manifest, so they assemble exactly
  the lineup it declares.
- The continuous cut keeps its own folder (`videos_continuous/`) and therefore
  its own `manifest.txt`, so a standard and a continuous re-roll never collide.

**Re-rolling a still works the same way** (a still is a segment's locked opening
frame, so changing it is how you change a shot's composition/pose):

```bash
python3 generate_images.py restill 7            # -> stills/seg07_opening_v2.jpg (original kept)
python3 generate_images.py usestill 7 2         # select it in stills/manifest.txt
python3 upload_images.py stills                 # upload the CHOSEN still per segment
```

`restill` writes a new versioned still and leaves the stills manifest alone;
`usestill` selects it; then `upload_images.py stills` uploads **the chosen still
for each segment** (re-uploading only those whose selection changed) and updates
`upload_urls.json`, so paste the new URL into `GDRIVE_STILL_IDS` and the next
video generation opens on the frame you picked. (`generate_images.py
stillmanifest` prints/rebuilds the stills lineup.)

### 11. Renumber the segments (do this before assembling)

Over a project's life, segment ids drift out of clean order -- cut segments
leave gaps, and inserted shots (e.g. splitting one segment into two with a hard
cut) get out-of-place or 3-digit ids. **Before assembling the film, renumber the
segments to a clean `1..N` in film order** so the final film and the segment list
read in order:

```bash
python3 make_movie.py renumber             # standard cut
python3 make_movie.py renumber continuous  # continuous version
```

This renumbers in **list (film) order** -- so continuation joins are unaffected
-- and keeps everything in sync: it renames the video and still files, remaps
the JSON sidecars (`upload_urls.json` stills, the task file,
`stills_progress.json`), and rewrites the `id` fields (plus any `mid_cut_ref` /
`extra_still_refs`) in `film_config.py` (a backup is saved to `backup/`). It is
idempotent -- a no-op if the segments are already `1..N`. Run it **once, right
before assembling** (whether you stitch or import into Palmier Pro), then
regenerate the prompts/segment list if you need them to reflect the new numbers.

### 12. Assemble the final film

Once every segment is generated and renumbered, assemble the film. Both options
below assemble exactly the lineup in the videos-folder **`manifest.txt`** (the
chosen take of each segment, in film order; built automatically if absent), so
run `make_movie.py manifest [continuous]` first if you want to review or adjust
which takes are in the cut. There are two ways to assemble -- **ask the user which
they want**:

**Option A -- stitch into a single file (ffmpeg).** A fast, flat concatenation
with no editing:

```bash
python3 make_movie.py stitch
```

The script lists the segments it found (in film order) and asks for confirmation
before stitching. It uses ffmpeg's concat demuxer with stream copy (no
re-encoding). The original segment files are always preserved.

**Option B -- import into the Palmier Pro AI editor.** Use this when the user
wants to *edit* the assembly -- trim, reorder, add music, titles, transitions, or
effects -- rather than get a flat cut:

```bash
python3 make_movie.py palmier
```

This concatenates nothing. It writes `palmier_import.txt` -- every segment video
in film order, with its absolute path, title, and duration. Then **Claude imports
the segments into Palmier Pro through the `palmier-pro` MCP tools**: call
`import_media` once per segment (`source.path` = the absolute path from the
manifest -- local files are referenced in place, no upload needed), then
`add_clips` to place the clips end to end, in manifest order, on one video track.
A segment that carries audio gets its linked audio clip created automatically.
From there the cut lives in Palmier, where the user finishes it. (`editor` and
`import` are aliases for `palmier`.)

Both options also work on the continuous version -- append `continuous`
(`make_movie.py stitch continuous` or `make_movie.py palmier continuous`).

### 13. Segment list & regeneration review

When the film is assembled, write a numbered list of every segment to the
screenplay folder (alongside `screenplay.txt` / `segments.txt`) and review it
with the user:

```bash
python3 make_movie.py list             # standard cut
python3 make_movie.py list continuous  # continuous version
```

`stitch` runs this automatically on success, so after a stitch the list already
exists. The list is saved to `screenplay/segment_list.txt` (or
`screenplay/segment_list_continuous.txt`) and numbers every segment with its
title, duration, characters, a one-line description, and its current status
(`ready`, `failed`, `not generated`, plus the continuation marker in the
continuous version). **After writing it, ask the user whether any segments should be
regenerated.** To regenerate specific segments, delete their files from
`videos/` (or `videos_continuous/`) and re-run `make_movie.py generate`
(append `continuous` for the continuous version) -- generation skips segments
already on disk and re-creates only the ones you removed.

## API reference

### OpenAI gpt-image-2 (ChatGPT Images 2.0)

Used for: character reference images, location reference images, segment opening stills.

- **Generate** (no reference images): `client.images.generate(model="gpt-image-2", prompt=..., size="1280x720")`
- **Edit** (with reference images): `client.images.edit(model="gpt-image-2", image=[file_handles], prompt=..., size="1280x720")`
- Returns base64-encoded image data
- Supports up to 1280x720 resolution
- When references are provided, the API matches faces and details from them

**Character generation strategy:**
1. Generate the front-facing portrait first (no references)
2. Generate profile, three-quarter, and full-body using the front portrait as a reference
3. This ensures all four images depict the same person

**Still generation strategy:**
1. For each segment, generate the opening frame using character and location refs

### Lunostudio Seedance 2

Used for: video segment generation.

- **Endpoint**: `POST https://lunostudio.ai/api/v1/generate`
- **Auth**: `Authorization: Bearer luno_sk_...`
- **Status**: `GET https://lunostudio.ai/api/v1/status?task_id=...`
- **Upload**: `POST https://lunostudio.ai/api/v1/upload` (for getting public URLs)
- **Rate limit**: 25 requests/minute

**Key parameters:**

| Parameter | Values | Notes |
|-----------|--------|-------|
| model | `seedance-2` | Required |
| prompt | string | Use `@image1`, `@audio1` tags to reference media |
| duration | 4-15 seconds | Default: 5 |
| aspect_ratio | `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9` | Default: 16:9 |
| resolution | `480p`, `720p`, `1080p` | Default: 720p; fast mode caps at 720p |
| mode | `standard`, `fast` | standard: cinema-grade quality, 2-10 min; fast: iteration quality, 1-5 min. **Always `standard` for film segments** (see quality note below) |
| reference_images | array of URLs | Up to 9 public URLs. Luno docs: "Single image = image-to-video. Multiple = reference mode." **Tested 2026-07-11: neither mode pins the first frame** -- single-image i2v still re-synthesizes the opening with ~2% zoom drift; exact opening frames need Nano-GPT `imageUrl` (see [Forward frame-chaining](#forward-frame-chaining-preferred-for-continuous-scenes)) |
| reference_videos | array of URLs | Up to 3 public URLs (Seedance video extend; **not used** by this template's locked-frame continuity) |
| reference_audio | array of URLs | Up to 3 public URLs |
| last_frame | URL | Locked end frame for a segment followed by a continuation (its `zclosing` still). **Note: Lunostudio does not actually honor this** -- a locked end frame only works in Seedance keyframe mode on Nano-GPT, with no character refs; join segments are routed there. See [First-and-last-frame (keyframe) join segments](#first-and-last-frame-keyframe-join-segments) |

**Quality ("high bitrate") rule.** Lunostudio's generation API has **no
bitrate parameter** -- verified against the Luno developer docs on 2026-07-09
(the complete parameter list is exactly the table above plus an optional
`tag`; the account dashboard has no quality setting either). Output quality
is controlled by two things only:

- **`mode` -- always `"standard"`** (cinema-grade, the high-quality encode).
  `fast` is iteration-quality, caps resolution at 720p, and must never be
  used for final film segments. `SEEDANCE_MODE = "standard"` is the template
  default; do not change it to speed up a batch.
- **`resolution`** (`480p`/`720p`/`1080p`) -- the other quality lever; per
  the workflow, confirm it with the user before generating (1080p at 10-15s
  can take 10+ minutes per segment).

**Status values:** `pending` -> `generating` -> `success` or `failed`

**Prompt structure -- standard / base segment (each segment independent):**
1. Opening rule: `@image1` is the locked first frame
2. Mid-cut reference (if applicable): `@image2` for the second composition
3. Visual style description (stated **once**, at the top -- there is **no style
   reminder at the end** of the prompt)
4. **Short** character descriptions with `@imageN` references -- one essential
   line per character (`video_desc` in `CHARACTERS`: who they are, their role
   or bearing), **never repeating appearance the reference images already
   show**. The face ref is attached always, plus a body shot only where the
   opening still doesn't already show it (see
   [Character reference images](#character-reference-images))
5. Action and sound description (with duration). **When anyone speaks, the
   action text itself states clearly who the speaker is addressing** (e.g.
   "Jane, facing Tom, says: '...'") -- there is no separate dialogue-direction
   section, and without it Seedance has the character talk to the camera. A
   soliloquy or voice-over is likewise made explicit in the action.
   **Integrate speech and action in the order they occur** -- what happens
   before a line, whether a line is spoken *during* an action, what follows --
   so the model paces the beats itself against the segment's duration. **There
   is no separate pacing rule in the prompt**; pacing comes from the target
   `seconds` plus this ordered action text (see
   [Pacing](#pacing-scenes-must-not-drag-but-must-still-breathe) and
   [Dialogue direction](#dialogue-direction-who-a-character-speaks-to))
6. Names & pronunciation (applied **only when a glossary term is actually
   spoken** in this segment's quoted dialogue): each glossary name is respelled
   **phonetically inside the spoken dialogue itself** (e.g. `Hermia` →
   `HUR-mee-uh`), because Seedance voices the literal dialogue text and a
   side-note alone does not reliably fix pronunciation. A short reinforcement
   note then lists those respellings. Stage directions and character labels keep
   the real name. Segments that speak no glossary term are left untouched
7. Camera movement description (the prompt ends here)

**Continuous version -- continuation segments.** A `continues_previous` segment
uses the **exact same prompt structure** as any other segment (it has its own
`@image1` opening still, character refs, and `@audio1`). It only adds a short
**continuity rule** ("this shot continues the previous scene; its opening frame
is identical to the frame the previous shot ended on -- carry the action straight
on, do not restart or recompose"). It does **not** reference any video.

A segment that is **followed by** a continuation additionally carries a
**`last_frame`** -- its locked closing (`zclosing`) frame, which is a copy of the
next segment's opening still -- plus a short **closing rule** ("the video must
arrive at and end on that exact composition by its final frame"). So the previous
shot ends on, and the continuation begins on, one identical frame -- a seamless
join with no video chaining and no drift.

**Reference convention:**
- `@image1` = segment opening still (locked first frame) -- every segment
- `@image2`, `@image3`, ... = character reference images (face always; a body
  shot only where the opening still doesn't already show it)
- `@audio1`, `@audio2`, ... = voice reference audio. One per **speaker**: a
  single speaker (`voice_audio`) is `@audio1`; for two speakers in one shot set
  `voice_audios` (an ordered list of character keys) and each gets its own
  `@audioN`, with a prompt note telling Seedance which voice is which (see
  [Voice references](#voice-references-the-voice-pipeline))
- `last_frame` = the locked closing (`zclosing`) still -- attached only to a
  segment that is FOLLOWED by a continuation (continuous version)

All reference URLs must be publicly accessible. The upload script provides Lunostudio CDN URLs by default. Google Drive direct-download URLs also work: `https://drive.google.com/uc?export=download&id=FILE_ID`

### Backup provider and upscaling (Nano-GPT)

Implemented in `nanogpt_video.py`. Used for two things:

1. **Backup video generation** -- a segment that fails twice on Lunostudio is
   re-routed here automatically, and `VIDEO_PROVIDER = "nanogpt"` generates the
   whole film here from the start. Same model family (ByteDance Seedance 2.0),
   different host.
2. **Upscaling** -- the default upscaler (`make_movie.py upscale`), described in
   the next section.

**API (host `https://nano-gpt.com`):**

- **Submit**: `POST /api/generate-video` -> JSON with `runId` / `id` / `requestId`
- **Auth**: `Authorization: Bearer sk-nano-...` (also sends `x-api-key`)
- **Status**: `GET /api/video/status?runId=...` -> `{ data: { status, output } }`
  (terminal `status`: `completed`/`succeeded` ok; `failed`/`error`/`canceled` fail)
- **Download**: `GET` the `output.video.url` returned on completion (MP4 bytes)
- **Models**: list at `GET /api/v1/video-models`. Seedance default
  `doubao-seedance-2-0` (plain Seedance 2.0); alternates `doubao-seedance-2-0-fast`,
  `bytedance-seedance-2-0` (Turbo), or `bytedance/seedance-2.0/image-to-video-spicy`
  for guaranteed native audio.

**Key Seedance 2.0 parameters** (set via the `nanogpt_video` spec):

| Parameter | Values | Notes |
|-----------|--------|-------|
| model | `doubao-seedance-2-0` | Set via `NANOGPT_MODEL` |
| prompt | string | No `@`-tags; references passed as separate URL fields |
| duration | 4-15 seconds | |
| aspect_ratio | `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9` | |
| resolution | `480p`, `720p`, `1080p` | |
| imageUrl | URL | opening still / continuity start frame (image-to-video) |
| last_image | URL | locked closing frame for a continued segment |
| reference_images | JSON-array string of URLs | character & style refs |
| reference_audios | JSON-array string of URLs | voice reference audio |
| reference_videos | JSON-array string of URLs | previous-scene video (max 15s) |
| generateAudio | bool | lets Seedance produce dialogue/sound |

Nano-GPT does **not** use Lunostudio's `@`-tag prompt syntax, and unlike
OpenRouter it requires **real public URLs** for every reference (no base64 data
URLs). The project's stills, character refs, and voice audio are already on the
Lunostudio CDN, so those URLs work as-is. Anything that only exists locally -- a
continuity frame extracted from a previous clip, or a clip being upscaled -- is
uploaded to **litterbox** (`litterbox.catbox.moe`) first to get a temporary
public URL. Litterbox files auto-expire (default 72h), so nothing stays hosted.

**Continuous-version continuity on Nano-GPT:** the locked-frame approach needs
nothing special. A continuation carries its own opening still (`imageUrl`) just
like any segment, and a segment followed by a continuation carries the locked
closing still as `last_image`. No video is referenced.

*(Legacy: `openrouter_video.py` still implements the old OpenRouter backup --
`POST https://openrouter.ai/api/v1/videos`, structured `frame_images` /
`input_references`, base64 data URLs allowed -- and is used only when
`VIDEO_PROVIDER = "openrouter"`.)*

### Upscaling finished clips (SeedVR2, default)

`make_movie.py upscale [continuous]` enlarges every finished segment with
Nano-GPT's **SeedVR2** (`seedvr2-video-upscaler`), the default upscaler.

- Each local clip is staged on litterbox, then submitted to the same
  `POST /api/generate-video` endpoint with `{ model, videoUrl, resolution }`.
- Output is written to `videos_upscaled/segNN.mp4` (or
  `videos_continuous_upscaled/` for the continuous cut). Existing files are
  skipped, so it resumes cleanly, and the upscaler **preserves the audio track**.
- Resolution comes from `UPSCALE_RESOLUTION` (`720p` default; SeedVR2 also does
  `1080p`, `2k`, `4k`). The aspect ratio is preserved, so a 836x480 source lands
  near 1268x728 at 720p rather than an exact 1280x720.
- Cost is roughly `$0.02/sec` at 720p (`$0.03` at 1080p). Alternate upscalers:
  set `UPSCALE_MODEL` to `bytedance-seedance-upscaler`, `video-upscaler`
  (FlashVSR), or `clarity-ai/crystal-video-upscaler`.

## Workflow details

### Screenplay segmentation rules
*(If the user gave their own video-creation rules in the prompt, those override
everything here.)*
- Every segment must be between 4 and 15 seconds (no shorter, no longer)
- **No preference for longer segments** -- use the length the action naturally
  needs; shorter segments are fine
- **Budget a segment's `seconds` as `(words ÷ 2.7) + beat/action time`**, not
  as `words ÷ 2.7` alone (see [Pacing](#pacing-scenes-must-not-drag-but-must-still-breathe)).
  Speech runs ~2.7 words/second -- that rate is for the words themselves only --
  so reserve ~1.5-2s on top for the opening beat, natural pauses, and action --
  more if the shot has real staged action or a held pause; a little faster for
  emotionally charged scenes, slower (~1.5-2 wps) for weighty or
  heightened/verse lines. Spoken words should fill ~60-75% of the runtime, not
  all of it. Both over-padding and cramming edge-to-edge make a scene play wrong
- **A segment may contain multiple shots.** A segment can cut within itself
  (e.g. to a closeup), with the internal cut described in the video prompt.
  Don't split a segment just because the shot changes -- split where the scene
  changes or a different speaker takes over
- **Every segment should have its own opening image.** Chained segments (which
  open on the previous shot's extracted last frame instead) are used **only
  when the user asks** for continuous scenes -- and a chain is at most **three
  segments including the initial one** (usually two), with **no characters in
  the chained segments who are not in the initial segment** (hard rule; see
  [Forward frame-chaining](#forward-frame-chaining-preferred-for-continuous-scenes))
- No more than **two** people speak on screen in any segment. For a
  two-speaker segment set `voice_audios`, and the action text must attribute
  **every line to its named speaker** (see
  [Dialogue direction](#dialogue-direction-who-a-character-speaks-to))
- A radio/phone voice doesn't count against the ceiling (it isn't on screen)
- When two characters converse, the exchange may share one segment (a
  two-speaker segment); split per speaker only where the exchange won't fit or
  a new composition is genuinely wanted -- and prefer splits that don't create
  continuity problems (see below)
- **No conversation stretches over more than two segments** -- conversations in
  a standard film are intentionally very short; for longer exchanges use a
  continuous film's locked-frame continuations. (Voice-overs/narration are not
  conversations and are exempt.)
- **Minimize segment switches inside a dialogue scene** -- avoid breaks that
  would create continuity problems: one 15s segment is usually preferable to
  two 8s ones. Characters changing relative positions between segments is the
  most frequent continuity failure in dialogue; fewer breaks avoid it. A
  two-speaker segment (`voice_audios`) usually keeps the exchange in one shot;
  where it genuinely won't fit, a proposed continuous treatment (flagged before
  generating clips) beats a continuity-breaking cut.
- **Every segment that follows a cut begins with an opening image** (a
  continuous film's extension segments are the only segments without one)
- **Protecting dialogue across cuts:** If a voiceover or line of dialogue would be badly interrupted by a cut, keep the scene in one segment even if it spans what would normally be two compositions. In that case, set `mid_cut_ref` on the segment to the ID of the segment whose opening still represents the second composition -- that image is attached as `@image2` in the video prompt so the model knows where to transition mid-shot. Avoid this when possible; prefer splitting at natural pauses in speech

### Pacing (scenes must not drag, but must still breathe)

**There is no injected "PACING" rule in the prompts, on purpose.** A generic
"speak ~2.5 words/second, no long pauses" sentence stapled to every prompt just
confuses Seedance -- it fights the scene's own rhythm and either races the whole
shot or flattens its beats. Pacing is not a rule to bolt on; it *emerges* from
two things the segment already carries, when both are set well:

1. **The target duration** (`seconds`) -- sent to Seedance as the clip length
   and named in the `ACTION AND SOUND (N seconds)` header. This is the single
   most powerful pacing lever: the model fits its delivery to the time it is
   given. Budget it right (below) and the speech lands at a natural rate on its
   own; budget it too generously and the same words stretch out and drag.
2. **An action text that integrates speech and action in order.** When the
   `action` makes the *sequence* explicit -- what happens before a line, whether
   a line is spoken *during* an action, and what follows -- Seedance knows where
   the beats fall and paces them itself. Don't just list a block of dialogue and
   a separate block of action; interleave them in the order they occur (e.g.
   "Jane crosses to the window, pauses, then says: '...'; she sets the cup down
   as she finishes"). Given a target time and a clear ordering, the model
   handles the pauses and the rhythm without being told to.

**Budgeting a segment's duration.** This is where the pacing work actually
happens. Add speech time and non-speech time -- don't just divide words by a
rate and call it the duration:

```
seconds  =  (dialogue words ÷ speech-rate)  +  beat/action time
```

- **Speech time** = words ÷ the speech-rate for this line. Normal delivery is
  about **2.7 words/second** (Seedance's own default is slower, ~2 wps, but the
  target duration pulls it up to a natural conversational pace -- you do not
  instruct the rate, you *budget* for it). The rate covers **the words
  themselves only** -- pauses, beats, and action are budgeted separately below.
- **Beat/action time** = an opening beat before the first word (~0.5-1s), a
  closing beat after the last word (~0.5-1s), any dramatic pause the line calls
  for, and the real time any described physical action needs. A plain
  talking-head shot needs **~1.5-2s** of this on top of the speech; a shot with
  actual staged action or a held dramatic pause needs more.
- **Sanity check:** over a whole dialogue segment, spoken words should fill only
  about **60-75%** of the runtime, *not* 100%. Put another way, a normal
  dialogue segment carries roughly **1.6-2 words per second of its total
  length** -- the rest is beats and action. Clamp to 4-15s. Don't over-pad
  either: a generous duration with little to fill it drags, because the model
  stretches whatever time it is given.

**Adjusting the speech-rate for drama** (the `words ÷ rate` part of the budget
-- you are choosing how much time to *allow*, not writing an instruction):

- **Faster (~3 wps):** urgent, panicked, rapid-fire, an argument -- budget less
  time per word. An **emotionally charged** scene in general can run a little
  above the base rate without feeling rushed.
- **Slower (~1.5-2 wps):** weighty, grief-struck, menacing, reflective -- budget
  more time per word, and write the pause beats into the action so the model
  places them.
- **Slower for heightened language (~1.5-2 wps):** Shakespeare, verse, archaic
  or formal diction, dense literary text. The meter needs room and the audience
  needs time to parse unfamiliar words -- give these segments a generous
  duration; never budget them at the full 2.7 wps.

In short: **you control pacing through the `seconds` you budget and the order
you write the action in -- not through any prompt rule.** `make_movie.py` adds no
pacing sentence, and neither should a hand-written prompt.

### Generate in parallel, always

**Images and videos are generated simultaneously, never one at a time.** Both
generation APIs are slow per item, so serial generation multiplies the wait by
the number of items; concurrent generation finishes in roughly the time of the
slowest item.

- `generate_images.py` already runs characters, locations, and stills through
  concurrent worker pools -- keep it that way.
- `make_movie.py generate` already submits **every** segment (throttled
  `SUBMIT_SPACING`s apart for the 25 req/min rate limit) and then polls them
  all -- no segment waits for another segment's result.
- **The rule extends to any manual or ad-hoc generation**: when regenerating a
  batch of segments or stills, or writing a one-off script, submit all the
  requests first and poll them together. Never submit one, wait for it to
  finish, then submit the next.
- The one legitimate exception is **forward frame-chaining** (used only when
  the user asks for chained scenes), which is inherently sequential -- each
  shot needs the previous shot's final frame.

### Character reference images
- 3 angles per character are **generated and uploaded** by default so they are
  available to pick from: front face, three-quarter (head-and-shoulders/waist
  up), and full body (strict side profile is dropped; override with `ref_angles`
  if a film needs it)
- If a character changes appearance (costume, aging), create a variant set
- Front portrait generated first, then others reference it for consistency
- Character images can be any reasonable size

**Which references to *attach* to each video segment (use 3 only where
necessary):**
- **Always attach the face** (`front_full_face`). `make_movie.py` enforces this.
- Beyond the face, attach a body shot **only where it adds something the opening
  still (`@image1`) does not already show.** Most scenes use just **face +
  head-and-shoulders** or **face + full body**; use all three only where
  necessary.
- **Do not duplicate the opening still's framing.** If the opening still is
  already a head-and-shoulders shot, don't also attach the three-quarter ref; if
  it's a full-body shot, don't attach the full-body ref.
- The default attached set is face + three-quarter. Override per segment with
  `char_ref_angles` in `film_config.py`, e.g.
  `"char_ref_angles": {"jane": ["front_full_face", "full_body"]}`, or just
  `["front_full_face"]` when the still already carries the body. Only angles
  actually present in `GDRIVE_CHAR_REF_IDS` are attached; missing ones are
  skipped. (This applies to the video prompt; the opening *still* itself is
  still generated from the available references.)

### Location reference images
- 1280x720 resolution (matches video aspect ratio)
- Separate variants for significant changes (day/night, seasons, etc.)

### Still images
- 1280x720 resolution
- One opening frame is generated for **every** segment (used as `@image1`, the
  locked first frame in video)
- Generated from the segment's location reference (its `location` key) plus the
  characters' reference views, so recurring settings and faces stay consistent
- **Closing frames (continuous version):** after the openings, `run_stills`
  copies each `continues_previous` segment's opening still onto the **previous**
  segment as that segment's locked closing frame, `seg<prev>_zclosing.jpg`. Only
  segments that are followed by a continuation get one. These upload alongside the
  openings (into `GDRIVE_CLOSING_IDS`) and are passed to the previous segment as
  `last_frame`, so it ends on the exact frame the continuation begins on.

### Continuous films (locked shared frames)

> **Take limits (apply to every continuous technique):** a continuous take is
> at most **three segments including the initial one**, usually **two**; and a
> continuation must not introduce any character absent from the take's initial
> segment (**hard rule**). `make_movie.py prompts/generate continuous` prints
> violations in the continuation report -- fix the config rather than generate
> through them.
- A continuous film is built **only with the user's sign-off** -- either they
  asked for one, or it was proposed (e.g. to protect dialogue continuity) and
  they approved before any clips were generated (see
  [Standard by default](#standard-by-default-chained-segments-only-on-request))
  and generated with the `continuous` commands (`make_movie.py generate
  continuous`), which keep their own files so a standard run from the same
  config is never clobbered.
- A segment marked `"continues_previous": True` in `film_config.py` flows on from
  the previous segment with no cut. It is **still a normal independent shot** with
  its own opening still and (if someone speaks) audio ref.
- **How the seam is made:** that continuation's opening still is also copied onto
  the previous segment as its locked closing frame (`zclosing`). The previous
  shot is generated to **end** on that exact frame (passed as `last_frame`) and
  the continuation to **begin** on it (`@image1`). The two shots meet on one
  identical frame -- a seamless join. No video is chained off another video, so
  nothing drifts. (The Seedance extend feature was dropped for exactly this
  reason: chained extends drift and degrade fast.)
- **Keyframe mode, no character refs, on Nano-GPT.** The previous (join) shot --
  the one carrying the `last_frame` -- is a **first-and-last-frame** shot, which
  Seedance runs in **keyframe mode**. Keyframe mode is **mutually exclusive** with
  character reference images, so a join segment is built with **first frame +
  last frame + audio only, NO character refs**, and is **routed to Nano-GPT**
  (the only provider that honors `last_frame`). The continuation segment that
  *begins* on the frame is unaffected -- it still uses its opening still + char
  refs normally. See
  [First-and-last-frame (keyframe) join segments](#first-and-last-frame-keyframe-join-segments).
- **Coherence:** keep a continuation in the **same location** (and normally the
  **same characters**) as the segment it continues, so the shared frame is
  coherent. `make_movie.py prompts continuous` reports any continuation that
  changes location. The two-speaker-per-segment ceiling applies here too.
- **Generation:** every segment is independent, so all segments generate **in
  parallel** (submissions throttled `SUBMIT_SPACING`s apart) -- no base-then-extend
  ordering, nothing waits on another segment's video. It skips work already on
  disk, so it resumes cleanly.
- Provider fallback is unchanged: a segment that fails twice on Lunostudio falls
  back to Nano-GPT (carrying the same start/closing frames); see
  [Provider fallback and upscaling (Nano-GPT)](#provider-fallback-and-upscaling-nano-gpt).

### Forward frame-chaining (preferred for continuous scenes)

This is the **preferred way to build continuous action or dialogue.** Instead of
locking both ends of a shot, it carries a real frame forward:

1. Generate a shot from its **opening image** + character/audio references, with
   **no** locked closing frame (`last_frame`). It is an ordinary image-to-video
   generation, so it keeps **all** of its character reference images.
2. Let Seedance reach its own ending.
3. **Extract that shot's final frame** and **upload it as the opening image of
   the next shot.** The next shot begins on the exact frame the previous one
   ended on -- a seamless cut with nothing to drift or mirror-flip.
4. Repeat down the scene.

**Chain limits (standing rules, 2026-07-16; enforced by `chain_regen.py` and
`make_movie.py`'s continuation validator):**

- **A chain is at most THREE segments long, INCLUDING the initial segment**
  (initial + two chained continuations). Beyond that the quality deteriorates
  too much -- each chained shot opens on a compressed video frame, and the
  softness and artifacts compound down the chain.
- **Usually keep a chain to TWO segments** (initial + one continuation). Go to
  three only when the scene truly cannot break.
- **HARD RULE -- no new characters after the initial segment.** A chained
  segment (the 2nd or 3rd of a chain) must not include any character who is not
  in the initial segment: every chained shot's cast must be a subset of the
  initial shot's cast. A new character always gets a new base segment (opening
  on its own still) instead. `chain_regen.py` refuses to run a chain that
  violates this.

Longer continuous scenes are built as **several short chains**: break the scene
at a natural cut (a reaction, an insert, an angle change), give the next chain
its own base segment with a fresh opening still, and chain from there.

**Provider policy for chained shots: Nano-GPT (`doubao-seedance-2-0`) is
PRIMARY.** Its `imageUrl` field is true image-to-video conditioning and starts
the video on the supplied frame **pixel-exactly** (verified on The Examined
Life, 2026-07-11: alignment correlation 0.998 with zero shift on all three test
seams). **Lunostudio is fallback only, because it does not pin an opening frame
in any mode** (tested same day, same seam):

- Passing the frame in `reference_images` alongside character refs puts Seedance
  in **reference mode** -- the opening is mere guidance and the shot is re-staged
  (~2-8% zoom/pan seam jumps measured).
- Even **single-image image-to-video** ("Single image = image-to-video" per the
  Luno docs) re-synthesizes the opening with ~2% zoom drift (correlation 0.85).

On Nano-GPT the chained shot keeps its **character reference images and voice
references** alongside `imageUrl` (plus `generateAudio: true`; the reference
image/audio fields take JSON-encoded array strings). First-frame-only i2v
composes with refs -- the refs-vs-frames exclusivity rule applies only to
keyframe mode's locked *last* frame. Per-run env override:
`CHAIN_PROVIDER=luno_i2v` (single-image i2v, drops char refs -- a second image
would flip it back to reference mode) or `CHAIN_PROVIDER=lunostudio` (legacy
reference mode).

**Why it's preferred over locked first-and-last-frame joins:**

| | Forward frame-chaining (preferred) | Locked first+last frame (alternative) |
|---|---|---|
| Character ref images | **kept** (normal image-to-video) | dropped (keyframe mode forbids them) |
| Seam | previous shot's **actual** last frame -- always matches | a locked target the provider must "honor" |
| Provider | **Nano-GPT i2v primary** (Luno fallback -- Luno never pins opening frames) | join shots forced to Nano-GPT keyframe mode |
| Drift / mirror-flip | none | possible if the end frame is ignored |
| Generation order | **sequential** (each shot needs the previous output) | parallel |

The only tradeoff is that a chain is **sequential**. For continuous dialogue and
action, the exact, drift-free seam (with full character references) is worth it.

**Tool -- `chain_regen.py`** (copy `chain_regen_template.py` into the project):

```bash
# one shot -- from its opening still, or from a previous clip's last frame
python3 chain_regen.py regen <id> still
python3 chain_regen.py regen <id> frame <prev_video.mp4>

# build a whole continuous run
python3 chain_regen.py chainstill <id1> <id2> ...        # id1 from its still, the rest chained forward
python3 chain_regen.py chainfrom <prev_video> <id1> ...  # id1 from an existing clip's last frame, rest chained
```

Each shot is written into the cut's videos folder as a new **versioned take**
(`seg<NN>_v<N>.mp4`; the original `seg<NN>.mp4` is its "v1") -- `videos_continuous/`
by default, or `videos/` with **`CHAIN_CUT=standard`** (for a standard film that
chains at generation time with no `continues_previous` flags, like The Examined
Life). Nothing is overwritten and **the manifest is not touched, so the film is
unchanged.** Review the takes, then select one with
`make_movie.py use <id> <version> [continuous]` (see
[Regenerating segments and stills](#regenerating-segments-and-stills-versioned-in-place--a-manifest)).
The chain frame is extracted as the clip's **true final frame** (the old
`-sseof -0.2` grab landed ~5 frames early -- invisible on a still ending, a
visible jump on a moving one).
Two optional per-run env toggles (comma-separated ids): `CHAIN_LOCREF=3,4,5` also
attaches each shot's location reference still; `CHAIN_MULTIAUDIO=17,18` attaches
voice references for **all** of a shot's speakers (each mapped to its own
`@audioN`). Note that multiple voice references are now a first-class config
option too -- set `voice_audios` on the segment (see
[Voice references](#voice-references-the-voice-pipeline)) -- so the env toggle is
only for ad-hoc runs.

**Nano-GPT error handling (chained shots must NOT silently fall to Luno):**

- **HTTP 429** ("too many video generations are already starting") is a
  *transient* start-rate limit -- it fires whenever several chains launch in
  parallel. `run_nano()` retries the submit up to **20 times at 30s intervals**
  (printing `start-limit 429 -- retry n/20`) instead of treating it as a
  failure, which would have fallen back to Luno and re-framed the chained
  opening.
- **HTTP 402** (insufficient balance) will not recover mid-run, so the script
  **aborts the whole chain with a clear message** rather than silently
  generating the rest of the chain on Luno. Top up the Nano-GPT balance, then
  resume with `chainfrom` from the last completed segment's clip.
- Any *other* submit failure still falls back to Lunostudio i2v as before.

### First-and-last-frame (keyframe) join segments

> The take limits above (max three segments per take including the initial,
> usually two; no new characters after the initial segment -- hard rule) apply
> to keyframe-joined takes exactly as to chained ones.

*(Alternative method -- prefer [forward frame-chaining](#forward-frame-chaining-preferred-for-continuous-scenes)
above for new continuous scenes.)*

The seamless join in a continuous film depends on the **previous** shot ending on
the *exact* frame the next shot opens on. That makes the previous shot a
**first-and-last-frame** generation, and Seedance handles first/last-frame control
as a distinct **keyframe mode** with two non-obvious rules that this template now
bakes in automatically.

**1. Keyframe mode is mutually exclusive with character reference images.**
Seedance has two input modes: **keyframe** (a first frame + a last frame) and
**reference** (character/multi-reference images). They cannot be combined -- if
you send a `last_frame` *and* character refs, Seedance stays in reference mode and
**silently ignores the `last_frame`**. The shot then ends wherever the action
happens to land -- a different, sometimes left-right **mirror-flipped** composition
-- and the seam visibly jumps. So a join segment is built with **only**:

- the **opening still** as the first frame (`imageUrl` / `@image1`),
- the **`zclosing` still** as the `last_frame`, and
- the **voice reference audio** (audio is compatible with keyframe mode).

**No character reference images** are attached. The locked first and last frames
already pin the faces and the composition at both ends, so nothing is lost; the
character *descriptions* still go in the prompt text (just not as ref images).
`make_movie.py` drops the refs for these segments in `openrouter_spec` /
`build_prompt` whenever a `last_frame` is present.

**2. Only Nano-GPT honors the locked `last_frame`.** We tested all three hosts on
the identical first+last+audio job:

| Provider | Accepts photoreal faces? | Honors locked `last_frame`? | Verdict |
|----------|--------------------------|-----------------------------|---------|
| **Nano-GPT** (`doubao-seedance-2-0`) | **Yes** | **Yes** -- converges to the end frame, even with the audio ref | **Use for join segments** |
| Lunostudio (`seedance-2`) | Yes | **No** -- ignores `last_frame` (and tends to time out on a frames-only job) | seams drift |
| OpenRouter (`bytedance/seedance-2.0`) | **No** -- rejects faces (`InputImageSensitiveContentDetected`) | n/a | unusable for face films |

So join segments are **routed to Nano-GPT** by `KEYFRAME_PROVIDER = "nanogpt"` in
`film_config.py`, **independently of `VIDEO_PROVIDER`**: the rest of the film can
still generate on Lunostudio while every join shot goes to Nano-GPT in keyframe
mode. `make_movie.py generate continuous` does this automatically -- it pulls the
`last_frame`-carrying segments out, runs them on Nano-GPT (logged as
`keyframe mode -- first+last frame + audio, no char-ref images`), and generates
the remaining segments the normal way. Set `KEYFRAME_PROVIDER = None` to opt out
(the locked end frame is then not honored and seams drift).

**To re-do just the join segments of an existing continuous film** (e.g. after a
script tweak), delete those segment videos from `videos_continuous/` and re-run
`make_movie.py generate continuous`; only the missing ones regenerate, and the
join segments take the Nano-GPT keyframe path.

### Voice references (the voice pipeline)
- **Who needs one:** a character who speaks in **more than one segment**, so
  their voice stays consistent across independently generated shots. Every
  segment is an independent shot that attaches its own audio reference (continuity
  is carried by locked shared frames, not by chaining video), so every speaking
  segment counts. A character who speaks in only one segment needs no reference.
- The voice pipeline is `generate_voices.py`, run as the **last step before
  generating the movie** (after `make_movie.py prompts`):
  - `list` writes the qualifying characters to `audio/audio_references.txt`
    and flags any `voice_desc` that is missing or lacks the speaker's rough age.
  - `prompts` writes an in-scene prompt per character to
    `audio/voice_ref_prompts.txt`. Each prompt has the character deliver a
    **5-7 second section of their own dialogue, typical of how they speak**
    (~13-18 words; auto-picked from their dialogue in `SEGMENTS`, or set via
    `voice_ref_line` in `CHARACTERS`) -- **not** a generic screen test. The
    clip is played **in the context of its scene**: the prompt carries the
    scene's setting and a short description of the moment the line comes from,
    never just "a person talking". It also carries the character's **full
    description** and an explicit **VOICE description** that must **always
    include the rough age** plus the voice's **general characteristics**
    (confident, careful, weary...) -- lasting traits, **never scene-specific
    moods** like "worried". Distinct, well-described voices are what keep the
    cast from sounding alike. The prompt uses **only the frontal face shot**
    (`@image1`) as its single image reference.
  - `generate` submits each 7-second clip to **Seedance** (Mark prefers
    Seedance's audio to a dedicated TTS API), **always at 480p** -- we keep
    only the audio, so higher resolution would be wasted. The throwaway video
    is saved to `extras/`, then **only the audio** is exported to
    `audio/<char>_voice_reference.mp3` -- that file is the reference.
- After generating, `upload_images.py audio` hosts the audio files and the URLs
  go into `VOICE_REFS`. The audio reference is then attached as `@audio1` in the
  relevant segment prompts during video generation.
- A missing voice file is non-fatal: if the pipeline expects an audio reference
  it cannot find, it simply proceeds without that `@audio1` for the affected
  segments rather than stopping. The segment is still generated; Seedance just
  voices the line without a reference clip.

**Multiple voice references in one segment.** A single-speaker shot uses
`voice_audio` (one character key) -> `@audio1`. When **two characters speak in
the same shot**, set **`voice_audios`** to an ordered list of character keys,
e.g. `"voice_audios": ["josef", "leni"]`. Each key's voice reference is attached
as its own `@audio1`, `@audio2`, … (only keys present in `VOICE_REFS` are
attached, order preserved), each character's description cites *its* `@audioN`,
and a **VOICE REFERENCES** note in the prompt tells Seedance which voice belongs
to which character so the two voices stay distinct. This is wired through every
provider -- Lunostudio's `reference_audio` list and Nano-GPT / OpenRouter's
`reference_audios` -- and through `chain_regen.py` (which builds on the same
prompt builder; its `CHAIN_MULTIAUDIO` env toggle remains for ad-hoc runs).

### Dialogue direction (who a character speaks to)
- When two characters are in a shot, Seedance often gets confused about who a
  line is aimed at and has the character **speak straight to the camera /
  audience.**
- To prevent this, **the segment's `action` text itself must state clearly who
  the speaker is addressing** -- e.g. "Jane, facing Tom, says: 'I told you.'"
  Write this into the action when filling in `SEGMENTS`; there is **no separate
  DIALOGUE DIRECTION section** in the prompt (the old auto-added section and
  the `speaking_to` config key are gone).
- **In a two-speaker segment this matters double: attribute every line to its
  named speaker** ("Jane, facing Tom, says: '...' Tom replies: '...'") so the
  prompt leaves no doubt who speaks what, and set `voice_audios` so each named
  speaker is tied to their own voice reference (`@audioN`). A line of dialogue
  the action doesn't attribute is a line Seedance may give to the wrong mouth.
- For **self-directed speech, a soliloquy, or a voice-over**, make that
  explicit in the action too ("to himself", "voice-over as the camera pans").

### Names, unusual words, and pronunciation
- The video model often mishandles unusual names and foreign words (e.g.
  "Ashkenazi," or the Greek names in *A Midsummer Night's Dream*).
- Handle this in the PROMPTS, not the screenplay. The screenplay and segment
  scripts should spell each name plainly and consistently.
- Fill in the `PRONUNCIATIONS` glossary in `film_config.py`: map each tricky
  term to a **clean, speakable phonetic respelling** (CAPS = stressed syllable,
  hyphens separate syllables), e.g. `"Ashkenazi": "ahsh-keh-NAH-zee"` or
  `"Hermia": "HUR-mee-uh"`. **When in doubt, include the respelling** -- a
  glossary entry for a name that would have been fine costs nothing, while a
  mangled name costs a regeneration.
- **Names preflight -- before the final video-generation step.** Alongside the
  length/resolution check that precedes `make_movie.py generate`, sweep the
  dialogue for any name or proper noun likely to give the model trouble --
  foreign, archaic, or invented names, unusual place names, ambiguous spellings
  -- including ones not yet in the glossary. **Flag them to the user and propose
  a solution for each** (usually a phonetic respelling to add to
  `PRONUNCIATIONS`; occasionally rephrasing a line so the name isn't spoken, or
  a short test clip to tune the worst one first). Generation is the slow,
  costly step -- catching a bad name here is far cheaper than re-rolling
  segments after.
- **Bake the pronunciation into the spoken dialogue -- don't rely on a note.**
  This is the key lesson: Seedance voices the *literal* quoted dialogue text, so
  a standalone "pronounce X as Y" instruction does **not** reliably change how it
  says the word. `make_movie.py` therefore **substitutes the phonetic respelling
  directly into the spoken dialogue** -- the words the character actually says --
  for every glossary term that appears inside the **quoted dialogue** of a
  segment's `action`. It then appends a short reinforcement note listing those
  respellings. The substitution touches **only the quoted dialogue** (it
  correctly ignores apostrophes inside contractions/possessives like *don't* or
  *Hermia's*); **stage directions and the character labels keep the real,
  correctly-spelled name**, so the screenplay still reads normally and the model
  still knows who is who. A glossary term spoken nowhere in a segment changes
  nothing there.
- **Expect to iterate.** Even with the respelling in the dialogue, a name can
  take a couple of tries to land. The cheapest way to dial one in is a single
  short test clip of the speaker saying a sentence with the names (rather than
  regenerating full segments); adjust the respelling until it sounds right, then
  regenerate the affected segments. Pronunciation is not always perfect even
  then -- tune the worst offenders and move on.
- If anything in the user's initial description looks like an important
  mistake or misspelling (a garbled name, a wrong date), clarify it with the
  user BEFORE generating the screenplay or prompts.

### Backups
- Before any revision, copy the current state to the `backup/` folder
- Name backups with timestamps: `backup/v1_2024-01-15/`
- **When making a major revision, also save a backup of the scripts and prompts
  to the `extras/` folder** -- a timestamped copy (e.g. `extras/revision_2024-01-15/`)
  of the screenplay/segments (`screenplay/`), `film_config.py`, and the
  generated prompt files (`seedance_prompts.txt`, `seedance_prompts_continuous.txt`,
  `audio/voice_ref_prompts.txt`). This keeps the text you most often iterate on
  easy to find and roll back, separate from full-state snapshots in `backup/`.

## Provider fallback and upscaling (Nano-GPT)

Video segments are generated on **Lunostudio Seedance 2** by default, with
automatic fallback to **Nano-GPT** (same Seedance 2 model family, different
host). Nano-GPT also provides the default **SeedVR2 upscaler**.

**Automatic fallback after two failures.** For each segment, `make_movie.py`
attempts Lunostudio up to `MAX_LUNO_ATTEMPTS` (= 2) times. If both attempts
fail, the segment is re-routed to Nano-GPT with no manual step. A segment is
only reported as `failed` if it fails on Lunostudio twice **and** on Nano-GPT.
`make_movie.py status` shows which provider produced each segment.

**Using Nano-GPT from the start.** If the user asks for a film to go through
Nano-GPT instead of Lunostudio, set `VIDEO_PROVIDER = "nanogpt"` in
`film_config.py` (or `"lunostudio"` for the default behavior). With
`"nanogpt"`, every segment is generated on Nano-GPT from the first attempt and
Lunostudio is not used. (The legacy `"openrouter"` value still routes to the old
OpenRouter client.)

**Setup.** Copy `nanogpt_video_template.py` into the project as
`nanogpt_video.py` (the quick-start `cp` lists already include it) and set
`NANOGPT_API_KEY` in `film_config.py` (the key is in `api_keys.txt`). The model
is `NANOGPT_MODEL` (default `doubao-seedance-2-0`).

**File storage (litterbox).** Nano-GPT requires real public URLs for every
reference. Project stills/char-refs/voice audio are already on the Lunostudio
CDN, so they work as-is; anything that only exists locally (a continuity frame
extracted from a previous clip, or a clip being upscaled) is uploaded to
**litterbox** to get a temporary, auto-expiring public URL.

**What carries over to Nano-GPT:** the opening still (as the start frame),
character reference images, the voice reference audio, the full visual-style and
character descriptions, and the names/pronunciation handling (the phonetic
respelling baked into the spoken dialogue, plus the reinforcement note).
Nano-GPT uses separate reference URL fields instead of Lunostudio's `@`-tags, so
its prompts omit the tag language but keep all the descriptive content.

**Continuous-version continuity:** continuity is carried entirely by locked
shared frames (each continuation's opening still is the previous segment's
`last_frame`), so it works the same on either provider with no special handling
-- Nano-GPT passes the opening as `imageUrl` and the locked closing as
`last_image`. No video is referenced.

**Upscaling.** `make_movie.py upscale [continuous]` enlarges every finished
segment with SeedVR2 to `UPSCALE_RESOLUTION` (720p default), writing
`videos_upscaled/segNN.mp4`. Audio is preserved and existing files are skipped.
See [Upscaling finished clips](#upscaling-finished-clips-seedvr2-default).

## Project folder structure

A film project lives **on the Desktop, in a folder named for the film**
(`~/Desktop/My_Film/`). The same folder holds the pipeline's working files
AND the Film Studio labs (see [Film Studio integration](#film-studio-integration)):

```
~/Desktop/My_Film/
  film_config.py          # filled-in config (from template)
  generate_images.py      # image generation (from template)
  generate_voices.py      # voice reference pipeline (from template)
  upload_images.py        # CDN upload (from template)
  make_movie.py           # video generation + upscaling (from template)
  nanogpt_video.py        # Nano-GPT backup provider + SeedVR2 upscaler (from template)
  openrouter_video.py     # legacy OpenRouter alternate provider (optional, from template)
  screenplay/
    screenplay.txt        # full screenplay
    segments.txt          # segmented version
    frame_descriptions.txt # generated by generate_images.py
    segment_list.txt      # numbered segment list for review (make_movie.py list)
  characters/
    protagonist_front_full_face.jpg
    protagonist_profile_face.jpg
    protagonist_three_quarter.jpg
    protagonist_full_body.jpg
    ...
  locations/
    kitchen_evening.jpg
    kitchen_night.jpg
    ...
  stills/
    seg01_opening.jpg        # original opening still ("v1")
    seg02_opening.jpg
    seg07_opening_v2.jpg     # a re-rolled still (generate_images.py restill 7)
    manifest.txt             # chosen opening still per segment (usestill / edit)
    ...
  videos/
    seg01.mp4                # original take ("v1")
    seg02.mp4
    seg07_v2.mp4             # a re-rolled take (make_movie.py regenerate 7)
    manifest.txt             # the film lineup: chosen take per segment (use / edit)
    ...
  videos_upscaled/          # SeedVR2-upscaled segments (make_movie.py upscale)
    seg01.mp4 ...
  audio/
    audio_references.txt        # list of characters needing a voice ref (generate_voices.py list)
    voice_ref_prompts.txt       # 5s voice-clip prompts (generate_voices.py prompts)
    protagonist_voice_reference.mp3  # audio-only voice reference (generate_voices.py generate)
  extras/
    protagonist_voice_ref_clip.mp4   # throwaway 480p voice clip (only its audio is kept)
    ...                              # plus any assets, backups, or notes you keep here
  backup/
    v1_YYYY-MM-DD/        # backup before major revisions
                          # (re-rolls are NOT backed up here -- every take is kept
                          #  in place as seg<NN>_v<N>.mp4; nothing is overwritten)
  seedance_prompts.txt    # generated by make_movie.py (standard cut)
  seedance_tasks.json     # task tracking (auto-generated)
  stills_progress.json    # still generation tracking (auto-generated)
  palmier_import.txt      # Palmier Pro import manifest (make_movie.py palmier)
  my_film_full.mp4        # final stitched film (standard cut -- ffmpeg path)

  # Continuous film (only with the user's sign-off -- uses its own files, never the above):
  videos_continuous/             # continuous-version segment videos (+ versioned
    seg01.mp4 ...                #   takes seg<NN>_v<N>.mp4 and its own manifest.txt)
  seedance_prompts_continuous.txt # continuous prompts
  seedance_tasks_continuous.json  # continuous task tracking
  continuous_segments.txt         # the saved list of locked-frame continuations
  palmier_import_continuous.txt   # Palmier Pro import manifest (make_movie.py palmier continuous)
  my_film_continuous_full.mp4     # final stitched continuous cut (ffmpeg path)

  browse/                   # Finder-friendly hard-link views (auto-refreshed
    film/     seg01.mp4 ... #   by sync_pipeline.js): the current cut,
    stills/   seg01_opening.jpg ...  # chosen stills, and
    newest/   ...           #   whatever the last generation batch produced

  # Film Studio labs (same folder -- created/updated by sync_pipeline.js;
  # the app opens ~/Desktop/My_Film directly as a project):
  project.json              # Film Studio project marker
  project_manifest.json     # single entry point linking all labs (auto-updated)
  pipeline_sync.json        # sync ledger: what has already been synced
  script_lab/               # screenplay / segments / prompts as versioned revisions
  asset_lab/                # stills, characters (with views), locations
  generated_videos/         # one item per segment; each take a revision
  audio_lab/                # voice references
  timeline/                 # timeline.json (active takes in film order)
```

## Film Studio integration

**One folder, two systems.** The film folder is simultaneously the pipeline's
working directory and a Film Studio project. The **pipeline stays the main
means of generation**; the Film Studio app (at
`~/Desktop/film_studio`) is where the results are reviewed and
manipulated -- versioned takes, active-take selection, timeline preview,
clipping, export. The pipeline's flat folders (`videos/`, `stills/`, ...) are
never touched by the app; the labs hold copies registered as items/revisions.

**The bridge is one command, run after every pipeline stage:**

```bash
node ~/Desktop/film_studio/src/tools/sync_pipeline.js ~/Desktop/My_Film
```

It is **incremental and idempotent** (a ledger, `pipeline_sync.json`, records
what has been synced), so run it freely -- after generating images, after the
voice pipeline, after `make_movie.py generate`/`fetch`, after re-rolls, after
`use`/`usestill` selections, after script revisions. Each run picks up only
what is new:

| Pipeline output | Film Studio result |
|---|---|
| `videos/seg07.mp4`, `seg07_v2.mp4`, ... | one "Seg 07 -- HEADING" video item; each take a revision |
| `videos/manifest.txt` chosen take | that revision becomes the item's **active** take |
| `stills/seg07_opening[_vN].jpg` (+ `stills_high/`) | "Seg 07 opening" still item with revisions |
| `characters/<key>_<angle>.jpg` | one character item per key, angles as views |
| `locations/*.jpg` | location items |
| `audio/*_voice_reference.mp3` | audio-lab voice items (named from `audio_references.txt`) |
| `screenplay/screenplay.txt`, `segments.txt`, `seedance_prompts.txt` | script-lab revisions (new revision on change) |
| (after any of the above) | timeline auto-syncs: every video's active take, in segment order |

**Finder-friendly views (`browse/`).** Every sync also refreshes a `browse/`
folder in the film folder, for reviewing media in the file browser without
digging through versioned folders or the app:

- `browse/film/` -- the **current cut**: the manifest-chosen take of each
  segment, hard-linked as `seg01.mp4`, `seg02.mp4`, ... so sorting by name is
  film order.
- `browse/stills/` -- the chosen opening still per segment.
- `browse/newest/` -- **exactly the files added by the most recent sync that
  added anything** (e.g. the takes and stills from the last regeneration
  batch) -- the place to look right after generating.

These are **hard links**: zero disk cost, and Finder treats them as real files
(thumbnails, QuickLook, gallery view all work). The folder is regenerated
wholesale on every sync -- safe to delete, pointless to edit by hand.

Notes:

- **Flow is one-way, pipeline -> labs.** Work done in the app (trims, clips,
  imports, script edits saved in the app) lives in the labs and is not written
  back to the pipeline folders.
- Timeline removals made in the app are respected -- sync will not re-add a
  video the user removed from the timeline.
- **Not synced on purpose:** `extras/` (its throwaway voice-ref *videos* would
  be auto-added to the film timeline), `chain_frames/`, `backup/`,
  `videos_upscaled/` (the upscale tracks the chosen take; the takes themselves
  are the items), code and progress files.
- For a continuous cut, add `--continuous` (reads `videos_continuous/` and
  `seedance_prompts_continuous.txt`).
- **Renumber before the first sync.** The ledger is keyed by filename, so run
  `make_movie.py renumber` before syncing if you plan to renumber; renaming
  files after a sync makes them look new.
- To open the film in the app: **Open...** and pick the film folder.
- Legacy two-folder imports still work: `import_pipeline.js <film>` makes a
  separate `<film>_Studio` copy (one-time), and `sync_pipeline.js <film> --dest
  <studioFolder>` syncs into a separate folder incrementally. New films should
  use the one-folder layout above instead. A studio-only project (no pipeline
  folders) gets its `browse/film` + `browse/stills` views from
  `node ~/Desktop/film_studio/src/tools/browse_studio.js <studioProject>`
  -- built from the labs' active takes; re-run after changing active takes in
  the app.

## Dependencies

```bash
pip install openai certifi
```

ffmpeg is required for stitching, for extracting the audio from voice-reference
clips in the voice pipeline (`generate_voices.py`), and for pulling a continuity
frame before a Nano-GPT continuation (install via `brew install ffmpeg` on
macOS). The Nano-GPT client (`nanogpt_video.py`) and the legacy OpenRouter client
(`openrouter_video.py`) use only the standard library plus `certifi` -- including
the litterbox upload that stages local files for Nano-GPT.

## Previous films

Reference implementations from completed films:

- **The Ashkenazi Nebula** (v1): `~/Desktop/Other_Films/Ashkenazi_Nebula/`
  - `generate_images.py` -- original image generation script
  - `screenplay/the_ashkenazi_nebula_segments.txt` -- segment breakdown example

- **The Ashkenazi Nebula** (v2): `~/Desktop/Other_Films/Ashkenazi_Nebula_v2/`
  - `make_movie.py` -- original video generation script
  - `seedance_prompts.txt` -- full prompt list example
  - `characters/` -- character reference image examples
  - `stills/` -- opening still examples
  - `videos/` -- generated video segments
