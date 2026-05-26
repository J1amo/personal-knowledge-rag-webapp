# PDF Reader Strategy

The project remains a Python standard-library backend with static HTML/CSS/JS. PDF rendering is now handled by vendored Mozilla PDF.js, not by the browser's opaque built-in renderer.

## Vendored Component

- Package: `pdfjs-dist`
- Version: `5.6.205`
- License: Apache-2.0
- Source in this workspace: local Codex runtime cache
- Project path: `vendor/pdfjs/`

Included files:

- `vendor/pdfjs/legacy/build/pdf.mjs`
- `vendor/pdfjs/legacy/build/pdf.worker.mjs`
- `vendor/pdfjs/web/pdf_viewer.css`
- `vendor/pdfjs/web/images/`
- `vendor/pdfjs/cmaps/`
- `vendor/pdfjs/standard_fonts/`
- `vendor/pdfjs/LICENSE`
- `vendor/pdfjs/package.json`

No model weights or large OCR/embedding assets are stored under `vendor/`.

## Routes

- `GET /viewer?source_id=...&page=...&chunk_id=...`
- `GET /api/source/raw?source_id=...`
- `GET /api/chunks?source_id=...`
- `GET /vendor/pdfjs/...`

`/api/source/raw` streams the retained original PDF as the highest-level evidence. The viewer uses PDF.js to render the page from that raw source.

## Citation Focus

Query evidence links preserve `source_id`, `page`, and `chunk_id`. When a citation/chunk is opened, the viewer:

- renders the target page with PDF.js;
- highlights the page with a focus overlay;
- selects the matching chunk in the side panel;
- keeps the URL stable for sharing inside the local app.

Current limitation: canonical chunks do not yet store exact PDF text bounding boxes, so the app cannot draw word-level or paragraph-level PDF highlights. The implemented behavior is page-level focus plus side-panel evidence, and the viewer states this limitation in its focus metadata.
