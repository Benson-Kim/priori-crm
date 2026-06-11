"""Export the OpenAPI JSON schema without starting the server or connecting to a DB."""

import json
import logging
import os
import sys

# Suppress all logging output before importing the app
logging.disable(logging.CRITICAL)

# Ensure we have a DATABASE_URL so pydantic settings don't fail
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "postgresql://x:x@localhost/x"

from app.main import app  # noqa: E402

schema = app.openapi()

# Write to a file directly to avoid any logging contamination in stdout
output_path = sys.argv[1] if len(sys.argv) > 1 else None
if output_path:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
else:
    json.dump(schema, sys.stdout, indent=2)
