# AI Filmmaking Pipeline

A complete pipeline for producing short films with AI: turn a screenplay into
character/location reference images, voice references, generated video clips, and
a final assembled film. It uses **OpenAI gpt-image-2** for images and
**Lunostudio Seedance 2** for video (with **Nano-GPT** as a backup provider).

The repo has two halves:

- **`film_template/`** — the Python pipeline. Templates you copy into a per-film
  folder and run to generate images, voices, and video, then stitch the cut.
- **`SKILL.md`** — a [Claude](https://claude.ai/code) skill that teaches an agent
  how to drive the pipeline: segmentation rules, prompt structure, pacing, voice
  references, regeneration, and the workflow conventions used throughout.

Together they let you say "make me a film of this script" to Claude and have it
run the whole pipeline, or run the scripts yourself by hand.

## What's in `film_template/`

| File | Purpose |
|------|---------|
| `film_config_template.py` | Per-project configuration — copy as `film_config.py` |
| `generate_images_template.py` | Character refs, location refs, segment stills |
| `generate_voices_template.py` | Voice-reference pipeline (Seedance audio) |
| `make_movie_template.py` | Video generation, upscaling (SeedVR2), final assembly |
| `chain_regen_template.py` | Forward frame-chaining for continuous scenes |
| `upload_images_template.py` | Upload media to the Lunostudio CDN |
| `nanogpt_video_template.py` | Nano-GPT backup video provider + SeedVR2 upscaler |
| `openrouter_video_template.py` | Legacy OpenRouter alternate provider (optional) |
| `workflow_prompt.txt` | The full prompt for kicking off a new film |
| `README.md` | **The authoritative, detailed pipeline reference** — API details, prompt structure, pacing rules, regeneration, everything |

For anything beyond this overview, read
[`film_template/README.md`](film_template/README.md) — it's the source of truth.

## Setup

1. **API keys.** Copy the example and fill in your own keys:

   ```bash
   cd film_template
   cp api_keys.example.txt api_keys.txt   # api_keys.txt is git-ignored
   ```

   You'll want an OpenAI key (images) and a Lunostudio key (video) at minimum;
   Nano-GPT and OpenRouter keys are optional backups.

2. **Start a film.** Create a folder for the film, copy the templates in, rename
   `film_config_template.py` to `film_config.py`, and fill in the screenplay,
   characters, locations, and segments. The `workflow_prompt.txt` walks through
   the whole process; the detailed README documents every step.

3. **Run the stages** (images → voices → videos → assembly), generating in
   parallel. See the pipeline README for the exact commands per stage.

## Pipeline overview

```
Screenplay -> Segments -> Characters/Locations -> Still Images -> Prompts -> Voice References -> Videos -> Final Film
```

Each film is a set of independent segments by default; continuous / chained
scenes are supported (forward frame-chaining) when you want a seamless take.
Every shot keeps all its versioned takes, and a `manifest.txt` per folder names
the one that's in the cut — nothing is ever overwritten.

## Using it with Claude (the skill)

`SKILL.md` is a Claude skill. Drop it where Claude Code discovers skills (e.g.
`~/.claude/skills/filmmaking/SKILL.md`), point its paths at your copy of this
repo, and Claude will follow the pipeline's conventions when you ask it to make a
film. The skill defers to `film_template/README.md` wherever the two differ.

## Pairs with Film Studio

[Film Studio](https://github.com/markgimein/film-studio) is a companion desktop
app + MCP server for reviewing takes, picking active revisions, previewing the
timeline, and exporting the finished film. The pipeline stays the means of
generation; its output syncs into Film Studio's labs with:

```bash
node /path/to/film_studio/src/tools/sync_pipeline.js /path/to/<Film_Name>
```

## A note on the paths in these files

The templates and skill assume a particular layout (e.g.
`~/Desktop/film_template/...` for the pipeline and `~/Desktop/<Film_Name>/` for a
film project). Adjust them to wherever you keep your copy.
