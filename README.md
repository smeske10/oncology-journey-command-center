# Oncology Journey Command Center

A public portfolio demonstration of a synthetic-data oncology navigation workflow. It is not for patient care and must never receive real health information.

## Local setup

1. Copy `.env.example` to `.env` if local environment variables are needed.
2. Install web dependencies with `npm install`.
3. Install the Playwright browser with `npm exec --workspace apps/web playwright install chromium`.
4. Install API dependencies with `python -m pip install --require-hashes -r .\\services\\api\\requirements.lock`, then install the local API without re-resolving dependencies using `python -m pip install --no-deps --no-build-isolation -e .\\services\\api`.
5. Start the local database with `docker compose up -d db`.
6. Create two different explicitly named disposable demo databases as described below. Reset the
   API database and set `DATABASE_URL` to it; reserve the second database for the live browser gate.
7. Run the full verification pipeline with both mandatory live-database arguments:
   `.\\scripts\\verify.ps1 -LiveDatabaseUrl $liveDatabaseUrl -LiveConfirmDatabaseName $liveDatabaseName`.

The API health endpoint is available at `GET /health` and returns `{"status":"ok"}`.

## Synthetic patient demo

`/demo/patient` uses a same-origin `/api` rewrite, creates a short-lived synthetic demo session, then loads the current check-in definition before allowing submission. It requires the deterministic synthetic supporting actor and active check-in definition created by the reset workflow; without that seed, the page shows the safe demo-unavailable state rather than accepting an unauthenticated submission. Configure `OJCC_API_ORIGIN` only for the server-side rewrite target; browser requests remain same-origin and credentialed.

### Deterministic synthetic reset

The reset is destructive and therefore accepts only an explicit loopback PostgreSQL URL whose
database name is `ojcc_demo_<8-32 lowercase hex>` or `ojcc_task7_<8-32 lowercase hex>`. The
database name must also be repeated as confirmation. It refuses the persistent `ojcc` database,
remote hosts, omitted or unexpected ports, all URL query parameters, and confirmation mismatches
before creating a database engine or dropping any schema.

```powershell
$apiDatabaseName = "ojcc_demo_$([guid]::NewGuid().ToString('N'))"
$liveDatabaseName = "ojcc_demo_$([guid]::NewGuid().ToString('N'))"
if ($apiDatabaseName -eq $liveDatabaseName) { throw "Database names must be different" }
docker compose exec -T db createdb -U ojcc $apiDatabaseName
if ($LASTEXITCODE -ne 0) { throw "API database creation failed" }
docker compose exec -T db createdb -U ojcc $liveDatabaseName
if ($LASTEXITCODE -ne 0) { throw "Live database creation failed" }
$apiDatabaseUrl = "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/$apiDatabaseName"
$liveDatabaseUrl = "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/$liveDatabaseName"
.\\scripts\\reset_demo.ps1 -DatabaseUrl $apiDatabaseUrl -ConfirmDatabaseName $apiDatabaseName
$env:DATABASE_URL = $apiDatabaseUrl
$env:DEMO_ORGANIZATION_ID = "aeb456d4-3728-5f64-ac05-afed26cd0edc"
.\\scripts\\verify.ps1 `
    -LiveDatabaseUrl $liveDatabaseUrl `
    -LiveConfirmDatabaseName $liveDatabaseName
```

The reset recreates the schema at Alembic head, runs the entirely synthetic fixed seed twice to
prove idempotency, and finishes with the read-only integrity audit. The seed includes separate
platform-user and patient identities, historical roles, pathway/submission versions, open and
closed work, every safety state, approval history, workflow and knowledge lineage, and user,
agent, policy, and system audit actors.

The reset and standalone seed temporarily remove every inherited process environment variable
whose name begins with `PG` (case-insensitive) before constructing an engine or starting migration,
seed, and audit child processes, then restore the exact prior values in `finally`. This prevents
libpq environment routing from overriding the single explicit, validated loopback URL.

The full verifier applies the same fail-closed validation to both targets before importing the API
or running a child process. It refuses missing, persistent, remote, query-bearing, mismatched, or
shared API/live targets. The live stage alone owns resets of the live database; it runs the real
cookie-authenticated browser-to-PostgreSQL journey sequentially at desktop and mobile widths and
audits integrity after each journey. It never resets the API test database.

### Delivered closed-loop demonstration

The seeded transportation story demonstrates exact patient evidence, policy and resource review,
explicit navigator approval, governed claim/start/complete transitions, patient follow-up, an
authorized closing Outcome, queue removal, and audience-safe retained history after reload. The
live gate uses the real API, same-origin Next.js rewrite, signed synthetic sessions, and PostgreSQL;
the three existing route-mocked browser tests remain separate smoke coverage.

Remaining limits are intentional: entry is seeded rather than created automatically from a new
check-in; proposal revision and reassignment are not implemented; the demo performs no external
outreach or transportation booking; historical unbound tasks remain read-only; patient history
uses controlled labels; and real-data operation, production deployment, and clinical-use claims
remain out of scope.

### Restore and bulk-load integrity audit

Run the audit after every restore, ETL, or bulk load that used
`session_replication_role = replica`. That PostgreSQL setting disables ordinary lifecycle triggers,
so successfully loading a backup does not prove that its domain history is coherent.

```powershell
Push-Location .\\services\\api
python scripts/check_integrity.py --database-url $env:DATABASE_URL
Pop-Location
```

The command prints machine-readable JSON, exits nonzero for any violation, and is strictly
read-only: it never repairs history, invents an approval, or chooses between competing terminal
records. A failure requires an explicit, audited reconciliation before application writes reopen.

## Product design

The approved design is available at [docs/product-design.md](docs/product-design.md).
