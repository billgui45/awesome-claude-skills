#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.1",
#     "pillow-heif>=0.16",
#     "numpy>=1.24",
# ]
# ///
"""Image editing CLI for Claude Code.

Transforms, adjustments, effects, watermarks, batch processing, format
conversion, transparency operations and file-size optimization. Every run
prints a structured JSON report (to stdout, or to stderr when the image
itself is written to stdout with ``--output=-``).

Run with:  uv run image_edit.py INPUT [OPTIONS]
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import (
    ExifTags,
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    ImageOps,
)

try:  # HEIC/HEIF support is optional at import time; reported when needed.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # pragma: no cover - depends on environment
    HEIF_AVAILABLE = False

Image.MAX_IMAGE_PIXELS = None  # trust local files; large scans are common

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

EXT_TO_FORMAT = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".heic": "HEIF",
    ".heif": "HEIF",
}
FORMAT_TO_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "TIFF": ".tiff", "HEIF": ".heic"}
FORMAT_ALIASES = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "tif": "TIFF",
    "tiff": "TIFF",
    "heic": "HEIF",
    "heif": "HEIF",
}
ALPHA_FORMATS = {"PNG", "WEBP", "TIFF", "HEIF"}
QUALITY_FORMATS = {"JPEG", "WEBP", "HEIF"}
DEFAULT_QUALITY = {"JPEG": 95, "WEBP": 90, "HEIF": 90}
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")
COLOR_SPACES = {"RGB": "RGB", "RGBA": "RGBA", "CMYK": "CMYK", "L": "L", "GRAY": "L", "GRAYSCALE": "L"}
ORIENTATION_TAG = 0x0112
EXIF_IFD_TAG = 0x8769
GPS_IFD_TAG = 0x8825

FONT_CANDIDATES = (
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:\\Windows\\Fonts\\arial.ttf",
    "Arial.ttf",
)


class ImageEditError(Exception):
    """A user-facing error reported as JSON."""


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def parse_color(value: str) -> tuple[int, ...]:
    """Parse a color into an RGBA tuple.

    Accepts CSS names, #RGB/#RRGGBB/#RRGGBBAA, ``R,G,B[,A]``, ``rgb(...)``,
    ``hsl(...)`` and ``transparent``.
    """
    text = value.strip()
    if text.lower() == "transparent":
        return (0, 0, 0, 0)
    if "," in text and "(" not in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) not in (3, 4):
            raise ImageEditError(f"Invalid color '{value}': expected R,G,B or R,G,B,A")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            raise ImageEditError(f"Invalid color '{value}': components must be integers") from None
        if any(n < 0 or n > 255 for n in nums):
            raise ImageEditError(f"Invalid color '{value}': components must be 0-255")
        return tuple(nums) if len(nums) == 4 else (*nums, 255)
    try:
        rgb = ImageColor.getrgb(text)
    except ValueError:
        raise ImageEditError(f"Invalid color '{value}'") from None
    return rgb if len(rgb) == 4 else (*rgb, 255)


def parse_size_pair(value: str, name: str) -> tuple[int, int]:
    parts = value.lower().replace("x", ",").split(",")
    try:
        w, h = (int(p) for p in parts)
    except ValueError:
        raise ImageEditError(f"{name} expects W,H (e.g. 300,300), got '{value}'") from None
    if w <= 0 or h <= 0:
        raise ImageEditError(f"{name} dimensions must be positive")
    return w, h


def parse_edges(value: str, name: str) -> tuple[int, int, int, int]:
    """Parse ``N`` or ``LEFT,TOP,RIGHT,BOTTOM`` into a 4-tuple."""
    parts = [p.strip() for p in value.split(",")]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise ImageEditError(f"{name} expects N or LEFT,TOP,RIGHT,BOTTOM, got '{value}'") from None
    if len(nums) == 1:
        nums = nums * 4
    elif len(nums) == 2:  # horizontal, vertical
        nums = [nums[0], nums[1], nums[0], nums[1]]
    elif len(nums) != 4:
        raise ImageEditError(f"{name} expects 1, 2 or 4 values, got {len(nums)}")
    if any(n < 0 for n in nums):
        raise ImageEditError(f"{name} values must be non-negative")
    return tuple(nums)  # type: ignore[return-value]


def format_from_path(path: str | Path) -> str | None:
    return EXT_TO_FORMAT.get(Path(path).suffix.lower())


# --------------------------------------------------------------------------
# Mode helpers
# --------------------------------------------------------------------------


def has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        img.mode == "P" and "transparency" in img.info
    )


def normalize_mode(img: Image.Image) -> Image.Image:
    """Convert any input mode to RGB or RGBA for a predictable pipeline."""
    if img.mode in ("RGB", "RGBA"):
        return img
    if img.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        arr = np.asarray(img, dtype=np.float64)
        if img.mode.startswith("I;16") or (img.mode == "I" and arr.max(initial=0) > 255):
            arr = arr / 257.0
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, "L").convert("RGB")
    if has_alpha(img):
        return img.convert("RGBA")
    return img.convert("RGB")


def map_rgb(img: Image.Image, fn: Callable[[Image.Image], Image.Image]) -> Image.Image:
    """Apply ``fn`` to the color channels only, preserving any alpha channel."""
    if has_alpha(img):
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        out = fn(rgba.convert("RGB")).convert("RGB")
        out.putalpha(alpha)
        return out
    return fn(img if img.mode == "RGB" else img.convert("RGB"))


def flatten(img: Image.Image, color: tuple[int, ...] = (255, 255, 255, 255)) -> Image.Image:
    """Composite an image with alpha onto a solid background."""
    if not has_alpha(img):
        return img
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (*color[:3], 255))
    bg.alpha_composite(rgba)
    return bg.convert("RGB")


# --------------------------------------------------------------------------
# Loading and info
# --------------------------------------------------------------------------


def open_image(path: str | Path) -> Image.Image:
    p = Path(path)
    if not p.exists():
        raise ImageEditError(f"No such file or directory: {p}")
    if p.is_dir():
        raise ImageEditError(f"Input is a directory, not an image: {p}")
    if p.suffix.lower() in (".heic", ".heif") and not HEIF_AVAILABLE:
        raise ImageEditError("HEIC/HEIF support requires pillow-heif (run via `uv run` to auto-install)")
    try:
        img = Image.open(p)
        img.load()
    except Exception as exc:
        raise ImageEditError(f"Cannot open image {p}: {exc}") from None
    return img


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        if len(value) > 64:
            return f"<{len(value)} bytes>"
        try:
            text = value.decode("ascii").strip("\x00 ")
            if text.isprintable():
                return text
        except UnicodeDecodeError:
            pass
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    try:
        f = float(value)  # IFDRational and friends
        return round(f, 6) if math.isfinite(f) else str(value)
    except (TypeError, ValueError):
        return str(value)


def exif_to_dict(exif: Image.Exif) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag, value in exif.items():
        if tag in (EXIF_IFD_TAG, GPS_IFD_TAG):
            continue
        out[ExifTags.TAGS.get(tag, f"Tag{tag}")] = _jsonable(value)
    try:
        for tag, value in exif.get_ifd(EXIF_IFD_TAG).items():
            if tag == 0x927C:  # MakerNote: large, vendor-specific blob
                continue
            out[ExifTags.TAGS.get(tag, f"Tag{tag}")] = _jsonable(value)
    except Exception:
        pass
    try:
        gps = exif.get_ifd(GPS_IFD_TAG)
        if gps:
            out["GPSInfo"] = {ExifTags.GPSTAGS.get(t, f"Tag{t}"): _jsonable(v) for t, v in gps.items()}
    except Exception:
        pass
    return out


def image_info(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    img = open_image(p)
    info: dict[str, Any] = {
        "path": str(p),
        "format": img.format,
        "mode": img.mode,
        "width": img.width,
        "height": img.height,
        "has_alpha": has_alpha(img),
        "file_size_bytes": p.stat().st_size,
        "file_size_mb": round(p.stat().st_size / 1_000_000, 4),
        "frames": getattr(img, "n_frames", 1),
        "icc_profile": bool(img.info.get("icc_profile")),
    }
    dpi = img.info.get("dpi")
    if dpi:
        info["dpi"] = [round(float(d), 2) for d in dpi]
    exif = exif_to_dict(img.getexif())
    info["exif"] = exif
    if "Orientation" in exif:
        info["orientation"] = exif["Orientation"]
    return info


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------


def op_rotate(img: Image.Image, angle: float, transparent_ok: bool) -> Image.Image:
    norm = angle % 360
    if norm == 0:
        return img
    lossless = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}
    if norm in lossless:
        return img.transpose(lossless[int(norm)])
    if transparent_ok and img.mode != "RGBA":
        img = img.convert("RGBA")
    fill = (0, 0, 0, 0) if img.mode == "RGBA" else (255, 255, 255)
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=fill)


def op_flip(img: Image.Image, direction: str) -> Image.Image:
    if direction == "horizontal":
        return ImageOps.mirror(img)
    return ImageOps.flip(img)


def op_autocrop_transparency(img: Image.Image, threshold: float, warnings: list[str]) -> Image.Image:
    if not has_alpha(img):
        warnings.append("--autocrop-transparency skipped: image has no alpha channel")
        return img
    alpha = img.convert("RGBA").getchannel("A")
    cutoff = int(round(threshold / 100 * 255))
    bbox = alpha.point(lambda a: 255 if a > cutoff else 0).getbbox()
    if bbox is None:
        warnings.append("--autocrop-transparency skipped: image is fully transparent at this threshold")
        return img
    return img.crop(bbox)


def op_crop(img: Image.Image, edges: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = edges
    w, h = img.size
    if left + right >= w or top + bottom >= h:
        raise ImageEditError(f"--crop {edges} would remove the entire {w}x{h} image")
    return img.crop((left, top, w - right, h - bottom))


def op_resize(img: Image.Image, width: int | None, height: int | None) -> Image.Image:
    w, h = img.size
    if width and height:
        scale = min(width / w, height / h)
    elif width:
        scale = width / w
    else:
        scale = height / h  # type: ignore[operator]
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    if new_size == img.size:
        return img
    return img.resize(new_size, Image.Resampling.LANCZOS)


def op_thumbnail(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _match_color_mode(img: Image.Image, color: tuple[int, ...]) -> tuple[Image.Image, tuple[int, ...]]:
    """Return an image/color pair whose modes agree (adds alpha if needed)."""
    if color[3] < 255 and img.mode != "RGBA":
        img = img.convert("RGBA")
    return img, (color if img.mode == "RGBA" else color[:3])


def op_pad(img: Image.Image, edges: tuple[int, int, int, int], color: tuple[int, ...], edge: bool) -> Image.Image:
    left, top, right, bottom = edges
    if edge:
        arr = np.asarray(img)
        pad = ((top, bottom), (left, right)) + ((0, 0),) * (arr.ndim - 2)
        return Image.fromarray(np.pad(arr, pad, mode="edge"), img.mode)
    img, fill = _match_color_mode(img, color)
    return ImageOps.expand(img, border=edges, fill=fill)


def op_border(img: Image.Image, px: int, color: tuple[int, ...], inside: bool) -> Image.Image:
    img, fill = _match_color_mode(img, color)
    if not inside:
        return ImageOps.expand(img, border=px, fill=fill)
    if px * 2 >= min(img.size):
        raise ImageEditError(f"--border {px} --border-inside is too thick for a {img.width}x{img.height} image")
    out = img.copy()
    draw = ImageDraw.Draw(out)
    draw.rectangle((0, 0, out.width - 1, out.height - 1), outline=fill, width=px)
    return out


def op_enhance(img: Image.Image, enhancer: type, factor: float) -> Image.Image:
    return map_rgb(img, lambda rgb: enhancer(rgb).enhance(factor))


def op_blur(img: Image.Image, radius: float) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius))


def op_sharpen(img: Image.Image, amount: float) -> Image.Image:
    percent = max(1, int(round(100 * amount)))
    return map_rgb(img, lambda rgb: rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=percent, threshold=3)))


def op_mask(img: Image.Image, mask_path: str) -> Image.Image:
    mask = open_image(mask_path)
    mask = (mask.getchannel("A") if has_alpha(mask) and mask.mode == "RGBA" else mask.convert("L"))
    if mask.size != img.size:
        mask = mask.resize(img.size, Image.Resampling.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def op_extract_mask(img: Image.Image, warnings: list[str]) -> Image.Image:
    if not has_alpha(img):
        warnings.append("--extract-mask: image has no alpha channel; mask is fully opaque (white)")
        return Image.new("L", img.size, 255)
    return img.convert("RGBA").getchannel("A")


def op_grayscale(img: Image.Image) -> Image.Image:
    return img.convert("LA") if has_alpha(img) else img.convert("L")


def op_sepia(img: Image.Image) -> Image.Image:
    matrix = np.array(
        [[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]], dtype=np.float32
    )

    def apply(rgb: Image.Image) -> Image.Image:
        arr = np.asarray(rgb, dtype=np.float32)
        out = np.clip(arr @ matrix.T, 0, 255).astype(np.uint8)
        return Image.fromarray(out, "RGB")

    return map_rgb(img, apply)


def op_tint(img: Image.Image, color: tuple[int, ...], strength: float) -> Image.Image:
    """Luminance-preserving tint blended over the original by ``strength``."""

    def apply(rgb: Image.Image) -> Image.Image:
        tinted = ImageOps.colorize(rgb.convert("L"), black=(0, 0, 0), white=color[:3], mid=None)
        return Image.blend(rgb, tinted.convert("RGB"), strength)

    return map_rgb(img, apply)


def op_color_space(img: Image.Image, space: str) -> Image.Image:
    target = COLOR_SPACES[space.upper()]
    if target == img.mode:
        return img
    if target in ("CMYK", "RGB"):
        return flatten(img).convert(target)
    if target == "L":
        return img.convert("LA") if has_alpha(img) else img.convert("L")
    return img.convert(target)


def op_flatten_transparency(img: Image.Image, color: tuple[int, ...], warnings: list[str]) -> Image.Image:
    if not has_alpha(img):
        warnings.append("transparency removal skipped: image has no alpha channel")
        return img
    gray = img.mode in ("LA", "La")
    out = flatten(img, color)
    return out.convert("L") if gray else out


def load_font(size: int, font_path: str | None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            raise ImageEditError(f"Cannot load font: {font_path}") from None
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _position(base: tuple[int, int], item: tuple[int, int], where: str) -> tuple[int, int]:
    bw, bh = base
    iw, ih = item
    margin = max(10, int(min(bw, bh) * 0.02))
    x = {"left": margin, "right": bw - iw - margin, "center": (bw - iw) // 2}
    y = {"top": margin, "bottom": bh - ih - margin, "center": (bh - ih) // 2}
    if where == "center":
        return x["center"], y["center"]
    vert, horiz = where.split("-")
    return x[horiz], y[vert]


def op_watermark(img: Image.Image, args: argparse.Namespace) -> Image.Image:
    original_mode = img.mode
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))

    if args.watermark_image:
        mark = open_image(args.watermark_image).convert("RGBA")
        target_w = max(1, round(base.width * args.watermark_scale))
        target_h = max(1, round(mark.height * target_w / mark.width))
        mark = mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
        alpha = mark.getchannel("A").point(lambda a: a * args.watermark_opacity // 255)
        mark.putalpha(alpha)
        layer.alpha_composite(mark, _clamp_pos(_position(base.size, mark.size, args.watermark_position)))

    if args.watermark_text:
        font = load_font(args.watermark_font_size, args.watermark_font)
        draw = ImageDraw.Draw(layer)
        left, top, right, bottom = draw.multiline_textbbox((0, 0), args.watermark_text, font=font)
        x, y = _position(base.size, (right - left, bottom - top), args.watermark_position)
        color = parse_color(args.watermark_color)[:3]
        draw.multiline_text(
            (x - left, y - top), args.watermark_text, font=font, fill=(*color, args.watermark_opacity)
        )

    base.alpha_composite(layer)
    if original_mode == "RGBA":
        return base
    if original_mode == "LA":
        return base.convert("LA")
    rgb = base.convert("RGB")
    return rgb if original_mode == "RGB" else rgb.convert(original_mode)


def _clamp_pos(pos: tuple[int, int]) -> tuple[int, int]:
    return max(0, pos[0]), max(0, pos[1])


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def run_pipeline(img: Image.Image, args: argparse.Namespace, out_format: str) -> tuple[Image.Image, list[str], list[str]]:
    """Apply operations in the fixed documented order.

    Returns the image, a list of applied operation tags, and warnings.
    """
    ops: list[str] = []
    warnings: list[str] = []

    def step(tag: str, result: Image.Image) -> Image.Image:
        if tag not in ops:
            ops.append(tag)
        return result

    if args.rotate is not None:
        img = step("rotate", op_rotate(img, args.rotate, out_format in ALPHA_FORMATS))
    if args.flip:
        img = step("flip", op_flip(img, args.flip))
    if args.autocrop_transparency is not None:
        img = step("autocrop", op_autocrop_transparency(img, args.autocrop_transparency, warnings))
    if args.crop:
        img = step("crop", op_crop(img, parse_edges(args.crop, "--crop")))
    if args.thumbnail:
        img = step("thumbnail", op_thumbnail(img, parse_size_pair(args.thumbnail, "--thumbnail")))
    elif args.width or args.height:
        img = step("resize", op_resize(img, args.width, args.height))
    if args.pad:
        img = step("pad", op_pad(img, parse_edges(args.pad, "--pad"), parse_color(args.pad_color), args.pad_edge))
    if args.border:
        img = step("border", op_border(img, args.border, parse_color(args.border_color), args.border_inside))
    if args.brightness is not None:
        img = step("brightness", op_enhance(img, ImageEnhance.Brightness, args.brightness))
    if args.contrast is not None:
        img = step("contrast", op_enhance(img, ImageEnhance.Contrast, args.contrast))
    if args.saturation is not None:
        img = step("saturation", op_enhance(img, ImageEnhance.Color, args.saturation))
    if args.blur:
        img = step("blur", op_blur(img, args.blur))
    if args.sharpen:
        img = step("sharpen", op_sharpen(img, args.sharpen))
    if args.mask:
        img = step("mask", op_mask(img, args.mask))
    if args.extract_mask:
        img = step("mask", op_extract_mask(img, warnings))
    if args.grayscale:
        img = step("grayscale", op_grayscale(img))
    if args.sepia:
        img = step("sepia", op_sepia(img))
    if args.tint:
        img = step("tint", op_tint(img, parse_color(args.tint), args.tint_strength))
    if args.color_space:
        img = step(args.color_space.lower(), op_color_space(img, args.color_space))
    if args.remove_transparency:
        img = step("flat", op_flatten_transparency(img, (255, 255, 255, 255), warnings))
    elif args.replace_transparency:
        img = step("flat", op_flatten_transparency(img, parse_color(args.replace_transparency), warnings))
    if args.watermark_text or args.watermark_image:
        img = step("watermark", op_watermark(img, args))
    if args.strip_exif:
        ops.append("noexif")
    if args.dpi:
        ops.append("dpi")
    return img, ops, warnings


# --------------------------------------------------------------------------
# Encoding / saving
# --------------------------------------------------------------------------


def prepare_for_format(img: Image.Image, fmt: str, warnings: list[str]) -> Image.Image:
    """Convert the image to a mode the target format can store."""
    if fmt == "JPEG":
        if has_alpha(img):
            warnings.append("JPEG has no transparency: alpha flattened onto white")
            gray = img.mode in ("LA", "La")
            img = flatten(img)
            return img.convert("L") if gray else img
        if img.mode not in ("RGB", "L", "CMYK"):
            return img.convert("RGB")
    elif fmt == "PNG":
        if img.mode == "CMYK":
            warnings.append("PNG does not support CMYK: converted to RGB")
            return img.convert("RGB")
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P", "I", "1"):
            return img.convert("RGBA" if has_alpha(img) else "RGB")
    elif fmt in ("WEBP", "HEIF"):
        if img.mode == "CMYK":
            warnings.append(f"{fmt} does not support CMYK: converted to RGB")
        if img.mode not in ("RGB", "RGBA"):
            return img.convert("RGBA" if has_alpha(img) else "RGB")
    return img


def encode(img: Image.Image, fmt: str, opts: dict[str, Any]) -> bytes:
    kwargs: dict[str, Any] = {}
    quality = opts.get("quality")
    if fmt == "JPEG":
        kwargs.update(quality=quality, optimize=True)
        if quality >= 90:
            kwargs["subsampling"] = 0
    elif fmt == "WEBP":
        kwargs.update(quality=quality, method=4)
    elif fmt == "HEIF":
        kwargs.update(quality=quality)
    elif fmt == "PNG":
        kwargs.update(optimize=opts.get("optimize", False))
    elif fmt == "TIFF":
        kwargs.update(compression="tiff_lzw" if img.mode != "CMYK" else "tiff_adobe_deflate")
    if opts.get("dpi") and fmt in ("JPEG", "PNG", "TIFF"):
        kwargs["dpi"] = opts["dpi"]
    if opts.get("exif"):
        kwargs["exif"] = opts["exif"]
    if opts.get("icc_profile") and img.mode in ("RGB", "RGBA"):
        kwargs["icc_profile"] = opts["icc_profile"]
    img.info = {}  # never let stale metadata leak through format fallbacks
    buf = io.BytesIO()
    try:
        img.save(buf, format=fmt, **kwargs)
    except Exception as exc:
        raise ImageEditError(f"Failed to encode {fmt}: {exc}") from None
    return buf.getvalue()


def encode_to_size(
    img: Image.Image, fmt: str, opts: dict[str, Any], max_bytes: int, warnings: list[str]
) -> tuple[bytes, dict[str, Any]]:
    """Encode under ``max_bytes`` by lowering quality, then palette/scale."""
    details: dict[str, Any] = {}

    def best_quality(candidate: Image.Image) -> tuple[bytes, int] | None:
        hi = opts.get("quality") or DEFAULT_QUALITY[fmt]
        lo = 10
        data = encode(candidate, fmt, {**opts, "quality": hi})
        if len(data) <= max_bytes:
            return data, hi
        found: tuple[bytes, int] | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            data = encode(candidate, fmt, {**opts, "quality": mid})
            if len(data) <= max_bytes:
                found, lo = (data, mid), mid + 1
            else:
                hi = mid - 1
        return found

    def try_lossless(candidate: Image.Image) -> bytes | None:
        data = encode(candidate, fmt, {**opts, "optimize": True})
        if len(data) <= max_bytes:
            return data
        if fmt == "PNG" and candidate.mode in ("RGB", "RGBA"):
            method = Image.Quantize.FASTOCTREE if candidate.mode == "RGBA" else Image.Quantize.MEDIANCUT
            data = encode(candidate.quantize(256, method=method), fmt, {**opts, "optimize": True})
            if len(data) <= max_bytes:
                details["palette"] = 256
                return data
        return None

    candidate = img
    scale = 1.0
    for _ in range(25):
        if fmt in QUALITY_FORMATS:
            result = best_quality(candidate)
            if result:
                details["quality"] = result[1]
                data = result[0]
                break
        else:
            data = try_lossless(candidate)
            if data:
                break
        scale *= 0.85
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        if min(new_size) < 16:
            raise ImageEditError(
                f"Cannot reduce to {max_bytes / 1_000_000:g} MB: try a larger target or a lossy format (.jpg/.webp)"
            )
        candidate = img.resize(new_size, Image.Resampling.LANCZOS)
    else:  # pragma: no cover - loop exhausts only for extreme targets
        raise ImageEditError(f"Cannot reduce to {max_bytes / 1_000_000:g} MB")

    if scale < 1.0:
        details["downscaled_to"] = list(candidate.size)
        warnings.append(f"--max-size: image downscaled to {candidate.width}x{candidate.height} to fit target")
    details["final_size"] = list(candidate.size)
    return data, details


def build_save_opts(source: Image.Image, args: argparse.Namespace, fmt: str) -> dict[str, Any]:
    opts: dict[str, Any] = {"quality": args.quality or DEFAULT_QUALITY.get(fmt)}
    if args.dpi:
        opts["dpi"] = (args.dpi, args.dpi)
    elif source.info.get("dpi"):
        opts["dpi"] = tuple(float(d) for d in source.info["dpi"])
    if not args.strip_exif:
        exif = source.getexif()
        if len(exif):
            if ORIENTATION_TAG in exif:
                exif[ORIENTATION_TAG] = 1  # pixels are already upright
            try:
                opts["exif"] = exif.tobytes()
            except Exception:
                pass
    if source.info.get("icc_profile") and source.mode in ("RGB", "RGBA", "P"):
        opts["icc_profile"] = source.info["icc_profile"]
    return opts


# --------------------------------------------------------------------------
# Output paths
# --------------------------------------------------------------------------


def resolve_format(input_path: Path, output: str | None, args: argparse.Namespace, source_format: str | None) -> str:
    if args.format:
        return FORMAT_ALIASES[args.format.lower()]
    if output and output != "-" and not args.batch:
        fmt = format_from_path(output)
        if not fmt:
            raise ImageEditError(
                f"Unsupported output extension '{Path(output).suffix}'. Use one of: {', '.join(sorted(EXT_TO_FORMAT))}"
            )
        return fmt
    fmt = format_from_path(input_path)
    if fmt:
        return fmt
    if source_format in FORMAT_TO_EXT:
        return source_format
    return "PNG"


def auto_output_path(directory: Path, input_path: Path, tags: list[str], fmt: str) -> Path:
    suffix = "_".join(tags) if tags else "edited"
    ext = output_ext(input_path, fmt)
    candidate = directory / f"{input_path.stem}_{suffix}{ext}"
    i = 1
    while candidate.exists():
        candidate = directory / f"{input_path.stem}_{suffix}_{i}{ext}"
        i += 1
    return candidate


def output_ext(input_path: Path, fmt: str) -> str:
    """Keep the input's extension spelling when the format is unchanged."""
    if format_from_path(input_path) == fmt:
        return input_path.suffix
    return FORMAT_TO_EXT[fmt]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def run_diff(input_path: Path, args: argparse.Namespace, output: str | None, out_dir: Path | None) -> dict[str, Any]:
    a = flatten(normalize_mode(open_image(input_path)))
    b = flatten(normalize_mode(open_image(args.diff)))
    warnings: list[str] = []
    if a.size != b.size:
        warnings.append(f"sizes differ ({a.width}x{a.height} vs {b.width}x{b.height}); second image resized to compare")
        b = b.resize(a.size, Image.Resampling.LANCZOS)

    arr_a = np.asarray(a, dtype=np.int16)
    arr_b = np.asarray(b, dtype=np.int16)
    delta = np.abs(arr_a - arr_b).max(axis=2)
    changed = delta > 0
    mse = float(np.mean((arr_a - arr_b).astype(np.float64) ** 2))
    psnr = None if mse == 0 else round(10 * math.log10(255**2 / mse), 3)

    gray = np.asarray(a.convert("L"), dtype=np.float32)
    faded = (gray * 0.3 + 255 * 0.7).astype(np.uint8)
    vis = np.stack([faded] * 3, axis=2)
    strength = np.clip(delta.astype(np.float32) / 64.0, 0.4, 1.0)[..., None]
    red = np.array([230, 0, 0], dtype=np.float32)
    vis = np.where(changed[..., None], (vis * (1 - strength) + red * strength), vis).astype(np.uint8)
    diff_img = Image.fromarray(vis, "RGB")

    fmt = resolve_format(input_path, output, args, None)
    if fmt == "JPEG" and not args.format and (not output or output == "-"):
        fmt = "PNG"  # lossless output for an accurate diff map
    data = encode(prepare_for_format(diff_img, fmt, warnings), fmt, {"quality": args.quality or DEFAULT_QUALITY.get(fmt)})
    target = write_output(data, output, out_dir or input_path.parent, input_path, ["diff"], fmt)

    total = delta.size
    result = {
        "status": "ok",
        "operation": "diff",
        "input": str(input_path),
        "compare": str(args.diff),
        "output": target,
        "identical": not bool(changed.any()),
        "changed_pixels": int(changed.sum()),
        "total_pixels": int(total),
        "changed_percent": round(100 * float(changed.sum()) / total, 4),
        "mean_abs_diff": round(float(np.abs(arr_a - arr_b).mean()), 4),
        "max_diff": int(delta.max(initial=0)),
        "psnr_db": psnr,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def write_output(
    data: bytes, output: str | None, directory: Path, input_path: Path, tags: list[str], fmt: str
) -> str:
    if output == "-":
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return "-"
    path = Path(output) if output else auto_output_path(directory, input_path, tags, fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def process_one(input_path: Path, args: argparse.Namespace, output: str | None, out_dir: Path | None) -> dict[str, Any]:
    if args.info:
        return {"status": "ok", **image_info(input_path)}
    if args.diff:
        return run_diff(input_path, args, output, out_dir)

    source = open_image(input_path)
    warnings: list[str] = []
    if getattr(source, "n_frames", 1) > 1:
        warnings.append(f"input has {source.n_frames} frames; only the first frame was processed")

    fmt = resolve_format(input_path, output, args, source.format)
    if fmt == "HEIF" and not HEIF_AVAILABLE:
        raise ImageEditError("HEIC/HEIF output requires pillow-heif (run via `uv run` to auto-install)")
    # Upright the pixels first: build_save_opts resets the orientation tag.
    img = ImageOps.exif_transpose(source) or source
    opts = build_save_opts(source, args, fmt)
    img = normalize_mode(img)
    original_size = list(img.size)
    img, tags, op_warnings = run_pipeline(img, args, fmt)
    warnings.extend(op_warnings)
    img = prepare_for_format(img, fmt, warnings)

    details: dict[str, Any] = {}
    if args.max_size:
        data, details = encode_to_size(img, fmt, opts, int(args.max_size * 1_000_000), warnings)
        tags.append("optimized")
    else:
        data = encode(img, fmt, opts)

    target = write_output(data, output, out_dir or input_path.parent, input_path, tags, fmt)
    final_size = details.get("final_size", list(img.size))
    result: dict[str, Any] = {
        "status": "ok",
        "input": str(input_path),
        "output": target,
        "format": fmt,
        "mode": img.mode,
        "operations": tags,
        "original_size": original_size,
        "size": final_size,
        "file_size_bytes": len(data),
        "file_size_mb": round(len(data) / 1_000_000, 4),
    }
    if fmt in QUALITY_FORMATS:
        result["quality"] = details.get("quality", opts["quality"])
    if opts.get("dpi") and fmt in ("JPEG", "PNG", "TIFF"):
        result["dpi"] = [round(float(d), 2) for d in opts["dpi"]]
    result["exif_preserved"] = bool(opts.get("exif"))
    for key in ("palette", "downscaled_to"):
        if key in details:
            result[key] = details[key]
    if warnings:
        result["warnings"] = warnings
    return result


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    if args.output == "-":
        raise ImageEditError("--batch cannot write to stdout")
    files = sorted(
        {Path(p) for p in glob.glob(os.path.expanduser(args.input), recursive=True) if Path(p).is_file()}
    )
    files = [f for f in files if f.suffix.lower() in EXT_TO_FORMAT]
    if not files:
        raise ImageEditError(f"No supported images match pattern: {args.input}")

    out_dir: Path | None = None
    if args.output:
        out_dir = Path(args.output)
        if out_dir.suffix.lower() in EXT_TO_FORMAT:
            raise ImageEditError("With --batch, -o must be a directory, not a file")
        out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for f in files:
        output = None
        if out_dir is not None and not args.info:
            fmt = FORMAT_ALIASES[args.format.lower()] if args.format else format_from_path(f)
            candidate = out_dir / f"{f.stem}{output_ext(f, fmt)}"
            # Never overwrite a source image; fall back to auto-naming instead.
            output = None if candidate.resolve() == f.resolve() else str(candidate)
        try:
            results.append(process_one(f, args, output, out_dir))
        except ImageEditError as exc:
            results.append({"status": "error", "input": str(f), "error": str(exc)})

    failed = sum(1 for r in results if r["status"] != "ok")
    return {
        "status": "ok" if failed == 0 else ("partial" if failed < len(results) else "error"),
        "processed": len(results) - failed,
        "failed": failed,
        "results": results,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="image_edit.py",
        description="Edit images: transforms, adjustments, effects, watermarks, conversion, optimization.",
        epilog="Operations apply in a fixed order regardless of flag order. Output is a JSON report.",
    )
    p.add_argument("input", help="Input image path (or a quoted glob pattern with --batch)")
    p.add_argument("-o", "--output", help="Output path; auto-named if omitted; '-' writes the image to stdout")
    p.add_argument("--format", choices=sorted(FORMAT_ALIASES), help="Force output format (useful with --batch or stdout)")
    p.add_argument("--info", action="store_true", help="Show image metadata as JSON")
    p.add_argument("--batch", action="store_true", help="Treat INPUT as a glob pattern; -o is a directory")

    t = p.add_argument_group("transforms")
    t.add_argument("--rotate", type=float, metavar="ANGLE", help="Rotate counterclockwise in degrees")
    t.add_argument("--flip", choices=("horizontal", "vertical"))
    t.add_argument("--width", type=int, metavar="W", help="Target width (keeps aspect ratio)")
    t.add_argument("--height", type=int, metavar="H", help="Target height (keeps aspect ratio)")
    t.add_argument("--thumbnail", metavar="W,H", help="Center-crop to fill exactly W x H")
    t.add_argument("--crop", metavar="PIXELS", help="Crop from edges: N, H,V or LEFT,TOP,RIGHT,BOTTOM")
    t.add_argument("--pad", metavar="PIXELS", help="Pad edges: N, H,V or LEFT,TOP,RIGHT,BOTTOM")
    t.add_argument("--pad-color", default="white", metavar="COLOR", help="Padding color (default: white)")
    t.add_argument("--pad-edge", action="store_true", help="Replicate edge pixels instead of a solid color")
    t.add_argument("--border", type=int, metavar="PX", help="Add a border of PX pixels")
    t.add_argument("--border-color", default="black", metavar="COLOR", help="Border color (default: black)")
    t.add_argument("--border-inside", action="store_true", help="Draw the border inside the image")

    a = p.add_argument_group("adjustments")
    a.add_argument("--brightness", type=float, metavar="F", help="Brightness factor (1.0 = original)")
    a.add_argument("--contrast", type=float, metavar="F", help="Contrast factor (1.0 = original)")
    a.add_argument("--saturation", type=float, metavar="F", help="Saturation factor (0 = gray, 1.0 = original)")
    a.add_argument("--blur", type=float, metavar="R", help="Gaussian blur radius")
    a.add_argument("--sharpen", type=float, metavar="A", help="Sharpen amount (1 = light)")

    e = p.add_argument_group("effects")
    e.add_argument("--grayscale", action="store_true")
    e.add_argument("--sepia", action="store_true")
    e.add_argument("--tint", metavar="COLOR", help="Apply a color tint")
    e.add_argument("--tint-strength", type=float, default=0.3, metavar="S", help="Tint strength 0.0-1.0 (default: 0.3)")
    e.add_argument("--color-space", type=str.upper, choices=sorted(COLOR_SPACES), help="Convert color space")

    w = p.add_argument_group("watermark")
    w.add_argument("--watermark-text", metavar="TEXT")
    w.add_argument("--watermark-image", metavar="PATH")
    w.add_argument("--watermark-position", choices=POSITIONS, default="bottom-right")
    w.add_argument("--watermark-opacity", type=int, default=128, metavar="A", help="0-255 (default: 128)")
    w.add_argument("--watermark-font-size", type=int, default=24, metavar="PX")
    w.add_argument("--watermark-font", metavar="PATH", help="TrueType/OpenType font file")
    w.add_argument("--watermark-color", default="white", metavar="COLOR")
    w.add_argument("--watermark-scale", type=float, default=0.2, metavar="S", help="Logo width / image width (default: 0.2)")

    q = p.add_argument_group("quality")
    q.add_argument("--quality", type=int, metavar="Q", help="1-100 for JPEG/WebP/HEIC (defaults: JPEG 95, WebP/HEIC 90)")
    q.add_argument("--dpi", type=int, metavar="DPI", help="Output DPI (default: keep original)")
    q.add_argument("--max-size", type=float, metavar="MB", help="Target maximum file size in megabytes")

    tr = p.add_argument_group("transparency")
    tr.add_argument("--remove-transparency", action="store_true", help="Flatten alpha onto white")
    tr.add_argument("--replace-transparency", metavar="COLOR", help="Flatten alpha onto COLOR")
    tr.add_argument("--extract-mask", action="store_true", help="Output the alpha channel as a grayscale mask")
    tr.add_argument("--mask", metavar="PATH", help="Apply a grayscale image as the alpha channel")
    tr.add_argument("--autocrop-transparency", type=float, metavar="T", help="Trim borders with alpha <= T%% (0-100)")

    o = p.add_argument_group("other")
    o.add_argument("--strip-exif", action="store_true", help="Remove EXIF metadata")
    o.add_argument("--diff", metavar="PATH", help="Write a visual diff against another image")
    return p


def validate(args: argparse.Namespace) -> None:
    def check(cond: bool, msg: str) -> None:
        if not cond:
            raise ImageEditError(msg)

    check(args.width is None or args.width > 0, "--width must be positive")
    check(args.height is None or args.height > 0, "--height must be positive")
    check(not (args.thumbnail and (args.width or args.height)), "--thumbnail cannot be combined with --width/--height")
    check(args.border is None or args.border > 0, "--border must be positive")
    for name in ("brightness", "contrast", "saturation"):
        val = getattr(args, name)
        check(val is None or val >= 0, f"--{name} must be >= 0")
    check(args.blur is None or args.blur >= 0, "--blur must be >= 0")
    check(args.sharpen is None or args.sharpen >= 0, "--sharpen must be >= 0")
    check(0 <= args.tint_strength <= 1, "--tint-strength must be between 0 and 1")
    check(0 <= args.watermark_opacity <= 255, "--watermark-opacity must be between 0 and 255")
    check(args.watermark_font_size > 0, "--watermark-font-size must be positive")
    check(0 < args.watermark_scale <= 1, "--watermark-scale must be in (0, 1]")
    check(args.quality is None or 1 <= args.quality <= 100, "--quality must be between 1 and 100")
    check(args.dpi is None or args.dpi > 0, "--dpi must be positive")
    check(args.max_size is None or args.max_size > 0, "--max-size must be positive")
    check(
        args.autocrop_transparency is None or 0 <= args.autocrop_transparency <= 100,
        "--autocrop-transparency must be between 0 and 100",
    )
    check(
        not (args.remove_transparency and args.replace_transparency),
        "Use either --remove-transparency or --replace-transparency, not both",
    )
    check(not (args.mask and args.extract_mask), "Use either --mask or --extract-mask, not both")
    check(not (args.info and args.output), "--info does not write an image; drop -o")
    # Fail fast on bad colors before touching any file.
    for opt in ("pad_color", "border_color", "watermark_color", "tint", "replace_transparency"):
        val = getattr(args, opt)
        if val:
            parse_color(val)
    for opt in ("mask", "watermark_image", "diff"):
        val = getattr(args, opt)
        if val and not Path(val).is_file():
            raise ImageEditError(f"No such file or directory: {val} (--{opt.replace('_', '-')})")
    if not args.batch and not Path(args.input).exists() and any(c in args.input for c in "*?["):
        raise ImageEditError(f"'{args.input}' looks like a glob pattern; add --batch to process multiple files")


def emit(payload: dict[str, Any], to_stderr: bool) -> None:
    stream = sys.stderr if to_stderr else sys.stdout
    stream.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    image_on_stdout = args.output == "-"
    try:
        validate(args)
        if args.batch:
            result = run_batch(args)
        else:
            result = process_one(Path(args.input), args, args.output, None)
    except ImageEditError as exc:
        emit({"status": "error", "error": str(exc)}, image_on_stdout)
        return 1
    emit(result, image_on_stdout)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
