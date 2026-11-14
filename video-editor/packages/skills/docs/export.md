# Export Skill

Final export step for a job.

Copies the latest rendered video into the job output directory and generates a
human-readable report plus a newline-delimited decision log.

## Outputs

- `outputs/{job_id}/final.mp4`
- `outputs/{job_id}/report.md`
- `outputs/{job_id}/decision_log.jsonl`
- `projects/{job_id}/export/export.json`

## CLI

```bash
ve export <job_id> [--output-name final.mp4] \
  --work-root ./projects --output-root ./outputs
```

## Python

```python
from pathlib import Path
from video_editor_state.job import JobContext
from export.export import ExportSkill

ctx = JobContext("demo", work_root="./projects", output_root="./outputs")
skill = ExportSkill(ctx)
final = skill.run()
print(final)
```
