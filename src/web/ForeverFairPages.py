# web/ForeverFairPages.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Define FastAPI routes and wire web dependencies.

from __future__ import annotations
import base64
import math
import mimetypes
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
import AuctionController
import BiddingController
from ForeverFairData import ForeverFairData
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import SetupForeverFairDB

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATCHMENT_ROOT = PROJECT_ROOT / "Catchment_data"
DEBUG_DB_PATH = CATCHMENT_ROOT / "Data_for_debugging" / "small_debug_database.db"
ACTIVE_CATCHMENT_ENV_VAR = "FOREVER_FAIR_CATCHMENT"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

def _available_catchment_dirs() -> list[Path]:
	if not CATCHMENT_ROOT.exists(): return []
	return sorted((path for path in CATCHMENT_ROOT.iterdir() if path.is_dir() and path.name != "Data_for_debugging"), key=lambda path: path.name.lower())

load_dotenv()
_configured = os.environ.get(ACTIVE_CATCHMENT_ENV_VAR, "").strip()
if not _configured:
	raise ValueError(f"{ACTIVE_CATCHMENT_ENV_VAR} is not set. Create a .env file in the project root with {ACTIVE_CATCHMENT_ENV_VAR}=<catchment name>.")
_available = _available_catchment_dirs()
if not any(path.name == _configured for path in _available):
	raise ValueError(f"Catchment {_configured!r} not found in {CATCHMENT_ROOT}. Available: {[p.name for p in _available]}")
_active_catchment_name = _configured
del _configured, _available
ffdata = ForeverFairData(db_path=CATCHMENT_ROOT / _active_catchment_name / "foreverfair.db", debug_db_path=DEBUG_DB_PATH)

def set_active_catchment(catchment_name: str) -> None:
	global ffdata, _active_catchment_name
	selected_name = catchment_name.strip()
	available_names = {path.name for path in _available_catchment_dirs()}
	if selected_name not in available_names: raise ValueError(f"Unknown catchment: {selected_name}")
	_active_catchment_name = selected_name
	ffdata = ForeverFairData(db_path=CATCHMENT_ROOT / selected_name / "foreverfair.db", debug_db_path=DEBUG_DB_PATH)

app = FastAPI(title="Forever Fair 2026")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

_auctionmanager_debug_log: list[str] = []
_auctionmanager_run_active = False

def add_auctionmanager_debug(message: str) -> None: _auctionmanager_debug_log.append(str(message))


def _format_iso_datetime_short(value: Any) -> str:
	text = str(value or "").strip()
	if not text:
		return ""
	try:
		normalized = text.replace("Z", "+00:00")
		dt = datetime.fromisoformat(normalized)
		return dt.strftime("%Y-%m-%d %H:%M:%S")
	except Exception:
		return text

def clear_auctionmanager_debug() -> None: _auctionmanager_debug_log.clear()

def get_auctionmanager_debug_text() -> str: return "\n".join(_auctionmanager_debug_log[-400:])

def set_auctionmanager_run_active(is_active: bool) -> None:
	global _auctionmanager_run_active
	_auctionmanager_run_active = is_active

def get_auctionmanager_run_active() -> bool: return _auctionmanager_run_active

def _flash_redirect(url: str, msg: str, status_code: int = 303) -> RedirectResponse:
	r = RedirectResponse(url=url, status_code=status_code)
	r.set_cookie("flash", msg, max_age=60, httponly=True, samesite="lax")
	return r

def _common_template_context() -> dict[str, Any]: return {"active_catchment_name": _active_catchment_name}

def _active_catchment_dir() -> Path:
	return CATCHMENT_ROOT / _active_catchment_name

def _get_catchment_svg_path() -> Path:
	catchment_dir = _active_catchment_dir()
	preferred = catchment_dir / "Tianqiao_wells_control_points.svg"
	if preferred.exists(): return preferred
	for svg_path in sorted(catchment_dir.glob("*.svg")):
		return svg_path
	raise HTTPException(status_code=404, detail=f"No SVG map found in {catchment_dir}")

def _get_catchment_png_path() -> Path:
	catchment_dir = _active_catchment_dir()
	configured_name = str(ffdata.get_map_background_settings().get("filename") or "").strip()
	if configured_name:
		configured_path = catchment_dir / configured_name
		if configured_path.exists(): return configured_path
	preferred = catchment_dir / "Tianxiao.png"
	if preferred.exists(): return preferred
	for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
		for image_path in sorted(catchment_dir.glob(pattern)):
			return image_path
	raise HTTPException(status_code=404, detail=f"No background image found in {catchment_dir}")

def _image_media_type(path: Path) -> str:
	media_type, _encoding = mimetypes.guess_type(str(path))
	return media_type or "application/octet-stream"

def _svg_canvas_box(svg_text: str) -> tuple[float, float, float, float]:
	viewbox = re.search(r'viewBox="([^"]+)"', svg_text)
	if viewbox:
		parts = [float(part) for part in re.split(r"\s+", viewbox.group(1).strip()) if part]
		if len(parts) == 4 and parts[2] > 0.0 and parts[3] > 0.0:
			return parts[0], parts[1], parts[2], parts[3]
	width_match = re.search(r'width="([0-9.]+)"', svg_text)
	height_match = re.search(r'height="([0-9.]+)"', svg_text)
	if width_match and height_match:
		width = float(width_match.group(1))
		height = float(height_match.group(1))
		if width > 0.0 and height > 0.0:
			return 0.0, 0.0, width, height
	return 0.0, 0.0, 1000.0, 1000.0

def _project_lat_lon_to_svg(lat: float, lon: float, bbox: tuple[float, float, float, float], canvas_box: tuple[float, float, float, float]) -> tuple[float, float] | None:
	west, south, east, north = bbox
	if east <= west or north <= south: return None
	min_x, min_y, width, height = canvas_box
	x = min_x + ((lon - west) / (east - west)) * width
	y = min_y + ((north - lat) / (north - south)) * height
	return x, y

def _inject_background_image(svg_text: str, image_data_url: str, canvas_box: tuple[float, float, float, float]) -> str:
	if re.search(r'<image[^>]+href="[^"]+"', svg_text):
		if 'href="Tianxiao.png"' in svg_text:
			return svg_text.replace('href="Tianxiao.png"', f'href="{image_data_url}"', 1)
		return re.sub(r'href="[^"]+\.(png|jpg|jpeg|webp|bmp)"', f'href="{image_data_url}"', svg_text, count=1, flags=re.IGNORECASE)
	min_x, min_y, width, height = canvas_box
	bg_line = f'  <image x="{min_x:.2f}" y="{min_y:.2f}" width="{width:.2f}" height="{height:.2f}" preserveAspectRatio="none" href="{image_data_url}" />'
	return svg_text.replace("</svg>", "\n" + bg_line + "\n</svg>")

def _fmt_num(value: Any, decimals: int = 2) -> str:
	if value is None: return ""
	try:
		return f"{float(value):.{decimals}f}"
	except Exception:
		return ""

def _fmt_money(value: Any, decimals: int = 2) -> str:
	formatted = _fmt_num(value, decimals)
	return "" if not formatted else f"${formatted}"

def _fmt_date_label(value: Any) -> str:
	text = str(value or "")
	return text.split("T", 1)[0] if "T" in text else text

def _label_to_numeric_suffix(label: str) -> int | None:
	match = re.search(r"(\d+)$", str(label or ""))
	return int(match.group(1)) if match else None

def _well_svg_label_from_name(well_name: str) -> str | None:
	suffix = _label_to_numeric_suffix(well_name)
	return None if suffix is None else f"W{suffix}"

def _cp_svg_label_from_name(cp_name: str) -> str | None:
	suffix = _label_to_numeric_suffix(cp_name)
	return None if suffix is None else f"CP-{suffix}"

def _choose_period_id_from_date(idx_to_iso: dict[Any, Any], date_text: str | None) -> int | None:
	if not idx_to_iso: return None
	if date_text:
		for key, iso_text in idx_to_iso.items():
			if _fmt_date_label(iso_text) == date_text:
				return int(key)
	first_key = next(iter(idx_to_iso.keys()), None)
	return int(first_key) if first_key is not None else None

def _overlay_prices_on_svg(svg_text: str, well_prices: dict[str, float | None], cp_duals: dict[str, float | None], geo_well_points: list[tuple[float, float, str, float | None]] | None = None, geo_cp_points: list[tuple[float, float, str, float | None]] | None = None) -> str:
	well_pattern = re.compile(r'<text[^>]*class="label wellLabel"[^>]*x="([^"]+)"[^>]*y="([^"]+)"[^>]*>([^<]+)</text>')
	cp_pattern = re.compile(r'<text[^>]*class="label cpLabel"[^>]*x="([^"]+)"[^>]*y="([^"]+)"[^>]*>([^<]+)</text>')
	overlay_lines: list[str] = []
	placed_boxes: list[tuple[float, float, float, float]] = []
	geo_well_points = geo_well_points or []
	geo_cp_points = geo_cp_points or []
	use_geo_wells = len(geo_well_points) > 0
	use_geo_cps = len(geo_cp_points) > 0

	view_box_match = re.search(r'<svg[^>]*viewBox="([^"]+)"', svg_text)
	svg_bounds: tuple[float, float, float, float] | None = None
	if view_box_match:
		parts = [part for part in view_box_match.group(1).replace(",", " ").split() if part]
		if len(parts) == 4:
			try:
				min_x = float(parts[0])
				min_y = float(parts[1])
				width = float(parts[2])
				height = float(parts[3])
				if width > 0.0 and height > 0.0:
					svg_bounds = (min_x, min_y, min_x + width, min_y + height)
			except Exception:
				svg_bounds = None
	if svg_bounds is None:
		width_match = re.search(r'width="([0-9.]+)"', svg_text)
		height_match = re.search(r'height="([0-9.]+)"', svg_text)
		if width_match and height_match:
			try:
				width = float(width_match.group(1))
				height = float(height_match.group(1))
				if width > 0.0 and height > 0.0:
					svg_bounds = (0.0, 0.0, width, height)
			except Exception:
				svg_bounds = None

	candidate_offsets: list[tuple[float, float]] = [
		(10.0, 0.0), (-10.0, 0.0), (0.0, -10.0), (0.0, 10.0),
		(14.0, -10.0), (14.0, 10.0), (-14.0, -10.0), (-14.0, 10.0),
		(20.0, 0.0), (-20.0, 0.0), (0.0, -20.0), (0.0, 20.0),
		(22.0, -16.0), (22.0, 16.0), (-22.0, -16.0), (-22.0, 16.0),
		(30.0, 0.0), (-30.0, 0.0), (0.0, -30.0), (0.0, 30.0),
		(34.0, -24.0), (34.0, 24.0), (-34.0, -24.0), (-34.0, 24.0),
		(44.0, 0.0), (-44.0, 0.0), (0.0, -44.0), (0.0, 44.0),
		(48.0, -34.0), (48.0, 34.0), (-48.0, -34.0), (-48.0, 34.0),
	]

	well_marker_pattern = re.compile(
		r'<circle[^>]*class="[^"]*\bwell\b[^"]*"[^>]*cx="([^"]+)"[^>]*cy="([^"]+)"[^>]*r="([^"]+)"[^>]*/>\s*'
		r'<text[^>]*class="[^"]*\bwellLabel\b[^"]*"[^>]*>([^<]+)</text>',
		re.IGNORECASE,
	)
	cp_marker_pattern = re.compile(
		r'<circle[^>]*class="[^"]*\bcp\b[^"]*"[^>]*cx="([^"]+)"[^>]*cy="([^"]+)"[^>]*r="([^"]+)"[^>]*/>\s*'
		r'<text[^>]*class="[^"]*\bcpLabel\b[^"]*"[^>]*>([^<]+)</text>',
		re.IGNORECASE,
	)
	marker_by_label: dict[str, tuple[float, float, float]] = {}
	all_markers: list[tuple[float, float]] = []
	marker_tick_pattern = re.compile(r'<circle[^>]*class="[^"]*\b(?:well|cp)\b[^"]*"[^>]*cx="([^"]+)"[^>]*cy="([^"]+)"[^>]*/>', re.IGNORECASE)
	for cx_text, cy_text in marker_tick_pattern.findall(svg_text):
		try:
			all_markers.append((float(cx_text), float(cy_text)))
		except Exception:
			pass
	for cx_text, cy_text, r_text, label_text in well_marker_pattern.findall(svg_text):
		try:
			marker_by_label[label_text.strip()] = (float(cx_text), float(cy_text), float(r_text))
		except Exception:
			pass
	for cx_text, cy_text, r_text, label_text in cp_marker_pattern.findall(svg_text):
		try:
			marker_by_label[label_text.strip()] = (float(cx_text), float(cy_text), float(r_text))
		except Exception:
			pass

	labels: list[tuple[float, float, float, float, str, str, float]] = []
	all_anchors: list[tuple[float, float]] = []

	def add_label(layout_x: float, layout_y: float, point_label: str, text: str, fill: str, marker_radius: float) -> None:
		marker = marker_by_label.get(point_label)
		if marker is None:
			marker_x, marker_y, radius = layout_x, layout_y, marker_radius
		else:
			marker_x, marker_y, radius = marker
		labels.append((layout_x, layout_y, marker_x, marker_y, text, fill, radius))
		all_anchors.append((layout_x, layout_y))

	def _escape_svg_text(text: str) -> str:
		return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

	def text_box(tx: float, ty: float, text: str) -> tuple[float, float, float, float]:
		text_width = 5.3 * len(text) + 4.0
		return (tx - 1.0, ty - 8.0, tx + text_width + 1.0, ty + 2.0)

	def overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
		overlap_w = min(a[2], b[2]) - max(a[0], b[0])
		overlap_h = min(a[3], b[3]) - max(a[1], b[1])
		if overlap_w <= 0.0 or overlap_h <= 0.0:
			return 0.0
		return overlap_w * overlap_h

	def distance_sq(x1: float, y1: float, x2: float, y2: float) -> float:
		dx = x1 - x2
		dy = y1 - y2
		return dx * dx + dy * dy

	def clamp_box_to_bounds(tx: float, ty: float, text: str) -> tuple[float, float, tuple[float, float, float, float]]:
		box = text_box(tx, ty, text)
		if svg_bounds is None:
			return tx, ty, box
		min_x, min_y, max_x, max_y = svg_bounds
		margin = 2.0
		shift_x = 0.0
		shift_y = 0.0
		if box[0] < min_x + margin:
			shift_x = (min_x + margin) - box[0]
		elif box[2] > max_x - margin:
			shift_x = (max_x - margin) - box[2]
		if box[1] < min_y + margin:
			shift_y = (min_y + margin) - box[1]
		elif box[3] > max_y - margin:
			shift_y = (max_y - margin) - box[3]
		if shift_x != 0.0 or shift_y != 0.0:
			tx += shift_x
			ty += shift_y
			box = text_box(tx, ty, text)
		return tx, ty, box

	def anchor_penalty(box: tuple[float, float, float, float], anchor_x: float, anchor_y: float) -> float:
		penalty = 0.0
		exclusion = 7.0
		for ax, ay in all_anchors:
			if ax == anchor_x and ay == anchor_y:
				continue
			closest_x = min(max(ax, box[0]), box[2])
			closest_y = min(max(ay, box[1]), box[3])
			if distance_sq(ax, ay, closest_x, closest_y) < exclusion * exclusion:
				penalty += 240.0
		return penalty

	def placement_score(box: tuple[float, float, float, float], anchor_x: float, anchor_y: float, tx: float, ty: float) -> float:
		overlap_penalty = sum(overlap_area(box, other) * 90.0 for other in placed_boxes)
		crowd_penalty = anchor_penalty(box, anchor_x, anchor_y)
		distance_penalty = abs(tx - anchor_x) * 0.25 + abs(ty - anchor_y) * 0.25
		return overlap_penalty + crowd_penalty + distance_penalty

	def nearest_point_on_box(from_x: float, from_y: float, box: tuple[float, float, float, float]) -> tuple[float, float]:
		px = min(max(from_x, box[0]), box[2])
		py = min(max(from_y, box[1]), box[3])
		return px, py

	def point_on_marker_edge(cx: float, cy: float, toward_x: float, toward_y: float, radius: float) -> tuple[float, float]:
		dx = toward_x - cx
		dy = toward_y - cy
		dist = math.hypot(dx, dy)
		if dist < 1e-6:
			return cx, cy
		scale = radius / dist
		return cx + (dx * scale), cy + (dy * scale)

	for x_val, y_val, label, price in geo_well_points:
		if price is None:
			continue
		add_label(x_val, y_val, label, f"{label}, {_fmt_money(price, 2)}", "#8a1f11", 4.6)

	for x_val, y_val, label, dual in geo_cp_points:
		if dual is None:
			continue
		add_label(x_val, y_val, label, f"{label}, d {_fmt_money(dual, 2)}", "#0a5a38", 5.1)

	if not use_geo_wells:
		for x_text, y_text, label in well_pattern.findall(svg_text):
			price = well_prices.get(label)
			if price is None:
				continue
			add_label(float(x_text), float(y_text), label, f"{label}, {_fmt_money(price, 2)}", "#8a1f11", 4.6)

	if not use_geo_cps:
		for x_text, y_text, label in cp_pattern.findall(svg_text):
			dual = cp_duals.get(label)
			if dual is None:
				continue
			add_label(float(x_text), float(y_text), label, f"{label}, d {_fmt_money(dual, 2)}", "#0a5a38", 5.1)

	# Place densest anchors first to improve outcomes in tightly clustered areas.
	density_by_index: dict[int, int] = {}
	for idx, (x_val, y_val, _marker_x, _marker_y, _text, _fill, _radius) in enumerate(labels):
		density = 0
		for jdx, (other_x, other_y, _other_marker_x, _other_marker_y, _other_text, _other_fill, _other_radius) in enumerate(labels):
			if idx == jdx:
				continue
			if distance_sq(x_val, y_val, other_x, other_y) <= 34.0 * 34.0:
				density += 1
		density_by_index[idx] = density

	ordered_labels = [labels[idx] for idx in sorted(range(len(labels)), key=lambda idx: density_by_index[idx], reverse=True)]

	for x, y, marker_x, marker_y, text, fill, marker_radius in ordered_labels:
		best_tx = x + candidate_offsets[0][0]
		best_ty = y + candidate_offsets[0][1]
		best_tx, best_ty, best_box = clamp_box_to_bounds(best_tx, best_ty, text)
		best_score = placement_score(best_box, x, y, best_tx, best_ty)
		for dx, dy in candidate_offsets:
			tx, ty = x + dx, y + dy
			tx, ty, box = clamp_box_to_bounds(tx, ty, text)
			score = placement_score(box, x, y, tx, ty)
			if score < best_score:
				best_tx, best_ty, best_box, best_score = tx, ty, box, score
		placed_boxes.append(best_box)
		dx = best_tx - x
		dy = best_ty - y
		if abs(dx) > 1.0 or abs(dy) > 1.0:
			line_end_x, line_end_y = nearest_point_on_box(marker_x, marker_y, best_box)
			line_start_x, line_start_y = point_on_marker_edge(marker_x, marker_y, line_end_x, line_end_y, marker_radius)
			overlay_lines.append(f'  <line x1="{line_start_x:.2f}" y1="{line_start_y:.2f}" x2="{line_end_x:.2f}" y2="{line_end_y:.2f}" stroke="#666" stroke-width="0.6"/>')
		overlay_lines.append(f'  <text class="label" x="{best_tx:.2f}" y="{best_ty:.2f}" fill="{fill}" style="font-size:10px;paint-order:stroke;stroke:#fff;stroke-width:1.2;">{_escape_svg_text(text)}</text>')

	if svg_bounds is not None:
		min_x, min_y, max_x, max_y = svg_bounds
		tick_len = 10.0
		for marker_x, marker_y in all_markers:
			if min_y <= marker_y <= max_y:
				overlay_lines.append(f'  <line x1="{(max_x - tick_len):.2f}" y1="{marker_y:.2f}" x2="{max_x:.2f}" y2="{marker_y:.2f}" stroke="#333" stroke-width="0.8"/>')
			if min_x <= marker_x <= max_x:
				overlay_lines.append(f'  <line x1="{marker_x:.2f}" y1="{(max_y - tick_len):.2f}" x2="{marker_x:.2f}" y2="{max_y:.2f}" stroke="#333" stroke-width="0.8"/>')

	if not overlay_lines: return svg_text
	stripped_svg = re.sub(r'<text[^>]*\bwellLabel\b[^>]*>[^<]*</text>', '', svg_text)
	stripped_svg = re.sub(r'<text[^>]*\bcpLabel\b[^>]*>[^<]*</text>', '', stripped_svg)
	return stripped_svg.replace("</svg>", "\n" + "\n".join(overlay_lines) + "\n</svg>")

def _build_well_price_matrix(auction: dict[str, Any], calendar: dict[str, Any], well_price_rows: list[dict[str, Any]]) -> dict[str, Any]:
	period_ids = [int(p["id"]) for p in auction.get("periods", [])]
	pumping_label_by_id = {idx: label for idx, label in calendar.get("idx_to_pumping_iso", {}).items()}
	periods = [{"period_id": period_id, "period_label": _fmt_date_label(pumping_label_by_id.get(period_id, f"P{period_id}"))} for period_id in period_ids]
	price_by_key = {(int(row["well_id"]), int(row["period_id"])): row.get("price") for row in well_price_rows}
	well_order: list[tuple[int, str]] = []
	seen: set[int] = set()
	for row in well_price_rows:
		well_id = int(row["well_id"])
		if well_id in seen: continue
		seen.add(well_id)
		well_order.append((well_id, str(row.get("well_name") or f"well-{well_id}")))
	matrix_rows: list[dict[str, Any]] = []
	for well_id, well_name in well_order:
		cells = [{"period_id": period["period_id"], "price": price_by_key.get((well_id, period["period_id"])), "price_text": _fmt_money(price_by_key.get((well_id, period["period_id"])), 4)} for period in periods]
		matrix_rows.append({"well_id": well_id, "well_name": well_name, "cells": cells})
	return {"periods": periods, "rows": matrix_rows}

def _build_control_point_matrix(calendar: dict[str, Any], control_point_rows: list[dict[str, Any]]) -> dict[str, Any]:
	effect_label_by_id = {idx: label for idx, label in calendar.get("idx_to_effect_iso", {}).items()}
	effect_period_ids = sorted({int(row["period_id"]) for row in control_point_rows})
	periods = [{"period_id": period_id, "period_label": _fmt_date_label(effect_label_by_id.get(period_id, f"E{period_id}"))} for period_id in effect_period_ids]
	cp_order: list[tuple[int, str]] = []
	seen: set[int] = set()
	by_key = {(int(row["control_point_id"]), int(row["period_id"])): row for row in control_point_rows}
	for row in control_point_rows:
		cp_id = int(row["control_point_id"])
		if cp_id in seen: continue
		seen.add(cp_id)
		cp_order.append((cp_id, str(row.get("control_point_name") or f"cp-{cp_id}")))

	metric_defs = [("bound", "Bound"), ("used", "Used impact"), ("slack", "Slack"), ("dual", "Dual price")]
	matrix_rows: list[dict[str, Any]] = []
	for period in periods:
		period_id = period["period_id"]
		for metric_key, metric_label in metric_defs:
			cells: list[str] = []
			for cp_id, _ in cp_order:
				row = by_key.get((cp_id, period_id), {})
				if metric_key == "dual": cells.append(_fmt_money(row.get("dual_value"), 4))
				elif metric_key == "used": cells.append(_fmt_num(row.get("used_capacity"), 4))
				elif metric_key == "bound": cells.append(_fmt_num(row.get("bound_capacity"), 4))
				else: cells.append(_fmt_num(row.get("slack"), 4))
			matrix_rows.append({"period_id": period_id, "period_label": period["period_label"], "metric_label": metric_label, "cells": cells})
	return {"periods": periods, "control_points": [{"control_point_id": cp_id, "control_point_name": cp_name} for cp_id, cp_name in cp_order], "rows": matrix_rows}

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
	traders = ffdata.list_of_traders()
	now_iso = ffdata.the_time_at_the_tone_is().isoformat(timespec="minutes")
	upcoming = ffdata.list_auctions()
	next_final = next((a for a in reversed(upcoming) if a["status"] == "OPEN" and a["auction_type"] != "tentative" and (a["closed_date"] or "") > now_iso), None)
	next_tentative = next((a for a in reversed(upcoming) if a["status"] == "OPEN" and a["auction_type"] == "tentative" and (a["closed_date"] or "") > now_iso), None)
	context = _common_template_context()
	context.update({"traders": traders, "next_final": next_final, "next_tentative": next_tentative, })
	return templates.TemplateResponse(request, "LoginPage.html", context)

@app.post("/login")
def do_login(trader_id: int = Form(...)):
	response = RedirectResponse(url="/trader", status_code=303)
	response.set_cookie("trader_id", str(trader_id), max_age=86400, httponly=True)
	return response

@app.get("/researcher", response_class=HTMLResponse)
def researcher_page(request: Request):
	traders = ffdata.list_of_traders()
	trader_well_counts = {int(trader["id"]): len(ffdata.get_trader_wells(int(trader["id"]))) for trader in traders}
	context = _common_template_context()
	context.update({"traders": traders, "trader_well_counts": trader_well_counts})
	return templates.TemplateResponse(request, "Researcher.html", context)

@app.get("/database-documentation", response_class=HTMLResponse)
def database_documentation_page(request: Request):
	return templates.TemplateResponse(request, "Database_documentation.html", _common_template_context())

@app.get("/hydrologist", response_class=HTMLResponse)
def doc_hydrologist(request: Request):
	notice = request.cookies.get("flash", "")
	start_pumping_dates = ["(no open auction)", "(no open auction)", "(no open auction)", "(no open auction)"]
	next_auction = ffdata.get_next_auction_info()
	auction_id: int | None = None
	if next_auction is not None:
		auction_id = int(next_auction["auction_id"])
	else:
		auctions = ffdata.list_auctions()
		if auctions:
			auction_id = max(int(auction["auction_id"]) for auction in auctions)
	if auction_id is not None:
		periods = ffdata.get_auction_info(auction_id)["periods"]
		for idx in range(min(4, len(periods))): start_pumping_dates[idx] = str(periods[idx]["label"]).split("T")[0]
	context = _common_template_context()
	context.update({"bounds_imported_at": ffdata.bounds_imported_at(), "notice": notice, "start_pumping_dates": start_pumping_dates,
		"head_adjustment_notice": ffdata.latest_aquifer_head_adjustment_notice(), })
	resp = templates.TemplateResponse(request, "Hydrologist.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.post("/hydrologist/create-synthetic-head-file")
async def hydrologist_create_synthetic_head_file(request: Request):
	try:
		form = await request.form()
		change_in_forecast = [float(str(form.get(f"change_in_forecast_{idx}", "1.0")).strip() or "1.0") for idx in range(4)]
		csv_path = AuctionController.create_synthetic_new_head(ffdata, change_in_forecast=change_in_forecast)
		return FileResponse(path=csv_path, media_type="text/csv", filename=csv_path.name)
	except Exception as e:
		return _flash_redirect("/hydrologist", f"Error creating synthetic head file: {e}")

@app.get("/programmer", response_class=HTMLResponse)
def doc_programmer(request: Request):
	report = SetupForeverFairDB.missing_import_data_report(ffdata.db_path)
	notice = request.cookies.get("flash", "")
	context = _common_template_context()
	map_settings = ffdata.get_map_background_settings()
	bbox = map_settings.get("bbox")
	period_length_hours = ffdata.latest_period_length_hours()
	bidding_periods = ffdata.get_number_of_bidding_periods()
	now_dt = ffdata.the_time_at_the_tone_is()
	context["today_display"] = now_dt.strftime("%d %b %Y")
	context["today_weekday"] = now_dt.strftime("%A")
	context["period_length_hours"] = period_length_hours
	context["bidding_periods"] = bidding_periods
	if period_length_hours is not None:
		close_dt, _, _ = ffdata.get_auction_close_first_last_dates(now_dt, period_length_hours, bidding_periods)
		period_td = timedelta(hours=period_length_hours)
		context["first_three_closes"] = [( close_dt + i * period_td).strftime("%d %b %Y %H:%M") for i in range(3)]
	else: context["first_three_closes"] = None
	context.update({"notice": notice, "missing_report": report, "available_catchments": [path.name for path in _available_catchment_dirs()], "max_bid_steps": ffdata.get_max_bid_steps(), "aquifer_head_limits_upload_date": ffdata.latest_aquifer_head_limits_upload_date(), "map_background_filename": str(map_settings.get("filename") or ""), "map_bbox_west": (bbox[0] if bbox else ""), "map_bbox_south": (bbox[1] if bbox else ""), "map_bbox_east": (bbox[2] if bbox else ""), "map_bbox_north": (bbox[3] if bbox else ""), })
	resp = templates.TemplateResponse(request, "Programmer.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/auctionmanager", response_class=HTMLResponse)
def doc_auctionmanager(request: Request):
	bidding_periods = ffdata.get_number_of_bidding_periods()
	next_auction = ffdata.get_next_auction_info()
	if next_auction is None:
		auctions = ffdata.list_auctions()
		next_auction_id = max((int(auction["auction_id"]) for auction in auctions), default=0) + 1
		if next_auction_id <= bidding_periods:
			AuctionController.create_auction(ffdata.db_path)
			next_auction = ffdata.get_next_auction_info()
	auctions = ffdata.list_auctions()
	remaining_auctions = ffdata.get_remaining_auctions_for_auction(int(next_auction["auction_id"])) if next_auction is not None else 0
	next_real_bid_count, next_default_bid_count = ffdata.get_bid_count(next_auction["auction_id"]) if next_auction is not None else (0, 0)
	period_length_hours = ffdata.latest_period_length_hours()
	now_dt = ffdata.the_time_at_the_tone_is()
	close_dt, default_first, default_last = ffdata.get_auction_close_first_last_dates(now_dt, period_length_hours or 168, bidding_periods)

	response_period_count = ffdata.response_matrix_period_count()
	context: dict[str, Any] = {"auction": next_auction, "auctions": auctions, "period_length_hours": period_length_hours, "response_period_count": response_period_count, "bidding_periods": bidding_periods, "remaining_auctions": remaining_auctions, "next_auction_id": next_auction["auction_id"] if next_auction is not None else "none", "next_real_bid_count": next_real_bid_count, "next_default_bid_count": next_default_bid_count,}
	context["rights_policy"] = ffdata.get_rights_policy()
	notice = request.cookies.get("flash", "")
	context["notice"] = notice
	context["now"] = now_dt.isoformat(timespec="minutes")
	context["today_display"] = now_dt.strftime("%d %b %Y")
	context["today_weekday"] = now_dt.strftime("%A")
	context["scheduled_close_display"] = close_dt.strftime("%d %b %Y %H:%M")
	context["default_first_display"] = default_first.strftime("%d %b %Y")
	context["default_last_display"] = default_last.strftime("%d %b %Y")
	context["default_close_time"] = close_dt.isoformat(timespec="minutes")
	context["default_first_water_take"] = default_first.isoformat(timespec="minutes")
	context["default_last_water_take"] = default_last.isoformat(timespec="minutes")
	context.update(_common_template_context())
	context["debug_text"] = get_auctionmanager_debug_text()
	if period_length_hours is not None and response_period_count:
		context["default_last_constrained"] = (default_first + timedelta(hours=period_length_hours * response_period_count)).strftime("%d %b %Y")
	else: context["default_last_constrained"] = ""
	resp = templates.TemplateResponse(request, "AuctionManager.html", context)
	resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
	resp.headers["Pragma"] = "no-cache"
	resp.headers["Expires"] = "0"
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/", response_class=HTMLResponse)
def home(): return RedirectResponse(url="/researcher", status_code=303)


def _build_trader_context(request: Request) -> dict[str, Any] | RedirectResponse:
	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	next_auction = ffdata.get_next_auction_info()
	if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
	auction_id = next_auction["auction_id"]
	auction_info = ffdata.get_auction_info(auction_id)
	history_auction_id = ffdata.get_previous_auction_id(auction_id)
	history_auction_info = ffdata.get_auction_info(history_auction_id) if history_auction_id is not None else None
	current_wells = ffdata.get_trader_wells(trader_id)
	selected_well_cookie = request.cookies.get("current_well_id", "")
	current_well_id_from_cookie = int(selected_well_cookie) if selected_well_cookie.isdigit() else None
	current_well = next((well for well in current_wells if int(well["id"]) == current_well_id_from_cookie), None) if current_well_id_from_cookie is not None else None
	if current_well is None and current_wells: current_well = current_wells[0]
	bid_history = ffdata.get_bid_history(history_auction_id, trader_id) if history_auction_id is not None else []
	current_well_id = current_well["id"] if current_well else None
	quota_by_period = ffdata.get_well_start_quota(well_id=current_well_id, auction_id=auction_id) if isinstance(current_well_id, int) else {}
	scaled_quota_by_period = ffdata.get_well_scaled_start_quota(well_id=current_well_id, auction_id=auction_id) if isinstance(current_well_id, int) else {}
	clearing_price_by_period = ffdata.get_well_clearing_price_for_current_rows(well_id=current_well_id, auction_id=auction_id) if isinstance(current_well_id, int) else {}
	history_quota_by_period = ffdata.get_well_start_quota(well_id=current_well_id, auction_id=history_auction_id) if isinstance(current_well_id, int) and history_auction_id is not None else {}
	history_clearing_price_by_period = {(period_id): price for ((well_id, period_id), price) in ffdata.get_well_dual_prices(history_auction_id).items() if well_id == current_well_id} if isinstance(current_well_id, int) and history_auction_id is not None else {}
	max_bid_steps = ffdata.get_max_bid_steps()
	latest_bids_by_period: dict[int, list[dict[str, Any]]] = {}
	for bid in bid_history:
		period_id = bid["period_id"]
		if period_id not in latest_bids_by_period: latest_bids_by_period[period_id] = []
		if latest_bids_by_period[period_id] and latest_bids_by_period[period_id][0]["bid_id"] != bid["bid_id"]: continue
		latest_bids_by_period[period_id].append(bid)
	period_rows: list[dict[str, Any]] = []
	for period in auction_info["periods"]: period_rows.append({"period_id": period["id"], "period_key": period["id"], "period_label": period["label"], "period_label_display": _format_iso_datetime_short(period["label"]), "allocation": quota_by_period.get(period["id"], 0.0), "scaled_allocation": scaled_quota_by_period.get(period["id"], 0.0), "clearing_price": clearing_price_by_period.get(period["id"]), "latest_bids": latest_bids_by_period.get(period["id"], []),})
	history_period_label_by_id = ({int(period["id"]): _format_iso_datetime_short(period["label"]) for period in history_auction_info["periods"]}
		if history_auction_info is not None else {})
	history_rows: list[dict[str, Any]] = []
	history_row_by_bid_id: dict[int, dict[str, Any]] = {}
	total_last_auction_payment = 0.0
	for bid in bid_history:
		bid_id = int(bid["bid_id"])
		if bid_id not in history_row_by_bid_id:
			period_id = int(bid["period_id"])
			allocation = history_quota_by_period.get(period_id, 0.0)
			final_allocation = bid["final_allocation"]
			traded_price = bid["traded_price"]
			quota_change = None
			clearing_action = "\u2014"
			payment_text = "\u2014"
			payment_value = 0.0
			if final_allocation is not None and traded_price is not None:
				quota_change = float(final_allocation) - float(allocation)
				if abs(quota_change) < 1.0e-9:
					clearing_action = "No change"
					payment_text = "$0.00"
					payment_value = 0.0
				elif quota_change > 0.0:
					clearing_action = "Purchase"
					payment_value = -abs(quota_change) * abs(float(traded_price))
					payment_text = f"Pays ${abs(payment_value):,.2f}"
				else:
					clearing_action = "Sale"
					payment_value = abs(quota_change) * abs(float(traded_price))
					payment_text = f"Receives ${abs(payment_value):,.2f}"
			total_last_auction_payment += payment_value
			history_row_by_bid_id[bid_id] = {
				"submitted_at": _format_iso_datetime_short(bid["submitted_at"]),
				"period_label": history_period_label_by_id.get(period_id, str(period_id)),
				"bid_id": bid_id,
				"allocation": allocation,
				"clearing_price": history_clearing_price_by_period.get(period_id),
				"steps": [],
				"final_allocation": final_allocation,
				"traded_price": traded_price,
				"quota_change": quota_change,
				"clearing_action": clearing_action,
				"payment_value": payment_value,
				"payment_text": payment_text,
			}
			history_rows.append(history_row_by_bid_id[bid_id])
		history_row_by_bid_id[bid_id]["steps"].append({"quantity": bid["quantity"], "price": bid["price"]})
	manual_submitted_at = [_format_iso_datetime_short(bid["submitted_at"]) for bid in bid_history if not bool(bid.get("is_default", False)) and bid.get("submitted_at")]
	bid_entry_status_message = f"Last submitted on {max(manual_submitted_at)}." if manual_submitted_at else "Not yet submitted. An automatic bid is in place."
	last_auction_submitted_values = [_format_iso_datetime_short(bid["submitted_at"]) for bid in bid_history if bid.get("submitted_at")]
	last_auction_submitted_at = max(last_auction_submitted_values) if last_auction_submitted_values else "-"
	last_auction_id_display = history_auction_id if history_auction_id is not None else "-"
	
	matching_traders = [t for t in ffdata.list_of_traders() if t["id"] == trader_id]
	current_trader: dict[str, Any] = matching_traders[0] if matching_traders else {"id": trader_id, "name": ""}
	context: dict[str, Any] = {"current_trader": current_trader, "current_well": current_well, "current_wells": current_wells, "bid_history": bid_history, "bid_history_rows": history_rows, "period_rows": period_rows, "auction_id": auction_id, "auction_case": {"auction": auction_info}, "max_bid_steps": max_bid_steps, "optional_bid_step_numbers": list(range(2, max_bid_steps + 1)), "bid_entry_status_message": bid_entry_status_message, "total_last_auction_payment": total_last_auction_payment, "last_auction_id": last_auction_id_display, "last_auction_submitted_at": last_auction_submitted_at,}
	notice = request.cookies.get("flash", "")
	context["notice"] = notice
	context.update(_common_template_context())
	return context

def _build_environmental_buyer_context(request: Request) -> dict[str, Any] | RedirectResponse:
	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)
	next_auction = ffdata.get_next_auction_info()
	if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
	auction_id = next_auction["auction_id"]
	auction_info = ffdata.get_auction_info(auction_id)
	history_auction_id = ffdata.get_previous_auction_id(auction_id)
	bid_rows = ffdata.get_environmental_bid_rows(auction_id, trader_id)
	for row in bid_rows: row["effect_date_display"] = _format_iso_datetime_short(row["effect_date"])
	bid_history = ffdata.get_environmental_bid_history(history_auction_id, trader_id) if history_auction_id is not None else []
	history_rows: list[dict[str, Any]] = []
	history_row_by_bid_id: dict[int, dict[str, Any]] = {}
	total_last_auction_payment = 0.0
	for bid in bid_history:
		env_bid_id = int(bid["env_bid_id"])
		if env_bid_id not in history_row_by_bid_id:
			traded_head_end = bid["traded_head_end"]
			traded_price = bid["traded_price"]
			payment_value = None if traded_head_end is None or traded_price is None else -abs(float(traded_head_end)) * abs(float(traded_price))
			if payment_value is not None: total_last_auction_payment += payment_value
			history_row_by_bid_id[env_bid_id] = {"env_bid_id": env_bid_id, "submitted_at": _format_iso_datetime_short(bid["submitted_at"]), "control_point_id": int(bid["control_point_id"]),
				"control_point_name": str(bid["control_point_name"]), "cpe_id": int(bid["cpe_id"]), "effect_date_display": _format_iso_datetime_short(bid["effect_date"]),
				"steps": [], "traded_head_end": traded_head_end, "traded_price": traded_price, "payment_value": payment_value}
			history_rows.append(history_row_by_bid_id[env_bid_id])
		history_row_by_bid_id[env_bid_id]["steps"].append({"quantity": bid["quantity"], "price": bid["price"]})
	last_auction_submitted_values = [_format_iso_datetime_short(bid["submitted_at"]) for bid in bid_history if bid.get("submitted_at")]
	last_auction_submitted_at = max(last_auction_submitted_values) if last_auction_submitted_values else "-"
	last_auction_id_display = history_auction_id if history_auction_id is not None else "-"
	matching_traders = [t for t in ffdata.list_of_traders() if t["id"] == trader_id]
	current_trader: dict[str, Any] = matching_traders[0] if matching_traders else {"id": trader_id, "name": "", "trader_type": "environmental"}
	context: dict[str, Any] = {"current_trader": current_trader, "bid_rows": bid_rows, "bid_history_rows": history_rows, "auction_id": auction_id,
		"auction_case": {"auction": auction_info}, "max_bid_steps": ffdata.get_max_bid_steps(), "optional_bid_step_numbers": list(range(2, ffdata.get_max_bid_steps() + 1)),
		"bid_entry_status_message": "Environmental buyers bid directly for control point events.", "last_auction_id": last_auction_id_display,
		"last_auction_submitted_at": last_auction_submitted_at, "total_last_auction_payment": total_last_auction_payment, "notice": request.cookies.get("flash", "")}
	context.update(_common_template_context())
	return context

@app.get("/trader", response_class=HTMLResponse)
def trader_page(request: Request):
	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)
	if ffdata.get_trader_type(trader_id) == "environmental":
		context = _build_environmental_buyer_context(request)
		if isinstance(context, RedirectResponse): return context
		resp = templates.TemplateResponse(request, "EnvironmentalBuyer.html", context)
		if context["notice"]: resp.delete_cookie("flash")
		return resp
	context = _build_trader_context(request)
	if isinstance(context, RedirectResponse): return context
	resp = templates.TemplateResponse(request, "Trader.html", context)
	if context["notice"]: resp.delete_cookie("flash")
	return resp

@app.get("/environmental-buyer", response_class=HTMLResponse)
def environmental_buyer_page(request: Request):
	trader_cookie = request.cookies.get("trader_id", "")
	environmental_traders = [trader for trader in ffdata.list_of_traders() if str(trader.get("trader_type") or "well") == "environmental"]
	if not environmental_traders: return _flash_redirect("/login", "No environmental buyer is configured.")
	environmental_trader_ids = {int(trader["id"]) for trader in environmental_traders}
	try: trader_id = int(trader_cookie) if trader_cookie else 0
	except ValueError: trader_id = 0
	if trader_id not in environmental_trader_ids:
		default_environmental_trader_id = min(environmental_trader_ids)
		response = RedirectResponse(url="/environmental-buyer", status_code=303)
		response.set_cookie("trader_id", str(default_environmental_trader_id), max_age=86400, httponly=True)
		return response
	context = _build_environmental_buyer_context(request)
	if isinstance(context, RedirectResponse): return context
	resp = templates.TemplateResponse(request, "EnvironmentalBuyer.html", context)
	if context["notice"]: resp.delete_cookie("flash")
	return resp

@app.post("/trader/select-well")
def trader_select_well(request: Request, well_id: int = Form(...)):
	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)
	current_wells = ffdata.get_trader_wells(trader_id)
	valid_well_ids = {int(well["id"]) for well in current_wells}
	if well_id not in valid_well_ids: return _flash_redirect("/trader", f"Error: Well {well_id} is not assigned to your account.")
	resp = RedirectResponse(url="/trader", status_code=303)
	resp.set_cookie("current_well_id", str(well_id), max_age=86400, httponly=True, samesite="lax")
	return resp

@app.post("/bids/new")
async def create_bid(request: Request):
	form = await request.form()
	return_to = str(form.get("return_to", "/trader")).strip()
	if return_to != "/trader": return_to = "/trader"
	try:
		auction_id = int(str(form["auction_id"]).strip())
		well_id = int(str(form["well_id"]).strip())
		period_id = int(str(form["period_id"]).strip())
		quantity = float(str(form["quantity"]).strip())
		price = float(str(form["price"]).strip())
		if get_auctionmanager_run_active():
			return _flash_redirect(return_to, "Error: Bid submission is locked because the auction manager is running the auction.")
	except Exception:
		q_text = str(form.get("quantity", "")).strip()
		p_text = str(form.get("price", "")).strip()
		pid_text = str(form.get("period_id", "")).strip()
		return _flash_redirect(return_to, f"Error: invalid bid form values for pumping period {pid_text} (quantity='{q_text}', price='{p_text}')")
	is_default = str(form.get("is_default", "")).strip().lower() in {"true", "on", "1", "yes"}

	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	bid_steps: list[tuple[float, float]] = [(quantity, price)]
	for step_num in range(2, ffdata.get_max_bid_steps() + 1):
		quantity_text = str(form[f"quantity{step_num}"] or "").strip()
		price_text = str(form[f"price{step_num}"] or "").strip()
		if not quantity_text and not price_text: continue
		if not quantity_text or not price_text:
			return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: pumping period {period_id}, step {step_num} requires both quantity and price (quantity='{quantity_text}', price='{price_text}')")
		try: bid_steps.append((float(quantity_text), float(price_text)))
		except ValueError:
			return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: pumping period {period_id}, step {step_num} has invalid quantity or price (quantity='{quantity_text}', price='{price_text}')")
	try: BiddingController.submitBid(ffdata, auction_id=auction_id, this_trader_id=trader_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_bid_default=is_default, bid_steps=bid_steps,)
	except ValueError as e: return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: {e}")
	return _flash_redirect(f"{return_to}?auction_id={auction_id}", "Bid saved")

@app.post("/environmental-bids/new")
async def create_environmental_bid(request: Request):
	form = await request.form()
	return_to = str(form.get("return_to", "/trader")).strip()
	if return_to != "/trader": return_to = "/trader"
	try:
		auction_id = int(str(form["auction_id"]).strip())
		cpe_id = int(str(form["cpe_id"]).strip())
		quantity = float(str(form["quantity"]).strip())
		price = float(str(form["price"]).strip())
		if get_auctionmanager_run_active(): return _flash_redirect(return_to, "Error: Bid submission is locked because the auction manager is running the auction.")
	except Exception:
		q_text = str(form.get("quantity", "")).strip()
		p_text = str(form.get("price", "")).strip()
		cpe_text = str(form.get("cpe_id", "")).strip()
		return _flash_redirect(return_to, f"Error: invalid environmental bid values for control point event {cpe_text} (quantity='{q_text}', price='{p_text}')")
	is_default = str(form.get("is_default", "")).strip().lower() in {"true", "on", "1", "yes"}
	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)
	bid_steps: list[tuple[float, float]] = [(quantity, price)]
	for step_num in range(2, ffdata.get_max_bid_steps() + 1):
		quantity_text = str(form[f"quantity{step_num}"] or "").strip()
		price_text = str(form[f"price{step_num}"] or "").strip()
		if not quantity_text and not price_text: continue
		if not quantity_text or not price_text: return _flash_redirect(return_to, f"Error: control point event {cpe_id}, step {step_num} requires both quantity and price (quantity='{quantity_text}', price='{price_text}')")
		try: bid_steps.append((float(quantity_text), float(price_text)))
		except ValueError: return _flash_redirect(return_to, f"Error: control point event {cpe_id}, step {step_num} has invalid quantity or price (quantity='{quantity_text}', price='{price_text}')")
	try: BiddingController.submitEnvironmentalBid(ffdata, trader_id=trader_id, auction_id=auction_id, cpe_id=cpe_id, quantity=quantity, price=price, is_bid_default=is_default, bid_steps=bid_steps)
	except ValueError as e: return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: {e}")
	return _flash_redirect(f"{return_to}?auction_id={auction_id}", "Environmental bid saved")

@app.post("/bids/{bid_id}/delete")
async def delete_bid(request: Request, bid_id: int):
	form = await request.form()
	return_to = str(form.get("return_to", "/trader")).strip()
	if return_to != "/trader": return_to = "/trader"
	trader_cookie = request.cookies.get("trader_id", "")
	trader_id = int(trader_cookie) if trader_cookie and trader_cookie.isdigit() else 0
	deleted = BiddingController.deleteBid(ffdata, bid_id, trader_id)
	return _flash_redirect(return_to, "Bid deleted" if deleted else "Bid not found")

@app.post("/auctionmanager/run-auction")
async def manager_run_auction(request: Request):
	try:
		form = await request.form()
		auction_id = int(str(form["auction_id"]).strip())
	except (ValueError, KeyError): return JSONResponse({"ok": False, "message": "Error: Missing or invalid auction_id"}, status_code=400)
	if ffdata.get_remaining_auctions_for_auction(auction_id) <= 0:
		if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": False, "message": "Error: No auctions remain in this schedule."}, status_code=400)
		return _flash_redirect("/auctionmanager", "Error: No auctions remain in this schedule.")
	
	# Guard: do not run an auction that has already closed by time.
	target = next((a for a in ffdata.list_auctions() if a["auction_id"] == auction_id), None)
	if target is None:
		if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": False, "message": "Error: Auction not found"}, status_code=404)
		return _flash_redirect("/auctionmanager", "Error: Auction not found")

	bid_close = target["closed_date"] or ""
	if target["status"] == "CLOSED" or (bid_close and bid_close < ffdata.the_time_at_the_tone_is().isoformat(timespec="minutes")):
		if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": False, "message": "Error: Cannot run a closed auction"}, status_code=400)
		return _flash_redirect("/auctionmanager", "Error: Cannot run a closed auction")
	try:
		clear_auctionmanager_debug()
		set_auctionmanager_run_active(True)
		add_auctionmanager_debug(f"Run requested for auction_id={auction_id}")

		# Run the auction,
		revenue = await run_in_threadpool(AuctionController.runCurrentAuction, ffdata, auction_id, add_auctionmanager_debug)
		add_auctionmanager_debug("Auction run completed")
	except Exception as e:
		add_auctionmanager_debug(f"Error: {e}")
		if request.headers["x-requested-with"] == "fetch":
			set_auctionmanager_run_active(False)
			if str(e) == "The auction cannot run because it has no bids.": return JSONResponse({"ok": False, "message": "The auction cannot run because it has no bids."}, status_code=400)
			return JSONResponse({"ok": False, "message": f"Error: {e}"}, status_code=500)
		set_auctionmanager_run_active(False)
		if str(e) == "The auction cannot run because it has no bids.": return _flash_redirect("/auctionmanager", "The auction cannot run because it has no bids.")
		return _flash_redirect("/auctionmanager", f"Error: {e}")
	set_auctionmanager_run_active(False)
	redirect_url = "/auctionmanager"
	if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": True, "message": "Auction run completed", "redirect_url": redirect_url, "revenue": float(revenue or 0.0)})
	return RedirectResponse(url=redirect_url, status_code=303)

@app.get("/api/auctionmanager-debug")
def api_auctionmanager_debug() -> JSONResponse: return JSONResponse({"debug_text": get_auctionmanager_debug_text(), "run_active": get_auctionmanager_run_active()}, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})

@app.get("/catchment-map-svg")
def catchment_map_svg(request: Request, auction_id: int | None = None, pumping_date: str | None = None):
	if auction_id is None:
		latest_with_results = ffdata.get_latest_auction_with_catchment_results()
		if latest_with_results is None: raise HTTPException(status_code=404, detail="No auction with catchment results found")
		auction_id = latest_with_results
	calendar = ffdata.get_auction_calendar(auction_id)
	svg_text = _get_catchment_svg_path().read_text(encoding="utf-8")
	canvas_box = _svg_canvas_box(svg_text)
	background_path = _get_catchment_png_path()
	background_b64 = base64.b64encode(background_path.read_bytes()).decode("ascii")
	background_data_url = f"data:{_image_media_type(background_path)};base64,{background_b64}"
	svg_text = _inject_background_image(svg_text, background_data_url, canvas_box)
	bbox_settings = ffdata.get_map_background_settings().get("bbox")

	pumping_period_id = _choose_period_id_from_date(calendar.get("idx_to_pumping_iso", {}), pumping_date)
	effect_period_id = _choose_period_id_from_date(calendar.get("idx_to_effect_iso", {}), pumping_date)
	well_rows, control_point_rows = ffdata.catchment_price_rows(auction_id)
	well_prices_by_label: dict[str, float | None] = {}
	cp_duals_by_label: dict[str, float | None] = {}
	geo_well_points: list[tuple[float, float, str, float | None]] = []
	geo_cp_points: list[tuple[float, float, str, float | None]] = []
	for row in well_rows:
		if pumping_period_id is not None and int(row["period_id"]) != pumping_period_id: continue
		lat_value = row.get("latitude")
		lon_value = row.get("longitude")
		well_name = str(row.get("well_name") or "")
		display_label = _well_svg_label_from_name(well_name) or well_name
		if bbox_settings is not None and lat_value is not None and lon_value is not None:
			xy = _project_lat_lon_to_svg(float(lat_value), float(lon_value), bbox_settings, canvas_box)
			if xy is not None:
				geo_well_points.append((xy[0], xy[1], display_label, row.get("price")))
				continue
		svg_label = _well_svg_label_from_name(well_name)
		if svg_label is None: continue
		well_prices_by_label[svg_label] = row.get("price")
	for row in control_point_rows:
		if effect_period_id is not None and int(row["period_id"]) != effect_period_id: continue
		lat_value = row.get("latitude")
		lon_value = row.get("longitude")
		cp_name = str(row.get("control_point_name") or "")
		display_label = _cp_svg_label_from_name(cp_name) or cp_name
		if bbox_settings is not None and lat_value is not None and lon_value is not None:
			xy = _project_lat_lon_to_svg(float(lat_value), float(lon_value), bbox_settings, canvas_box)
			if xy is not None:
				geo_cp_points.append((xy[0], xy[1], display_label, row.get("dual_value")))
				continue
		svg_label = _cp_svg_label_from_name(cp_name)
		if svg_label is None: continue
		cp_duals_by_label[svg_label] = row.get("dual_value")
	svg_text = _overlay_prices_on_svg(svg_text, well_prices_by_label, cp_duals_by_label, geo_well_points=geo_well_points, geo_cp_points=geo_cp_points)
	return Response(content=svg_text, media_type="image/svg+xml")

@app.get("/catchment-map-background")
def catchment_map_background():
	path = _get_catchment_png_path()
	return FileResponse(path, media_type=_image_media_type(path))

@app.get("/catchment", response_class=HTMLResponse)
def catchment_page(request: Request, auction_id: int | None = None):
	if auction_id is None:
		latest_with_results = ffdata.get_latest_auction_with_catchment_results()
		if latest_with_results is not None:
			auction_id = latest_with_results
		else:
			next_auction = ffdata.get_next_auction_info()
			if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
			auction_id = next_auction["auction_id"]
	auction = ffdata.get_auction_info(auction_id)
	calendar = ffdata.get_auction_calendar(auction_id)
	well_price_rows, control_point_rows = ffdata.catchment_price_rows(auction_id)
	context: dict[str, Any] = {
		"catchment_name": ffdata.get_catchment_name(),
		"auction": auction,
		"map_svg_url": str(request.url_for("catchment_map_svg")),
		"well_price_rows": well_price_rows,
		"control_point_rows": control_point_rows,
		"well_price_matrix": _build_well_price_matrix(auction, calendar, well_price_rows),
		"control_point_matrix": _build_control_point_matrix(calendar, control_point_rows),
	}
	context.update(_common_template_context())
	notice = request.cookies.get("flash", "")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "CatchmentView.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/api/system-state")
def system_state_api(auction_id: int | None = None) -> dict[str, Any]:
	if auction_id is None:
		next_auction = ffdata.get_next_auction_info()
		if next_auction is None: return {"error": "No open auction. Ask the auction manager to create one."}
		auction_id = next_auction["auction_id"]
	latest_run = ffdata.get_run_summary(auction_id)
	well_price_rows, control_point_rows = ffdata.catchment_price_rows(auction_id)
	return {"catchment_name": ffdata.get_catchment_name(), "auction": ffdata.get_auction_info(auction_id), "rights_conversion": ffdata.get_rights_conversion_dict(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

@app.get("/api/open-auctions")
def api_open_auctions():
	try:
		return JSONResponse([{"id": a["auction_id"], "closed_date": a["closed_date"]} for a in ffdata.list_open_auctions()])
	except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/setup/db-status")
def setup_db_status(): return JSONResponse(SetupForeverFairDB.db_status(ffdata.db_path), headers={"Cache-Control": "no-store"})

@app.post("/setup/create-db")
def setup_create_db():
	try:
		SetupForeverFairDB.create_empty_db(ffdata.db_path)
		return _flash_redirect("/programmer", "Empty database created")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error creating database: {e}")

@app.post("/setup/select-catchment")
def setup_select_catchment(catchment_name: str = Form(...)):
	try:
		set_active_catchment(catchment_name)
		return _flash_redirect("/programmer", f"Active catchment set to {catchment_name}")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error selecting catchment: {e}")

@app.post("/setup/delete-db")
def setup_delete_db():
	try:
		SetupForeverFairDB.delete_db(ffdata.db_path)
		if ffdata.db_path.exists(): return _flash_redirect("/programmer", "Error deleting database: database file still exists")
		SetupForeverFairDB.create_empty_db(ffdata.db_path)
		return _flash_redirect("/programmer", "Database deleted")
	except Exception as e:
		import logging
		logging.getLogger("uvicorn.error").exception("Delete-db failed for %s", db_path)
		return _flash_redirect("/programmer", f"Error deleting database: {e}")

@app.post("/setup/import-decvar")
async def setup_import_decvar(file: UploadFile = File(...),):
	status = SetupForeverFairDB.db_status(ffdata.db_path)
	tables = status["tables"]
	if not status["exists"] or "wells" not in tables:
		return _flash_redirect("/programmer", "Create new dataase first")
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_decvar(ffdata.db_path, text)
	notice = (f"DECVAR import complete: {result['wells_inserted']} wells inserted"
	          f" (inferred: {result['num_wells']} wells, {result['num_pump_periods']} pump periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-hedcon")
async def setup_import_hedcon(file: UploadFile = File(...),):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		result = SetupForeverFairDB.import_hedcon(ffdata.db_path, text)
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing HEDCON: {e}")
	notice = (f"HEDCON import complete: {result['control_points_inserted']} control points,"
	          f" {result['control_point_rows_inserted']} minimum-head rows inserted"
	          f" (inferred: {result['num_control_points']} control points, {result['num_control_periods']} control periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-mps")
async def setup_import_mps(file: UploadFile = File(...), period_unit: str = Form(...),):
	try:
		unit_hours = {"hour": 1, "day": 24, "week": 168}[str(period_unit).strip().lower()]
		text = (await file.read()).decode("utf-8", errors="replace")
		result = SetupForeverFairDB.import_mps(ffdata.db_path, text, period_length_hours=unit_hours)
	except KeyError:
		return _flash_redirect("/programmer", "Error importing MPS: invalid period unit")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing MPS: {e}")
	notice = (f"MPS import complete: {result['response_matrix_inserted']} response factors,"
		      f" {result['aquifer_head_rows_inserted']} aquifer-head rows,"
		      f" {result['license_rows_inserted']} trader-license rows"
		      f" ({result['wells_ensured']} wells ensured)"
		      f" (using: {result['num_wells']} wells, {result['num_pump_periods']} pump periods,"
		      f" {result['num_control_points']} control points, {result['num_control_periods']} control periods)")
	notice += f"; period length set to {result['period_length_hours']} hours"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.get("/setup/current-period-unit")
def setup_current_period_unit() -> dict[str, Any]:
	if not ffdata.db_path.exists(): return {"period_unit": None, "period_length_hours": None}
	hours = ffdata.latest_period_length_hours()
	if hours is None: return {"period_unit": None, "period_length_hours": None}
	if hours == 1: unit = "hour"
	elif hours == 24: unit = "day"
	elif hours == 168: unit = "week"
	else: unit = "day"
	return {"period_unit": unit, "period_length_hours": hours}

@app.post("/setup/set-period-unit")
async def setup_set_period_unit(request: Request) -> dict[str, Any]:
	body = await request.json()
	unit = str(body["period_unit"]).strip().lower()
	try: unit_hours = {"hour": 1, "day": 24, "week": 168}[unit]
	except KeyError: return {"ok": False, "error": "invalid period unit"}
	if not ffdata.db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "period_length_hours", unit_hours)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "period_length_hours": unit_hours}

@app.get("/setup/current-bidding-periods")
def setup_current_bidding_periods():
	return {"num_bidding_periods": ffdata.get_number_of_bidding_periods()}

@app.get("/setup/current-max-bid-steps")
def setup_current_max_bid_steps() -> dict[str, Any]:
	return {"max_bid_steps": ffdata.get_max_bid_steps()}

@app.get("/setup/current-rights-policy")
def setup_current_rights_policy() -> dict[str, Any]:
	return {"rights_policy": ffdata.get_rights_policy()}

@app.post("/setup/set-rights-policy")
async def setup_set_rights_policy(request: Request) -> dict[str, Any]:
	VALID_POLICIES = {"Users_pay", "Auction_manager_pays", "Quota_scaled"}
	body = await request.json()
	value = body["rights_policy"]
	if value not in VALID_POLICIES:
		return {"ok": False, "error": f"invalid rights policy: {value}"}
	if not ffdata.db_path.exists():
		return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "Rights_policy", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "rights_policy": value}

@app.post("/setup/set-bidding-periods")
async def setup_set_bidding_periods(request: Request) -> dict[str, Any]:
	body = await request.json()
	try:
		value = int(body["num_bidding_periods"])
	except Exception:
		return {"ok": False, "error": "invalid number of bidding periods"}
	if value < 1 or value > 52: return {"ok": False, "error": "number of bidding periods must be 1..52"}
	if not ffdata.db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "num_bidding_periods", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "num_bidding_periods": value}

@app.post("/setup/set-max-bid-steps")
async def setup_set_max_bid_steps(request: Request) -> dict[str, Any]:
	body = await request.json()
	try:
		value = int(body["max_bid_steps"])
	except Exception:
		return {"ok": False, "error": "invalid maximum bid steps"}
	if value < 1 or value > 5:
		return {"ok": False, "error": "maximum bid steps must be 1..5"}
	if not ffdata.db_path.exists():
		return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "MAX_BID_STEPS", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "max_bid_steps": value}

@app.post("/setup/import-trader-names")
async def setup_import_trader_names(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_trader_names(ffdata.db_path, text)
	notice = (f"Trader names import: {result['traders_inserted']} inserted,"
	          f" {result['traders_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-trader-wells")
async def setup_import_trader_wells(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_trader_wells(ffdata.db_path, text)
	notice = f"Trader-well assignments: {result['wells_assigned']} wells assigned"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-well-lat-lon")
async def setup_import_well_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_well_lat_lon(ffdata.db_path, text)
	notice = (f"Well lat-lon import: {result['wells_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-control-point-lat-lon")
async def setup_import_control_point_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_control_point_lat_lon(ffdata.db_path, text)
	notice = (f"Control-point lat-lon import: {result['control_points_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-map-background")
async def setup_import_map_background(file: UploadFile = File(...), bbox_west: float = Form(...), bbox_south: float = Form(...), bbox_east: float = Form(...), bbox_north: float = Form(...)):
	if not (-180.0 <= bbox_west <= 180.0 and -180.0 <= bbox_east <= 180.0 and -90.0 <= bbox_south <= 90.0 and -90.0 <= bbox_north <= 90.0):
		return _flash_redirect("/programmer", "Map bbox is out of valid latitude/longitude range")
	if bbox_west >= bbox_east:
		return _flash_redirect("/programmer", "Map bbox must satisfy west < east")
	if bbox_south >= bbox_north:
		return _flash_redirect("/programmer", "Map bbox must satisfy south < north")
	original_name = Path(file.filename or "").name
	suffix = Path(original_name).suffix.lower()
	if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
		return _flash_redirect("/programmer", "Background image must be .png, .jpg, .jpeg, .webp, or .bmp")
	body = await file.read()
	if not body:
		return _flash_redirect("/programmer", "Background image file is empty")
	if len(body) > 25 * 1024 * 1024:
		return _flash_redirect("/programmer", "Background image is too large (max 25 MB)")
	safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem).strip("._") or "map_background"
	safe_name = f"{safe_stem}{suffix}"
	target_path = _active_catchment_dir() / safe_name
	target_path.write_bytes(body)
	ffdata.save_map_background_settings(safe_name, bbox_west, bbox_south, bbox_east, bbox_north)
	return _flash_redirect("/programmer", f"Map background saved: {safe_name} with bbox {bbox_west},{bbox_south},{bbox_east},{bbox_north}")

@app.post("/setup/import-aquifer-head-limits")
async def setup_import_aquifer_head_limits(file: UploadFile = File(...)):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		updated_rows = ffdata.update_aquifer_head_limits(text)
		return _flash_redirect("/programmer", f"Aquifer head limits updated: {updated_rows} rows")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing aquifer head limits: {e}")

@app.post("/setup/setup-first-auction")
def setup_first_auction():
	try:
		auction_id = AuctionController.set_up_auction_system(ffdata.db_path)
		return _flash_redirect("/programmer", f"Auction system set up: auction_id={auction_id}")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error setting up auction system: {e}")
