# Output Strategy: Markdown First

The app produces auditable Markdown packages instead of native PPTX.

Reasons:

- Markdown keeps citations readable.
- Codex and presentation workflows can consume it directly.
- It avoids pretending a layout engine exists.
- It is easier to inspect, repair, and regenerate.

Presentation support is delivered as:

- `presentation_guidance.md`
- `presentation_codex_execution_guidance.md`
- `presentation_codex_prompt.md`
