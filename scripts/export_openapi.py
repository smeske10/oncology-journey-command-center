"""Export the disabled-docs API schema as a checked-in contract artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.openapi.utils import get_openapi

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "api"))

from app.main import app


def main() -> None:
    schema = get_openapi(title=app.title, version="0.1.0", routes=app.routes)
    schema["info"]["description"] = (
        "Synthetic demonstration API contract. It is not for patient care and does not accept real PHI."
    )
    contract_path = REPOSITORY_ROOT / "contracts" / "openapi.json"
    contract_path.parent.mkdir(exist_ok=True)
    contract_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
