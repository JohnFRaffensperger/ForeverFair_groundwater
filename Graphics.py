from __future__ import annotations

from pathlib import Path
import shutil

from PIL import Image

# Tianqiao model-space coordinates from legacy case data.
# Format: (id, row, col)
WELLS = [
    ("gwm-well-1", 29, 168),
    ("gwm-well-2", 161, 109),
    ("gwm-well-3", 205, 80),
    ("gwm-well-4", 207, 79),
    ("gwm-well-5", 200, 79),
    ("gwm-well-6", 207, 78),
    ("gwm-well-7", 161, 111),
    ("gwm-well-8", 205, 82),
    ("gwm-well-9", 161, 110),
    ("gwm-well-10", 160, 110),
    ("gwm-well-11", 205, 84),
    ("gwm-well-12", 161, 112),
    ("gwm-well-13", 206, 78),
    ("gwm-well-14", 162, 110),
    ("gwm-well-15", 200, 80),
    ("gwm-well-16", 160, 109),
    ("gwm-well-17", 30, 169),
    ("gwm-well-18", 200, 81),
    ("gwm-well-19", 160, 108),
    ("gwm-well-20", 204, 82),
    ("gwm-well-21", 159, 109),
    ("gwm-well-22", 204, 80),
    ("gwm-well-23", 159, 108),
    ("gwm-well-24", 155, 120),
    ("gwm-well-25", 64, 169),
    ("gwm-well-26", 64, 168),
    ("gwm-well-27", 208, 77),
    ("gwm-well-28", 207, 81),
    ("gwm-well-29", 150, 125),
]

CONTROL_POINTS = [
    ("cp-1", 206, 80),
    ("cp-2", 160, 111),
    ("cp-3", 238, 122),
    ("cp-4", 134, 18),
    ("cp-5", 123, 155),
    ("cp-6", 49, 156),
    ("cp-7", 161, 108),
    ("cp-8", 204, 81),
    ("cp-9", 30, 168),
    ("cp-10", 65, 169),
]

MAPS_DIR = Path("src/web/static/maps")
LEGACY_BG = Path("Documentation/Tianxiao.png")
BG_PATH = MAPS_DIR / "Tianxiao.png"
SVG_PATH = MAPS_DIR / "Tianqiao_wells_control_points.svg"


def _ensure_background() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    if not BG_PATH.exists() and LEGACY_BG.exists():
        shutil.move(str(LEGACY_BG), str(BG_PATH))
    if not BG_PATH.exists():
        raise FileNotFoundError(f"Background image not found: {BG_PATH}")


def _project_points(width: int, height: int, margin: int = 28) -> tuple[list[dict], list[dict]]:
    all_rows = [row for _, row, _ in WELLS] + [row for _, row, _ in CONTROL_POINTS]
    all_cols = [col for _, _, col in WELLS] + [col for _, _, col in CONTROL_POINTS]

    min_row, max_row = min(all_rows), max(all_rows)
    min_col, max_col = min(all_cols), max(all_cols)

    span_x = max_col - min_col
    span_y = max_row - min_row

    draw_w = max(1, width - 2 * margin)
    draw_h = max(1, height - 2 * margin)

    def to_xy(row: int, col: int) -> tuple[float, float]:
        x = margin + (col - min_col) / span_x * draw_w
        y = margin + (row - min_row) / span_y * draw_h
        return x, y

    wells_xy = []
    cps_xy = []

    for pid, row, col in WELLS:
        x, y = to_xy(row, col)
        wells_xy.append({"id": pid, "x": x, "y": y, "row": row, "col": col})

    for pid, row, col in CONTROL_POINTS:
        x, y = to_xy(row, col)
        cps_xy.append({"id": pid, "x": x, "y": y, "row": row, "col": col})

    return wells_xy, cps_xy


def _build_svg(width: int, height: int, wells_xy: list[dict], cps_xy: list[dict]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "  <defs>",
        "    <style>",
        "      .label { font-family: Arial, sans-serif; font-size: 12px; font-weight: 700; }",
        "      .well { fill: #f9fbff; stroke: #004c99; stroke-width: 1.5; }",
        "      .cp { fill: #111111; stroke: #ffffff; stroke-width: 1.0; }",
        "      .cpLabel { fill: #111111; }",
        "      .wellLabel { fill: #003b7a; }",
        "      .title { font-family: Arial, sans-serif; font-size: 18px; font-weight: 700; fill: #111111; }",
        "      .attribution { font-family: Arial, sans-serif; font-size: 11px; fill: #111111; }",
        "    </style>",
        "  </defs>",
        f'  <image href="{BG_PATH.name}" x="0" y="0" width="{width}" height="{height}" preserveAspectRatio="none"/>',
        '  <rect x="0" y="0" width="100%" height="100%" fill="none" stroke="#111111" stroke-width="1"/>',
        '  <text x="16" y="28" class="title">Tianqiao Wells and Control Points</text>',
    ]

    for w in wells_xy:
        lines.append(f'  <circle class="well" cx="{w["x"]:.2f}" cy="{w["y"]:.2f}" r="4.6"/>')
        lines.append(
            f'  <text class="label wellLabel" x="{w["x"] + 6.0:.2f}" y="{w["y"] - 6.0:.2f}">{w["id"].replace("gwm-well-", "W")}</text>'
        )

    for cp in cps_xy:
        lines.append(f'  <circle class="cp" cx="{cp["x"]:.2f}" cy="{cp["y"]:.2f}" r="5.1"/>')
        lines.append(
            f'  <text class="label cpLabel" x="{cp["x"] + 6.0:.2f}" y="{cp["y"] + 14.0:.2f}">{cp["id"].upper()}</text>'
        )

    lines.extend([
        f'  <text x="16" y="{height - 30}" class="attribution">Map style/tiles: CyclOSM (OpenStreetMap France)</text>',
        f'  <text x="16" y="{height - 14}" class="attribution">Map data: OpenStreetMap contributors (ODbL 1.0)</text>',
        "</svg>",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    _ensure_background()
    width, height = Image.open(BG_PATH).size
    wells_xy, cps_xy = _project_points(width=width, height=height)
    svg_text = _build_svg(width=width, height=height, wells_xy=wells_xy, cps_xy=cps_xy)
    SVG_PATH.write_text(svg_text, encoding="utf-8")

    print(f"Background image: {BG_PATH.resolve()}")
    print(f"SVG written:      {SVG_PATH.resolve()}")
    print(f"Canvas size:      {width}x{height}")
    print(f"Wells:            {len(WELLS)}")
    print(f"Control points:   {len(CONTROL_POINTS)}")


if __name__ == "__main__":
    main()
