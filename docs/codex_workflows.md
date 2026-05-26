# Codex Workflows

Use the Maintenance page to generate a repair task when retrieval quality or ingestion health looks wrong.

CLI equivalents:

```bash
./scripts/pkb.sh workflow
./scripts/pkb.sh doctor
./scripts/pkb.sh codex --reason "具体问题"
./scripts/pkb.sh codex --audit-id aud_xxx --expected "期望命中的论文/chunk/行为"
```

The generated file includes:

- current health JSON
- failed jobs
- missing indexes
- duplicate files
- missing originals
- suggested Codex task
- safety rules

Default safety rules:

- Do not delete raw files automatically.
- Do not send private documents to API.
- Do not overwrite rule files blindly.
