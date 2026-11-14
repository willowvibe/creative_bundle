# Earshot SFX integration

[Earshot](https://github.com/Leonxlnx/earshot) is a code-synthesized sound
bank (252 sounds, v0.4, MIT) with an `sfx` renderer. The engine uses it as the
first free SFX provider: `earshot sfx render <id>` **loads no CLAP/AST models
and needs no network**, so a per-cue subprocess takes seconds. The listening
commands (`describe`, `match`, `find`, `critique`) do load models (~20–40 s per
CLI call); for those, use Earshot's MCP server or Python API, not per-cue calls.

## Install (its own environment)

Python 3.10+ and ffmpeg/ffprobe on `PATH`; CPU only.

```bash
git clone https://github.com/Leonxlnx/earshot ~/tools/earshot
cd ~/tools/earshot
python -m venv .venv
.venv/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -e ".[mcp,llm,dev]"
.venv/bin/python -m earshot doctor          # --load only if you will listen, not just render
```

The first listening use downloads ~1.1 GB of CLAP/AST weights into
`.cache/hf` (or `EARSHOT_HOME`). The `sfx` renderer needs none of them.

Verify the bank:

```bash
.venv/bin/python -m earshot sfx list --kind whoosh
.venv/bin/python -m earshot sfx render whoosh_fast/whip -o /tmp/w.wav --dur 0.8
```

## Configure the engine

Add the provider to `config.yaml` (the example is in `config.example.yaml`).
Point `command` at Earshot's own interpreter so its torch/model dependencies
stay out of this venv:

```yaml
assets:
  providers:
    earshot_sfx:
      timeout: 60.0
      provider_options:
        command: ["/home/you/tools/earshot/.venv/bin/python", "-m", "earshot"]
        bank_ids:
          hit: cut_hit/punch
          click: ui_pop/step
          card_flip: shutter/quick
          latch: knock/desk
          whoosh: whoosh_fast/whip
        # apex: 0.32   # optional loudest moment, seconds into the take
```

The default map lives in
`packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/earshot_sfx.py`;
override entries after `earshot sfx list`. The short path builds its chain from
the **configured** providers in this order:

```
earshot_sfx -> freesound_sfx -> openverse_sfx
```

so adding the block turns Earshot on; removing it falls back to the free stock
indexes. `sfx: off` remains the default for a pack.

## How it is called

The resolver sends `params["cue_sound"]` (the cue-table sound) and
`params["duration_seconds"]`. The provider maps the sound to a bank id and runs:

```bash
<python> -m earshot sfx render <bank_id> -o <cache>/....wav --dur <seconds> [--apex <seconds>]
```

The result is cached like any other asset (priced at zero) and carries
`license: generated` in the manifest; `credits.txt` needs no attribution for
these. Fitted CC0 sounds in Earshot's bank are CC0; its `docs/CREDITS_FIT.md`
has the details.

## Tests

`tests/test_earshot_sfx.py` uses a fake executable, so the engine's test suite
does not require Earshot or its models.


## Installed on this machine (2026-10-28)

- Earshot **v0.4.0** at `~/tools/earshot` (its own `.venv`, CPU wheels, no
  model download needed for `sfx`).
- The provider block is in the gitignored `config.local.yaml`;
  `whoosh` maps to `whoosh_fast/whip` (116 ms peak, inside the 120 ms whoosh
  tolerance).
- Verified: the five mappings audit clean; eight scene-cut whooshes rendered
  on `projects/saju-january-animal/voiced.mp4`, 8/8 sync within 120 ms, the
  mix adds +0.0 LUFS over the voice, and `credits.txt` says "none required".
- Listening trial completed on `day_master_deck.mp4`: see
  [`../tool-lab/trials/2026-10-28-earshot/README.md`](../../tool-lab/trials/2026-10-28-earshot/README.md) for the
  cut/sync/loudness numbers and what to trust in `critique`.
