from config.path_config import RESOURCE_PATH
from utils.imageutils import BuildImage

data_path = RESOURCE_PATH / "image" / "memes"


def load_image(path: str) -> BuildImage:
    return BuildImage.open(data_path / "images" / path)


def load_thumb(path: str) -> BuildImage:
    return BuildImage.open(data_path / "thumbs" / path)
