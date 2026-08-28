from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from typing import Iterator, List, Optional, Set, Union

from fontTools.ttLib import TTFont
from matplotlib.font_manager import FontManager, FontProperties
from matplotlib.ft2font import FT2Font
from nonebot.log import logger
from PIL import ImageFont
from PIL.ImageFont import FreeTypeFont

from config.path_config import FONT_PATH

from .config import default_fallback_fonts
from .types import FontStyle, FontWeight


# Pillow 10 起删除了 getsize / getsize_multiline，但项目里三十多处排版仍按旧语义
# 计算坐标。这里照搬 Pillow 9 的实现补回来：走 C 层 font.getsize（仍然保留），
# 它返回的高度含 ascent/descent，和 getbbox 的裁剪高度不是一回事，用错会让文字偏移。
def _compat_getsize(self, text, direction=None, features=None, language=None, stroke_width=0):
    size, _offset = self.font.getsize(text, "L", direction, features, language)
    return size[0] + stroke_width * 2, size[1] + stroke_width * 2


def _compat_getsize_multiline(
    self, text, direction=None, spacing=4, features=None, language=None, stroke_width=0
):
    lines = text.split("\n")
    line_spacing = self.getsize("A", stroke_width=stroke_width)[1] + spacing
    max_width = max(
        (self.getsize(line, direction, features, language, stroke_width)[0] for line in lines),
        default=0,
    )
    return max_width, len(lines) * line_spacing - spacing


def _compat_bitmap_getsize(self, text, *args, **kwargs):
    return self.font.getsize(text)


if not hasattr(ImageFont.FreeTypeFont, 'getsize'):
    ImageFont.FreeTypeFont.getsize = _compat_getsize  # type: ignore[attr-defined]
if not hasattr(ImageFont.FreeTypeFont, 'getsize_multiline'):
    ImageFont.FreeTypeFont.getsize_multiline = _compat_getsize_multiline  # type: ignore[attr-defined]
if not hasattr(ImageFont.ImageFont, 'getsize'):
    ImageFont.ImageFont.getsize = _compat_bitmap_getsize  # type: ignore[attr-defined]


font_manager = FontManager()


def local_fonts() -> Iterator[str]:
    for f in FONT_PATH.iterdir():
        if f.is_file() and f.suffix in [".otf", ".ttf", ".ttc", ".afm"]:
            yield f.name


def add_font_to_manager(path: Union[str, Path]):
    try:
        if isinstance(path, Path):
            path = str(path.resolve())
        font_manager.addfont(path)
    except OSError as exc:
        logger.warning(f"Failed to open font file {path}: {exc}")
    except Exception as exc:
        logger.warning(f"Failed to extract font properties from {path}: {exc}")


# 彩色 emoji 字体是 CBDT/CBLC 位图格式，matplotlib 的 FT2Font 解析不了，
# 注册必定失败并在每次启动刷一条告警。这些字体由 find_special_font 按文件
# 路径直接加载，本来就不需要进 font_manager，跳过即可。
_UNMANAGEABLE_FONTS = {"NotoColorEmoji.ttf", "Apple Color Emoji.ttc"}

for fontname in local_fonts():
    if fontname in _UNMANAGEABLE_FONTS:
        continue
    add_font_to_manager(FONT_PATH / fontname)


class Font:
    def __init__(self, family: str, fontpath: Path, valid_size: Optional[int] = None):
        self.family = family
        """字体族名字"""
        self.path = fontpath.resolve()
        """字体文件路径"""
        self.valid_size = valid_size
        """某些字体不支持缩放，只能以特定的大小加载"""
        self._glyph_table: Set[int] = set()
        for table in TTFont(self.path, fontNumber=0)["cmap"].tables:  # type: ignore
            for key in table.cmap.keys():
                self._glyph_table.add(key)

    @classmethod
    @lru_cache()
    def find(
        cls,
        family: str,
        style: FontStyle = "normal",
        weight: FontWeight = "normal",
        fallback_to_default: bool = True,
    ) -> "Font":
        """查找插件路径下的字体"""
        font = cls.find_special_font(family)
        if font:
            return font
        font = cls.find_local_font(family)
        if font:
            return font
        font = cls.find_pil_font(family)
        if font:
            return font
        filepath = font_manager.findfont(
            FontProperties(family, style=style, weight=weight),  # type: ignore
            fallback_to_default=fallback_to_default,
        )
        font = FT2Font(filepath)
        return cls(font.family_name, Path(font.fname))

    @classmethod
    def find_local_font(cls, name: str) -> Optional["Font"]:
        """查找插件路径下的字体"""
        for fontname in local_fonts():
            if name == fontname or name == fontname.split(".")[0]:
                fontpath = FONT_PATH / fontname
                return cls(fontname, fontpath)

    @classmethod
    def find_pil_font(cls, name: str) -> Optional["Font"]:
        """通过 PIL ImageFont 查找系统字体"""
        try:
            font = ImageFont.truetype(name, 20)
            fontpath = Path(str(font.path))
            return cls(name, fontpath)
        except OSError:
            pass

    @classmethod
    def find_special_font(cls, family: str) -> Optional["Font"]:
        """查找特殊字体，主要是不可缩放的emoji字体"""
        SpecialFont = namedtuple("SpecialFont", ["family", "fontname", "valid_size"])
        SPECIAL_FONTS = {
            "Apple Color Emoji": SpecialFont(
                "Apple Color Emoji", "Apple Color Emoji.ttc", 96
            ),
            "Noto Color Emoji": SpecialFont(
                "Noto Color Emoji", "NotoColorEmoji.ttf", 109
            ),
        }
        if family in SPECIAL_FONTS:
            prop = SPECIAL_FONTS[family]
            fontname = prop.fontname
            valid_size = prop.valid_size
            fontpath = None
            if fontname in local_fonts():
                fontpath = FONT_PATH / fontname
            else:
                try:
                    font = ImageFont.truetype(fontname, valid_size)
                    fontpath = Path(str(font.path))
                except OSError:
                    pass
            if fontpath:
                return cls(family, fontpath, valid_size)

    @lru_cache()
    def load_font(self, fontsize: int) -> FreeTypeFont:
        """以指定大小加载字体"""
        return ImageFont.truetype(str(self.path), fontsize, encoding="utf-8")

    @lru_cache()
    def has_char(self, char: str) -> bool:
        """检查字体是否支持某个字符"""
        return ord(char) in self._glyph_table


def get_proper_font(
    char: str,
    style: FontStyle = "normal",
    weight: FontWeight = "normal",
    fontname: Optional[str] = None,
    fallback_fonts: List[str] = None,
) -> Font:
    """
    获取合适的字体，将依次检查备选字体是否支持想要的字符

    :参数:
        * ``char``: 字符
        * ``style``: 字体样式，默认为 "normal"
        * ``weight``: 字体粗细，默认为 "normal"
        * ``fontname``: 可选，指定首选字体
        * ``fallback_fonts``: 可选，指定备选字体
    """
    fallback_fonts = fallback_fonts or default_fallback_fonts.copy()
    if fontname:
        fallback_fonts.insert(0, fontname)

    for family in fallback_fonts:
        try:
            font = Font.find(family, style, weight, fallback_to_default=False)
        except ValueError as e:
            logger.info(str(e))
            try:
                default_fallback_fonts.remove(family)
            except:
                pass
            continue
        if font.has_char(char):
            return font
    return Font.find(fallback_fonts[0], style, weight)

