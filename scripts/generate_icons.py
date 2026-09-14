"""Generate official PACE icons (.ico and .png) with mathematical precision and anti-aliasing."""
from __future__ import annotations

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets" / "branding"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def draw_pace_icon(size: int, transparent_bg: bool = False, state_color: str | None = None) -> Image.Image:
    """Draw the PACE acoustic cursor icon at an arbitrary resolution with supersampling."""
    scale = 4  # 4x supersampling for razor-sharp anti-aliased curves
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = int(canvas_size * 0.0625)  # 32px on 512px
    squircle_size = canvas_size - 2 * margin
    corner_radius = int(canvas_size * 0.203)  # 104px on 512px

    if not transparent_bg:
        # Background Squircle: Matte Obsidian Slate (#10121A to #0A0B0F)
        squircle_box = (margin, margin, margin + squircle_size, margin + squircle_size)
        draw.rounded_rectangle(
            squircle_box,
            radius=corner_radius,
            fill=(14, 16, 23, 255),
            outline=(38, 43, 60, 255),
            width=max(scale, int(scale * 1.5)),
        )

        # Subtle ambient core glow behind the bars
        glow_size = int(canvas_size * 0.5)
        glow_center = canvas_size // 2
        glow_img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_img)
        glow_color = (99, 102, 241, 35)  # Electric Indigo with soft opacity
        glow_draw.ellipse(
            (
                glow_center - glow_size // 2,
                glow_center - glow_size // 2,
                glow_center + glow_size // 2,
                glow_center + glow_size // 2,
            ),
            fill=glow_color,
        )
        glow_img = glow_img.filter(ImageFilter.GaussianBlur(radius=scale * 12))
        img = Image.alpha_composite(img, glow_img)
        draw = ImageDraw.Draw(img)

    # 5 Acoustic Bars Geometry
    # On a 512 coordinate system:
    # Pill width: 28, spacing: 22 -> pitch 50.
    # Center x: 256. Center y: 256.
    c = canvas_size / 512.0
    bar_w = 28 * c
    radius = bar_w / 2.0
    spacing = 22 * c
    pitch = bar_w + spacing
    cx, cy = canvas_size / 2.0, canvas_size / 2.0

    # Bars definitions: (offset_from_center, height, is_center_cursor)
    bars = [
        (-2 * pitch, 76 * c, False),   # Left outer
        (-1 * pitch, 150 * c, False),  # Left middle
        (0,          240 * c, True),   # Center Caret / Lumen
        (1 * pitch,  150 * c, False),  # Right middle
        (2 * pitch,  76 * c, False),   # Right outer
    ]

    side_color = (68, 76, 102, 255) if not transparent_bg else (148, 163, 184, 255)
    
    if state_color:
        cursor_color = state_color
    else:
        cursor_color = (255, 255, 255, 255)

    # Draw side bars first
    for offset_x, h, is_center in bars:
        if is_center:
            continue
        x0 = cx + offset_x - radius
        x1 = cx + offset_x + radius
        y0 = cy - h / 2.0
        y1 = cy + h / 2.0
        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=side_color)

    # Draw center luminous cursor with subtle glow filter
    center_h = 240 * c
    x0 = cx - radius
    x1 = cx + radius
    y0 = cy - center_h / 2.0
    y1 = cy + center_h / 2.0

    if not transparent_bg:
        # Luminous caret halo
        halo = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        halo_draw = ImageDraw.Draw(halo)
        halo_draw.rounded_rectangle(
            (x0 - 4 * c, y0 - 4 * c, x1 + 4 * c, y1 + 4 * c),
            radius=radius + 4 * c,
            fill=(129, 140, 248, 110),
        )
        halo = halo.filter(ImageFilter.GaussianBlur(radius=scale * 4))
        img = Image.alpha_composite(img, halo)
        draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=cursor_color)

    # Downsample cleanly to target resolution
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    print(f"Generating PACE branding assets into {ASSETS_DIR}...")
    
    # 1. High-Res 512px PNG
    icon_512 = draw_pace_icon(512)
    png_path = ASSETS_DIR / "pace_512.png"
    icon_512.save(png_path, format="PNG")
    print(f"[OK] Saved {png_path}")

    # 2. Transparent 512px PNG (for web, docs, embeds)
    icon_trans = draw_pace_icon(512, transparent_bg=True)
    trans_path = ASSETS_DIR / "pace_symbol_trans.png"
    icon_trans.save(trans_path, format="PNG")
    print(f"[OK] Saved {trans_path}")

    # 3. Multi-resolution Windows .ico
    # Standard sizes: 16, 24, 32, 48, 64, 128, 256
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_pace_icon(s) for s in sizes]
    ico_path = ASSETS_DIR / "pace.ico"
    images[-1].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print(f"[OK] Saved multi-resolution {ico_path} with sizes: {sizes}")

    # 4. Also copy pace.ico to root for quick access
    root_ico = PROJECT_ROOT / "pace.ico"
    images[-1].save(
        root_ico,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print(f"[OK] Saved {root_ico}")


if __name__ == "__main__":
    main()
