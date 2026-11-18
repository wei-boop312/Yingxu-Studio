"""ComfyUI Workflow Architect extension entry point."""

from .workflow_architect.routes import register_routes

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

# Route registration is idempotent and intentionally does not fail outside ComfyUI.
register_routes()

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
