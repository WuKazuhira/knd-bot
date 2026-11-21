# Adapted from AiriCore plugins/airi_status (MIT License)
from pathlib import Path

from config.path_config import DATA_PATH

_EXTERNAL_RESOURCES_DIR: Path = DATA_PATH / "resources" / "plugins" / "airi_status"
_FALLBACK_RESOURCES_DIR: Path = Path(__file__).parent / "resources"
resources_dir: Path = (
    _EXTERNAL_RESOURCES_DIR if _EXTERNAL_RESOURCES_DIR.exists() else _FALLBACK_RESOURCES_DIR
)

marker_img_path: Path = resources_dir / "images" / "marker.png"
mask_img_path: Path = resources_dir / "images" / "airi_status_mask.png"
bg_dir_path: Path = resources_dir / "images" / "background_new"
baotu_font_path: Path = resources_dir / "fonts" / "baotu.ttf"
spicy_font_path: Path = resources_dir / "fonts" / "SpicyRice-Regular.ttf"
adlam_font_path: Path = resources_dir / "fonts" / "ADLaMDisplay-Regular.ttf"
