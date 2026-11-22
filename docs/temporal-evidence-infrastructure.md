# Temporal financial evidence infrastructure

**Status:** implemented research infrastructure for trusted, file-based ingestion and a Supabase/PostgreSQL migration. No remote project was changed, no live market feed is connected, and no financial data or credentials are included. A passing local PGlite test is useful but does not substitute for testing on the intended Supabase/PostgreSQL version, security review or license review. Never use this as investment advice.

## What is implemented

| Need | Implementation |
|---|---|
| Financial documents and corporate announcements | UTF-8 text and text-extractable PDFs (maximum 10 MB and 100 pages). A deterministic heuristic retains exact financial-claim sentences and classifies dividend, results, merger, buyback and guidance sentences. Extracts are **unverified**, not a general information extraction system; scanned PDFs need OCR outside this implementation. |
| Historical stock prices | Import an explicitly licensed, provider-normalized CSV with timestamped daily OHLC and/or volume; close required. No vendor downloader, historical security master, adjusted-price logic, market calendar, delisting treatment or corporate-action adjustment is supplied. |
| Mutual funds | Import provider-normalized NAV CSV with explicit fund identifier, date, unit, currency and release timestamps. No assumption is made that NAV is available at the period end. |
| Statements | Import normalized JSON rows, or a constrained SEC companyfacts wrapper for one explicitly specified accession. Taxonomy tags and periods are preserved; no general XBRL taxonomy mapping or automatic filing-acceptance-time lookup is claimed. |
| Source and time provenance | Private source and document metadata, SHA-256, provider, license and permissions; observation value/unit/currency/reporting period, source/filing version, publication, availability, database retrieval time, validation and supersession. `XXX` is the explicit no-currency code for shares/pure quantities. |
| Graph and vectors | Exact claim spans and manually supplied, owner-scoped `supports`/`contradicts`/`references` edges. Store externally generated 384-dimensional pgvector embeddings with model IDs and perform tenant- and time-scoped cosine lookup. No embedding model, claim entailment, relevance guarantee or automatic graph discovery is included. |
| Quality and revisions | Required fields and chronological ordering, finite decimal values, required units, version uniqueness, price/NAV positivity, integer nonnegative volume and OHLC consistency. Nonpositive price/NAV, invalid volume and periods ending after availability are quarantined; structural, timestamp and contradictory OHLC errors reject the entire import transaction. Corrections/restatements are additional versions, optionally linked by `supersedes_id`. |
| Snapshots and reports | A repeatable-read transaction freezes observations, excluded latest versions, document metadata, claims, announcements, relations and embeddings in one append-only manifest, with a SHA-256 hash. `provenance_report` summarizes providers, license declarations, counts and restrictions. Sample **offline** report: [market-preview-report.json](../experiments/evidence/market-preview-report.json). |

## Licensing and source obligations

Use only data for which the **actual** provider agreement permits the intended collection, storage, derivation and display. `allow_raw_storage`, `allow_derived_storage` and `allow_redistribution` are explicit operator-supplied assertions, not legal verification. Derived storage must be permitted to ingest. If raw storage is forbidden, neither raw bytes nor extracted full text or text claims are retained; only allowed structured derivatives and the input hash may be stored. If redistribution is forbidden, do not publish records, quotes, snapshots or summaries without checking the actual agreement: a boolean or aggregate report does not override a license. The code has no public export endpoint and owner-scoped data access is **not** proof of permission to redistribute. Original URLs cannot contain usernames, passwords, query parameters or fragments; use a clean canonical source URL and keep signed URLs/tokens in a separate secret manager. The small fixture uses synthetic data only.

The system imports files already acquired by a trusted operator; it does not contact data providers. The `original_source_uri` should identify the exact upstream document or dataset. Record its license reference and retain evidence of terms outside the database as required. Normalized CSV/JSON or the SEC wrapper may itself be a **derived** file: its `content_sha256` hashes the submitted file, not automatically the original upstream bytes. Preserve the original provider artifact and its hash separately, where licensed, and document the normalization mapping; do not misrepresent a wrapper hash as the source document hash. There is no automated source-authenticity verification.

## Deploy only to a designated project

The Supabase integration was inspected, but no active target was identified and **no migration was applied remotely**. Review `supabase/migrations/20261122000100_evidence_infrastructure.sql` before applying it to a dedicated test project, then to a specifically authorized production project only after security/legal approval. The migration requires Supabase `auth.users`, `auth.uid()`, `authenticated`, `anon` and `service_role`; it creates schema `investiq_evidence` and requires pgvector in the `extensions` schema. If pgvector is already installed elsewhere the migration fails explicitly; do not silently relocate an extension used by another application. PostgreSQL 15+ is the target. Apply the migration using your normal Supabase migration process or privileged `psql` with connection settings supplied securely by a secret manager. Do not put passwords or service-role credentials in Git, browser code, chat, shell history or process arguments. Back up and review the target first.

The schema is not automatically exposed through Supabase PostgREST and no frontend, HTTP API, auth handler or direct client write path is installed. To use it with Supabase, configure the exposed schema deliberately if needed, with a reviewed API and grants. All rows—including supposedly public market evidence—are currently private to a single user; there is no shared research dataset. `authenticated` has SELECT only, filtered by `owner_id = auth.uid()` with RLS forced. `anon` has no table privileges. All writes use a **trusted backend** holding a service role / privileged database connection, which bypasses RLS: authenticate the requester and independently authorize the requested `owner_id` before invoking any store function. The CLI assumes a trusted operator and does **not** authenticate end users. Do not expose it or a service credential to clients. Composite owner/ID foreign keys prevent cross-tenant references even when the trusted service makes a mistake. Append-only triggers reject UPDATE and DELETE. These controls do not protect against a database administrator, a stolen service credential, a malicious backend, or an incorrect source/license assertion. Use a secure secret store, network controls, TLS, backups, access logging and external review. The implementation does not provide encryption key management, consent workflows, audit-grade tamper-proof storage or a personal-data deletion workflow; resolve retention and privacy rights before ingesting personal financial information. A database owner can ultimately bypass triggers; immutable here means protected against ordinary writes, not physically immutable against administrators.

## Timing, corrections and snapshots

All timestamp inputs must be timezone-aware. `published_at` is the source's publication instant; `available_at` is the earliest *documented* public/access entitlement instant, no earlier than publication. Never infer either from period end or a date-only field. Exact acceptance versus public dissemination, vendor delays, after-close handling and timezone precision need source-specific validation. In particular, the SEC wrapper requires release/availability timestamps supplied from independently mapped filing metadata; companyfacts alone does not establish them. Unknown times must be rejected or conservatively sourced; do not guess.

`retrieved_at` is assigned by the **database clock at import**, not by supplied metadata, so it is a conservative record of when this installation obtained the submitted file/record; it is not a verified earlier download time on an operator's machine. The clock must be synchronized and monitored. Source, claims, relationship, embedding and snapshot creation timestamps are likewise database-assigned. An as-of snapshot selects only records with both `available_at <= as_of` **and** `retrieved_at <= as_of`. Claims, announcements, relationships and embeddings additionally require their creation time to be at or before cutoff. Thus importing a 2019 archive today does **not** make it appear in a 2019 as-known snapshot. A historical reconstruction based on trusted archives is a different pseudo-real-time study and is not implemented; do not relabel such an import as an operational information set. Future cutoffs are rejected.

Each logical key hashes source ID, entity ID, metric, reporting period, currency and unit. Each version is separately inserted; `supersedes_id` is optional, but when provided the DB enforces same owner, provider source, entity, metric, period, currency and unit, and nondecreasing availability. CSV can supply `supersedes_close_id`, `supersedes_nav_id`, etc.; statement rows use `supersedes_id`. Corrections require a new document ID and a new `version_identifier`; never rewrite the original. At cutoff, latest known version is ranked by availability, retrieval then ID. **Only after ranking** is validation applied: a quarantined newest correction excludes that logical key rather than silently reviving an older version. `excluded_latest` makes that loss visible. A different source ID defines a separate series: normalize source identities and retain mappings deliberately. No automatic reconciliation across providers is performed.

A snapshot JSON manifest is created in one repeatable-read transaction and inserted in a single row. Raw files are referenced by ID and SHA-256 rather than copied into each manifest; where raw storage is prohibited, reproduction of extraction requires the operator to reacquire the exact licensed input and verify the hash. A report or snapshot containing restricted data is itself sensitive. The SHA-256 canonicalization is `json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)` over the manifest; retrieval verifies it. Database triggers and grants prevent normal mutation, but they are not signatures or independently controlled WORM storage. `get_snapshot(conn, owner_id, snapshot_id)` checks the owner and hash; ordinary user reads remain subject to RLS.

## Input contracts and commands

Install dependencies as in the [README](../README.md). Run an **offline** synthetic preview without a database:

```sh
python -m investiq.evidence preview \
  --metadata tests/fixtures/evidence/market-metadata.json \
  --file tests/fixtures/evidence/market.csv
```

This reports no retrieval time and creates no snapshot. `metadata` is JSON with exact `Source` and `Document` fields illustrated by that fixture. New documents need unique UUIDs; a source UUID can be reused only with exactly the same owner, provider and license metadata. For real data, use a licensed provider name, canonical source/document URLs, truthful license and flags, document type, media type, publication/availability and version. Set `INVESTIQ_DATABASE_URL` **on the trusted server** from a secret manager. Then, with an authenticated/authorized owner UUID belonging to `auth.users`:

```sh
python -m investiq.evidence ingest --metadata path/to/metadata.json --file path/to/data.csv --owner "$AUTHORIZED_USER_UUID"
python -m investiq.evidence snapshot --owner "$AUTHORIZED_USER_UUID" --as-of 2026-11-22T12:00:00Z
python -m investiq.evidence report --owner "$AUTHORIZED_USER_UUID" --snapshot-id "$SNAPSHOT_ID"
```

The as-of time must not be later than the database clock. Ingestion is atomic for source, document, observations, claims and announcements; it returns IDs, hash, quarantine count and warnings. The caller must persist returned IDs for correction links. Reusing a document or observation version is an error rather than a silent overwrite. The source's license and metadata must not be silently changed; create an explicitly new source version after appropriate review.

### Formats

**Stock CSV:** columns `entity_id,period_start,period_end,published_at,available_at,currency,unit,version_identifier,open,high,low,close,volume`; only close is mandatory among metrics, other nonempty fields are extracted. `unit` is price unit (e.g. `USD/share`); volume unit becomes `shares`. Reporting dates are ISO dates, timestamps ISO with offset. As with all fields the source must explicitly supply them.

**NAV CSV:** same common columns plus `nav` (e.g. `INR/unit`); `entity_id` should be a stable provider fund/share-class ID, not merely a display name.

**Statement JSON:** `{"rows":[{"entity_id":"CIK:0000000123","metric":"us-gaap:Revenues","value":"1000.00","currency":"USD","unit":"USD","period_start":"2023-01-01","period_end":"2023-12-31","published_at":"2024-02-01T21:00:00Z","available_at":"2024-02-01T21:00:00Z","version_identifier":"accession-v1"}]}`. Amounts remain decimal text; supplied raw text is retained in `original_value` and used to construct a `Decimal`, never a binary float. Statements may include optional `supersedes_id`.

**SEC companyfacts wrapper:** `{"companyfacts": <original companyfacts JSON object>, "accession":"...", "published_at":"...Z", "available_at":"...Z"}`. The selected facts must contain the accession, CIK, value, period end and supported unit (`USD`, another three-letter currency, `USD/shares`, `shares` or `pure`); missing facts/release timestamps and unrecognized units for the selected accession reject the input, rather than being silently dropped or imputed. Duplicate identity/version rows reject the import for review. Timestamps are supplied and checked by the operator; `filed` date alone is insufficient. A companyfacts wrapper is not an original SEC filing and its hash is not the original filing hash.

**Documents and announcements:** `text/plain` or `application/pdf`. Parsing is bounded but PDF parsing of untrusted documents is still a security risk; use isolated workers with resource limits. Extracted PDF text and offsets refer to the extractor output, not original PDF byte offsets. Claims and announcements contain exact quoted spans and begin as unverified. An absent number, unsupported vocabulary, multi-line layout, OCR, semantic entailment or a misleading quoted source can lead to missed or wrong interpretations. Relationships are explicit human/external annotations via `link_claims`; they are not inferred or automatically validated as truthful.

`store.add_embedding` accepts a caller-provided nonzero finite 384-dimensional vector plus a model identifier; `store.search_claims` uses pgvector cosine distance, the same owner/model and a time cutoff on the embedding, claim and document. Search is brute-force and does not prove relevance or factual support. Embeddings themselves may reveal sensitive information, inherit access controls and licensing restrictions, and are included in the snapshot. No vectors or data are sent to an external model by this code.

## Tests and reproducibility

```sh
python -m pytest -q
```

Without a test DB, three database tests are intentionally skipped. They are destructive and require **both** `INVESTIQ_DISPOSABLE_TEST_DB=1` and `INVESTIQ_TEST_DATABASE_URL`; the fixture refuses a database not named `investiq_test_*`, except a local PGlite server identifying itself as such. It drops the `auth` and `investiq_evidence` schemas, so never point it at a real project. The user must set up a disposable PostgreSQL instance with pgvector, create roles and install extensions with sufficient test privileges. These tests create a minimal auth stub, not Supabase Auth.

For a disposable, in-memory local PGlite/pgvector smoke test with Node.js and npm:

```sh
npm ci --prefix tests/evidence/pglite
node tests/evidence/pglite/server.mjs  # separate terminal; binds 127.0.0.1 only
INVESTIQ_DISPOSABLE_TEST_DB=1 INVESTIQ_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:15432/postgres python -m pytest -q
```

The local check covers migration application, tenant RLS, append-only behavior, source rights, graph/embedding access, point-in-time imports, restatements, snapshot hash replay and quarantine. PGlite and a minimal `auth.uid()` stub are **not** the deployed Supabase environment. Before rollout, repeat on an isolated target-version Supabase/PostgreSQL instance, exercise real JWTs and storage access, review RLS with two users and an anonymous session, test backup/recovery and migration rollback, and conduct independent security and source-license reviews.
