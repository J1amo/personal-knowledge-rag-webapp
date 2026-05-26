# Local LLM / Gemma4

Gemma4 is supported through an OpenAI-compatible local endpoint.

Environment variables:

```text
GEMMA4_OPENAI_BASE_URL=http://127.0.0.1:1234/v1
GEMMA4_MODEL=gemma4
```

Aliases:

```text
LOCAL_LLM_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_LLM_MODEL=gemma4
```

The model receives retrieved evidence and a deterministic Markdown scaffold. It must mark unsupported claims:

```text
[无直接来源，需人工确认]
```

If the endpoint is missing or fails, the app exports the scaffold, evidence map, citations, and review checklist instead of fabricating claims.

## Diagnostics

CLI:

```bash
./scripts/check_local_llm.py
```

Web API:

```text
GET /api/local-llm/status
```

The diagnostic sends a tiny OpenAI-compatible `/chat/completions` request to the configured local endpoint and reports:

- configured / not configured
- reachable / failed
- model name
- base URL
- API key presence, without printing the key
- latency
- short sample response
- error message when unavailable

The Settings page exposes the same status check.
