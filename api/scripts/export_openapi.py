"""Export the FastAPI OpenAPI schema to a JSON file

Generates the contract the frontend's ``gen:api`` step consumes, *offline*:
it imports the app and serialises ``app.openapi()`` directly, so no running
server (and no database connection) is needed. This makes contract-type
generation deterministic and runnable in CI.

Usage:
    python -m scripts.export_openapi [output_path]

Defaults to writing ``openapi.json`` in the current working directory.
``app.openapi()`` does not depend on ``settings.is_development`` (which only
gates the served ``/openapi.json`` route), so the schema is produced in any
environment.
"""

import json
import sys
from pathlib import Path

from app.main import app


def export_openapi(output_path: Path) -> None:
    """Write the application's OpenAPI schema to ``output_path``."""
    schema = app.openapi()
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote OpenAPI schema to {output_path} ({len(schema['paths'])} paths)")


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    export_openapi(output)


if __name__ == "__main__":
    main()
