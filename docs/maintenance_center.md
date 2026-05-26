# Maintenance Center

Implemented health sections:

- database status
- source/document counts
- index coverage
- storage usage
- API key configured flag
- failed ingestion jobs
- duplicate files
- missing original files
- recent Markdown outputs
- retrieval audit count
- Codex repair guidance generation
- audit-specific detail and repair guidance generation
- local LLM / Gemma4 diagnostic status

Primary endpoints:

- `GET /api/maintenance/report`
- `POST /api/maintenance/codex-task`
- `GET /api/coverage`
- `GET /api/processing-status`
- `GET /api/retrieval-audit?audit_id=...`
- `POST /api/retrieval-audits/repair`
- `GET /api/local-llm/status`

Repair guidance files are written under:

```text
outputs/maintenance/
```

They are intentionally ignored by git because they can contain query text and source snippets.
