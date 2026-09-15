# Oncology Journey Command Center

A public portfolio demonstration of a synthetic-data oncology navigation workflow. It is not for patient care and must never receive real health information.

## Local setup

1. Copy `.env.example` to `.env` if local environment variables are needed.
2. Install web dependencies with `npm install`.
3. Install the Playwright browser with `npm exec --workspace apps/web playwright install chromium`.
4. Install API dependencies with `python -m pip install --require-hashes -r .\\services\\api\\requirements.lock`, then install the local API without re-resolving dependencies using `python -m pip install --no-deps --no-build-isolation -e .\\services\\api`.
5. Start the local database with `docker compose up -d db`.
6. Provision the local owner/API roles and two explicitly named disposable demo databases as
   described below. Reset the API database; reserve the second database for the live browser gate.
7. Run the full verifier with the live bootstrap, owner, API, and confirmation arguments shown
   below.

The API health endpoint is available at `GET /health` and returns `{"status":"ok"}`.

## Synthetic patient demo

`/demo/patient` uses a same-origin `/api` rewrite, creates a short-lived synthetic demo session, then loads the current check-in definition before allowing submission. It requires the deterministic synthetic supporting actor and active check-in definition created by the reset workflow; without that seed, the page shows the safe demo-unavailable state rather than accepting an unauthenticated submission. Configure `OJCC_API_ORIGIN` only for the server-side rewrite target; browser requests remain same-origin and credentialed.

`DEMO_ACTORS_JSON` is a server-only roster of the intended synthetic users. Generate it with the
connection-free seed CLI mode shown below; do not expose the roster, session secret, or database
URLs to the Next.js child process.

### Deterministic synthetic reset

The application, migration process, and bootstrap process use different credentials:

- `DATABASE_URL` is the non-owner `ojcc_api` login. It inherits the narrow `ojcc_app` privilege
  group but cannot administer or `SET ROLE` to that group.
- `MIGRATION_DATABASE_URL` is the non-superuser `ojcc_migrator` object owner. Locally and in CI it
  may create disposable databases, but it cannot create roles or become the bootstrap role.
- `BOOTSTRAP_DATABASE_URL` is the existing PostgreSQL administrator. It is used only to provision
  roles/databases and to replay immutable migration 0005, then removed from migration and runtime
  child environments.

The owner and API URLs must use different usernames and the same normalized host, port, and exact
database name. The reset validates all three URLs before connecting. It accepts only an explicit
loopback target named `ojcc_demo_<8-32 lowercase hex>` or `ojcc_task7_<8-32 lowercase hex>`, with
the name repeated as confirmation. It refuses persistent `ojcc`, remote hosts, unexpected ports,
query parameters, and target/confirmation mismatches before creating an engine or dropping a
schema.

Provision the fixed local roles from the bootstrap account. The command creates missing roles and
refuses an existing role with unexpected capabilities; it does not silently repair role drift.

```powershell
$provisioningBootstrapUrl = `
    "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/postgres"
Push-Location .\services\api
python -m scripts.provision_database_roles `
    --bootstrap-database-url $provisioningBootstrapUrl `
    --migration-role ojcc_migrator `
    --migration-password migrator-local-synthetic-only `
    --application-role ojcc_api `
    --application-password api-local-synthetic-only `
    --application-group ojcc_app
if ($LASTEXITCODE -ne 0) { throw "Local role provisioning failed" }
Pop-Location

$apiDatabaseName = "ojcc_demo_$([guid]::NewGuid().ToString('N'))"
$liveDatabaseName = "ojcc_demo_$([guid]::NewGuid().ToString('N'))"
if ($apiDatabaseName -eq $liveDatabaseName) { throw "Database names must be different" }
docker compose exec -T db createdb -U ojcc -O ojcc_migrator $apiDatabaseName
if ($LASTEXITCODE -ne 0) { throw "API database creation failed" }
docker compose exec -T db createdb -U ojcc -O ojcc_migrator $liveDatabaseName
if ($LASTEXITCODE -ne 0) { throw "Live database creation failed" }
$bootstrapDatabaseUrl = "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/$apiDatabaseName"
$migrationDatabaseUrl = "postgresql+psycopg://ojcc_migrator:migrator-local-synthetic-only@127.0.0.1:5432/$apiDatabaseName"
$applicationDatabaseUrl = "postgresql+psycopg://ojcc_api:api-local-synthetic-only@127.0.0.1:5432/$apiDatabaseName"
$liveBootstrapDatabaseUrl = "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/$liveDatabaseName"
$liveMigrationDatabaseUrl = "postgresql+psycopg://ojcc_migrator:migrator-local-synthetic-only@127.0.0.1:5432/$liveDatabaseName"
$liveApplicationDatabaseUrl = "postgresql+psycopg://ojcc_api:api-local-synthetic-only@127.0.0.1:5432/$liveDatabaseName"
.\\scripts\\reset_demo.ps1 `
    -BootstrapDatabaseUrl $bootstrapDatabaseUrl `
    -MigrationDatabaseUrl $migrationDatabaseUrl `
    -DatabaseUrl $applicationDatabaseUrl `
    -ConfirmDatabaseName $apiDatabaseName
$env:BOOTSTRAP_DATABASE_URL = $bootstrapDatabaseUrl
$env:MIGRATION_DATABASE_URL = $migrationDatabaseUrl
$env:DATABASE_URL = $applicationDatabaseUrl
$env:DEMO_ORGANIZATION_ID = "aeb456d4-3728-5f64-ac05-afed26cd0edc"
Push-Location .\services\api
try {
    $demoActorsJson = python -m scripts.seed_demo --print-demo-actors
    if ($LASTEXITCODE -ne 0) { throw "Synthetic demo actor configuration failed" }
}
finally {
    Pop-Location
}
$env:DEMO_ACTORS_JSON = $demoActorsJson
.\\scripts\\verify.ps1 `
    -LiveBootstrapDatabaseUrl $liveBootstrapDatabaseUrl `
    -LiveMigrationDatabaseUrl $liveMigrationDatabaseUrl `
    -LiveDatabaseUrl $liveApplicationDatabaseUrl `
    -LiveConfirmDatabaseName $liveDatabaseName
```

The reset recreates the schema at Alembic head, runs the synthetic fixed seed twice to prove
idempotency, and finishes with read-only integrity audits as both owner and API login. Fresh replay
runs migrations 0001–0004 as the owner. Immutable 0005 contains an `ALTER ROLE ... NOSUPERUSER`
statement that PostgreSQL 16 allows only a superuser to execute, even when removing that
capability, so the reset runs only 0005 through the bootstrap bridge, transfers its exact object
allowlist to the owner, removes the bootstrap URL, and returns to the owner for 0006 onward. It
never uses broad `REASSIGN OWNED`.

For a release system that requires offline SQL, generate the supported fresh-replay bundle rather
than executing raw `alembic upgrade head --sql` output:

```powershell
Push-Location .\services\api
python -m scripts.replay_schema `
    --bootstrap-database-url $env:BOOTSTRAP_DATABASE_URL `
    --migration-database-url $env:MIGRATION_DATABASE_URL `
    --database-url $env:DATABASE_URL `
    --revision head `
    --sql-output-directory $env:OJCC_OFFLINE_BUNDLE_DIRECTORY
Pop-Location
```

The new directory contains `manifest.json` plus `01-owner.sql`, `02-bootstrap.sql`, and
`03-owner.sql`. It contains database and role names but no URLs or passwords. Execute the files in
manifest order with owner, bootstrap, then owner credentials and stop on the first error (for
example, `psql -v ON_ERROR_STOP=1 ... -f <stage>`). Each file verifies its connected identity,
database, and starting revision before mutation. The bootstrap file performs the exact 0005
ownership transfer before its transaction commits. A failed stage rolls back only that stage;
inspect the current revision before resuming because earlier stages remain committed.

Raw `alembic upgrade head --sql` remains useful for inspection and generates without a database
connection, but it is not an executable fresh replay: it cannot perform the required credential
transition and 0005 ownership transfer. For a database already at 0006, the bounded
`0006_navigator_closed_loop:head --sql` artifact is executable as the migration owner.

The seed includes separate platform-user and patient identities, historical roles,
pathway/submission versions, open and closed work, every safety state, approval history, workflow
and knowledge lineage, and user, agent, policy, and system audit actors.

The reset and standalone seed temporarily remove every inherited process environment variable
whose name begins with `PG` (case-insensitive) before constructing an engine or starting migration,
seed, and audit child processes, then restore the exact prior values in `finally`. This prevents
libpq environment routing from overriding the single explicit, validated loopback URL.

The full verifier applies the same fail-closed validation to both credential triples before
importing the API or running a child process. It refuses missing, persistent, remote,
query-bearing, mismatched, or shared API/live targets. The live stage alone owns resets of the live
database; it runs FastAPI with only the API URL, gives Next.js no database credential, exercises
the real cookie-authenticated browser-to-PostgreSQL journey sequentially at desktop and mobile
widths, and audits integrity as the API login after each journey. It never resets the API test
database.

CI provisions and validates the same three roles before Alembic, generates two unique disposable
database names, and exports separate API/live credential triples. Outside this synthetic local/CI
workflow, provision roles and secrets in the database provider's credential manager; Alembic does
not create logins or passwords. Never commit or print a real password, and never provide the owner
or bootstrap URL to an API or web runtime. Cleanup uses ordinary `DROP DATABASE` only after owned
processes stop; if any connection or ownership is uncertain, leave the database for explicit
operator review rather than terminating sessions.

PostgreSQL 16 references: [object and role grants](https://www.postgresql.org/docs/16/sql-grant.html),
[database and schema privilege meanings](https://www.postgresql.org/docs/16/ddl-priv.html),
[role membership options](https://www.postgresql.org/docs/16/role-membership.html), and
[safe `SECURITY DEFINER` configuration](https://www.postgresql.org/docs/16/sql-createfunction.html).

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

## License and permitted use

Copyright © 2026 Vision Venture AI. All rights reserved.

This repository is **source-available, not open source**. Individuals may download, run locally,
and privately modify the project solely for personal evaluation, self-directed education, or
noncommercial personal research. Organizational, commercial, production, clinical, hosted-service,
and redistribution uses are not permitted without a separate written license from Vision Venture
AI. Third-party components remain subject to their own licenses.

See the [Vision Venture AI Individual Evaluation License 1.0](LICENSE.md) for the complete terms.

## Contributions

Issues and feedback are welcome. Pull requests and other code contributions are not accepted unless
Vision Venture AI first agrees to separate written contribution terms.
