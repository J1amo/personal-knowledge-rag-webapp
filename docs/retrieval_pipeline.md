# Retrieval Pipeline

1. Normalize query and filters.
2. Route by retrieval mode.
3. Search local vector, BM25, graph/entity, and API vector when configured.
4. Merge by canonical `chunk_id`.
5. Preserve `found_by`, backend ranks, and backend scores.
6. Create citation objects for final evidence.
7. Write `retrieval_audits` and `retrieval_results`.
8. Pass one deduplicated evidence package to analysis or Markdown output.

Audit data records backend result lists, merged final results, dropped duplicates, citations, warnings, and latency.

## Audit Detail

Endpoints:

```text
GET /api/retrieval-audits
GET /api/retrieval-audit?audit_id=...
POST /api/retrieval-audits/repair
```

`/api/retrieval-audit` returns the stored audit row plus normalized JSON fields and joined `retrieval_results` rows:

- final rank
- `chunk_id`
- `source_id`
- filename
- page number
- citation id
- `found_by`
- per-backend ranks and scores
- snippet

`/api/retrieval-audits/repair` writes a Markdown repair prompt under `outputs/maintenance/`. It includes the original query, expected behavior, observed evidence, backend results, dropped duplicates, citations, likely files to inspect, and safety rules. This is intended for one-click handoff from the Maintenance/Audit UI into the next Codex repair round.
