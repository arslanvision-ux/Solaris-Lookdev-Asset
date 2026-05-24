"""
Procedural Macbeth ColorChecker chart generator.

Writes a 6×4 patch grid PNG using the canonical sRGB values from the
X-Rite ColorChecker spec. Cached on first call so subsequent renders
reuse the same file.
"""

import os
import struct
import zlib


# X-Rite ColorChecker Classic — sRGB 8-bit values (D50 illuminant).
# Order: row-by-row, top-left to bottom-right.
#   Row 1: Dark Skin, Light Skin, Blue Sky, Foliage, Blue Flower,
#          Bluish Green
#   Row 2: Orange, Purplish Blue, Moderate Red, Purple, Yellow Green,
#          Orange Yellow
#   Row 3: Blue, Green, Red, Yellow, Magenta, Cyan
#   Row 4: White, Neutral 8, Neutral 6.5, Neutral 5, Neutral 3.5, Black
_MACBETH_SRGB = [
    (115, 82, 68),   (194, 150, 130), (98, 122, 157),  (87, 108, 67),
    (133, 128, 177), (103, 189, 170),
    (214, 126, 44),  (80, 91, 166),   (193, 90, 99),   (94, 60, 108),
    (157, 188, 64),  (224, 163, 46),
    (56, 61, 150),   (70, 148, 73),   (175, 54, 60),   (231, 199, 31),
    (187, 86, 149),  (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121),
    (85, 85, 85),    (52, 52, 52),
]

_COLS, _ROWS = 6, 4
_PATCH_PX = 96            # each patch is _PATCH_PX × _PATCH_PX
_GAP_PX = 6               # gap between patches (drawn as the border color)
_BORDER_RGB = (32, 32, 32)


def get_macbeth_chart_path(cache_dir: str = "") -> str:
    """Return a path to a generated Macbeth chart PNG, creating it if
    needed.

    Defaults to `~/houdini/asset_manager/macbeth_chart.png` if
    `cache_dir` isn't provided. Subsequent calls return the existing
    file without regenerating.
    """
    if not cache_dir:
        cache_dir = os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager",
        )
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, "macbeth_chart.png").replace("\\", "/")
    if os.path.exists(out_path):
        return out_path

    _write_macbeth_png(out_path)
    return out_path


def _write_macbeth_png(out_path: str) -> None:
    """Render the 6×4 patch grid + gaps and save as a PNG.

    Uses Pillow when available (best fidelity / colorspace handling);
    falls back to a raw-buffer PNG writer that has no dependencies.
    """
    width = _COLS * _PATCH_PX + (_COLS + 1) * _GAP_PX
    height = _ROWS * _PATCH_PX + (_ROWS + 1) * _GAP_PX

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _write_macbeth_raw_png(out_path, width, height)
        return

    img = Image.new("RGB", (width, height), _BORDER_RGB)
    draw = ImageDraw.Draw(img)
    for row in range(_ROWS):
        for col in range(_COLS):
            idx = row * _COLS + col
            x0 = _GAP_PX + col * (_PATCH_PX + _GAP_PX)
            y0 = _GAP_PX + row * (_PATCH_PX + _GAP_PX)
            x1 = x0 + _PATCH_PX
            y1 = y0 + _PATCH_PX
            draw.rectangle([x0, y0, x1, y1], fill=_MACBETH_SRGB[idx])
    img.save(out_path, "PNG")


def _write_macbeth_raw_png(out_path: str, width: int, height: int) -> None:
    """Pillow-free fallback: build the RGB buffer and emit a minimal
    valid PNG (single IDAT chunk, no filter compression beyond zlib).

    Used only when Pillow isn't installed; the result is identical to
    the Pillow path for our purposes (sRGB integer patches on a flat
    border).
    """
    # Build the pixel buffer top-to-bottom.
    rows = []
    for y in range(height):
        row = bytearray()
        row.append(0)  # PNG filter byte (None)
        for x in range(width):
            col_idx = (x - _GAP_PX) // (_PATCH_PX + _GAP_PX)
            row_idx = (y - _GAP_PX) // (_PATCH_PX + _GAP_PX)
            in_col = (
                0 <= col_idx < _COLS
                and (_GAP_PX + col_idx * (_PATCH_PX + _GAP_PX))
                <= x < (_GAP_PX + col_idx * (_PATCH_PX + _GAP_PX)
                        + _PATCH_PX)
            )
            in_row = (
                0 <= row_idx < _ROWS
                and (_GAP_PX + row_idx * (_PATCH_PX + _GAP_PX))
                <= y < (_GAP_PX + row_idx * (_PATCH_PX + _GAP_PX)
                        + _PATCH_PX)
            )
            if in_col and in_row:
                r, g, b = _MACBETH_SRGB[row_idx * _COLS + col_idx]
            else:
                r, g, b = _BORDER_RGB
            row.extend((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)
    compressed = zlib.compress(raw, level=6)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I",
                              zlib.crc32(tag + data) & 0xFFFFFFFF))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    out = (signature
           + _chunk(b"IHDR", ihdr)
           + _chunk(b"IDAT", compressed)
           + _chunk(b"IEND", b""))
    with open(out_path, "wb") as f:
        f.write(out)
