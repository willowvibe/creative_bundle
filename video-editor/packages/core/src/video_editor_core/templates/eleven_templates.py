"""The fourteen supported video-creation templates.

Each template is a declarative recipe that maps a user request to the right
pipeline, preset, aspect ratio, and provider defaults. Templates do not
render anything themselves; they are consumed by ``TemplateRouter`` to produce
a ``TemplateResult`` the CLI or a future dispatcher can act on.
"""

from __future__ import annotations

from pathlib import Path

from video_editor_core.templates.base import InputSpec, Template
from video_editor_core.templates.storyteller_concepts import build_storyteller_concept
from video_editor_core.templates.tiers import STORYTELLER_TIER_PRESETS


def _default_concept(path: Path) -> str:
    """Return a starter concept body when no build_concept is supplied."""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _image_story_concept(path: Path) -> str:
    """Prompt the user to describe each image/scene in the concept file."""
    return (
        "# Image Story\n\n"
        "Describe each scene below. The engine will generate matching visuals "
        "and animate them with a gentle Ken Burns pan/zoom.\n\n"
        "## Scene 1\n\n"
        "- Visual: A wide shot of ...\n"
        "- Narration: ...\n\n"
        "## Scene 2\n\n"
        "- Visual: A close-up of ...\n"
        "- Narration: ...\n"
    )


def _voice_canvas_concept(path: Path) -> str:
    """Starter concept for voiceover + generated b-roll."""
    return (
        "# Voice Canvas\n\n"
        "Paste or write the script that matches the supplied voiceover audio. "
        "Each paragraph becomes a scene; visuals are generated to support the "
        "narration rather than show a speaker.\n\n"
        "## Scene 1\n\n"
        "Narration: ...\n\n"
        "## Scene 2\n\n"
        "Narration: ...\n"
    )


def _product_spot_concept(path: Path) -> str:
    """Starter concept for a product showcase."""
    return (
        "# Product Spot\n\n"
        "Describe the product, its key benefit, and the audience. The engine "
        "will generate clean product-focused visuals and a concise VO.\n\n"
        "## Product\n\n"
        "Name: ...\n"
        "Benefit: ...\n"
        "Audience: ...\n\n"
        "## Script\n\n"
        "1. Hook: ...\n"
        "2. Problem: ...\n"
        "3. Solution: ...\n"
        "4. CTA: ...\n"
    )


def _social_hook_concept(path: Path) -> str:
    """Starter concept for a short vertical social clip."""
    return (
        "# Social Hook\n\n"
        "Write a 15–60 second hook. Keep sentences punchy so captions pop. "
        "The output is 9:16 vertical with bold captions and music.\n\n"
        "## Hook\n\n"
        "...\n\n"
        "## Body\n\n"
        "...\n\n"
        "## CTA\n\n"
        "...\n"
    )


def _training_module_concept(path: Path) -> str:
    """Starter concept for a step-by-step tutorial."""
    return (
        "# Training Module\n\n"
        "Break the topic into numbered steps. Use the data-viz syntax for "
        "diagrams: `asset_query: data:chart_type=bar;labels=A,B;values=1,2`.\n\n"
        "## Step 1\n\n"
        "- Narration: ...\n"
        "- Visual: ...\n"
        "- Data: ...\n\n"
        "## Step 2\n\n"
        "- Narration: ...\n"
        "- Visual: ...\n"
    )


def _musical_video_concept(path: Path) -> str:
    """Starter concept for a music-driven visual video."""
    return (
        "# Musical Video\n\n"
        "The music file is the spine of the video. Put all images and short "
        "video clips into the media directory; the engine will cycle through "
        "them in directory order, one segment per ``segment_seconds``.\n\n"
        "## Inputs\n\n"
        "- Music: ./song.mp3\n"
        "- Media directory: ./visuals/\n"
        "- Lyrics (optional): ./lyrics.txt\n"
        "- Language (optional): en\n\n"
        "## Notes\n\n"
        "- Still images get a gentle Ken Burns pan/zoom.\n"
        "- Video clips are trimmed to the segment length and play in-line (no extra motion).\n"
        "- Lyrics, if provided, are aligned to the music and burned as captions.\n"
        "- If the built-in render loses audio, use scripts/compose_musical_video.py as a direct FFmpeg fallback.\n"
    )


TEMPLATES: list[Template] = [
    Template(
        name="podium_cut",
        description="Polish a single talking-head video: auto-cut, captions, brand, music.",
        inputs=[
            InputSpec(
                name="video_file",
                description="One video file with a visible speaker and clear audio.",
                example="./talk.mp4",
            ),
            InputSpec(
                name="transcript",
                description="Optional word-level transcript JSON to skip WhisperX.",
                required=False,
                example="./talk.json",
            ),
            InputSpec(
                name="brand.json",
                description="Optional brand override in presets/brands/ or presets/brand.json.",
                required=False,
                example="presets/brand.json",
            ),
        ],
        pipeline_kind="v2_skills",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={},
        scene_type_hints=["talking_head"],
        caveats=[
            "Only one source video is processed; multi-speaker or multi-angle switching is not supported.",
        ],
    ),
    Template(
        name="explainer_reel",
        description="Turn a markdown script into a fully generated explainer video.",
        inputs=[
            InputSpec(
                name="concept.md",
                description="Markdown script with the full narration and topic.",
                example="./concept.md",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "elevenlabs_tts",
            "image": "fal_ai",
            "video": "hyperframes_video",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="upbeat",
        scene_type_hints=["opening", "narration", "broll", "closing"],
        build_concept=_default_concept,
        caveats=[
            "Visuals are generated from prompts; exact scene control requires editing the generated scene_plan.json.",
        ],
    ),
    Template(
        name="image_story",
        description="Generate a Ken Burns montage from a script describing scenes or images.",
        inputs=[
            InputSpec(
                name="concept.md",
                description="Markdown describing each scene/image and its narration.",
                example="./concept.md",
            ),
            InputSpec(
                name="image_paths",
                description="Optional: copy reference images into the work tree and reference them manually.",
                required=False,
                example="projects/job1/assets/image1.jpg",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "elevenlabs_tts",
            "image": "fal_ai",
            "music": "poyo_music",
        },
        default_music_mood="cinematic",
        scene_type_hints=["narration", "broll"],
        build_concept=_image_story_concept,
        caveats=[
            "There is no direct image-upload provider; images are either described and regenerated, or manually referenced.",
            "Scenes default to Ken Burns motion unless props.requires_motion=true.",
        ],
    ),
    Template(
        name="voice_canvas",
        description="Lay generated b-roll under a supplied voiceover audio file.",
        inputs=[
            InputSpec(
                name="voiceover_audio",
                description="Pre-recorded voiceover MP3/WAV.",
                example="./vo.mp3",
            ),
            InputSpec(
                name="concept.md",
                description="Script that matches the voiceover (used for scene timing and captions).",
                example="./concept.md",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "image": "fal_ai",
            "video": "hyperframes_video",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="ambient",
        scene_type_hints=["broll", "narration"],
        build_concept=_voice_canvas_concept,
        caveats=[
            "The supplied audio is used as the scene voiceover; TTS generation is skipped.",
            "Scene durations should match the voiceover segments.",
        ],
    ),
    Template(
        name="product_spot",
        description="Generate a product-focused showcase reel from a description.",
        inputs=[
            InputSpec(
                name="concept.md",
                description="Product description, benefit, audience, and script.",
                example="./concept.md",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="short_explainer",
        default_aspect_ratio="9:16",
        default_providers={
            "tts": "elevenlabs_tts",
            "image": "fal_ai",
            "video": "hyperframes_video",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="upbeat",
        scene_type_hints=["opening", "narration", "closing"],
        build_concept=_product_spot_concept,
        caveats=[
            "No real product footage is captured; visuals are AI-generated from the description.",
            "Use Image Story if you have actual product photos to animate.",
        ],
    ),
    Template(
        name="social_hook",
        description="Create a fast, vertical social clip from a short hook script.",
        inputs=[
            InputSpec(
                name="concept.md",
                description="15–60 second hook script with clear beats.",
                example="./hook.md",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="short_explainer",
        default_aspect_ratio="9:16",
        default_providers={
            "tts": "elevenlabs_tts",
            "image": "fal_ai",
            "video": "hyperframes_video",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="energetic",
        scene_type_hints=["opening", "narration", "closing"],
        build_concept=_social_hook_concept,
        caveats=[
            "Best for 9:16 output. 16:9 is possible by overriding aspect_ratio.",
        ],
    ),
    Template(
        name="training_module",
        description="Turn a step-by-step markdown guide into a tutorial with diagrams.",
        inputs=[
            InputSpec(
                name="concept.md",
                description="Numbered steps, each with narration and optional chart data.",
                example="./tutorial.md",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "elevenlabs_tts",
            "image": "fal_ai",
            "data_viz": "matplotlib_data_viz",
            "music": "poyo_music",
        },
        default_music_mood="calm",
        scene_type_hints=["narration", "broll"],
        build_concept=_training_module_concept,
        caveats=[
            "Diagrams use matplotlib data_viz via asset_query: data:... syntax.",
            "Screen recordings must be captured separately and are not generated.",
        ],
    ),
    Template(
        name="brand_promo",
        description="Render a script with locked-in brand colors, fonts, and logo overlay.",
        inputs=[
            InputSpec(
                name="concept.md",
                description="Script for the promo.",
                example="./concept.md",
            ),
            InputSpec(
                name="brand.json",
                description="Brand file in presets/brand.json or presets/brands/<job>.json.",
                example="presets/brand.json",
            ),
        ],
        pipeline_kind="script_to_video",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "elevenlabs_tts",
            "image": "fal_ai",
            "music": "poyo_music",
        },
        default_music_mood="professional",
        scene_type_hints=["opening", "narration", "closing"],
        build_concept=_default_concept,
        caveats=[
            "Brand compliance depends on presets/brand.json being present and correctly formatted.",
        ],
    ),
    Template(
        name="multi_language_dub",
        description="Re-voice an existing video in a new language with captions.",
        inputs=[
            InputSpec(
                name="video_file",
                description="Original video to dub.",
                example="./source_en.mp4",
            ),
            InputSpec(
                name="translated_script.md",
                description="Translated script matching the original timing/segments.",
                example="./script_hi.md",
            ),
        ],
        pipeline_kind="v2_skills",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "elevenlabs_tts",
        },
        scene_type_hints=["talking_head"],
        caveats=[
            "The current v2 pipeline replaces the full audio track via TTS, not lip-sync.",
            "For precise dubbing, supply a forced-alignment transcript in the target language.",
        ],
    ),
    Template(
        name="raw_batch",
        description="Run the v1 raw-footage pipeline on a folder of clips.",
        inputs=[
            InputSpec(
                name="source_directory",
                description="Folder containing mp4/mov clips.",
                example="./raw_clips/",
            ),
            InputSpec(
                name="transcript",
                description="Optional transcript for the first clip.",
                required=False,
                example="./clip1.json",
            ),
        ],
        pipeline_kind="v1_raw",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={},
        scene_type_hints=["raw_footage"],
        caveats=[
            "v1 transcribes, analyzes, and cuts only the first discovered clip. "
            "Clips 2..N are discovered but ignored. Concatenate clips first if you need them all.",
        ],
    ),
    Template(
        name="musical_video",
        description="Cut user images and short clips to a music track with simple motion and optional lyric captions.",
        inputs=[
            InputSpec(
                name="music_file",
                description="The music/audio track that drives the video.",
                example="./song.mp3",
            ),
            InputSpec(
                name="media_dir",
                description="Folder containing JPG/PNG/MP4/MOV assets.",
                example="./visuals/",
            ),
            InputSpec(
                name="lyrics",
                description="Optional text file with lyrics for forced-alignment captions.",
                required=False,
                example="./lyrics.txt",
            ),
            InputSpec(
                name="language",
                description="Optional language code for the lyrics output filename.",
                required=False,
                example="en",
            ),
        ],
        pipeline_kind="music_video",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={"local_media": "local_media"},
        scene_type_hints=["music_visual"],
        build_concept=_musical_video_concept,
        caveats=[
            "User media is cycled in directory order; no AI generation is performed.",
            "Videos longer than a segment are trimmed; shorter videos freeze on their last frame.",
            "Beat detection is not yet implemented; use --segment-seconds to control cut timing.",
            "Headless renders may emit a silent AAC track; if the output has no audio, use scripts/compose_musical_video.py as a deterministic FFmpeg fallback.",
        ],
    ),
    Template(
        name="storyteller_budget",
        description="Lowest-cost storyteller preset: budget image (flux-schnell) and budget video (wan2.2-image-to-video-fast, gentle parallax). LLM picks ~50% of scenes for motion.",
        inputs=[
            InputSpec(
                name="script.md",
                description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.",
                example="./story.md",
            ),
        ],
        pipeline_kind="storyteller",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "tts_router",
            "image": "poyo_ai",
            "video": "poyo_ai",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="cinematic",
        scene_type_hints=["story"],
        build_concept=build_storyteller_concept,
        quality_tier_pair=STORYTELLER_TIER_PRESETS["storyteller_budget"],
        caveats=[
            "LLM picks motion scenes; the choice is deterministic per (text, speaker) but not user-tunable. Use `> motion: true|false` to override per scene.",
            "Budget image model `flux-schnell` is faster but lower fidelity than HD's `gpt-image-2`.",
            "Budget motion model `wan2.2-image-to-video-fast` is the cheapest PoYo video model ($0.030/gen at 480p); it adds gentle motion/parallax to a still image, not action shots.",
            "Override tiers per-run: `ve run story.md --template storyteller_budget --image-tier standard --video-tier premium`.",
        ],
    ),
    Template(
        name="storyteller_hd",
        description="Standard quality storyteller preset: gpt-image-2 1K low + seedance-2-fast 720p. User marks `> motion: true` to make a scene animate.",
        inputs=[
            InputSpec(
                name="script.md",
                description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.",
                example="./story.md",
            ),
        ],
        pipeline_kind="storyteller",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "tts_router",
            "image": "poyo_ai",
            "video": "poyo_ai",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="cinematic",
        scene_type_hints=["story"],
        build_concept=build_storyteller_concept,
        quality_tier_pair=STORYTELLER_TIER_PRESETS["storyteller_hd"],
        caveats=[
            "Multi-voice multi-language: per-speaker voice_id + language; ElevenLabs v4 is the default voice-over; a sarvam:/kokoro: prefix picks that provider instead.",
            "Motion scenes require the `> motion: true` directive per scene; the default is still image with Ken Burns.",
            "Uses the existing FableCut bridge for timeline preview; after browser edits, run `ve render-fablecut <job>` for the final cut.",
            "Override tiers per-run: `ve run story.md --template storyteller_hd --video-tier premium`.",
        ],
    ),
    Template(
        name="storyteller_premium",
        description="Cinematic storyteller preset: gpt-image-2 2K + seedance-2 1080p. Highest cost; best for hero/story-finale reels.",
        inputs=[
            InputSpec(
                name="script.md",
                description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.",
                example="./story.md",
            ),
        ],
        pipeline_kind="storyteller",
        default_preset="long_form",
        default_aspect_ratio="16:9",
        default_providers={
            "tts": "tts_router",
            "image": "poyo_ai",
            "video": "poyo_ai",
            "music": "poyo_music",
            "sfx": "elevenlabs_sfx",
        },
        default_music_mood="cinematic",
        scene_type_hints=["story"],
        build_concept=build_storyteller_concept,
        quality_tier_pair=STORYTELLER_TIER_PRESETS["storyteller_premium"],
        caveats=[
            "Same as `storyteller_hd`, but every motion scene uses `seedance-2` at 1080p (~10x the cost of budget).",
            "Use for hero reels, brand films, and final-cut output — not for prototyping.",
            "Override tiers per-run to drop cost temporarily: `ve run story.md --template storyteller_premium --image-tier standard`.",
        ],
    ),
]

TEMPLATE_NAMES: list[str] = [t.name for t in TEMPLATES]
TEMPLATE_MAP: dict[str, Template] = {t.name: t for t in TEMPLATES}
