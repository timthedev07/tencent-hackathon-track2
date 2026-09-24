#!/usr/bin/env python3
"""Generate OpenAPI JSON from FastAPI app for frontend type generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api" / "src"))

from app.main import app


def main() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "packages" / "contracts" / "openapi"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "openapi.json"

    openapi_schema = app.openapi()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
