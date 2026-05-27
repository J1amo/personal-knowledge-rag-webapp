# DOI Downloader

DOI Downloader is an authorized-access helper for the Personal Research OS. It automates the same article-level PDF download steps a user could perform manually with their existing university, institutional, publisher-account, VPN, EZproxy, Shibboleth, or open-access permissions.

It does not use an LLM for downloading. It does not bypass access controls.

## Compliance Boundary

- Only process DOI values explicitly supplied by the user.
- Do not use Sci-Hub, shadow libraries, pirate mirrors, guessed token URLs, or paywall bypasses.
- Do not download whole issues, volumes, books, keyword search result sets, or publisher collections.
- Default concurrency is `1`.
- Stop on login, MFA, CAPTCHA, 429, suspicious activity, or rate-limit signals.
- Record per-article access denial or 403 as `blocked_by_access`, save evidence if possible, and continue to the next explicitly supplied DOI without bypassing access controls.
- Save DOI, landing URL, publisher domain, file path, timestamp, status, failure reason, stop policy, and diagnostic signals in logs.

## Install Playwright

The app can be developed and tested without Playwright, but real DOI downloading requires it:

```bash
cd personal-knowledge-rag-webapp
python3 -m pip install playwright
python3 -m playwright install chromium
```

Check status:

```bash
./scripts/check_doi_downloader.py
```

The browser profile is stored separately from the user's main browser:

```text
cache/browser_profiles/doi_downloader/
```

The app never reads or copies Chrome/Safari profile cookies.

## CLI Usage

Single DOI:

```bash
./scripts/download_by_doi.py --doi "10.xxxx/yyyy" --out data/raw/papers
```

DOI list:

```bash
./scripts/download_by_doi.py --doi-file dois.txt --out data/raw/papers --max-items 10
```

Manual institutional login:

```bash
./scripts/download_by_doi.py --doi-file dois.txt --out data/raw/papers --headed --allow-manual-login
```

Fast mode, only for small open-access or explicitly confirmed batches:

```bash
./scripts/download_by_doi.py --doi-file dois.txt --fast-mode --max-items 5
```

Optional ingestion:

```bash
./scripts/download_by_doi.py --doi "10.xxxx/yyyy" --auto-ingest
```

## Web UI

Open:

```text
http://127.0.0.1:8765
```

Use the `DOI 下载器` page to:

- paste a DOI or DOI list;
- choose a save directory;
- run with or without visible browser mode;
- allow manual login waiting;
- enable fast mode;
- optionally add downloaded PDFs to the document library;
- view job and item logs;
- clear the DOI downloader browser profile.

The Web UI displays the compliance warning before download controls.

## Waiting Strategy

Page action waits:

```text
0.3-1.2 seconds
```

These are only jitter between event-driven page actions. The downloader still prefers navigation, response, selector, and browser event waits.

Article-level delay:

```text
15-25 seconds
```

Fast mode:

```text
5-10 seconds
max 5 items
default off
```

Fast mode is not concurrent and still stops immediately on CAPTCHA, 429, suspicious activity, rate-limit, or login/MFA. Per-article access denial is logged and skipped.

## Save Layout

PDFs default to:

```text
data/raw/papers/
```

Each PDF gets a sidecar:

```text
<same_basename>.metadata.json
```

Download logs:

```text
outputs/doi_download_logs/
```

Failure artifacts:

```text
outputs/doi_download_logs/snapshots/
```

Runtime tables:

- `doi_download_jobs`
- `doi_download_items`
- `doi_metadata`

## Metadata

Crossref metadata is attempted first. Failure to retrieve metadata does not block the download, but it is recorded in the sidecar/log.

Saved fields include DOI, title, authors, journal, year, publisher, landing URL, final PDF URL, downloaded time, file path, file hash, status, and failure reason when applicable.

## Failure Handling

When risk signals are detected, the current item is marked with a stop status and the batch stops:

- `needs_login`
- `blocked_by_captcha`
- `blocked_by_rate_limit`

When a single article shows an access-denial or purchase-access page, the item is marked as `blocked_by_access`, diagnostics and snapshots are saved when possible, and the batch continues after the normal article delay.

If possible, the app saves a screenshot and HTML snapshot under `outputs/doi_download_logs/snapshots/`.

## Adding To Personal Research OS

By default, the DOI downloader saves PDF + metadata only. It does not automatically ingest into the canonical structured data layer.

To ingest after download:

- CLI: pass `--auto-ingest`.
- Web UI: check `加入文档库`.

Optional index rebuild is available in the backend/CLI as `rebuild_after_ingest`, but it is intentionally not the default.
