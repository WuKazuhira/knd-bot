# Adapted from AiriCore plugins/airi_status (MIT License)
from pathlib import Path

resources_dir: Path = Path(__file__).parent / "resources"
marker_img_path: Path = resources_dir / "images" / "marker.png"
mask_img_path: Path = resources_dir / "images" / "airi_status_mask.png"
bg_dir_path: Path = resources_dir / "images" / "background_new"
baotu_font_path: Path = resources_dir / "fonts" / "baotu.ttf"
spicy_font_path: Path = resources_dir / "fonts" / "SpicyRice-Regular.ttf"
adlam_font_path: Path = resources_dir / "fonts" / "ADLaMDisplay-Regular.ttf"
