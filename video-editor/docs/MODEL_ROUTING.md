# Model Routing

The `packages/llm` `ModelRouter` routes tasks to providers and models. The
project `config.yaml` routes through **OpenRouter** by default, while
`config.free.yaml` routes the same tasks through **Ollama Cloud Pro**. The
router is model-agnostic: you can add OpenAI, OpenRouter, Replicate, or any
OpenAI-compatible endpoint by declaring it in `config.yaml`.

*Model IDs and rates refreshed on **2026-10-15**. Verify live pricing before
large production runs — see [`models.yaml`](../models.yaml) (single source;
`pricing.yaml` is generated from it via `ve gen-models`).*

`models.yaml` is the single source of truth for routing **and** rates across
providers — `llm.providers.{openrouter,ollama_cloud_pro,typesafe}` hold each
provider's `default_model` + per-task `tasks` map, and `llm.tasks` is the
free-mode (Ollama) fallback the `ModelRouter` uses when no `config.yaml` is
supplied. Per-model rates for **both** OpenRouter and Ollama live in the
`providers:` cost section and are what `ve estimate`/`cost-report` read.

> OpenRouter also fronts **image, video, and music** generation. That is a
> separate, opt-in asset provider — see
> [`OPENROUTER_GENERATION.md`](OPENROUTER_GENERATION.md).

## Cost-aware provider selection (same model, cheapest provider)

When an asset request resolves to the **`auto`** provider *and* names a model,
selection is model-aware and cost-first: only providers that can serve that
model are considered (exact id, or via `models.yaml:model_equivalents`), each is
priced with its **real** per-model unit cost from `pricing.yaml`, and the
cheapest wins (quality/latency only break cost ties). The canonical model id is
then translated to the chosen provider's id (e.g. `gpt-image-2` →
`openai/gpt-image-2` for OpenRouter).

- Explicitly configured providers are **never** rerouted — only `auto` is.
- `model_equivalents` in `models.yaml` maps a canonical model to each provider's
  id (e.g. `gpt-image-2`: `poyo_ai`/`openrouter`, `flux-schnell`:
  `poyo_ai`/`replicate_image`, `seedance-2-mini`: `poyo_ai`/`fal_ai`).
- `PricingRegistry.providers_for_model()` / `.cheapest_provider()` /
  `.resolve_model()` expose the same logic for estimates and tooling.

## Default models (Ollama Cloud, `config.free.yaml`)

| Model | Role | Input / Output (per 1M) |
|-------|------|-------------------------|
| `minimax-m3:cloud` | Planning, decomposition, scene planning, image-prompt derivation | $0.60 / $2.40 |
| `kimi-k2.7-code:cloud` | Code generation and tool-heavy execution | $0.95 / $4.00 |
| `glm-5.3:cloud` | Fallback executor | $1.40 / $4.40 |
| `glm-5.3-flash:cloud` | Small utility tasks, LLM-as-judge verification | $0.15 / $0.50 |

## Default models (OpenRouter, `config.yaml`)

| Model | Role | Input / Output (per 1M) |
|-------|------|-------------------------|
| `anthropic/claude-sonnet-5.5` | Planning, decomposition, edit/scene planning, code | $2.00 / $10.00 |
| `google/gemini-3.8-flash` | Fallback, utility tasks, verification judge | $0.75 / $3.75 |

## Task mappings

```python
# Free mode (Ollama Cloud) — ModelRouter.TASK_MODELS
{
    "plan": "minimax-m3:cloud",
    "decompose": "minimax-m3:cloud",
    "edit_plan": "minimax-m3:cloud",
    "scene_plan": "minimax-m3:cloud",
    "code": "kimi-k2.7-code:cloud",
    "utility": "glm-5.3-flash:cloud",
    "fallback": "glm-5.3:cloud",
    "verify_judge": "glm-5.3-flash:cloud",
}
```

`config.yaml` overrides every one of those tasks to OpenRouter (see the table
above). Override routing per-project by creating `config.yaml` (see
`config.example.yaml`):

```bash
ve plan my-job --config ./config.yaml
ve run ./raw my-job --config ./config.yaml
```

## Classifier (JEV / TypeSafe)

`ModelRouter` handles generation; the **TypeSafe/JEV** classifier handles the
many small per-item judgments (scoring, judging, detecting, classifying) in one
batched call. It is routed by the `classify` task (provider `typesafe`, model
`jev-latest`) and used across 11 engine stages with an optional, fallback-safe
contract. See [`JEV_CLASSIFIER.md`](JEV_CLASSIFIER.md).

## Environment variables

- `OLLAMA_CLOUD_API_KEY` — used by the `ollama_cloud_pro` provider
- `OPENROUTER_API_KEY` — used by the `openrouter` provider
- `TYPESAFE_API_KEY` — used by the `typesafe` classifier provider (JEV)
- `OLLAMA_BASE_URL` — defaults to `https://api.ollama.com`
- `OLLAMA_API_KEY` — legacy fallback read directly by `OllamaClient` when no provider config is given

## Retry behavior

If the primary model fails and `fallback=True`, the router retries once with the
model configured for the `fallback` task (`glm-5.3:cloud` in free mode,
`google/gemini-3.8-flash` on OpenRouter).
