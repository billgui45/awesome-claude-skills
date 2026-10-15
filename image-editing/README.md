# Image Editing Skill

A comprehensive image manipulation CLI tool for Claude Code. Supports transforms, adjustments, effects, watermarks, batch processing, format conversions, transparency operations, and optimization — all with structured JSON output.

## Installation

1. Install [uv](https://docs.astral.sh/uv/):
   ```bash
   brew install uv
   # or: curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Copy this `image-editing/` folder into your project's `.claude/skills/` directory (or `~/.claude/skills/` for all projects).

3. Dependencies (`pillow`, `pillow-heif`, `numpy`) are auto-managed by uv — no manual install needed.

## Usage

```bash
uv run .claude/skills/image-editing/scripts/image_edit.py INPUT [OPTIONS]
```

Or invoke via Claude Code:
```
/image-editing photo.png --width 800
```

Every run prints a JSON report, for example:

```json
{
  "status": "ok",
  "input": "photo.png",
  "output": "resized.png",
  "format": "PNG",
  "mode": "RGB",
  "operations": ["resize"],
  "original_size": [1600, 1200],
  "size": [800, 600],
  "file_size_bytes": 412883,
  "file_size_mb": 0.4129,
  "exif_preserved": false
}
```

## Examples

### Basic Operations

```bash
# Resize (aspect ratio preserved)
uv run image_edit.py photo.png --width 800 -o resized.png

# Rotate 90° counterclockwise
uv run image_edit.py photo.png --rotate 90 -o rotated.png

# Flip horizontal
uv run image_edit.py photo.png --flip horizontal -o flipped.png

# Crop 50px from all edges (or LEFT,TOP,RIGHT,BOTTOM)
uv run image_edit.py photo.png --crop 50 -o cropped.png

# Add 20px white padding
uv run image_edit.py photo.png --pad 20 --pad-color white -o padded.png
```

### Adjustments

```bash
# Brighten image
uv run image_edit.py photo.png --brightness 1.3 -o bright.png

# Increase contrast
uv run image_edit.py photo.png --contrast 1.4 -o contrast.png

# Desaturate (0 = grayscale, 1 = original, >1 = vivid)
uv run image_edit.py photo.png --saturation 0.5 -o muted.png

# Gaussian blur
uv run image_edit.py photo.png --blur 3 -o blurred.png

# Sharpen (1 = light, >1 = stronger unsharp mask)
uv run image_edit.py photo.png --sharpen 2 -o sharp.png
```

### Color Effects

```bash
# Sepia tone
uv run image_edit.py photo.png --sepia -o sepia.png

# Blue tint at 30% strength
uv run image_edit.py photo.png --tint blue --tint-strength 0.3 -o tinted.png

# Convert to grayscale
uv run image_edit.py photo.png --grayscale -o gray.png

# Convert color space to CMYK (for print)
uv run image_edit.py photo.png --color-space CMYK -o print.tiff
```

### Borders

```bash
# 5px red border (expands canvas)
uv run image_edit.py photo.png --border 5 --border-color red -o bordered.png

# 10px border drawn inside the image
uv run image_edit.py photo.png --border 10 --border-color blue --border-inside -o framed.png
```

### Thumbnails

```bash
# Smart crop-to-fit: centers and crops to exact 300x300
uv run image_edit.py photo.png --thumbnail 300,300 -o thumb.png
```

### Watermarks

```bash
# Text watermark
uv run image_edit.py photo.png \
  --watermark-text "© 2026" \
  --watermark-position bottom-right \
  --watermark-opacity 180 \
  --watermark-font-size 36 \
  --watermark-color white \
  -o watermarked.png

# Image watermark (logo overlay)
uv run image_edit.py photo.png \
  --watermark-image logo.png \
  --watermark-position center \
  --watermark-scale 0.15 \
  --watermark-opacity 200 \
  -o branded.png
```

### Format Conversion

```bash
# PNG to WebP
uv run image_edit.py photo.png -o photo.webp --quality 80

# HEIC to JPEG
uv run image_edit.py photo.heic -o photo.jpg --quality 90

# PNG to TIFF for print
uv run image_edit.py photo.png -o photo.tiff --dpi 300
```

### Batch Processing

```bash
# Resize all PNGs to 800px wide
uv run image_edit.py "*.png" --width 800 --batch -o /output/dir

# Convert all JPEGs to WebP
uv run image_edit.py "photos/*.jpg" --batch --format webp -o /output/webp

# Apply sepia to all images (auto-named next to each input)
uv run image_edit.py "*.png" --sepia --batch
```

Quote the glob so the script expands it, not your shell. `**` patterns are supported for recursive matching. A batch never overwrites its source images.

### Image Comparison

```bash
# Generate visual diff between two images (changed pixels highlighted red)
uv run image_edit.py original.png --diff modified.png -o diff.png
```

The report includes `identical`, `changed_pixels`, `changed_percent`, `mean_abs_diff`, `max_diff`, and `psnr_db`.

### EXIF Handling

```bash
# Strip all metadata
uv run image_edit.py photo.jpg --strip-exif -o clean.jpg

# View image info including EXIF
uv run image_edit.py photo.jpg --info
```

EXIF orientation is applied on load, so photos are always edited upright. EXIF is otherwise preserved (with orientation reset to normal) unless `--strip-exif` is given.

### Transparency

```bash
# Remove transparency (white background)
uv run image_edit.py icon.png --remove-transparency -o icon_flat.png

# Replace transparency with custom color
uv run image_edit.py icon.png --replace-transparency "#f0f0f0" -o icon_gray.png

# Extract alpha channel as mask
uv run image_edit.py icon.png --extract-mask -o mask.png

# Apply mask as alpha channel
uv run image_edit.py photo.png --mask mask.png -o masked.png

# Auto-crop transparent borders
uv run image_edit.py icon.png --autocrop-transparency 5 -o trimmed.png
```

### File Size Optimization

```bash
# Reduce to target file size
uv run image_edit.py photo.png -o optimized.jpg --max-size 0.5
```

Quality is lowered first (JPEG/WebP/HEIC); PNGs are optimized and then palette-reduced. The image is downscaled only as a last resort, which is reported in `warnings`.

### Combined Operations

```bash
# Multiple operations in one command
uv run image_edit.py photo.png -o final.jpg \
  --rotate 90 \
  --width 800 \
  --brightness 1.2 --contrast 1.1 \
  --border 3 --border-color coral \
  --sepia \
  --watermark-text "Draft" --watermark-opacity 100 \
  --quality 85 --dpi 150
```

### Auto-Naming

```bash
# Omit -o for automatic output naming
uv run image_edit.py photo.png --rotate 90 --sepia
# → photo_rotate_sepia.png
```

Auto-named outputs never overwrite existing files (`photo_rotate_sepia_1.png`, ...).

### Stdout Output

```bash
# Pipe image to another tool (JSON report goes to stderr)
uv run image_edit.py input.png --width 100 --output=- | other-tool
```

## Options Reference

| Option | Description | Default |
|--------|-------------|---------|
| `-o`, `--output` | Output path (auto-named if omitted, `-` for stdout; a directory with `--batch`) | auto |
| `--format FMT` | Force output format: jpg, png, webp, tiff, heic | from `-o` / input |
| `--info` | Show image metadata as JSON | — |
| `--batch` | Process glob pattern | — |
| **Transforms** | | |
| `--rotate ANGLE` | Rotate counterclockwise (degrees) | — |
| `--flip` | `horizontal` or `vertical` | — |
| `--width W` | Target width (preserves aspect ratio) | — |
| `--height H` | Target height (preserves aspect ratio; with `--width`, fits inside W×H) | — |
| `--thumbnail W,H` | Smart crop-to-fit to exact dimensions | — |
| `--crop PIXELS` | Crop from edges: `N`, `H,V`, or `L,T,R,B` | — |
| `--pad PIXELS` | Add padding: `N`, `H,V`, or `L,T,R,B` | — |
| `--pad-color COLOR` | Padding color (`transparent` allowed) | white |
| `--pad-edge` | Replicate edge pixels for padding | false |
| `--border PX` | Add border | — |
| `--border-color COLOR` | Border color | black |
| `--border-inside` | Draw border inside image | false |
| **Adjustments** | | |
| `--brightness F` | Brightness factor (1.0 = original) | — |
| `--contrast F` | Contrast factor (1.0 = original) | — |
| `--saturation F` | Saturation factor (1.0 = original) | — |
| `--blur R` | Gaussian blur radius | — |
| `--sharpen A` | Sharpen amount (1 = light) | — |
| **Effects** | | |
| `--grayscale` | Convert to grayscale | — |
| `--sepia` | Apply sepia tone | — |
| `--tint COLOR` | Apply color tint | — |
| `--tint-strength S` | Tint strength (0.0–1.0) | 0.3 |
| `--color-space SPACE` | Convert color space: RGB, RGBA, CMYK, L (gray) | — |
| **Watermark** | | |
| `--watermark-text TEXT` | Text watermark content | — |
| `--watermark-image PATH` | Image watermark file | — |
| `--watermark-position` | top-left, top-right, bottom-left, bottom-right, center | bottom-right |
| `--watermark-opacity A` | Opacity (0–255) | 128 |
| `--watermark-font-size PX` | Font size | 24 |
| `--watermark-font PATH` | TrueType/OpenType font file | DejaVu Sans / Arial |
| `--watermark-color COLOR` | Text color | white |
| `--watermark-scale S` | Image watermark width relative to image (0–1) | 0.2 |
| **Quality** | | |
| `--quality Q` | Output quality (1–100, JPEG/WebP/HEIC) | JPEG=95, WebP/HEIC=90 |
| `--dpi DPI` | Output DPI (JPEG/PNG/TIFF) | original |
| `--max-size MB` | Target file size | — |
| **Transparency** | | |
| `--remove-transparency` | Flatten to white background | — |
| `--replace-transparency COLOR` | Flatten to custom color | — |
| `--extract-mask` | Save alpha as grayscale | — |
| `--mask PATH` | Apply mask as alpha | — |
| `--autocrop-transparency T` | Trim transparent borders (0–100%) | — |
| **Other** | | |
| `--strip-exif` | Remove EXIF/metadata | false |
| `--diff PATH` | Visual diff with another image | — |

## Supported Formats

| Format | Extensions | Read | Write |
|--------|-----------|------|-------|
| JPEG | .jpg, .jpeg | Yes | Yes |
| PNG | .png | Yes | Yes |
| WebP | .webp | Yes | Yes |
| TIFF | .tiff, .tif | Yes | Yes |
| HEIC/HEIF | .heic, .heif | Yes | Yes |

Multi-frame inputs (animated WebP, multi-page TIFF) are processed using the first frame, with a warning.

## Operation Order

Operations apply in this fixed order regardless of flag order:

1. rotate
2. flip
3. autocrop transparency
4. crop
5. thumbnail / resize
6. pad
7. border
8. brightness
9. contrast
10. saturation
11. blur
12. sharpen
13. mask (alpha blend) / extract mask
14. grayscale
15. sepia
16. tint
17. color space
18. transparency (remove/replace)
19. watermark
20. strip EXIF
21. DPI

## Color Formats

| Format | Examples |
|--------|----------|
| Named | `red`, `blue`, `coral`, `darkslategray` (140+ CSS colors) |
| Hex | `#RGB`, `#RRGGBB`, `#RRGGBBAA` |
| RGB | `255,128,0` or `rgb(255,128,0)` |
| HSL | `hsl(120,100%,50%)` |
| Transparent | `transparent` |

## Quality Guide

| Use Case | Format | Quality | DPI |
|----------|--------|---------|-----|
| Web/screen | WebP | 80-85 | 72 |
| Social media | JPEG | 85-90 | 72 |
| Print (standard) | JPEG/PNG | 95 | 150 |
| Print (high quality) | TIFF/PNG | 100 | 300 |
| Thumbnail | WebP | 70-75 | 72 |
| Archive | PNG | — | original |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `uv: command not found` | Install uv: `brew install uv` |
| `No such file or directory` | Check the input path exists |
| `Cannot reduce to X MB` | Try a larger target size or a lossy format (.jpg/.webp) |
| HEIC not loading | Ensure `pillow-heif` is available (auto-installed by uv) |
| `looks like a glob pattern` | Add `--batch` and quote the pattern |
| Watermark text invisible | Increase `--watermark-font-size` or `--watermark-opacity`, or change `--watermark-color` |
| Transparency lost in JPEG | JPEG has no alpha channel; output PNG/WebP or choose a background with `--replace-transparency` |
| CMYK output looks off on screen | CMYK is meant for print; PNG/WebP outputs are converted back to RGB automatically |
