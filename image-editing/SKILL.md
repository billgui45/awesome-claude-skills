---
name: image-editing
description: Edit images from the command line - resize, crop, rotate, adjust, apply effects, watermark, convert formats (JPEG/PNG/WebP/TIFF/HEIC), handle transparency, compare images, batch process, and shrink files to a target size, with structured JSON output. Use when the user wants to modify, convert, inspect, or optimize image files.
---

# Image Editing

A single Python CLI (`scripts/image_edit.py`) that performs common image edits and prints a JSON report of what it did. Dependencies (`pillow`, `pillow-heif`, `numpy`) are declared inline and installed automatically by `uv`.

## When to Use This Skill

- Resizing, cropping, rotating, flipping, padding, or framing images
- Adjusting brightness, contrast, saturation, blur, or sharpness
- Applying grayscale, sepia, tint, or color-space conversion (e.g. CMYK for print)
- Adding text or logo watermarks
- Converting between JPEG, PNG, WebP, TIFF, and HEIC
- Removing, replacing, extracting, or applying transparency
- Shrinking an image under a file-size limit
- Inspecting metadata (dimensions, DPI, EXIF) or diffing two images
- Applying the same edit to many files at once

## How to Use

Run the script from the skill directory (adjust the path to where the skill is installed, e.g. `.claude/skills/image-editing/`):

```bash
uv run scripts/image_edit.py INPUT [OPTIONS]
```

If `uv` is unavailable, `python3 scripts/image_edit.py` works when `pillow`, `pillow-heif`, and `numpy` are installed.

### Workflow

1. **Inspect first** when dimensions, format, or transparency matter: `--info` prints metadata as JSON.
2. **Combine operations in one command.** They always apply in a fixed order (see below), regardless of flag order. If the user needs a different order, chain two commands.
3. **Always pass `-o`** with a new filename unless the user asked for auto-naming. Never overwrite the user's original file unless they explicitly asked; confirm first.
4. **Read the JSON report.** Check `status`, `output`, `size`, and `warnings` (e.g. alpha flattened for JPEG, image downscaled to meet `--max-size`) and tell the user about anything surprising.
5. **Look at the result** when the edit is visual (watermark placement, crop, colors): open the output image to verify before reporting success.

### Common Commands

```bash
# Inspect
uv run scripts/image_edit.py photo.jpg --info

# Resize to 800px wide (aspect ratio kept); with both --width and --height it fits inside the box
uv run scripts/image_edit.py photo.png --width 800 -o resized.png

# Exact-size thumbnail (center crop to fill)
uv run scripts/image_edit.py photo.png --thumbnail 300,300 -o thumb.png

# Crop / pad: N, H,V, or LEFT,TOP,RIGHT,BOTTOM
uv run scripts/image_edit.py photo.png --crop 0,40,0,40 -o cropped.png
uv run scripts/image_edit.py photo.png --pad 20 --pad-color transparent -o padded.png

# Adjust and apply effects
uv run scripts/image_edit.py photo.png --brightness 1.2 --contrast 1.1 --sepia -o warm.png

# Watermark
uv run scripts/image_edit.py photo.png --watermark-text "© 2026" --watermark-font-size 36 -o wm.png
uv run scripts/image_edit.py photo.png --watermark-image logo.png --watermark-scale 0.15 -o branded.png

# Convert and optimize
uv run scripts/image_edit.py photo.heic -o photo.jpg --quality 90
uv run scripts/image_edit.py photo.png -o small.jpg --max-size 0.5

# Transparency
uv run scripts/image_edit.py icon.png --autocrop-transparency 5 -o trimmed.png
uv run scripts/image_edit.py icon.png --replace-transparency "#f0f0f0" -o flat.png

# Compare two images (writes a red-highlight diff map plus pixel metrics)
uv run scripts/image_edit.py before.png --diff after.png -o diff.png

# Batch: quote the glob; -o is a directory; --format converts every file
uv run scripts/image_edit.py "photos/*.jpg" --batch --width 1200 --format webp -o out/
```

## Operation Order

1. rotate → 2. flip → 3. autocrop transparency → 4. crop → 5. thumbnail / resize → 6. pad → 7. border → 8. brightness → 9. contrast → 10. saturation → 11. blur → 12. sharpen → 13. mask / extract mask → 14. grayscale → 15. sepia → 16. tint → 17. color space → 18. remove/replace transparency → 19. watermark → 20. strip EXIF → 21. DPI

EXIF orientation is applied on load, so camera photos are upright before any operation.

## Key Behaviors

- **Rotation** is counterclockwise. Multiples of 90° are lossless; other angles expand the canvas, filled transparent for PNG/WebP/TIFF/HEIC output and white for JPEG.
- **JPEG cannot store transparency**: alpha is flattened onto white with a warning. Use `--replace-transparency COLOR` to choose the background.
- **EXIF is preserved** (with orientation reset) unless `--strip-exif` is given. Strip EXIF before sharing photos publicly - it can contain GPS location.
- **`--max-size MB`** lowers quality first (JPEG/WebP/HEIC), tries palette reduction for PNG, and only downscales as a last resort (reported in `warnings` and `downscaled_to`).
- **Output to stdout** with `--output=-` (or `-o -`); the JSON report then goes to stderr. Use `--format` to pick the stream's format.
- **Auto-naming**: without `-o`, the output is written next to the input as `name_<ops>.ext` and never overwrites an existing file.
- **Errors** return `{"status": "error", "error": "..."}` with exit code 1. Batch runs report per-file results and a `partial` status if some files fail.

## Colors

Named CSS colors (`coral`, `darkslategray`), hex (`#RGB`, `#RRGGBB`, `#RRGGBBAA`), `R,G,B[,A]`, `rgb(...)`, `hsl(...)`, and `transparent`.

Run `uv run scripts/image_edit.py --help` for every option, or see `README.md` for the full reference.
