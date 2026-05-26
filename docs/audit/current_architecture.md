# Current Architecture Audit

Frontend:

- Static HTML/CSS/JS.
- No React/Next/Vue/Svelte.

Backend:

- Python standard-library `http.server`.
- No FastAPI/uvicorn dependency.

Database:

- SQLite metadata database.

Retrieval/indexing:

- Local hash vector index.
- API vector adapter.
- BM25 index.
- Regex graph/entity index.
- Coverage tracked in SQLite.

PDF processing:

- PyMuPDF parser.
- Raw PDF retained.
- Browser PDF renderer adaptation layer.

LLM:

- Local extractive answer fallback.
- OpenAI-compatible API LLM optional.
- Gemma4 OpenAI-compatible local endpoint for Markdown generation.
