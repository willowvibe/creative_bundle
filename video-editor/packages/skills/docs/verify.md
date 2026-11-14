# Requirements Verification Skill

Captures structured requirements at job intake, runs deterministic checks
(duration, aspect ratio, required/forbidden elements, transcript content),
and applies an LLM-as-judge layer for subjective checklist items.

## Integration

- `VerifySkill` writes `requirements.json` and `verification_report.json`
  under `<work>/verify/`.
- The gate is run automatically by `SkillsPipeline.run_async` when
  `--verify` is passed or when requirements have already been captured.
- A FAIL verdict raises `VerificationError`, blocking the job from being
  reported as ready.
