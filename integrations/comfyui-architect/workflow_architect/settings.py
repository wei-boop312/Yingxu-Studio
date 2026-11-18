from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
CAPABILITY_FILE = TEMPLATE_DIR / "capabilities.yaml"
CATALOG_FILE = TEMPLATE_DIR / "catalog.json"

MAX_TEXT_LENGTH = 4000
MIN_DIMENSION = 64
MAX_DIMENSION = 8192
MAX_PIXELS = 16_777_216
DEFAULT_WIDTH = 768
DEFAULT_HEIGHT = 1024
DEFAULT_FORMAT = "png"
API_BODY_LIMIT = 1_000_000

GENERATOR_VERSION = "2.15.2"
SUPPORTED_TASKS = {
    "text_to_image",
    "image_to_image",
    "inpaint",
    "image_resize",
    "image_upscale",
    "virtual_tryon",
    "ecommerce_detail",
}
LOW_VRAM_MAX_PIXELS = 1_048_576
