# Oncology Journey Command Center

A public portfolio demonstration of a synthetic-data oncology navigation workflow. It is not for patient care and must never receive real health information.

## Local setup

1. Copy `.env.example` to `.env` if local environment variables are needed.
2. Install web dependencies with `npm install`.
3. Install the Playwright browser with `npm exec --workspace apps/web playwright install chromium`.
4. Install API dependencies with `python -m pip install -e '.\\services\\api[dev]'`.
5. Start the local database with `docker compose up -d db`.
6. Run the full verification pipeline with `.\\scripts\\verify.ps1`.

The API health endpoint is available at `GET /health` and returns `{"status":"ok"}`.

## Product design

The approved design is available at [docs/product-design.md](docs/product-design.md).
