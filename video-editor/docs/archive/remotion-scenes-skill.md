---
type: code
description: Author Remotion scenes for the video editor engine.
---

# Remotion Scenes Skill

Use this skill when editing `apps/editor` or `apps/renderer` Remotion compositions.

## Scene types

- `title` — full-screen title card.
- `lowerThird` — animated nameplate.
- `bRoll` — image/video overlay with optional ken-burns.
- `dataViz` — chart or metric reveal.
- `talkingHead` — masked video frame with background.
- `placeholder` — generated color card with text.

## Composition conventions

- Use `Sequence` from `remotion` for per-scene timing.
- Read scene plan from a JSON prop passed to the root composition.
- Keep scene components in `apps/editor/src/compositions/`.
- Use `AbsoluteFill` for responsive root layout.

## Rendering

```bash
cd apps/editor
pnpm build              # bundle composition
cd ../renderer
pnpm render Preview     # headless render
```

## Type sharing

Shared scene/composition types live in `packages/remotion-types`. Import them with the workspace alias:

```ts
import { ScenePlanSchema } from "@video-editor/remotion-types";
```
