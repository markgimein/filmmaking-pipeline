"""
FILM CONFIGURATION TEMPLATE
============================
Copy this file to your film project folder and rename it to film_config.py.
Fill in all sections marked with [FILL IN] for your specific film.

This config is imported by generate_images.py and make_movie.py.

CONTINUOUS vs. STANDARD
  Every film is a STANDARD film unless the user explicitly asks for continuous
  scenes or chained segments -- never chain on your own initiative, and no need
  to ask about it up front. The choice shapes how the script is written:

  - STANDARD film (the default): independent shots, each with its own opening
    image. Script rules: no more than one person speaks per segment, and no
    conversation stretches over more than two segments (very short BY DESIGN --
    use a continuous film for longer exchanges; voice-overs/narration are not
    conversations and can run long). Every segment begins with an opening
    image. Leave "continues_previous" off every segment.

  - CONTINUOUS film: continuous action or dialogue is held together with LOCKED
    SHARED FRAMES (NOT the Seedance extend feature, which drifts over a chain).
    EVERY segment -- continuation or not -- is still an independent shot with its
    own opening image. A segment that flows on from the one before it with no cut
    is flagged "continues_previous": True. The trick: that continuation's opening
    still is ALSO copied onto the PREVIOUS segment as its locked LAST frame (its
    "zclosing" image). The previous shot is generated to END exactly on that
    frame and the continuation to BEGIN on it, so the join is invisible -- and
    because each shot is one fresh generation, nothing drifts. A continuation
    should keep the same location (and normally the same characters) as the
    segment it continues, so the shared frame is coherent. Still keep to one
    speaker per segment; the continuity comes from the shared frame, not from
    cramming a whole exchange into one shot.

  This config drives whichever kind of film was chosen. The standard cut
  ignores "continues_previous", so the flag is harmless if it is left in.

  OVERRIDE: If the user gives different specific rules for video creation in
  their prompt, those rules win over the defaults described here and in the
  README.
"""

from pathlib import Path

# === PROJECT PATHS ===
# [FILL IN] Set the film title and base directory
FILM_TITLE = "My Film Title"
FILM_SLUG = "my_film_title"  # lowercase, underscores, used for filenames

BASE_DIR = Path(__file__).parent  # the film project folder
CHARACTERS_DIR = BASE_DIR / "characters"
LOCATIONS_DIR = BASE_DIR / "locations"
STILLS_DIR = BASE_DIR / "stills"
VIDEOS_DIR = BASE_DIR / "videos"
AUDIO_DIR = BASE_DIR / "audio"
SCREENPLAY_DIR = BASE_DIR / "screenplay"
BACKUP_DIR = BASE_DIR / "backup"
# Catch-all folder in every project for assets, backups, notes, and the like.
# On a MAJOR revision, save a timestamped backup of the scripts and prompts here
# (the screenplay/segments, film_config.py, and the generated prompt files).
# The voice pipeline also drops its throwaway voice-reference clips here (only
# the extracted audio is kept as the actual reference).
EXTRAS_DIR = BASE_DIR / "extras"

# === API KEYS ===
# Read from the shared api_keys.txt or hard-code here
OPENAI_API_KEY = "sk-proj-..."     # [FILL IN]
LUNO_API_KEY = "luno_sk_..."       # [FILL IN]
NANOGPT_API_KEY = "sk-nano-..."    # [FILL IN] backup / alternate video provider + upscaler
OPENROUTER_API_KEY = "sk-or-v1-..."  # [FILL IN] legacy alternate video provider (optional)

# === GOOGLE DRIVE ===
# [FILL IN] The Google Drive folder ID where reference images are uploaded.
# This is the part after /folders/ in the Drive URL.
GDRIVE_FOLDER_ID = ""

# === IMAGE GENERATION SETTINGS ===
OPENAI_MODEL = "gpt-image-2"
IMAGE_SIZE = "1280x720"

# === VIDEO GENERATION SETTINGS ===
SEEDANCE_MODEL = "seedance-2"
SEEDANCE_RESOLUTION = "480p"  # 480p, 720p, or 1080p
SEEDANCE_ASPECT = "16:9"
SEEDANCE_MODE = "standard"  # standard or fast

# Which provider generates the video segments:
#   "lunostudio" (default) -- generate on Lunostudio Seedance 2; any segment
#                that fails twice is automatically re-routed to Nano-GPT.
#   "nanogpt"    -- generate every segment via Nano-GPT from the start
#                (still Seedance 2, just a different host).
#   "openrouter" -- legacy alternate host (kept for back-compat; not the
#                default backup any more).
VIDEO_PROVIDER = "lunostudio"

# Seedance 2 model id on Nano-GPT, used for the automatic fallback and when
# VIDEO_PROVIDER == "nanogpt". "doubao-seedance-2-0" is the plain Seedance 2.0
# (image-to-video with the full reference set). Alternates: "doubao-seedance-2-0-fast",
# "bytedance-seedance-2-0" (Turbo), or "bytedance/seedance-2.0/image-to-video-spicy"
# for guaranteed native audio generation.
NANOGPT_MODEL = "doubao-seedance-2-0"

# Legacy: Seedance 2 model id on OpenRouter, only used when
# VIDEO_PROVIDER == "openrouter". "bytedance/seedance-2.0-fast" is also valid.
OPENROUTER_MODEL = "bytedance/seedance-2.0"

# Provider for KEYFRAME (first-and-last-frame) segments -- continuous films only.
# A segment that is FOLLOWED by a continuation carries a locked last_frame and is
# generated in Seedance KEYFRAME mode: first frame + last frame + audio, and NO
# character reference images (keyframe mode is mutually exclusive with reference
# images -- attach any and Seedance ignores the last_frame, so the seam drifts).
# Only Nano-GPT reliably honors the locked last_frame, so these join segments are
# routed to Nano-GPT regardless of VIDEO_PROVIDER -- the rest of the film still
# follows VIDEO_PROVIDER (e.g. Lunostudio). Set to None to disable the routing
# (the locked end frame is then NOT honored and seams drift). See the README's
# "First-and-last-frame (keyframe) join segments" section.
KEYFRAME_PROVIDER = "nanogpt"

# === UPSCALING SETTINGS ===
# Finished clips are upscaled with Nano-GPT's SeedVR2 by default
# (`make_movie.py upscale`). The local clip is staged on litterbox to get a
# public URL, then enlarged to UPSCALE_RESOLUTION. SeedVR2 supports 720p /
# 1080p / 2k / 4k; alternate model ids: "bytedance-seedance-upscaler",
# "video-upscaler" (FlashVSR), "clarity-ai/crystal-video-upscaler".
UPSCALE_PROVIDER = "nanogpt"
UPSCALE_MODEL = "seedvr2-video-upscaler"
UPSCALE_RESOLUTION = "720p"

# === FILM PREAMBLE ===
# [FILL IN] A short description of the film's overall look and feel.
# This is prepended to every still-image prompt for visual consistency.
STILL_PREAMBLE = (
    "This is a photorealistic still frame from a short film, "
    f"'{FILM_TITLE}'. Cinematic 16:9 movie still, photorealistic. "
    "Among the attached images, any location/setting reference shows the "
    "room or place (recreate it); the others are character references -- "
    "match their faces, hair and clothing exactly."
    # [FILL IN] Add film-specific visual direction here, e.g.:
    # "Space scenes: violet and gold dust. Flashbacks: warm Kodachrome color."
)

# [FILL IN] Preamble for character reference image generation.
CHAR_PREAMBLE = (
    "Photorealistic character reference image for a short film, "
    f"'{FILM_TITLE}'. Naturalistic lighting, realistic skin texture, "
    "cinematic photographic quality, plain dark grey studio background. "
    "No text, no watermarks."
)

# [FILL IN] Preamble for location reference image generation.
LOCATION_PREAMBLE = (
    "Photorealistic location reference image for a short film, "
    f"'{FILM_TITLE}'. Cinematic 16:9 composition, naturalistic "
    "lighting, no text, no watermarks."
)

# === CHARACTERS ===
# [FILL IN] Define every character. Each key is a short prefix used in
# filenames and segment references.
#
# Fields:
#   name       - full character name for prompts
#   desc       - detailed physical description (face, hair, clothes, age, etc.).
#                Used to GENERATE the reference images -- keep the detail here.
#   video_desc - SHORT description used in the VIDEO prompts: one line, only
#                what is essential (who they are / their role / bearing). Do
#                NOT repeat appearance the reference images already show --
#                the refs carry the physical look. Falls back to desc if
#                omitted, but every new film should fill it in.
#   voice_desc - voice description for video generation (optional, only for
#                characters who speak more than once)
#   temperament- (optional) the character's general temperament, used by the
#                voice pipeline (generate_voices.py) in the in-character voice
#                prompt. Defaults to voice_desc if omitted.
#   voice_ref_line - (optional) the exact line the character delivers in their
#                7-second voice-reference clip. It MUST be a real line from the
#                piece (the clip is delivered IN CHARACTER, not a generic screen
#                test). If omitted, the voice pipeline auto-pulls a real quote
#                from this character's dialogue in SEGMENTS.
#   ref_angles - which reference angles to generate
#                (default: front_full_face, three_quarter, full_body)
#   variants   - optional dict of variant name -> description, for costume
#                changes or aging (each variant gets its own set of refs)

CHARACTERS = {
    # Example:
    # "protagonist": {
    #     "name": "Jane Doe",
    #     "desc": (
    #         "A 35-year-old woman with short auburn hair, green eyes, "
    #         "angular face, wearing a navy peacoat over a white blouse."
    #     ),
    #     "video_desc": "A watchful woman in her mid-30s, an investigator.",
    #     "voice_desc": (
    #         "A calm, measured alto with a slight Southern accent."
    #     ),
    #     "temperament": "Wry and watchful; speaks slowly, rarely raises her voice.",
    #     # A real line she speaks in the film (else a quote is auto-pulled):
    #     "voice_ref_line": "I told you this would happen. Nobody ever listens.",
    #     "ref_angles": ["front_full_face", "three_quarter", "full_body"],
    #     "variants": {},
    # },
}

# Reference angles available for character images.
ANGLES = {
    "front_full_face": (
        "Head-and-shoulders portrait, facing the camera directly, "
        "neutral expression, soft even lighting."
    ),
    "profile_face": (
        "Head-and-shoulders portrait in strict side profile facing "
        "left, soft even lighting."
    ),
    "three_quarter": (
        "Portrait from the waist up, body angled 45 degrees, face "
        "turned toward the camera, soft even lighting."
    ),
    "full_body": (
        "Full body shot, head to toe, standing, facing the camera, "
        "soft even lighting."
    ),
}

# === LOCATIONS ===
# [FILL IN] Define every location. Each gets one or more reference images.
#
# Fields:
#   name      - location name for prompts
#   desc      - detailed description of the location
#   variants  - dict of variant name -> description (e.g. "day", "night")
#               If empty, one default image is generated.

LOCATIONS = {
    # Example:
    # "kitchen": {
    #     "name": "Brooklyn kitchen, 1987",
    #     "desc": (
    #         "A warm cramped 1980s Brooklyn kitchen: flowered tablecloth, "
    #         "brass candlesticks, a pot on the stove, linoleum floor."
    #     ),
    #     "variants": {
    #         "evening": "Evening, warm lamplight, Shabbat candles lit.",
    #         "night": "Late night, candles burned low, dim.",
    #     },
    # },
}

# === VISUAL STYLES ===
# [FILL IN] Define reusable visual style strings. Reference these by key
# in the SEGMENTS list below. Add as many as your film needs.

VISUAL_STYLES = {
    # Example styles from The Ashkenazi Nebula:
    # "capsule": "Gritty 70mm science-fiction realism: practical instrument "
    #            "lighting, worn flight hardware, natural skin texture, "
    #            "shallow depth of field. True photographic realism.",
    # "kodachrome": "Warm 1980s Kodachrome 35mm film: soft halation, gentle "
    #               "grain, domestic intimacy, true-to-life faces.",
    # "surreal": "Photoreal surrealism: an impossible subject rendered with "
    #            "completely naturalistic photographic light and texture.",
}

# === PRONUNCIATIONS / UNUSUAL WORDS ===
# [FILL IN] Glossary of names and unusual words the video model tends to
# mangle -- foreign or archaic names, invented terms, technical jargon.
#
# Map each term to a CLEAN, SPEAKABLE PHONETIC RESPELLING (CAPS marks the
# stressed syllable, hyphens separate syllables), e.g. "HUR-mee-uh". This is NOT
# just a side note: make_movie.py substitutes the respelling directly INTO the
# spoken dialogue -- the actual words the character says -- whenever the term
# appears inside the quoted dialogue of a segment's "action". Seedance voices
# the literal dialogue text, so a separate "pronounce X as Y" note alone does
# not reliably change how it says the word; putting the phonetic spelling in the
# spoken words does. The substitution touches ONLY the quoted dialogue -- stage
# directions and the character labels keep the real, correctly-spelled name, so
# the screenplay reads normally and the model still knows who is who. A short
# reinforcement note listing the respellings is appended as well. A term spoken
# nowhere in a segment changes nothing there.
#
# So keep the plain, correctly-spelled name everywhere in the screenplay and in
# SEGMENTS; only this glossary holds the phonetic form. Expect to iterate the
# respelling -- generate one short test clip of the speaker saying the names and
# tune it until it sounds right; pronunciation can take a couple of tries.

PRONUNCIATIONS = {
    # Example (value = how it should SOUND; it is injected into the spoken line):
    # "Ashkenazi": "ahsh-keh-NAH-zee",
    # "Lysander":  "Lye-SAND-er",
    # "Hermia":    "HUR-mee-uh",
    # "Titania":   "tie-TAINE-ia",
}

# === SEGMENTS ===
# [FILL IN] Define every segment of the film. Each segment becomes one
# video clip (4-15 seconds).
#
# SEGMENTATION RULES (the user's own video-creation rules, if any, override
# these):
#   - No segment shorter than 4 seconds or longer than 15 seconds.
#   - No more than one person speaks on screen in a single segment.
#   - No conversation may stretch over more than two segments. Conversations
#     are very short BY DESIGN; for longer exchanges use a continuous film.
#     (Voice-overs/narration are not conversations and can run as long as needed.)
#   - Every segment that follows a cut BEGINS with an opening image.
#   - If a voiceover or dialogue would be badly interrupted by a cut,
#     keep the scene in one segment even if it spans what would normally
#     be two cuts. In that case, set "mid_cut_ref" to the segment ID
#     whose opening still represents the second composition, and that
#     image will be attached as @image2 in the video prompt. Avoid this
#     when possible -- prefer splitting at natural pauses.
#
# CONTINUOUS FILMS mark a segment that continues the previous scene unbroken with
# "continues_previous": True (see that field below). It is STILL an ordinary shot
# with its own opening image; continuity comes from a LOCKED SHARED FRAME -- the
# continuation's opening still is also copied onto the previous segment as its
# locked last frame, so the two shots meet on the exact same frame.
#
# Fields:
#   id          - segment number (1-based)
#   title       - short title for the segment
#   seconds     - duration in seconds (4-15)
#   style       - key into VISUAL_STYLES
#   characters  - list of character prefixes (keys into CHARACTERS) in this shot
#   location    - (optional) key into LOCATIONS; its reference image is attached
#                 when generating this segment's opening still so the setting
#                 stays consistent
#   location_variant - (optional) variant name of that location (e.g. "night")
#   opening     - description of the opening frame
#   action      - full action/sound description for video generation. If anyone
#                 speaks, the action text itself must state clearly WHO they are
#                 speaking to (e.g. "Jane, facing Tom, says: '...'") -- there is
#                 no separate dialogue-direction section, and without it Seedance
#                 has the character address the camera. For a soliloquy or
#                 voice-over, make that explicit in the action too.
#                 PACING comes from this text plus "seconds": integrate speech
#                 and action IN ORDER so Seedance knows the sequence -- what
#                 happens before a line, whether a line is spoken DURING an
#                 action, and what happens after (e.g. "Jane crosses to the
#                 window, pauses, then says: '...'; she sets the cup down as she
#                 finishes"). Given the target duration and a clear ordering,
#                 Seedance paces the beats itself -- do NOT add a generic
#                 "speak faster" note. See the README's Pacing section.
#   camera      - camera movement description
#   voice_audio - character prefix who SPEAKS in this segment (and whose voice
#                 audio reference to attach), or None if no one speaks. This is
#                 also how generate_voices.py decides who needs a voice
#                 reference: a character who speaks in more than one segment
#                 gets one.
#   voice_audios - (optional) MULTIPLE speakers' voice references for ONE segment,
#                 as an ordered list of character prefixes, e.g.
#                 ["josef", "leni"]. Each is attached as its own @audio1, @audio2,
#                 ... and the prompt tells Seedance which voice belongs to which
#                 character, so a two-hander keeps both voices consistent. Use
#                 this INSTEAD of voice_audio when two characters speak in the same
#                 shot (only keys present in VOICE_REFS are attached). A single
#                 speaker can still just use voice_audio.
#   speakers    - (optional) list of speaking character prefixes when a segment
#                 genuinely needs two (never more than two). Defaults to
#                 [voice_audio] (or voice_audios). Used by generate_voices.py.
#   char_ref_angles - (optional) which character reference angles to attach in
#                 this segment's VIDEO prompt, as {character_prefix: [angles]}.
#                 The frontal face ("front_full_face") is ALWAYS attached even
#                 if you leave it out. Default per character is face +
#                 head-and-shoulders ("three_quarter"). RULE: always use the
#                 face; add a body shot only where it helps; "most scenes use
#                 face + head-and-shoulders OR face + full body," and use all
#                 three only where necessary. If the opening still already shows
#                 the character head-and-shoulders or full-body, do NOT attach
#                 that same framing again -- e.g. for a full-body opening still
#                 use {"jane": ["front_full_face", "three_quarter"]}, or just
#                 ["front_full_face"] when the still already carries the body.
#                 (Only angles actually uploaded into GDRIVE_CHAR_REF_IDS are
#                 attached; missing ones are skipped.)
#   mid_cut_ref - segment ID whose opening still to attach as @image2 when
#                 the segment spans two compositions to avoid interrupting
#                 dialogue (optional, usually omitted)
#   extra_still_refs - list of other segment IDs whose stills to include
#                      as additional reference images (optional)
#   continues_previous - (optional, CONTINUOUS FILMS only) set True to mark this
#                 segment as a seamless continuation of the previous segment with
#                 no cut. It is STILL a normal independent shot with its own
#                 opening still; continuity is created by a LOCKED SHARED FRAME.
#                 generate_images.py copies this segment's opening still onto the
#                 PREVIOUS segment as that segment's locked closing ("zclosing")
#                 frame, and make_movie.py passes it to the previous segment as
#                 last_frame -- so the previous shot ENDS on the exact frame this
#                 one BEGINS on. No video is reused and nothing drifts across a
#                 chain. Generate with `python3 make_movie.py generate continuous`.
#                 A standard film leaves this off; if it is set, the standard cut
#                 ignores it and treats the segment as an ordinary independent shot.
#                 Only flag a segment when its dialogue/action is genuinely
#                 continuous with the previous one. Keep the SAME location (and
#                 normally the same characters) as the segment it continues, so
#                 the shared frame is coherent -- make_movie.py warns on a location
#                 change. Keep one speaker per segment; the continuity comes from
#                 the shared frame, not from packing an exchange into one shot.

SEGMENTS = [
    # Example:
    # {
    #     "id": 1,
    #     "title": "INT. KITCHEN -- MORNING",
    #     "seconds": 12,
    #     "style": "kodachrome",
    #     "characters": ["protagonist"],
    #     "location": "kitchen",
    #     "opening": "Jane stands at the kitchen counter, morning light...",
    #     "action": "Jane pours coffee and looks out the window. She says...",
    #     "camera": "Slow push-in from the doorway toward Jane at the counter.",
    #     "voice_audio": "protagonist",
    #     # Opening still is a waist-up shot, so don't re-attach three_quarter;
    #     # face + full_body covers what the still doesn't:
    #     "char_ref_angles": {"protagonist": ["front_full_face", "full_body"]},
    #     "extra_still_refs": [],
    # },
    # Example of a segment that continues the one above unbroken -- same
    # location, same single speaker, dialogue carrying across (continuous version
    # only; ignored by the standard cut). Its opening still is also copied onto
    # seg 1 as seg 1's locked closing frame, so seg 1 ends exactly where seg 2
    # begins:
    # {
    #     "id": 2,
    #     "title": "INT. KITCHEN -- MORNING (cont'd)",
    #     "seconds": 11,
    #     "style": "kodachrome",
    #     "characters": ["protagonist"],
    #     "location": "kitchen",
    #     "opening": "Jane still at the counter, mid-sentence...",
    #     "action": "Still pouring, she finishes: '...and then I woke up.'",
    #     "camera": "Hold, then a slow drift toward the window.",
    #     "voice_audio": "protagonist",
    #     "continues_previous": True,
    # },
]

# === VOICE / AUDIO REFERENCES ===
# Voice references are created by the voice pipeline (generate_voices.py), the
# last step before generating the movie. It finds every character who speaks in
# more than one segment, makes a 7-second 480p Seedance clip of
# each speaking to camera, saves that throwaway clip to extras/, and exports
# ONLY the audio to audio/<prefix>_voice_reference.mp3. Then run
# `python3 upload_images.py audio` and paste the returned URLs below.
# Key = character prefix or "narrator", value = public URL (or a Drive file ID).

VOICE_REFS = {
    # Example:
    # "narrator": "https://example.com/voice_reference.mp3",
    # "protagonist": "1abc123def456...",
}

# === REFERENCE IMAGE URLs / FILE IDS ===
# Populated after images are generated and uploaded. Values can be either
# Google Drive file IDs (converted to download URLs automatically) or
# direct public URLs (e.g. from upload_images.py / Lunostudio CDN).
# make_movie.py detects URLs vs IDs automatically.

GDRIVE_STILL_IDS = {
    # Maps segment ID (int) -> Google Drive file ID or public URL (str).
    # Example: 1: "https://cdn.example.com/seg01_opening.jpg",
}

# CONTINUOUS version only. Maps a segment ID (int) -> the public URL of its
# locked CLOSING ("zclosing") frame -- a copy of the NEXT segment's opening
# still. Attached as that segment's last_frame so it ends on the exact frame the
# next (continues_previous) shot begins on. Only segments that are FOLLOWED by a
# continuation appear here; upload_images.py fills this in (closings category).
GDRIVE_CLOSING_IDS = {
    # Example: 1: "https://cdn.example.com/seg01_zclosing.jpg",
}

GDRIVE_CHAR_REF_IDS = {
    # Maps "charprefix_angle" -> Google Drive file ID or public URL (str).
    # upload_images.py uploads EVERY generated angle, so you will normally have
    # the face plus a body shot or two per character. Video prompts attach the
    # face always, plus whichever body angle a scene needs (see char_ref_angles).
    # Example:
    #   "protagonist_front_full_face": "https://cdn.example.com/face.jpg",
    #   "protagonist_three_quarter":   "https://cdn.example.com/headshoulders.jpg",
    #   "protagonist_full_body":       "https://cdn.example.com/fullbody.jpg",
}

GDRIVE_LOCATION_IDS = {
    # Maps "location_variant" -> Google Drive file ID or public URL (str).
}
