# Vlogger + Tech YouTuber Pipeline Design

> Research-driven architecture for making the engine versatile for two creator profiles:
> 1. **Lifestyle / cooking / event vlogger** — raw footage, talking-head + B-roll, voice clone, SFX, transitions, animations.
> 2. **Tech YouTuber / educator** — screen recordings, podcasts, explainer videos, supplied clips/script/context, effects, transitions, animations.

> **Note (2026-10-16):** the renderer in this design was later changed to
> **HyperFrames (HTML) + single-pass FFmpeg**; Remotion is retired. The OpenRouter
> generation provider (image/video/music) and the TypeSafe/JEV classifier are now
> available too (see `docs/OPENROUTER_GENERATION.md`, `docs/JEV_CLASSIFIER.md`).

**Date:** 2026-08-07  
**Sources:** See inline links and the Sources section at the end.

---

## 1. What each creator actually needs

### 1.1 Vlogger (lifestyle / cooking / events)

| Phase | Pain point | Desired automation |
|-------|-----------|-------------------|
| **Ingest** | Clips from phone/camera + voice memos, variable formats | Proxy generation, scene classification, duplicate detection |
| **Transcribe** | Lots of ambient noise, rambling, false starts | Accurate WhisperX, speaker diarization, filler/silence removal |
| **Story structure** | Hard to find the narrative in hours of footage | LLM selects keepers, builds hook + chapters |
| **A-roll** | Talking-head segments need tightening | Auto rough-cut, face-aware reframe, punch-in zoom |
| **B-roll** | Must cover 40–60% of talking-head footage [^1] | Auto B-roll search/generation from spoken keywords + stock providers |
| **Voice** | Wants consistent narrator voice, sometimes clone own voice | ElevenLabs voice clone, multi-speaker dubbing, speech-to-speech |
| **Sound design** | Footage feels flat without SFX | Context-aware SFX placement (sizzle, crowd, whoosh, UI sounds) |
| **Music** | Background music that matches mood | Mood/genre matching + beat-aware ducking |
| **Captions** | Mobile viewers need word-synced captions | Animated, brand-styled, safe-zone aware |
| **Transitions** | Jump cuts between clips need smoothing | Auto cross-fade / motion transitions based on content |
| **Export** | Multiple platforms (YouTube, Reels, Shorts) | Multi-aspect render from same edit |

### 1.2 Tech YouTuber (tutorials / podcasts / explainers)

| Phase | Pain point | Desired automation |
|-------|-----------|-------------------|
| **Ingest** | Screen recordings + webcam + separate mic tracks | Multi-track sync, track assignment |
| **Transcribe** | Technical terms, code, long explanations | Accurate ASR with custom glossary |
| **Script alignment** | Provided script vs. spoken recording | Forced alignment / script-to-audio sync |
| **Visual polish** | Boring static screen for minutes | Auto zoom to cursor/clicks, keystroke overlays, callouts, highlights |
| **Podcast** | Long-form audio/video, multiple speakers | Chapter generation, multi-cam switch, topic segmentation |
| **Explainers** | Needs diagrams, code snippets, motion graphics | Auto diagram generation, code highlights, HTML/GSAP motion graphics |
| **B-roll** | Screen-only footage needs variety | Stock/code-record snippets, face cam inserts |
| **SFX** | UI clicks, notification sounds, emphasis | Timed SFX from script keywords |
| **Music** | Bed music that doesn't fight narration | Sidechain ducking, intro/outro music |
| **Captions** | Code/technical captions need accuracy | Word-level captions, code formatting, chapter markers |
| **Export** | YouTube long-form + Shorts from same content | Auto 9:16/1:1 reframing, chapter metadata |

---

## 2. Existing engine capabilities (baseline)

The engine already covers a surprising amount:

| Capability | Location | Maturity |
|-------------|----------|----------|
| Raw-footage rough cut (silence/filler removal) | `skills/rough_cut` | ✅ Strong |
| Word-level animated captions | `skills/captions` | ✅ Strong |
| Background music + ducking | `skills/music` | ✅ Strong |
| Graphics composition + brand presets | `skills/graphics` | ✅ Strong |
| Multi-skill export | `skills/export` | ✅ Strong |
| Transcription (WhisperX) | `apps/transcribe`, `video_editor_tools.whisperx_wrapper` | ✅ Strong |
| FFmpeg wrapper | `packages/ffmpeg` | ✅ Strong |
| EDL / timeline | `packages/core.timeline` | ✅ Strong |
| Asset ingestion (TTS, image, video, music, SFX) | `packages/asset_ingestion` | ✅ Strong |
| ElevenLabs TTS provider | `packages/asset_ingestion/providers/tts/elevenlabs_tts.py` | ✅ Strong |
| FAL image/video, Poyo, OpenAI TTS, Sarvam, etc. | `packages/asset_ingestion/providers` | ✅ Strong |
| Scene planning | `apps/engine.planner` | ✅ Strong |
| Templates (YAML manifests) | `templates/*.yaml` | ✅ Good |
| Presets (brand/layout) | `presets/*.json` | ✅ Good |
| Cost estimation gate | `ve estimate` + `PricingRegistry` | ✅ New |
| FableCut preview | `ve preview` | ✅ New |
| MCP server | `packages/mcp_server` | ⚠️ Type/runtime issues (audit finding C1) |

What is **missing or only partial**:

| Missing capability | Why it matters |
|---------------------|----------------|
| **Voice cloning** | ElevenLabs provider only does TTS with pre-known voice IDs. No IVC / voice clone from samples. |
| **Automatic B-roll insertion** | No semantic matching of spoken content to stock/generative clips. |
| **Face-aware reframe / zoom** | No MediaPipe/OpenCV face tracking for talking-head punch-ins. |
| **Screen-recording annotations** | No cursor tracking, click effects, keystroke overlays, zoom-to-cursor. |
| **Transition library** | Only basic concat transitions; no GL transitions, motion transitions, beat-synced cuts. |
| **SFX placement** | SFX provider exists but no automatic timing/placement from transcript/script. |
| **Audio cleanup** | No Demucs/RNNoise denoising / voice isolation. |
| **Beat detection** | No librosa beat/onset analysis for music-driven edits. |
| **Chapter generation** | No LLM/vision-based chapter markers for YouTube. |
| **Multi-speaker dubbing** | No diarization + per-speaker voice clone pipeline. |
| **Podcast-specific pipeline** | Long-form audio/video lacks chaptering, multi-cam, topic segmentation. |
| **Tech tutorial motion graphics** | No HTML/GSAP diagram/code snippet composition. |

---

## 3. Proposed new skills and providers

### 3.1 New skills

| Skill | Purpose | Fits |
|-------|---------|------|
| `voice_clone_skill` | Clone a voice from samples; manage voice IDs per project/persona | Both (vlogger clone, tech narrator consistency) |
| `broll_skill` | Find/generate B-roll from spoken keywords and insert into EDL | Vlogger primary, tech secondary |
| `reframe_skill` | Face tracking / auto reframe 16:9 ↔ 9:16 / punch-in zoom | Vlogger primary, tech Shorts |
| `screen_tutorial_skill` | Cursor tracking, click highlights, zoom, keystroke overlays | Tech primary |
| `transition_skill` | Auto transition selection + application (xfade, wipe, GL) | Both |
| `sfx_placement_skill` | Context-aware SFX timing from transcript/script | Both |
| `audio_cleanup_skill` | Demucs source separation + RNNoise denoising | Both (especially podcast/outdoor vlog) |
| `beat_sync_skill` | Beat/onset detection + beat-synced cuts | Music-driven vlogs, intro montages |
| `chapter_skill` | Generate YouTube chapter markers from content | Tech long-form, podcasts |
| `dubbing_skill` | Multi-speaker diarization + per-speaker voice clone dub | Vlogger multi-language, tech courses |
| `motion_graphics_skill` | HTML/GSAP/Remotion composition for diagrams/callouts | Tech primary |
| `thumbnail_skill` | Generate thumbnail candidates from final frame/key moments | Both |

### 3.2 New providers

| Provider | Role |
|----------|------|
| `pexels_provider` / `pixabay_provider` / `coverr_provider` / `mixkit_provider` | Free stock B-roll clips and images |
| `elevenlabs_clone_provider` | Instant Voice Cloning via ElevenLabs IVC API |
| `elevenlabs_dubbing_provider` | End-to-end dubbing API for multi-language/multi-speaker |
| `freesound_provider` | Free SFX library (context + keyword search) |
| `ace_step_provider` / `minimax_music_provider` | Free/cheap music generation |
| `mmaudio_provider` | Video-to-audio foley/SFX generation |
| `local_demucs_provider` | Source separation as an "asset" provider |
| `local_rnnnoise_provider` | Denoising as an "asset" provider |

### 3.3 New templates

| Template | pipeline_kind | Use case |
|----------|---------------|----------|
| `vlog_reel` | `raw_to_video` | Lifestyle/cooking/event vlog from raw clips |
| `talking_head_polish` | `raw_to_video` | Single talking-head take → polished YouTube video |
| `tech_tutorial` | `screen_to_video` | Screen recording + webcam + script → tutorial |
| `podcast_video` | `podcast_to_video` | Long-form podcast/video with chapters + multicam |
| `explainer_motion` | `script_to_video` | Script + supplied clips → motion-graphic explainer |
| `shorts_factory` | `shorts_from_long` | Auto generate Shorts/Reels from long-form video |

### 3.4 New presets

| Preset | Description |
|--------|-------------|
| `vlogger_youtube` | 16:9 talking-head + lower-thirds + animated captions |
| `vlogger_shorts` | 9:16 face-aware reframe + kinetic captions + punch-in zoom |
| `cooking_show` | Warm grade, ingredient callouts, sizzle SFX, top-down B-roll inserts |
| `tech_tutorial` | Screen-first layout, keystroke overlay, cursor zoom, code captions |
| `podcast_studio` | Split-screen or speaker focus, chapter markers, waveform visualizer |
| `event_recap` | Beat-synced montage, quick cuts, crowd/atmosphere SFX |

---

## 4. Integration architecture

### 4.1 How new skills fit the existing pipeline

The existing `SkillsPipeline.run_async` chains:

```
rough_cut → graphics → captions → music → broll? → export
```

Proposed chain for vlog/tech workflows:

```
ingest → audio_cleanup → rough_cut → reframe → broll → transition → sfx_placement
       → graphics (lower thirds, diagrams) → captions → music → review → export
chapter_skill runs in parallel/after rough_cut
thumbnail_skill runs after export
```

For screen tutorials:

```
ingest (screen + webcam + mic) → sync tracks → rough_cut → screen_tutorial
       → graphics (callouts) → captions → sfx_placement → music → export
```

### 4.2 Skill protocol

The engine already has `SkillProtocol`. Each new skill should implement:

```python
class SomeSkill:
    @property
    def name(self) -> str: ...
    def run(self, ctx: JobContext, **kwargs: Any) -> dict[str, Any]: ...
```

Skills should read/write artifacts under `ctx.work_dir / "<skill_name>"` and record operations to the manifest.

### 4.3 Data flow for B-roll insertion

```
1. Transcript (word-level) + scene EDL
2. LLM extracts "B-roll moments" — timestamps where a noun/verb should be visualized
3. For each moment, generate a search query or generative prompt
4. Provider search: Pexels → Pixabay → Coverr → Mixkit → generative fallback
5. Rank candidates by relevance (CLIP/semantic similarity or LLM judge)
6. Download + probe duration + crop/aspect
7. Insert into EDL as B-roll segments, optionally with L-cut/J-cut
8. Render via GraphicsSkill or a dedicated B-roll compositor
```

### 4.4 Data flow for voice clone dubbing

```
1. User supplies 1–3 minutes of clean voice samples (or extracts from existing clip)
2. voice_clone_skill calls ElevenLabs IVC → stores voice_id in project state
3. Script or transcript is rewritten/re-recorded via cloned voice (TTS provider with voice_id)
4. Optional: forced alignment to match original timing
5. Optional: speech-to-speech to match performance (excitement, whisper)
6. Mix into timeline
```

### 4.5 Data flow for screen tutorial

```
1. Ingest screen recording + webcam + separate audio
2. Transcribe and align to script (if supplied)
3. Detect cursor position per frame (OpenCV or use recording metadata)
4. Detect click events from audio spike + cursor dwell
5. LLM/Heuristic decides zoom regions and callout moments
6. Render: zoom crop + highlight ring + click animation + keystroke overlay
7. Mix with captions + SFX + music
```

### 4.6 Data flow for transitions

```
1. Analyze adjacent clip content + audio energy
2. LLM/rule selects transition type:
   - Cut (speech, fast pace)
   - Crossfade (calm, B-roll)
   - Whip pan / zoom (action, beat)
   - GL transition (motion graphics scene change)
3. `packages/ffmpeg/transitions.py` extended to support new transition types
4. Apply with overlap timing computed from audio onsets
```

---

## 5. Open-source libraries and tools to integrate

### 5.1 Face / body tracking

| Tool | Use |
|------|-----|
| [MediaPipe](https://developers.google.com/mediapipe) | Face detection/landmarks for reframe and zoom |
| [OpenCV](https://opencv.org/) | General computer vision, cursor tracking, motion detection |
| [ReelForge](https://github.com/akira231097/reelforge) | Reference talking-head auto-reframe pipeline (MIT) |
| [OpenSource Clipping](https://github.com/naufalrizqullah/opensource-clipping) | Reference short-form factory with face tracking (MIT) |
| [ffmpeg-mcp-video-editor](https://github.com/AbyAbyss/ffmpeg-mcp-video-editor) | MCP tools for `track_and_crop`, face blur (MIT) |

### 5.2 Screen recording / tutorial polish

| Tool | Use |
|------|-----|
| [Trace](https://github.com/drewsephski/trace) | Open-source Screen Studio alternative (MIT) |
| [Reframed](https://github.com/jkuri/reframed) | macOS screen recorder + editor with cursor zoom (MIT) |
| [OpenScreen](https://github.com/EtienneLescot/openscreen) | Cross-platform screen recorder/editor (MIT) |
| OpenCV template matching / color detection | Cursor tracking from existing screen recordings |

### 5.3 B-roll / stock providers

| Tool | Use |
|------|-----|
| [stockmedia-sdk](https://github.com/difyz9/stockmedia_sdk) | Unified Pexels + Pixabay client (MIT) |
| [pexels-sdk](https://github.com/Stink-Po/pexels-python) | Pydantic-validated Pexels client (MIT) |
| [VidFetch](https://github.com/heatingma/VidFetch) | Bulk downloader for Pexels/Coverr/Mixkit/Pixabay |
| [B-Roll Finder](https://github.com/erfsalehi/B-Roll-Finder) | Reference full B-roll pipeline + Premiere export |
| [Smart B-Roll Inserter](https://github.com/Pankajnegi06/Smart-B-Roll-Inserter) | Reference semantic B-roll matching |

### 5.4 Transitions

| Tool | Use |
|------|-----|
| [cut-fx](https://github.com/tomastimelock/cut-fx) | 559 transitions, Python API, FFmpeg/GL backends (MIT) |
| [ffmpeg-gl-transition](https://github.com/transitive-bullshit/ffmpeg-gl-transition/) | Native FFmpeg GL transitions (MIT) |
| [gl-transitions.com](https://gl-transitions.com/) | GLSL transition shaders |

### 5.5 Audio cleanup

| Tool | Use |
|------|-----|
| [Demucs](https://github.com/adefossez/demucs) | Source separation (vocals/drums/bass/other) (MIT) |
| [pyrnnoise](https://pypi.org/project/pyrnnoise/) | Python bindings for RNNoise speech denoising (Apache-2.0) |
| [dinscribe](https://pypi.org/project/dinscribe/) | Denoise → VAD → Whisper pipeline (MIT) |

### 5.6 Voice clone / dubbing

| Tool | Use |
|------|-----|
| [elevenlabs-python](https://github.com/elevenlabs/elevenlabs-python) | Official SDK with IVC + dubbing endpoints |
| [syncr](https://github.com/mohammed-alsalhi/syncr) | Reference multi-speaker dubbing pipeline |
| [YouTube Auto-Dub](https://github.com/mazzasaverio/youtube-auto-dub/) | Offline multi-speaker dubbing (MIT) |
| [pyVideoTrans](https://github.com/PeiJingbobo/pyvideotrans) | GUI/CLI translation + voice clone (GPL) |

### 5.7 SFX placement

| Tool | Use |
|------|-----|
| [foley](https://github.com/thorwhalen/foley) | Retrieval-first SFX selector (MIT) |
| [soundeffects-claude-code](https://github.com/manthanpatelll/soundeffects-claude-code) | Reference timed SFX track generation (MIT) |
| [ScriptSmith-Engine](https://github.com/TheUnknown550/ScriptSmith-Engine) | Reference script → SFX timing pipeline (MIT) |
| [Freesound API](https://freesound.org/docs/api/) | Free SFX library |

### 5.8 Beat detection / music-driven editing

| Tool | Use |
|------|-----|
| [librosa](https://librosa.org/) | Beat/onset/RMS analysis |
| [beatsync-engine](https://github.com/Antiarin/beatsync-engine) | Config-driven beat-synced editor (MIT) |
| [Bounce](https://github.com/Atomic-Germ/Bounce) | Beat-synchronized music video creator (MIT) |
| [beat-synced-edit](https://github.com/ZiadAbdelkarim/beat-synced-edit) | Claude Code skill for beat edits (MIT) |

### 5.9 Motion graphics / HTML composition

| Tool | Use |
|------|-----|
| [Remotion](https://github.com/remotion-dev/remotion) | React → MP4 (source-available) |
| [HyperFrames](https://github.com/heygen-com/hyperframes) | HTML/GSAP → MP4, Apache-2.0, agent-friendly |
| [Hyperframes Helper](https://github.com/robonuggets/hyperframes-helper) | Skill kit for HTML/GSAP videos (CC BY) |
| [react-bits-video](https://github.com/GordenSun/react-bits-video) | Single-HTML motion video (MIT) |
| [GSAP](https://greensock.com/gsap/) | Industry-standard web animation library |
| [Anime.js](https://animejs.com/) / [Mo.js](https://mojs.github.io/) | Lightweight web animations |

### 5.10 Chapter generation

| Tool | Use |
|------|-----|
| [youtube-timestamps](https://github.com/cmod/youtube-timestamps) | Whisper + GPT-4o chapter generator (MIT) |
| [video-to-chapters-with-transcript](https://pypi.org/project/video-to-chapters-with-transcript/) | Scene detection + Gemini chapters (MIT) |
| [Video-Chapter-Generator](https://github.com/its-rath/Video-Chapter-Generator) | End-to-end chapter pipeline with FastAPI |

### 5.11 End-to-end reference pipelines

| Tool | What to learn from |
|------|-------------------|
| [OpenMontage](https://github.com/calesthio/OpenMontage) | 12 pipelines, 100+ tools, agentic orchestration (AGPL) |
| [OpenCut-AI](https://github.com/Ekaanth/OpenCut-AI) | CapCut/Descript-like self-hosted editor (MIT) |
| [VANTA](https://github.com/itsjwill/vanta) | Batch vlog/reel engine with voice clone + WebGL transitions (MIT) |
| [Montai](https://github.com/jysperm/Montai) | Chat-based vlog storytelling (npm) |
| [X-CUT](https://github.com/MeiGen-AI/X-Cut) | Chat-driven editing agent + Remotion (AGPL) |
| [claude-youtube-editor](https://github.com/hassancs91/claude-youtube-editor) | 6-step talking-head → YouTube pipeline |
| [chrislema/videoeditor](https://github.com/chrislema/videoeditor) | 8-step Claude Code skills pipeline for talking-head |
| [video-edit-cli](https://github.com/computerlovetech/video-edit-cli) | Podcast/Shorts CLI with provenance sidecars |

---

## 6. Implementation roadmap

### Phase 1 — Foundation (2–3 weeks)

1. **Fix critical gaps in current engine**
   - MCP server type/runtime errors (audit C1)
   - `test_config_yaml_models.py` OpenRouter mismatch (audit C2)
   - `.gitignore` runtime dirs (audit C3)
   - `ve update-pricing` real fetch or rename (audit C4)
   - `estimate.py` video cost bug + hardcoded voice IDs (audit C5/C7)
   - `handlers.py:715` broken `storyteller_to_fablecut` call (audit C6)

2. **Add provider layer for free stock + voice clone**
   - `PexelsProvider`, `PixabayProvider`, `CoverrProvider`, `MixkitProvider`
   - `ElevenLabsCloneProvider` for IVC
   - Update `pricing.yaml` with stock and clone costs

3. **Audio cleanup skill**
   - Demucs source separation wrapper
   - RNNoise denoising wrapper
   - Integrate into rough-cut pipeline as optional pre-stage

### Phase 2 — Vlogger pipeline (2–3 weeks)

4. **B-roll skill**
   - Extract B-roll moments from transcript via LLM
   - Search stock providers; fallback to generative image/video
   - Semantic ranking + confidence threshold
   - Insert into EDL with J-cut/L-cut support

5. **Reframe skill**
   - MediaPipe face detection + smoothing
   - Auto 9:16 reframe from 16:9 talking-head
   - Optional punch-in zoom on emphasis words

6. **SFX placement skill**
   - Keyword → SFX mapping from transcript/script
   - Freesound search + ElevenLabs SFX generation
   - Mix via `packages/ffmpeg.broll.mix_sfx_into_video`

7. **Transition skill**
   - Extend `packages/ffmpeg/transitions.py` with GL transitions via `cut-fx`
   - Rule/LLM transition selection

8. **Templates + presets**
   - `vlog_reel.yaml`, `talking_head_polish.yaml`
   - `vlogger_youtube.json`, `vlogger_shorts.json`, `cooking_show.json`, `event_recap.json`

### Phase 3 — Tech YouTuber pipeline (2–3 weeks)

9. **Screen tutorial skill**
   - Cursor tracking from screen recordings (OpenCV / metadata)
   - Click detection + highlight ring animation
   - Keystroke overlay rendering
   - Zoom-to-cursor with easing

10. **Motion graphics skill**
    - HTML/GSAP composition renderer via HyperFrames
    - Templates for code highlights, diagrams, lower-thirds
    - Integrate with GraphicsSkill as a segment type

11. **Podcast video pipeline**
    - Multi-track sync + speaker diarization
    - Chapter generation skill
    - Multi-cam / speaker focus switching

12. **Templates + presets**
    - `tech_tutorial.yaml`, `podcast_video.yaml`, `explainer_motion.yaml`
    - `tech_tutorial.json`, `podcast_studio.json`

### Phase 4 — Polish + scale (2–3 weeks)

13. **Dubbing skill**
    - Multi-speaker diarization + per-speaker voice clone
    - Language translation + re-dubbing

14. **Shorts factory**
    - Auto extract viral moments from long-form
    - Auto 9:16 reframe + kinetic captions

15. **Thumbnail skill**
    - Extract key frames, generate thumbnail candidates

16. **Beat sync skill**
    - librosa beat detection for music-driven montages

17. **MCP tools for new skills**
    - Expose every new skill as an MCP tool
    - Add integration tests

---

## 7. Suggested new package layout

```
skills/
  rough_cut/
  graphics/
  captions/
  music/
  export/
  broll/              # NEW
  reframe/            # NEW
  screen_tutorial/    # NEW
  transitions/        # NEW (or extend graphics)
  sfx_placement/      # NEW
  audio_cleanup/      # NEW
  beat_sync/          # NEW
  chapters/           # NEW
  dubbing/            # NEW
  motion_graphics/    # NEW
  thumbnail/          # NEW

packages/asset_ingestion/providers/
  visual/
    pexels.py         # NEW
    pixabay.py        # NEW
    coverr.py         # NEW
    mixkit.py         # NEW
  tts/
    elevenlabs_clone.py  # NEW
    elevenlabs_dubbing.py # NEW
  sfx/
    freesound.py      # NEW
  audio/
    demucs.py         # NEW
    rnnnoise.py       # NEW

templates/
  vlog_reel.yaml      # NEW
  talking_head_polish.yaml  # NEW
  tech_tutorial.yaml  # NEW
  podcast_video.yaml  # NEW
  explainer_motion.yaml # NEW
  shorts_factory.yaml # NEW

presets/
  vlogger_youtube.json    # NEW
  vlogger_shorts.json     # NEW
  cooking_show.json       # NEW
  event_recap.json        # NEW
  tech_tutorial.json      # NEW
  podcast_studio.json     # NEW
```

---

## 8. Cost and provider considerations

| Feature | Recommended provider | Cost profile |
|---------|---------------------|--------------|
| Voice clone / TTS | ElevenLabs IVC + turbo | $0.01–$0.05 per 1k chars |
| B-roll stock | Pexels / Pixabay / Coverr / Mixkit | Free with API key |
| Generative B-roll | Poyo Seedance / FAL Kling / OpenRouter | $0.02–$0.05 per second |
| SFX | Freesound (free) + ElevenLabs SFX | Free / $0.02 per second |
| Music | ACE-Step (free local) / ElevenLabs Music / PoYo | Free / $0.01–$0.03 per second |
| Transcription | WhisperX local (free) / ElevenLabs forced alignment | Free / API cost |
| Face reframe | Local MediaPipe/OpenCV | Free |
| Screen tutorial | Local OpenCV | Free |
| Motion graphics | HyperFrames local render (free) / Remotion | Free / license |
| Audio cleanup | Demucs local (free) / RNNoise | Free |
| LLM orchestration | OpenRouter / Ollama Cloud Pro | Variable |

For a **free/local-first tier**, route everything through:
- WhisperX transcription
- Kokoro / KittenTTS / MOSS-TTS for TTS
- Pexels/Coverr/Mixkit for stock
- ACE-Step for music
- Demucs + RNNoise for cleanup
- MediaPipe/OpenCV for reframe
- HyperFrames local for motion graphics

---

## 9. Risks and open questions

| Risk | Mitigation |
|------|-----------|
| Screen cursor tracking from arbitrary screen recordings is hard | Start with recordings that embed cursor metadata (OBS, some tools); fallback to OpenCV template/color tracking |
| B-roll relevance is subjective | LLM judge + user review loop in FableCut preview; confidence threshold to skip low-relevance inserts |
| GL transitions need GPU or complex FFmpeg build | Use `cut-fx` CPU fallback; keep simple xfade/wipe as default |
| Voice clone quality varies with sample quality | Require 1–3 min clean samples; provide preview before full render |
| Multi-speaker diarization accuracy | Use pyannote.audio + manual correction in preview |
| Legal rights on stock footage | Always respect provider licenses; prefer generative for commercial use where unclear |
| MCP server currently broken | Fix before exposing new skills as MCP tools |

---

## 10. Recommended next step

The highest-leverage first implementation is the **vlogger talking-head pipeline**:

1. Add `PexelsProvider` + `PixabayProvider`
2. Build `broll_skill` with LLM moment extraction
3. Build `reframe_skill` with MediaPipe face tracking
4. Add `vlog_reel` template + `vlogger_youtube` preset
5. Wire into `SkillsPipeline` behind a `--template vlog_reel` flag

This gives a concrete end-to-end improvement for the first client profile while reusing the engine's strongest existing pieces (rough cut, captions, music, export).

---

## Sources

- [Increditors: Video Editing Workflow for YouTube Channels 2026](https://increditors.com/video-editing-workflow-for-youtube-channels-2026/)
- [VibeRoll: Talking-Head Video Editing Checklist 2026](https://viberoll.questera.ai/learn/talking-head-video-editing-checklist/)
- [Spotlight FX: Vlog Editing Workflow for Beginners 2026](https://spotlightfx.com/blog/vlog-editing-workflows)
- [Satura AI: What Video Editing Workflow Do YouTubers Use](https://saturaai.com/blog/what-do-youtubers-use-to-edit-videos)
- [VideoToBlog: YouTube Editing Workflow — Faster Cuts With Presets, Proxies & Non-Destructive Fixes](https://videotoblog.ai/blog/how-to-edit-faster-and-more-efficient-youtube-videos-practical-workflow-for-creators)
- [AutoZoom: How YouTubers Record Perfect Screen Tutorials](https://autozoom.app/articles/how-youtubers-record-tutorials)
- [Screenify Studio: How to Create Software Tutorials That Don't Suck](https://www.screenify.studio/blog/2026-04-20-software-tutorials-that-dont-suck)
- [Vidocu: Automatic Zoom-Ins & Callouts for Tutorial Videos 2026](https://vidocu.ai/blog/automatic-zoom-ins-and-callouts-for-tutorial-videos-how-to-add-them-without-manual-editing)
- [ngram: How to Make a Software Tutorial Video 2026](https://www.ngram.com/blog/how-to-make-a-software-tutorial-video)
- [OpenCut-AI](https://github.com/Ekaanth/OpenCut-AI)
- [VANTA](https://github.com/itsjwill/vanta)
- [Montai](https://github.com/jysperm/Montai)
- [ReelForge](https://github.com/akira231097/reelforge)
- [X-CUT](https://github.com/MeiGen-AI/X-Cut)
- [OpenMontage](https://github.com/calesthio/OpenMontage)
- [video-edit-cli](https://github.com/computerlovetech/video-edit-cli)
- [claude-youtube-editor](https://github.com/hassancs91/claude-youtube-editor)
- [chrislema/videoeditor](https://github.com/chrislema/videoeditor)
- [B-Roll Finder](https://github.com/erfsalehi/B-Roll-Finder)
- [Smart B-Roll Inserter](https://github.com/Pankajnegi06/Smart-B-Roll-Inserter)
- [ClipSync](https://github.com/himax12/ClipSync)
- [cut-fx](https://github.com/tomastimelock/cut-fx)
- [ffmpeg-gl-transition](https://github.com/transitive-bullshit/ffmpeg-gl-transition/)
- [gl-transitions.com](https://gl-transitions.com/)
- [Demucs](https://github.com/adefossez/demucs)
- [pyrnnoise](https://pypi.org/project/pyrnnoise/)
- [dinscribe](https://pypi.org/project/dinscribe/)
- [elevenlabs-python](https://github.com/elevenlabs/elevenlabs-python)
- [syncr](https://github.com/mohammed-alsalhi/syncr)
- [YouTube Auto-Dub](https://github.com/mazzasaverio/youtube-auto-dub/)
- [pyVideoTrans](https://github.com/PeiJingbobo/pyvideotrans)
- [Voxa](https://github.com/akshinmrv/Voxa)
- [Python Autodub](https://github.com/Daniel-McLarty/Python-Autodub)
- [foley](https://github.com/thorwhalen/foley)
- [soundeffects-claude-code](https://github.com/manthanpatelll/soundeffects-claude-code)
- [ScriptSmith-Engine](https://github.com/TheUnknown550/ScriptSmith-Engine)
- [AudioSFX](https://github.com/xyaksicxdaenlae437/AudioSFX)
- [Resound](https://github.com/sanity-labs/resound)
- [beatsync-engine](https://github.com/Antiarin/beatsync-engine)
- [Bounce](https://github.com/Atomic-Germ/Bounce)
- [beat-synced-edit](https://github.com/ZiadAbdelkarim/beat-synced-edit)
- [StemSync Video Editor](https://github.com/vrgamegirl19/StemSyncVideoEditor)
- [aeon-music-video](https://github.com/AEON-7/aeon-music-video)
- [Remotion](https://github.com/remotion-dev/remotion)
- [HyperFrames](https://github.com/heygen-com/hyperframes)
- [Hyperframes Helper](https://github.com/robonuggets/hyperframes-helper)
- [react-bits-video](https://github.com/GordenSun/react-bits-video)
- [youtube-timestamps](https://github.com/cmod/youtube-timestamps/)
- [video-to-chapters-with-transcript](https://pypi.org/project/video-to-chapters-with-transcript/)
- [yt_chapter_maker](https://github.com/vivekhaldar/yt_chapter_maker)
- [Video-Chapter-Generator](https://github.com/its-rath/Video-Chapter-Generator)
- [Trace](https://github.com/drewsephski/trace)
- [Reframed](https://github.com/jkuri/reframed)
- [OpenScreen](https://github.com/EtienneLescot/openscreen)
- [ffmpeg-mcp-video-editor](https://github.com/AbyAbyss/ffmpeg-mcp-video-editor)
- [OpenSource Clipping](https://github.com/naufalrizqullah/opensource-clipping)
- [stockmedia-sdk](https://github.com/difyz9/stockmedia_sdk)
- [pexels-sdk](https://github.com/Stink-Po/pexels-python)
- [VidFetch](https://github.com/heatingma/VidFetch)
- [Freesound API](https://freesound.org/docs/api/)
- [MediaPipe](https://developers.google.com/mediapipe)
- [OpenCV](https://opencv.org/)
- [librosa](https://librosa.org/)
- [GSAP](https://greensock.com/gsap/)
- [Anime.js](https://animejs.com/)
- [Mo.js](https://mojs.github.io/)
