#!/usr/bin/env python3
"""
gen_icon.py – Generate app icons for Sound2Text (macOS .icns + Windows .ico).

Design: 7-bar audio waveform on a blue gradient rounded-square background.
Run: python3 gen_icon.py
Output: app_icon.icns (macOS, requires iconutil) and app_icon.ico (Windows)
"""
import os, sys, shutil, subprocess
import numpy as np
from PIL import Image, ImageDraw

_HERE     = os.path.dirname(os.path.abspath(__file__))
_OUTPUT   = os.path.join(_HERE, "app_icon.icns")
_OUTPUT_ICO = os.path.join(_HERE, "app_icon.ico")


def _render(size: int) -> Image.Image:
    """Render the icon at `size` × `size` pixels."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # ── Gradient background ───────────────────────────────────────────────────
    # Top:    #1E3A8A  (blue-900 – deep navy)
    # Bottom: #2563EB  (blue-600 – vivid blue)
    c_top = np.array([30,  58, 138], dtype=np.float32)
    c_bot = np.array([37,  99, 235], dtype=np.float32)

    arr = np.zeros((size, size, 4), dtype=np.uint8)
    ys  = np.linspace(0, 1, size, dtype=np.float32)
    for y_idx, t in enumerate(ys):
        arr[y_idx, :, :3] = (c_top * (1 - t) + c_bot * t).astype(np.uint8)
        arr[y_idx, :,  3] = 255
    bg = Image.fromarray(arr, "RGBA")

    # ── Rounded-square mask (macOS standard ≈ 22% corner radius) ─────────────
    mask = Image.new("L", (size, size), 0)
    r    = int(size * 0.222)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=r, fill=255)
    bg.putalpha(mask)
    img.paste(bg, mask=bg)

    # ── 7-bar symmetric waveform ──────────────────────────────────────────────
    draw = ImageDraw.Draw(img)

    # Bar heights as fractions of icon size (tallest in centre)
    heights  = [0.18, 0.33, 0.52, 0.66, 0.52, 0.33, 0.18]
    n        = len(heights)
    bar_w    = size * 0.075
    gap      = size * 0.038
    total_w  = n * bar_w + (n - 1) * gap
    x0       = (size - total_w) / 2
    cy       = size * 0.50            # vertical centre of bars

    for i, h_frac in enumerate(heights):
        bx  = x0 + i * (bar_w + gap)
        bh  = size * h_frac
        by0 = cy - bh / 2
        by1 = cy + bh / 2
        draw.rounded_rectangle(
            [bx, by0, bx + bar_w, by1],
            radius=bar_w / 2,
            fill=(255, 255, 255, 255),
        )

    return img


def build_icns(out_path: str = _OUTPUT) -> str:
    """Generate all iconset sizes and convert to .icns with iconutil."""
    iconset_dir = out_path.replace(".icns", ".iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    sizes = [
        ("icon_16x16.png",       16),
        ("icon_16x16@2x.png",    32),
        ("icon_32x32.png",       32),
        ("icon_32x32@2x.png",    64),
        ("icon_128x128.png",    128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png",    256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png",    512),
        ("icon_512x512@2x.png",1024),
    ]

    base = _render(1024)
    for fname, sz in sizes:
        img = base.resize((sz, sz), Image.LANCZOS) if sz < 1024 else base
        img.save(os.path.join(iconset_dir, fname), format="PNG")

    subprocess.run(
        ["iconutil", "-c", "icns", iconset_dir, "-o", out_path],
        check=True,
    )
    shutil.rmtree(iconset_dir)
    return out_path


def build_ico(out_path: str = _OUTPUT_ICO) -> str:
    """Generate app_icon.ico (multi-resolution) for the Windows installer."""
    sizes = [16, 32, 48, 64, 128, 256]
    base = _render(256)
    base.save(out_path, format="ICO", sizes=[(s, s) for s in sizes])
    return out_path


if __name__ == "__main__":
    print("Generating Sound2Text icons …")

    ico_path = build_ico()
    print(f"✅  {ico_path}  ({os.path.getsize(ico_path) // 1024} KB)")

    if shutil.which("iconutil"):
        icns_path = build_icns()
        print(f"✅  {icns_path}  ({os.path.getsize(icns_path) // 1024} KB)")
    else:
        print("ℹ️  Skipping .icns (iconutil not available — macOS only)")
