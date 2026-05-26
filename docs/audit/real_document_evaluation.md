# Real Document Evaluation

Status: blocked in this environment.

Reason: the next-round guidance asks for evaluation on 3-5 real research PDFs. No safe set of 3-5 public research PDFs is currently available inside the project data directory. The only PDFs discovered outside the project during manual inspection were private/admin-looking personal documents, so they were not ingested or evaluated.

Implemented evaluator:

```bash
./scripts/evaluate_real_pdfs.py --pdf-dir /path/to/public-research-pdfs
```

Default behavior is conservative: without `--pdf-dir`, the evaluator only scans:

```text
data/raw/papers/
```

This avoids accidentally reading or ingesting private PDFs from Desktop, Downloads, or Documents. If fewer than 3 research-like PDFs are found, it writes a blocked report under:

```text
outputs/evaluation/
```

When 3-5 public research PDFs are provided, the evaluator uses an isolated temporary DB/runtime, ingests the PDFs as public papers, builds local vector/BM25/graph indexes, runs grounded queries, records audit ids, and writes a ready report.
