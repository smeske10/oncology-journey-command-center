# Oncology Journey Command Center

A public portfolio demonstration of a synthetic-data oncology navigation workflow. It is not for patient care and must never receive real health information.

## Local setup

1. Copy `.env.example` to `.env` if local environment variables are needed.
2. Install web dependencies with `npm install`.
3. Install the Playwright browser with `npm exec --workspace apps/web playwright install chromium`.
4. Install API dependencies with `python -m pip install --require-hashes -r .\\services\\api\\requirements.lock`, then install the local API without re-resolving dependencies using `python -m pip install --no-deps --no-build-isolation -e .\\services\\api`.
5. Start the local database with `docker compose up -d db`.
6. Run the full verification pipeline with `.\\scripts\\verify.ps1`.

The API health endpoint is available at `GET /health` and returns `{"status":"ok"}`.

## Synthetic patient demo

`/demo/patient` uses a same-origin `/api` rewrite, creates a short-lived synthetic demo session, then loads the current check-in definition before allowing submission. It requires the synthetic supporting-actor and active check-in definition created by the planned Task 12 seed/reset workflow; without that seed, the page shows the safe demo-unavailable state rather than accepting an unauthenticated submission. Configure `OJCC_API_ORIGIN` only for the server-side rewrite target; browser requests remain same-origin and credentialed.

## Product design

The approved design is available at [docs/product-design.md](docs/product-design.md).
