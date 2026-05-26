# Architecture Notes

## Canonical-first flow

1. Raw file is copied to `data/raw/<domain-folder>/`.
2. Parser extracts structured content.
3. Normalizer writes `sources`, `chunks`, and `multimodal_elements`.
4. Index builders read the same canonical `chunks`.
5. `index_coverage` records one row per chunk per index.
6. Retrieval searches available indexes by policy.
7. Merge layer deduplicates by `chunk_id`.
8. Analysis receives one evidence package with source trace.

## Privacy boundary

Public paper queries default to `All Available Indexes`.

Private and confidential scopes default to local-only behavior. API retrieval or API LLM requires explicit UI action and `.env` API configuration.
