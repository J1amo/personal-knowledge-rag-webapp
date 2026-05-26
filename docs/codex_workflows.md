# Codex Workflows

Use the Maintenance page to generate a repair task when retrieval quality or ingestion health looks wrong.

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
