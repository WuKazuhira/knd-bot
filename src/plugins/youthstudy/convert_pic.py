import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.path_config import DATA_PATH

_EXTERNAL_RESOURCE_DIR: Path = DATA_PATH / "resources" / "plugins" / "youthstudy"
_FALLBACK_RESOURCE_DIR: Path = Path(__file__).parent / "resource"
_RESOURCE_DIR: Path = (
    _EXTERNAL_RESOURCE_DIR if _EXTERNAL_RESOURCE_DIR.exists() else _FALLBACK_RESOURCE_DIR
)

font_path = str(_RESOURCE_DIR / "font" / "font.otf")
icon_path = str(_RESOURCE_DIR / "bac" / "bac.png")


def size(size: int) -> ImageFont:
    return ImageFont.truetype(font_path, size)


async def convert_pic(text):
    img = Image.new('RGB', (902, 987), (255, 255, 255))
    icon = Image.open(icon_path)
    img.paste(icon, (0, 0))
    draw = ImageDraw.Draw(img)
    title = ""
    period = ""
    for i in text["title"]:
        title += i + f"\n"
    for i in text["period"]:
        period += i + f"\n"
    draw.multiline_text((10, 10), title, fill='gold', font=size(55))
    draw.multiline_text((100, 80), period, fill='gold', font=size(50))
    j = 0
    for i in text["answer"]:
        draw.multiline_text((200 + j * 150, 40 if j == 0 else 90), i, fill='gold', font=size(40))
        j += 1
    draw.multiline_text((200, 700), text["end_time"], fill='gold', font=size(40))
    buf = BytesIO()
    img.save(buf, format="PNG")
    base64_str = base64.b64encode(buf.getbuffer()).decode()
    return "base64://" + base64_str
