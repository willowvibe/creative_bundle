# Explainer from Script Workflow

Generate a short explainer video from a written script. This workflow
takes a markdown script through the **plan → assets → render** chain
that turns a narrative into a finished MP4.

Unlike `ve run` (which works from a recorded source video), this
chain starts with **text** and generates every visual + audio asset
along the way. It is the recommended path for explainer / product /
tutorial content that begins as a written script.

## When to use it

- You have a written script (markdown).
- The content is a short explainer (≤ 60s, single narrator).
- You want the engine to generate narration, visuals, and music
  rather than reusing recorded footage.

For recorded footage (interviews, vlogs, screen recordings) use
[`workflows/raw_to_short_one_command.md`](raw_to_short_one_command.md)
and `ve run` instead.

## The three-step chain

```bash
# 1. Decompose the script into a scene plan via LLM.
#    --config is required: the LLM router has no implicit default.
ve plan-script ./fixtures/scripts/demo_explainer.md \
    explainer-demo \
    --work-root ./projects \
    --output-root ./outputs \
    --aspect-ratio 9:16 \
    --config ./config.yaml

# 2. Resolve every scene's assets (TTS narration, image / data-viz
#    visuals) and write scene_plan_with_assets.json.
ve assets explainer-demo \
    --work-root ./projects \
    --output-root ./outputs

# 3. Render the resolved plan to a final MP4 via HyperFrames.
ve render explainer-demo \
    --work-root ./projects \
    --output-root ./outputs
```

After step 3 the final video is at `outputs/explainer-demo/final.mp4`
(adjust the `--output-name` flag on `ve render` to control the
filename).

## What each step produces

| Step | Command | Output |
|------|---------|--------|
| Plan | `ve plan-script` | `projects/explainer-demo/plan/scene_plan.json` |
| Assets | `ve assets` | `projects/explainer-demo/plan/scene_plan_with_assets.json` |
| Render | `ve render` | `outputs/explainer-demo/final.mp4` |

`scene_plan.json` is the structured decomposition: a list of scenes,
each with a `type`, `duration_seconds`, `narration`, `visual_description`,
`asset_query`, and `props`. `scene_plan_with_assets.json` is the same plan
with every scene's `assets` array populated (TTS mp3, image png, etc.)
pointing at local media files in the job's `.cache/assets/` directory.

## Configuration

`ve plan-script` needs an LLM provider configured in `config.yaml`; the
scene decomposition uses the `scene_plan` task:

```yaml
llm:
  default_provider: openrouter
  providers:
    openrouter:
      name: openrouter
      base_url: https://openrouter.ai/api
      api_key_env: OPENROUTER_API_KEY
      default_model: anthropic/claude-sonnet-5.5
  tasks:
    scene_plan:
      task_type: scene_plan
      provider: openrouter
      model: anthropic/claude-sonnet-5.5
```

`ve assets` and `ve render` don't need an LLM. `ve assets` does
need provider configs for the asset sources (TTS, image, data-viz)
you want to use; see [`docs/LIVE_PROVIDER_RUN.md`](../docs/LIVE_PROVIDER_RUN.md).

## Trying it without API keys

The full chain requires LLM + asset-provider API keys (OpenAI,
ElevenLabs, FAL.AI, etc.). Without them:

- `ve plan-script` will fail at the LLM call.
- `ve assets` will fail at TTS / image generation.

You can still exercise the chain with stub providers after the plan
exists:

```bash
ve assets explainer-demo --tts-provider stub_tts --image-provider stub_image
```

To exercise the whole pipeline with **zero** API keys, use `ve run`
against a recorded source video instead — the demo fixtures at
`demo/raw.mp4` and `demo/transcript.json` are designed for this case
(see [`workflows/raw_to_short_one_command.md`](raw_to_short_one_command.md)).

## Iterating

Re-run individual steps without redoing everything. Each step
overwrites its own artifact, so there is no `--force` flag to learn:

```bash
# Regenerate the plan (overwrites scene_plan.json).
ve plan-script ./fixtures/scripts/demo_explainer.md explainer-demo \
    --work-root ./projects --output-root ./outputs --config ./config.yaml

# Re-resolve only the assets (e.g. swap the TTS voice).
ve assets explainer-demo --work-root ./projects --output-root ./outputs \
    --tts-provider elevenlabs_tts

# Re-render (overwrites outputs/explainer-demo/final.mp4).
ve render explainer-demo --work-root ./projects --output-root ./outputs
```
