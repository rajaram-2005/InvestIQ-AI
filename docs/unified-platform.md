# Unified research workspace: setup and operational limits

This is a **research prototype, not a production-ready financial service**. The interface contains ten workspace sections, but an empty page is not evidence of a working market feed or model. No real market data or research results are bundled in the platform. Nothing here is investment advice.

## What is wired

- Personal snapshots use user-reported INR amounts; cash flow and net worth are arithmetic. With an optional user-supplied measurement-uncertainty percentage, the TFSF prototype filters snapshots under a diagonal random-walk assumption. Its variance is model-dependent, not a validated confidence interval. Changes in actual finances, subjective measurement error and prior assumptions may dominate it.
- A configurable savings mission records actual contributions separately from an illustrative plan; savings, SIP and monthly compound-interest calculators use Decimal arithmetic. See [the audit and roadmap](implementation-audit-and-roadmap.md) for formulas, status and limitations.
- Budget comparisons execute an ECCR model of an accounting identity; they do not establish causal financial effects. The returned difference is also computed directly in decimal arithmetic. Arbitrary causal model construction is not exposed in the UI.
- Indian stock/fund views display suitable, licensed, previously ingested evidence observations only, not a live feed. Source timestamps, versions and links are shown; market provenance, adjustments, license and completeness still need independent verification. The evidence explorer and relationships read the evidence database. A `human_verified` label is a recorded assertion, not independently verified truth.
- The forecast ledger records user assertions, evidence references and a binary Brier score on resolution. It does not evaluate predictive validity or ensure the cited evidence proves the outcome. The research workspace displays existing claim relations; **DPRO debate generation is not integrated**. The lab tab links conceptually to the existing E0–E4 CLI but does not run experiments. The assistant is keyword-based evidence retrieval, not an LLM, and the report is a reviewed-evidence digest, not an AI recommendation.

For a proposed Windows local Supabase development setup, see [the Windows guide](windows-local-setup.md); it is not yet tested on Windows.

## Local preview (no credentials required)

Requires Node 22+ and Python 3.11 (other versions have not been verified).

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
npm --prefix web ci
.venv/bin/uvicorn investiq.platform.api:app --host 0.0.0.0 --port 8000
# In a second terminal:
npm --prefix web run dev -- --port 3000
```

Open the web server, not the API server. `/api/v1/status` transparently reports configuration. Secure `__Host-` cookies require HTTPS (or a browser's localhost secure-context exception). The same-origin Next.js rewrite proxies `/api/*` to the backend at server-side `API_INTERNAL_URL` (default `http://127.0.0.1:8000`); this is never a browser-side backend URL. Keep that destination trusted and fixed. No credentials are needed to explore unconfigured states.

## Controlled staging deployment procedure

1. Provision a **dedicated** Supabase/PostgreSQL project with pgvector, Auth and a disposable staging environment. Never apply these migrations to an unrelated existing project. Review both SQL migrations and run them in order: `supabase/migrations/20261122000100_evidence_infrastructure.sql` then `20261122000200_unified_platform.sql`, then `20261122000300_savings_mission.sql`. Back up and verify rollback/recovery before deployment. Neither migration has been applied to a remote project in this work.
2. Configure Supabase email/password authentication and create users through an independently secured administrative flow. Set the API process environment `SUPABASE_URL` (HTTPS), `SUPABASE_ANON_KEY` and `INVESTIQ_DATABASE_URL`; keep keys, connection strings and service-role secrets out of the frontend, source control and logs. The API validates each access token using Supabase Auth `/auth/v1/user`, and scopes database transactions to the `authenticated` role and user subject. The DB login must be allowed to `SET ROLE authenticated`; use a dedicated least-privilege backend identity, TLS and private networking. Validate grants, RLS and connection behavior against the actual Supabase deployment. Do not expose the app or evidence schemas via PostgREST: authenticated-role SQL grants are needed by the backend, but direct browser writes can forge audit events and human-review assertions.
3. Deploy FastAPI behind a private network and a public TLS-terminating frontend. Set frontend `API_INTERNAL_URL` to the backend's private reachable HTTP(S) origin, install with `npm ci`, build with `npm run build` and start with `npm start`. Set request size limits, rate limiting, transport security, trusted proxy configuration, logging/redaction, secret rotation, dependency scanning, backups and monitoring at the infrastructure layer. Do not enable permissive cross-origin requests: mutating calls rely on the non-simple `X-InvestIQ-Request: 1` header, a same-origin proxy and SameSite cookies for CSRF resistance. Validate origin protections and login flows in the target environment. Password and access token traffic must only traverse TLS on public networks.
4. Import only appropriately licensed data through the trusted ingestion workflow in [the evidence guide](temporal-evidence-infrastructure.md); independently verify all timestamps, corporate actions, source rights and review claims. There is no market feed, browser ingestion/review UI or automated entitlement management.
5. Before any production use, independently security-review and test the application, authentication, CSRF, RLS, secret handling and tenant isolation on real PostgreSQL/Supabase. Add retention/deletion and consent-compliant workflows, threat modeling, incident response, vulnerability management, observability, abuse controls, availability and load testing, accessibility and browser/device testing. The current append-only personal snapshots **cannot be deleted by the user**, consent withdrawal blocks API and authenticated-role snapshot reads/writes but does not erase stored data, and therefore privacy/regulatory requirements are not yet met. Audit events are mutable by privileged database operators and can be inserted by the authenticated database role; they are not tamper-proof or a trusted independent audit trail. Sign-out removes the local cookie but does not revoke the Supabase token server-side; there is no refresh-token flow, MFA enforcement, account recovery, session management or full auth integration test. No sensitive customer data should be used until these gaps are resolved.

## Tests

```sh
.venv/bin/pytest -q
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web audit
```

Database integration tests are destructive and run only when `INVESTIQ_TEST_DATABASE_URL` and `INVESTIQ_DISPOSABLE_TEST_DB=1` explicitly designate a disposable `investiq_test_*` Postgres database or the PGlite harness described in the evidence guide. The platform tests exercise migrations, selected API workflows, consent, owner isolation, evidence retrieval and forecast issuance against PGlite; they override authentication and reuse a test connection (the PGlite socket accepts one connection at a time). They do **not** constitute complete integration coverage: real Supabase Auth, PostgreSQL networking, browser cookies, concurrent requests, forecast resolution, deployment, market feeds and browser interactions still require end-to-end tests. PGlite differs from hosted PostgreSQL/Supabase and is not a substitute for testing the actual target.
