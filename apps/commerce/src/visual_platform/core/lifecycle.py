"""Opt-in container evidence written only after API lifespan cleanup completes."""

import json
import os
from pathlib import Path

API_SHUTDOWN_MARKER = Path("/tmp/visual-api-shutdown.json")


def clear_api_shutdown_marker():
    if os.environ.get("CONTAINER_LIFECYCLE") == "1":
        API_SHUTDOWN_MARKER.unlink(missing_ok=True)


def record_api_shutdown():
    if os.environ.get("CONTAINER_LIFECYCLE") == "1":
        temporary = API_SHUTDOWN_MARKER.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "pid": os.getpid(), "lifespan_shutdown_completed": True})
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(API_SHUTDOWN_MARKER)
