# Markdown Output Studio

The app is Markdown-first. It does not claim to generate native PPTX.

Supported output types:

- `research_summary`
- `literature_review`
- `comparison_matrix`
- `source_grounded_report`
- `mindmap_outline`
- `presentation_guidance`
- `presentation_codex_execution_guidance`
- `presentation_codex_prompt`
- `project_repair_guidance`
- `project_next_step_guidance`

Generation pipeline:

1. Retrieve source-grounded evidence.
2. Deduplicate evidence by `chunk_id`.
3. Build deterministic scaffold.
4. Try local Gemma4 if configured.
5. Fall back to scaffold if local LLM is unavailable.
6. Insert citation policy, uncertainty notes, and checklist.
7. Save `.md` under `outputs/<output_type>/`.
8. Record `markdown_outputs` and `local_llm_runs`.

Local Gemma4 readiness can be checked before generation:

```bash
./scripts/check_local_llm.py
```

The app also exposes `GET /api/local-llm/status` and a Settings page diagnostic. The check never prints API keys; it only reports whether a key is configured.
