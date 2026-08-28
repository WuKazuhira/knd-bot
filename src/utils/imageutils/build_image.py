import base64
from colorsys import rgb_to_hls
from io import BytesIO
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PIL.Image import Image as IMG
from PIL.ImageColor import getrgb
from PIL.ImageDraw import ImageDraw as Draw

from .fonts import Font
from .text2image import Text2Image
from .types import *
from .utils import add_kndbot_watermark


class BuildImage:
    def __init__(self, image: IMG):
        self.image = image

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def size(self) -> SizeType:
        return self.image.size

    @property
    def mode(self) -> ModeType:
        return self.image.mode  # type: ignore

    @property
    def draw(self) -> Draw:
        return ImageDraw.Draw(self.image)

    @classmethod
    def new(
        cls, mode: ModeType, size: SizeType, color: Optional[ColorType] = None
    ) -> "BuildImage":
        return cls(Image.new(mode, size, color))  # type: ignore

    @classmethod
    def open(cls, file: Union[str, bytes, BytesIO, Path]) -> "BuildImage":
        return cls(Image.open(file))

    def copy(self) -> "BuildImage":
        return BuildImage(self.image.copy())

    def composite(
            self,
            image: Union[IMG, "BuildImage"],
            mask: Union[IMG, "BuildImage"]
    ) -> "BuildImage":
        """
        背景图粘贴顶层图片

        :参数:
          * ``image``: 顶层图片
          * ``mask``: 蒙版图片
        """
        if isinstance(image, BuildImage):
            image = image.image
        if isinstance(mask, BuildImage):
            mask = mask.image
        frame = self.image.copy()
        frame.paste(image, None, mask)
        return BuildImage(frame)

    def resize(
        self,
        size: SizeType,
        resample: ResampleType = Image.Resampling.LANCZOS,
        keep_ratio: bool = False,
        inside: bool = False,
        direction: DirectionType = "center",
        bg_color: Optional[ColorType] = None,
        **kwargs
    ) -> "BuildImage":
        """
        调整图片尺寸

        :参数:
          * ``size``: 期望图片大小
          * ``keep_ratio``: 是否保持长宽比，默认为 `False`
          * ``inside``: `keep_ratio` 为 `True` 时，
                        若 `inside` 为 `True`，则调整图片大小至包含于期望尺寸，不足部分设为指定颜色；
                        若 `inside` 为 `False`，则调整图片大小至包含期望尺寸，超出部分裁剪
          * ``direction``: 调整图片大小时图片的方位；默认为居中
          * ``bg_color``: 不足部分设置的颜色
        """
        width, height = size
        if keep_ratio:
            if inside:
                ratio = min(width / self.width, height / self.height)
            else:
                ratio = max(width / self.width, height / self.height)
            width = int(self.width * ratio)
            height = int(self.height * ratio)

        image = BuildImage(
            self.image.resize((width, height), resample=resample, **kwargs)
        )

        if keep_ratio:
            image = image.resize_canvas(size, direction, bg_color, **kwargs)
        return image

    def resize_canvas(
        self,
        size: SizeType,
        direction: DirectionType = "center",
        bg_color: Optional[ColorType] = None,
        **kwargs
    ) -> "BuildImage":
        """
        调整“画布”大小，超出部分裁剪，不足部分设为指定颜色

        :参数:
          * ``size``: 期望图片大小
          * ``direction``: 调整图片大小时图片的方位；默认为居中
          * ``bg_color``: 不足部分设置的颜色
        """
        w, h = size
        x = int((w - self.width) / 2)
        y = int((h - self.height) / 2)
        if direction in ["north", "northwest", "northeast"]:
            y = 0
        elif direction in ["south", "southwest", "southeast"]:
            y = h - self.height
        if direction in ["west", "northwest", "southwest"]:
            x = 0
        elif direction in ["east", "northeast", "southeast"]:
            x = w - self.width
        image = BuildImage.new(self.mode, size, bg_color)
        image.paste(self.image, (x, y))
        return image

    def resize_width(self, width: int, **kwargs) -> "BuildImage":
        """调整图片宽度，不改变长宽比"""
        return self.resize((width, int(self.height * width / self.width)), **kwargs)

    def resize_height(self, height: int, **kwargs) -> "BuildImage":
        """调整图片高度，不改变长宽比"""
        return self.resize((int(self.width * height / self.height), height), **kwargs)

    def rotate(
        self,
        angle: float,
        resample: ResampleType = Image.BICUBIC,
        expand: bool = False,
        **kwargs
    ) -> "BuildImage":
        """旋转图片"""
        image = BuildImage(
            self.image.rotate(angle, resample=resample, expand=expand, **kwargs)
        )
        return image

    def square(self) -> "BuildImage":
        """将图片裁剪为方形"""
        length = min(self.width, self.height)
        return self.resize_canvas((length, length))

    def circle(self) -> "BuildImage":
        """将图片裁剪为圆形"""
        image = self.square()
        mask = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((1, 1, image.size[0] - 2, image.size[1] - 2), 255)
        mask = mask.filter(ImageFilter.GaussianBlur(0))
        image.image.putalpha(mask)
        return image

    def circle_corner(self, r: int) -> "BuildImage":
        """将图片裁剪为圆角矩形"""
        image = self.convert("RGBA")
        w, h = image.size
        alpha = image.image.split()[-1]
        circle = Image.new("RGBA", (r * 2, r * 2), (0,0,0,255))  # 创建黑色方形
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, r * 2, r * 2), fill=255)  # 黑色方形内切白色圆形
        lt = circle.crop((0, 0, r, r))
        rt = circle.crop((r, 0, r * 2, r))
        rb = circle.crop((r, r, r * 2, r * 2))
        lb = circle.crop((0, r, r, r * 2))
        alpha.paste(lt, (0, 0),lt.split()[-1])  # 左上角
        alpha.paste(rt, (w - r, 0),rt.split()[-1])  # 右上角
        alpha.paste(rb, (w - r, h - r),rb.split()[-1])  # 右下角
        alpha.paste(lb, (0, h - r),lb.split()[-1])  # 左下角
        image.image.putalpha(alpha)
        return image

    def crop(self, box: BoxType) -> "BuildImage":
        """裁剪图片"""
        return BuildImage(self.image.crop(box))

    def convert(self, mode: ModeType, **kwargs) -> "BuildImage":
        return BuildImage(self.image.convert(mode, **kwargs))

    def paste(
        self,
        img: Union[IMG, "BuildImage"],
        pos: PosTypeInt = (0, 0),
        alpha: bool = False,
        below: bool = False,
        center_type: Optional[CenterType] = None,
    ) -> "BuildImage":
        """
        粘贴图片

        :参数:
          * ``img``: 待粘贴的图片
          * ``pos``: 粘贴位置
          * ``alpha``: 图片背景是否为透明
          * ``below``: 是否粘贴到底层
          * ``center_type``: 居中类型，可能的值 center: 完全居中，by_width: 水平居中，by_height: 垂直居中
        """
        if center_type is not None:
            width, height = pos
            if center_type not in ["center", "by_height", "by_width"]:
                raise ValueError("center_type must be 'center', 'by_width' or 'by_height'")
            if center_type == "center":
                width = int((self.width - img.width) / 2)
                height = int((self.height - img.height) / 2)
            elif center_type == "by_width":
                width = int((self.width - img.width) / 2)
                height = pos[1]
            elif center_type == "by_height":
                width = pos[0]
                height = int((self.height - img.height) / 2)
            pos = (width, height)

        if isinstance(img, BuildImage):
            img = img.image
        new_img = Image.new(self.mode, self.size) if below else self.image.copy()
        if alpha:
            img = img.convert("RGBA")
            new_img.paste(img, pos, mask=img)
        else:
            new_img.paste(img, pos)
        if below:
            new_img.paste(self.image, mask=self.image if self.mode == "RGBA" else None)
        self.image = new_img
        return self

    def filter(self, filter: str, aud: int = None) -> "BuildImage":
        """
        滤波
        :param filter_: 变化效果
        :param aud: 利率
        """
        try:
            _x = getattr(ImageFilter, filter)
        except AttributeError:
            _x = None
        if _x:
            if aud:
                self.image = self.image.filter(_x(aud))
                return BuildImage(self.image.filter(_x(aud)))
            else:
                self.image = self.image.filter(_x)
                return BuildImage(self.image.filter(_x))

    def transparent(self, alpha_ratio: float = 1, n: int = 0):
        """
        说明：
            图片透明化
        参数：
            :param alpha_ratio: 透明化程度
            :param n: 透明化大小内边距
        """
        self.image = self.image.convert("RGBA")
        x, y = self.image.size
        for i in range(n, x - n):
            for k in range(n, y - n):
                color = self.image.getpixel((i, k))
                color = color[:-1] + (int(100 * alpha_ratio),)
                self.image.putpixel((i, k), color)

    def transpose(self, method: TransposeType) -> "BuildImage":
        """变换"""
        return BuildImage(self.image.transpose(method))

    def perspective(self, points: PointsTYpe) -> "BuildImage":
        """
        透视变换

        :参数:
          * ``points``: 变换后点的位置，顺序依次为：左上->右上->右下->左下
        """

        def find_coeffs(pa: PointsTYpe, pb: PointsTYpe):
            matrix = []
            for p1, p2 in zip(pa, pb):
                matrix.append(
                    [p1[0], p1[1], 1, 0, 0, 0, -p2[0] * p1[0], -p2[0] * p1[1]]
                )
                matrix.append(
                    [0, 0, 0, p1[0], p1[1], 1, -p2[1] * p1[0], -p2[1] * p1[1]]
                )
            A = np.matrix(matrix, dtype=np.float32)
            B = np.array(pb).reshape(8)
            res = np.dot(np.linalg.inv(A.T * A) * A.T, B)
            return np.array(res).reshape(8)

        img_w, img_h = self.size
        points_w = [p[0] for p in points]
        points_h = [p[1] for p in points]
        new_w = int(max(points_w) - min(points_w))
        new_h = int(max(points_h) - min(points_h))
        p = ((0, 0), (img_w, 0), (img_w, img_h), (0, img_h))
        coeffs = find_coeffs(points, p)
        self.image.transform((new_w, new_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
        return BuildImage(
            self.image.transform((new_w, new_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
        )

    def gradient_color(
        self,
        start_color: ColorType,
        stop_color: ColorType,
        direction: OrientType = "vertical",
    ) -> "BuildImage":
        """
        渐变色

        :参数:
          * ``start_color``: 起始颜色
          * ``stop_color``: 终止颜色
          * ``direction``: 渐变方向，"vertical"：从上到下；"horizontal"：从左到右
        """
        frame = Image.new("RGBA", self.size, start_color)
        top = Image.new("RGBA", self.size, stop_color)
        mask = Image.new("L", self.size)
        mask_data = []
        if direction == "vertical":
            for y in range(self.height):
                mask_data.extend([int(255 * (y / self.height))] * self.width)
        else:
            mask_line = []
            for x in range(self.width):
                mask_line.append(int(255 * (x / self.width)))
            mask_data = mask_line * self.height
        mask.putdata(mask_data)
        frame.paste(top, mask=mask)
        return BuildImage(frame)

    def motion_blur(self, angle: float = 0, degree: int = 0) -> "BuildImage":
        """
        运动模糊

        :参数:
          * ``angle``: 运动方向
          * ``degree``: 模糊程度
        """
        if degree <= 1:
            return self.copy()

        kernel = np.eye(degree, dtype=np.float32)
        kernel_img = Image.fromarray((kernel * 255).astype(np.uint8), "L").rotate(
            angle + 45,
            resample=Image.Resampling.BICUBIC,
            center=(degree / 2, degree / 2),
            fillcolor=0,
        )
        kernel = np.asarray(kernel_img, dtype=np.float32)
        kernel_sum = float(kernel.sum())
        if kernel_sum == 0:
            return self.copy()
        kernel = (kernel / kernel_sum).ravel().tolist()

        image = self.image.convert("RGBA") if self.mode not in ("L", "RGB", "RGBA") else self.image
        blurred = image.filter(ImageFilter.Kernel((degree, degree), kernel, scale=1.0))
        return BuildImage(blurred)

    def distort(self, coefficients: DistortType) -> "BuildImage":
        """
        畸变

        :参数:
          * ``coefficients``: 畸变参数
        """
        coeffs = np.asarray(coefficients, dtype=np.float32).ravel()
        if coeffs.size == 0:
            return self.copy()

        k1 = coeffs[0] if coeffs.size > 0 else 0.0
        k2 = coeffs[1] if coeffs.size > 1 else 0.0
        p1 = coeffs[2] if coeffs.size > 2 else 0.0
        p2 = coeffs[3] if coeffs.size > 3 else 0.0
        k3 = coeffs[4] if coeffs.size > 4 else 0.0

        arr = np.asarray(self.image)
        h, w = arr.shape[:2]
        fy = fx = 100.0
        cx, cy = w / 2.0, h / 2.0
        yy, xx = np.indices((h, w), dtype=np.float32)
        x = (xx - cx) / fx
        y = (yy - cy) / fy
        r2 = x * x + y * y
        radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
        x_dist = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        y_dist = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        src_x = np.clip(np.rint(x_dist * fx + cx).astype(np.intp), 0, w - 1)
        src_y = np.clip(np.rint(y_dist * fy + cy).astype(np.intp), 0, h - 1)
        return BuildImage(Image.fromarray(arr[src_y, src_x].astype(np.uint8), self.mode))

    def color_mask(self, color: ColorType) -> "BuildImage":
        """
        颜色滤镜，改变图片色调

        :参数:
          * ``color``: 目标颜色
        """
        if isinstance(color, str):
            color = getrgb(color)
        r, g, b = color[:3]
        target_h, _, target_s = rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)

        arr = np.asarray(self.image.convert("RGB"), dtype=np.float32) / 255.0
        maxc = arr.max(axis=2)
        minc = arr.min(axis=2)
        lightness = (maxc + minc) / 2.0

        if target_s == 0:
            result = np.dstack((lightness, lightness, lightness))
        else:
            q = np.where(
                lightness < 0.5,
                lightness * (1.0 + target_s),
                lightness + target_s - lightness * target_s,
            )
            p = 2.0 * lightness - q

            def hue_to_rgb(t: np.ndarray) -> np.ndarray:
                t = np.mod(t, 1.0)
                return np.select(
                    [t < 1 / 6, t < 1 / 2, t < 2 / 3],
                    [p + (q - p) * 6.0 * t, q, p + (q - p) * (2 / 3 - t) * 6.0],
                    default=p,
                )

            result = np.dstack(
                (
                    hue_to_rgb(np.full_like(lightness, target_h + 1 / 3)),
                    hue_to_rgb(np.full_like(lightness, target_h)),
                    hue_to_rgb(np.full_like(lightness, target_h - 1 / 3)),
                )
            )
        return BuildImage(Image.fromarray(np.clip(result * 255.0, 0, 255).astype(np.uint8), "RGB"))

    def draw_point(
        self, pos: PosTypeFloat, fill: Optional[ColorType] = None
    ) -> "BuildImage":
        """在图片上画点"""
        self.draw.point(pos, fill=fill)
        return self

    def draw_line(
        self,
        xy: XYType,
        fill: Optional[ColorType] = None,
        width: float = 1,
    ) -> "BuildImage":
        """在图片上画直线"""
        self.draw.line(xy, fill=fill, width=width)
        return self

    def draw_rectangle(
        self,
        xy: XYType,
        fill: Optional[ColorType] = None,
        outline: Optional[ColorType] = None,
        width: float = 1,
    ) -> "BuildImage":
        """在图片上画矩形"""
        self.draw.rectangle(xy, fill, outline, width)
        return self

    def draw_rounded_rectangle(
        self,
        xy: XYType,
        radius: int = 0,
        fill: Optional[ColorType] = None,
        outline: Optional[ColorType] = None,
        width: float = 1,
    ) -> "BuildImage":
        """在图片上画圆角矩形"""
        self.draw.rounded_rectangle(xy, radius, fill, outline, width)
        return self

    def draw_polygon(
        self,
        xy: List[PosTypeFloat],
        fill: Optional[ColorType] = None,
        outline: Optional[ColorType] = None,
        width: float = 1,
    ) -> "BuildImage":
        """在图片上画多边形"""
        self.draw.polygon(xy, fill, outline, width)
        return self

    def draw_arc(
        self,
        xy: XYType,
        start: float,
        end: float,
        fill: Optional[ColorType] = None,
        width: float = 1,
    ) -> "BuildImage":
        """在图片上画圆弧"""
        self.draw.arc(xy, start, end, fill, width)
        return self

    def draw_ellipse(
        self,
        xy: XYType,
        fill: Optional[ColorType] = None,
        outline: Optional[ColorType] = None,
        width: float = 1,
    ) -> "BuildImage":
        """在图片上画圆"""
        self.draw.ellipse(xy, fill, outline, width)
        return self

    def draw_text_raw(
        self,
        xy: XYType,text:str,
        fontsize: int = 25,
        fontname: str = "SourceHanSansCN-Medium.otf",
        fill: ColorType = "black",
        halign: HAlignType = "left",
        valign: VAlignType = "top",
        warp_align: HAlignType = "left",
    ):
        """
        使用原始ImageFont在图片上指定区域画文字

        :参数:
          * ``xy``: 文字区域，顺序依次为 左，上，右，下|或者只提供 左，上 位置也可以
          * ``text``: 文字，支持多行
          * ``fill``: 文字颜色
          * ``halign``: 水平对齐方式
          * ``valign``: 垂直对齐方式
          * ``warp_align``: 垂直对齐方式
          * ``fontname``: 指定首选字体
        """
        font = Font.find(fontname)
        font = ImageFont.truetype(str(font.path), size=fontsize)
        lines = text.split('\n')
        max_width = xy[2]-xy[0] if len(xy)==4 else None
        if max_width is not None:
            def wrap(line, max_width):
                (_w, _), (_, _) = font.font.getsize(line)
                last_idx = 0
                for idx in range(len(line)):
                    (_tmp_w, _), (_, _) = font.font.getsize(line[last_idx: idx + 1])
                    if _tmp_w > max_width:
                        yield line[last_idx:idx]
                        last_idx = idx
                yield line[last_idx:]

            new_lines = []
            for line in lines:
                l = wrap(line, max_width)
                new_lines.extend(l)
            lines = new_lines
        text = "\n".join(lines)
        size = font.getsize(text)
        size = (size[0], size[1]*len(lines))
        offset_x = offset_y = 0
        if len(xy) == 4:
            if halign == 'left':
                offset_x = 0
            elif halign == 'center':
                offset_x = (xy[2]-xy[0]-size[0])//2
            else:
                offset_x = xy[2]-xy[0]-size[0]
            if valign == 'top':
                offset_y = 0
            elif valign == 'center':
                offset_y = (xy[3]-xy[1]-size[1])//2
            else:
                offset_y = xy[3]-xy[1]-size[1]
        offset_x = offset_x + xy[0] if offset_x > 0 else xy[0]
        offset_y = offset_y + xy[1] if offset_y > 0 else xy[1]
        draw = ImageDraw.Draw(self.image)
        pos = (offset_x, offset_y)
        draw.text(pos, text, fill, font, align=warp_align)
        return self

    def draw_text(
        self,
        xy: XYType,
        text: str,
        fontsize: Optional[int] = None,
        max_fontsize: int = 30,
        min_fontsize: int = 12,
        style: FontStyle = "normal",
        weight: FontWeight = "normal",
        allow_wrap: bool = False,
        fill: ColorType = "black",
        spacing: int = 4,
        halign: HAlignType = "left",
        valign: VAlignType = "top",
        lines_align: HAlignType = "left",
        stroke_ratio: float = 0,
        stroke_fill: Optional[ColorType] = None,
        fontname: str = "",
        fallback_fonts: List[str] = None,
        ischeckchar: bool = True
    ) -> "BuildImage":
        """
        在图片上指定区域画文字

        :参数:
          * ``xy``: 文字区域，顺序依次为 左，上，右，下|或者只提供 左，上 位置也可以
          * ``text``: 文字，支持多行
          * ``max_fontsize``: 允许的最大字体大小
          * ``min_fontsize``: 允许的最小字体大小
          * ``allow_wrap``: 是否允许折行
          * ``style``: 字体样式，默认为 "normal"
          * ``weight``: 字体粗细，默认为 "normal"
          * ``fill``: 文字颜色
          * ``spacing``: 多行文字间距
          * ``halign``: 横向对齐方式
          * ``valign``: 纵向对齐方式
          * ``lines_align``: 多行文字对齐方式，默认为靠左
          * ``stroke_ratio``: 文字描边的比例，即 描边宽度 / 字体大小
          * ``stroke_fill``: 描边颜色
          * ``fontname``: 指定首选字体
          * ``fallback_fonts``: 指定备选字体
          * ``ischeckchar``: 检查每个字符在当前字体下是否存在，仅在fontname存在时有效
        """

        if fallback_fonts is None:
            fallback_fonts = []
        if len(xy) == 4:
            left = xy[0]
            top = xy[1]
            width = xy[2] - xy[0]
            height = xy[3] - xy[1]
        else:
            left = xy[0]
            top = xy[1]
            width = max_fontsize * len(max(text.split('\n'), key=len))
            height = max_fontsize * len(text.split('\n'))
        if fontsize is None:
            fontsize = max_fontsize
            while True:
                text2img = Text2Image.from_text(
                    text,
                    fontsize,
                    style,
                    weight,
                    fill,
                    spacing,
                    lines_align,
                    int(fontsize * stroke_ratio),
                    stroke_fill,
                    fontname,
                    fallback_fonts,
                    ischeckchar
                )
                text_w = text2img.width
                text_h = text2img.height
                if text_w > width and allow_wrap:
                    text2img.wrap(width)
                    text_w = text2img.width
                    text_h = text2img.height
                if text_w > width or text_h > height:
                    fontsize -= 1
                    if fontsize < min_fontsize:
                        raise ValueError("在指定的区域和字体大小范围内画不下这段文字")
                else:
                    x = left  # "left"
                    if halign == "center":
                        x += (width - text_w) / 2
                    elif halign == "right":
                        x += width - text_w
                    y = top  # "top"
                    if valign == "center":
                        y += (height - text_h) / 2
                    elif valign == "bottom":
                        y += height - text_h

                    self.paste(text2img.to_image(), (int(x), int(y)), alpha=True)
                    return self
        else:
            text2img = Text2Image.from_text(
                text,
                fontsize,
                style,
                weight,
                fill,
                spacing,
                lines_align,
                int(fontsize * stroke_ratio),
                stroke_fill,
                fontname,
                fallback_fonts,
                ischeckchar
            )
            text_w = text2img.width
            text_h = text2img.height
            x = left  # "left"
            if halign == "center":
                x += (width - text_w) / 2
            elif halign == "right":
                x += width - text_w
            y = top  # "top"
            if valign == "center":
                y += (height - text_h) / 2
            elif valign == "bottom":
                y += height - text_h
            self.paste(text2img.to_image(), (int(x), int(y)), alpha=True)
            return self

    def save(self, format: str, **params) -> BytesIO:
        """
        :param format: 储存格式 "PNG" "JPG"
        """
        output = BytesIO()
        self.image.save(output, format, **params)
        return output

    def save_jpg(self) -> BytesIO:
        output = BytesIO()
        image = self.image.convert("RGB")
        image.save(output, format="jpeg")
        return output

    def save_png(self) -> BytesIO:
        output = BytesIO()
        image = self.image.convert("RGBA")
        image.save(output, format="png")
        return output

    def save_file(self, path: Optional[Union[str, Path]]):
        """
        说明：
            保存图片
        参数：
            :param path: 图片路径
        """
        self.image.save(path)

    def pic2bs4(self) -> str:
        """
        说明：
            BuildImage 转 base64
        """
        buf = BytesIO()
        add_kndbot_watermark(self.image).save(buf, format="PNG")
        base64_str = base64.b64encode(buf.getvalue()).decode()
        return base64_str

    def show(self):
        """
        说明：
            显示图片，用于调试
        """
        self.image.show()