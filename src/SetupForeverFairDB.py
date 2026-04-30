# SetupForeverFairDB.py. JFR / Claude, 2026-04-24.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Database creation, deletion, and GWM2K file import utilities.
# Keep SCHEMA_DDL in sync with the executescript() call in services/repository.py.

from __future__ import annotations
import gc
import math
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS metadata (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL );
CREATE TABLE IF NOT EXISTS traders (trader_id INTEGER PRIMARY KEY AUTOINCREMENT, name_tag TEXT NOT NULL, trader_loginid TEXT, trader_password TEXT, trader_first_name TEXT, trader_last_name TEXT, trader_address TEXT, trader_city TEXT, trader_phone TEXT, trader_email TEXT );
CREATE TABLE IF NOT EXISTS wells (well_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, trader_id INTEGER, gw_model_layer INTEGER, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL, FOREIGN KEY (trader_id) REFERENCES traders(trader_id) );
CREATE TABLE IF NOT EXISTS control_points (control_point_id TEXT PRIMARY KEY, name TEXT NOT NULL, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL );
CREATE TABLE IF NOT EXISTS auctions (auction_id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, created_date TEXT NOT NULL, closed_date TEXT, firstWaterTakeDate TEXT, lastWaterTakeDate TEXT, period_length_hours INTEGER, auction_type TEXT, solve_status TEXT, objective_value REAL );
CREATE TABLE IF NOT EXISTS trader_allocations (auction_id TEXT NOT NULL, trader_id INTEGER NOT NULL, period_id TEXT NOT NULL, allocation REAL NOT NULL, PRIMARY KEY (auction_id, trader_id, period_id), FOREIGN KEY (trader_id) REFERENCES traders(trader_id) );
CREATE TABLE IF NOT EXISTS default_control_point_bounds (control_point_id TEXT NOT NULL, period_id INTEGER NOT NULL, bound REAL NOT NULL, imported_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (control_point_id, period_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS control_point_bounds (auction_id TEXT NOT NULL, control_point_id TEXT NOT NULL, period_id TEXT NOT NULL, bound REAL NOT NULL, alpha REAL, PRIMARY KEY (auction_id, control_point_id, period_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS response_matrix (well_id INTEGER NOT NULL, control_point_id TEXT NOT NULL, pumping_period INTEGER NOT NULL, effect_period INTEGER NOT NULL, factor_value REAL NOT NULL, PRIMARY KEY (well_id, control_point_id, pumping_period, effect_period), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS constraint_results (auction_id INTEGER NOT NULL, control_point_id TEXT NOT NULL, period_id TEXT NOT NULL, used_capacity REAL NOT NULL, bound_capacity REAL NOT NULL, dual_value REAL NOT NULL, PRIMARY KEY (auction_id, control_point_id, period_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS constraint_quota (auction_id TEXT NOT NULL, well_id INTEGER NOT NULL, control_point_id TEXT NOT NULL, period_id TEXT NOT NULL, alpha REAL NOT NULL, quota_value REAL NOT NULL, constraint_quota_value REAL NOT NULL, PRIMARY KEY (auction_id, well_id, control_point_id, period_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS response_matrix_info (rmi_id INTEGER PRIMARY KEY AUTOINCREMENT, period_length_hours NUMERIC, rmi_loaded_date TEXT, cubic_meters_per_impulse_unit INTEGER, notes TEXT );
CREATE TABLE IF NOT EXISTS trader_license (license_id INTEGER PRIMARY KEY AUTOINCREMENT, trader_id INTEGER, well_id INTEGER, license_quantity REAL, license_date TEXT, bid_period INTEGER );
CREATE TABLE IF NOT EXISTS trader_quota (quota_id INTEGER PRIMARY KEY AUTOINCREMENT, trader_id INTEGER, auction_id INTEGER, well_id INTEGER, quota_auction_start REAL, quota_adjusted REAL, quota_auction_end REAL, price REAL, take_date TEXT );
CREATE TABLE IF NOT EXISTS control_point_event (cpe_id INTEGER PRIMARY KEY AUTOINCREMENT, control_point_id TEXT, auction_id INTEGER, effect_date TEXT, head REAL, constraint_lower_bound REAL, head_constraint_upper_bound REAL, range_lower REAL, range_upper REAL, slack REAL, dual_price REAL, alpha REAL, FOREIGN KEY (auction_id) REFERENCES auctions(auction_id) );
CREATE TABLE IF NOT EXISTS trader_bids (bid_id INTEGER PRIMARY KEY AUTOINCREMENT, well_id INTEGER, trader_id INTEGER, auction_id INTEGER, bid_date TEXT, effect_date TEXT, expiry_date TEXT, is_bid_automatic INTEGER DEFAULT 0, qty1 REAL, price1 REAL, qty2 REAL, price2 REAL, qty3 REAL, price3 REAL, qty4 REAL, price4 REAL, qty5 REAL, price5 REAL, deleted INTEGER NOT NULL DEFAULT 0 );
"""

_WELL_RE = re.compile(r'[Ww][Ee][Ll][Ll](\d+)', re.ASCII)
_BON_RE  = re.compile(r'[Bb][Oo][Nn](\d+)', re.ASCII)
_NUM_RE  = re.compile(r'^(\d+)$')

def db_status(db_path: Path) -> dict:
	"""Return dict with exists flag, path, and per-table row counts."""
	if not db_path.exists(): return {"exists": False, "db_path": str(db_path)}
	try:
		conn = sqlite3.connect(db_path)
		tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name" ).fetchall()]
		counts = {t: conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] for t in tables}
		if "wells" in tables:
			counts["wells_with_trader_id"] = conn.execute("SELECT COUNT(*) FROM wells WHERE trader_id IS NOT NULL").fetchone()[0]
			counts["wells_with_lat"] = conn.execute("SELECT COUNT(*) FROM wells WHERE latitude IS NOT NULL").fetchone()[0]
		if "control_points" in tables: counts["cps_with_lat"] = conn.execute("SELECT COUNT(*) FROM control_points WHERE latitude IS NOT NULL").fetchone()[0]
		conn.close()
		return {"exists": True, "db_path": str(db_path), "tables": counts}
	except Exception as exc:
		return {"exists": True, "db_path": str(db_path), "error": str(exc), "tables": {}}

def create_empty_db(db_path: Path) -> None:
	"""Create all tables without inserting any data."""
	db_path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(db_path)
	conn.executescript(SCHEMA_DDL)
	conn.close()

def delete_db(db_path: Path) -> None:
	"""Delete the database file and any WAL/SHM sidecar files if they exist."""
	failed: list[str] = []
	for suffix in ("", "-wal", "-shm"):
		p = db_path.parent / (db_path.name + suffix)
		if not p.exists():
			continue
		last_exc: OSError | None = None
		for _ in range(20):
			if not p.exists():
				break
			try:
				p.unlink()
				break
			except OSError as exc:
				last_exc = exc
				# If a leaked sqlite3 handle is waiting for GC finalization, release it.
				gc.collect()
				time.sleep(0.05)
		if p.exists():
			failed.append(f"{p}: {last_exc}")
	if failed: raise OSError("Failed to delete one or more database files: " + "; ".join(failed))

def _decode_well(token: str, num_pump_periods: int) -> tuple[int, int]:
	"""wellNNNN → (well_number, pump_period), both 1-based."""
	m = _WELL_RE.match(token)
	if not m: raise ValueError(f"Cannot decode well token: {token!r}")
	idx = int(m.group(1))
	well_num   = 1 + (idx - 1) // num_pump_periods
	pump_period = 1 + (idx - 1) %  num_pump_periods
	return well_num, pump_period

def _decode_cp(token: str, num_control_periods: int) -> tuple[int, int]:
	"""Numeric row index or bonNNNN → (cp_number, effect_period), both 1-based."""
	m_num = _NUM_RE.match(token.strip())
	m_bon = _BON_RE.match(token)
	if m_num: idx = int(m_num.group(1))
	elif m_bon: idx = int(m_bon.group(1))
	else: raise ValueError(f"Cannot decode CP token: {token!r}")
	cp_num        = 1 + (idx - 1) // num_control_periods
	effect_period = 1 + (idx - 1) %  num_control_periods
	return cp_num, effect_period

def _auction_period_keys(first_water_take_date: str, last_water_take_date: str, period_length_hours: int) -> list[str]:
	if period_length_hours <= 0: raise ValueError("period_length_hours must be > 0")
	start_dt = datetime.fromisoformat(first_water_take_date)
	end_dt = datetime.fromisoformat(last_water_take_date)
	if end_dt < start_dt: return []
	step_seconds = int(period_length_hours) * 3600
	period_seconds = int((end_dt - start_dt).total_seconds())
	count = (period_seconds // step_seconds) + 1
	return [(start_dt + timedelta(hours=int(period_length_hours) * idx)).isoformat(timespec="minutes") for idx in range(int(count))]

def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None: conn.execute("INSERT OR REPLACE INTO metadata(meta_key, meta_value) VALUES (?,?)", (key, value),)

def _infer_decvar_dimensions(text: str) -> tuple[int, int]:
	max_idx = 0
	max_stress_period = 0
	for line in text.splitlines():
		tokens = line.split()
		if not tokens: continue
		m = _WELL_RE.match(tokens[0])
		if not m: continue
		idx = int(m.group(1))
		max_idx = max(max_idx, idx)
		if len(tokens) >= 8:
			try: max_stress_period = max(max_stress_period, int(tokens[7]))
			except ValueError: pass

	if max_idx <= 0: raise ValueError("Could not infer dimensions from DECVAR: no wellNNNN rows found")
	num_pump_periods = max_stress_period if max_stress_period > 0 else 1
	num_wells = int(math.ceil(max_idx / float(num_pump_periods)))
	return num_wells, num_pump_periods

def _infer_hedcon_dimensions(text: str) -> tuple[int, int]:
	max_idx = 0
	max_stress_period = 0
	for line in text.splitlines():
		tokens = line.split()
		if not tokens: continue
		m = _BON_RE.match(tokens[0])
		if not m: continue
		idx = int(m.group(1))
		max_idx = max(max_idx, idx)
		if len(tokens) >= 7:
			try: max_stress_period = max(max_stress_period, int(tokens[6]))
			except ValueError: pass

	if max_idx <= 0: raise ValueError("Could not infer dimensions from HEDCON: no bonNNNN rows found")
	num_control_periods = max_stress_period if max_stress_period > 0 else 1
	num_control_points = int(math.ceil(max_idx / float(num_control_periods)))
	return num_control_points, num_control_periods

def _load_int_meta(conn: sqlite3.Connection, key: str) -> int | None:
	row = conn.execute("SELECT meta_value FROM metadata WHERE meta_key=?", (key,)).fetchone()
	if row is None: return None
	try: return int(row[0])
	except Exception: return None

def _resolve_existing_auction_id(conn: sqlite3.Connection, auction_id: str | None = None) -> str:
	"""Return an existing auction_id, or raise if no manager-created auction exists."""
	if auction_id:
		row = conn.execute("SELECT auction_id FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
		if row is None: raise ValueError(f"Auction ID {auction_id!r} does not exist. Create the auction from AuctionManager first.")
		return auction_id

	row = conn.execute("SELECT auction_id FROM auctions " "ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, created_at DESC LIMIT 1" ).fetchone()
	if row is None: raise ValueError("No auctions are defined. Create an auction from AuctionManager first.")
	return str(row[0])

def _infer_mps_indexes(text: str) -> tuple[int, int]:
	section = None
	max_well_idx = 0
	max_row_idx = 0
	for raw_line in text.splitlines():
		stripped = raw_line.strip()
		if not stripped: continue
		tokens = stripped.split()
		if tokens[0] in ("NAME", "ROWS", "COLUMNS", "BOUNDS", "ENDATA"):
			section = tokens[0]
			continue
		if tokens[0] == "RHS" and len(tokens) == 1:
			section = tokens[0]
			continue

		if section == "COLUMNS":
			if len(tokens) < 4: continue
			m = _WELL_RE.match(tokens[0])
			if m and tokens[1].upper() != "OBJ":
				max_well_idx = max(max_well_idx, int(m.group(1)))
				m_row = _NUM_RE.match(tokens[2])
				if m_row: max_row_idx = max(max_row_idx, int(m_row.group(1)))
		elif section == "RHS":
			if len(tokens) < 4: continue
			m_row = _NUM_RE.match(tokens[2])
			if m_row: max_row_idx = max(max_row_idx, int(m_row.group(1)))
		elif section == "BOUNDS":
			if len(tokens) < 4: continue
			m = _WELL_RE.match(tokens[2])
			if m: max_well_idx = max(max_well_idx, int(m.group(1)))

	return max_well_idx, max_row_idx

def import_decvar(db_path: Path, text: str) -> dict:
	"""Parse a GWM2K .decvar file; insert one well row per unique well number.

	DECVAR line format:  wellNNNN  NC  LAYER  ROW  COL  FTYPE  FSTAT  stress_period
	  tokens[0] = wellNNNN (sequential index across all well × pump_period combinations)
	  tokens[3] = ROW grid coordinate (used as location note)
	  tokens[4] = COL grid coordinate (used as location note)
	There are NO lower/upper bounds in DECVAR; capacities come from the MPS BOUNDS section.
	Index NNNN is 1-based sequential across all (well × pump_period) combinations:
		well_number  = 1 + (NNNN - 1) // num_pump_periods
		pump_period  = 1 + (NNNN - 1) %  num_pump_periods
	Wells are inserted with trader_id=NULL (unassigned; FK checks off during import).
	gw_model_layer, gw_model_row, and gw_model_column are populated from the first pump-period entry.
	latitude and longitude remain NULL until georeferenced data is supplied.
	"""
	num_wells, num_pump_periods = _infer_decvar_dimensions(text)

	conn = sqlite3.connect(db_path)
	conn.execute("PRAGMA foreign_keys = OFF")
	seen: set[int] = set()
	inserted = 0
	errors: list[str] = []
	for line in text.splitlines():
		m = _WELL_RE.match(line.strip())
		if not m: continue
		try:
			tokens = line.split()
			well_num, pump_period = _decode_well(tokens[0], num_pump_periods)
			if well_num in seen: continue
			seen.add(well_num)
			# tokens[2]=LAYER, tokens[3]=ROW, tokens[4]=COL give the grid location of this physical well
			gw_model_layer = int(tokens[2]) if len(tokens) >= 3 and tokens[2].lstrip("-").isdigit() else None
			gw_model_row = int(tokens[3]) if len(tokens) >= 4 and tokens[3].lstrip("-").isdigit() else None
			gw_model_column = int(tokens[4]) if len(tokens) >= 5 and tokens[4].lstrip("-").isdigit() else None
			well_id = int(well_num)
			conn.execute("INSERT OR IGNORE INTO wells" "(well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?,?,?,?,?,?,?,?)", (well_id, f"gwm-well-{well_num}", None, gw_model_layer, gw_model_row, gw_model_column, None, None))
			conn.execute("UPDATE wells SET gw_model_layer=COALESCE(gw_model_layer, ?)," " gw_model_row=COALESCE(gw_model_row, ?)," " gw_model_column=COALESCE(gw_model_column, ?) WHERE well_id=?", (gw_model_layer, gw_model_row, gw_model_column, well_id))
			inserted += 1
		except Exception as exc:
			errors.append(str(exc))
	conn.commit()
	_set_meta(conn, "gwm_num_wells", str(num_wells))
	_set_meta(conn, "gwm_num_pump_periods", str(num_pump_periods))
	conn.commit()
	conn.close()
	return {"wells_inserted": inserted, "num_wells": num_wells, "num_pump_periods": num_pump_periods, "errors": errors[:20],}

def import_hedcon(db_path: Path, text: str, num_control_points: int | None = None, num_control_periods: int | None = None) -> dict:
	"""Parse a GWM2K .hedcon file; insert control points and default bounds.

	HEDCON line format:  bonNNNN  LAYER  ROW  COL  SENSE  RHS_head  stress_period
	  tokens[0] = bonNNNN (sequential index across all control_point × control_period)
	  tokens[5] = RHS_head (the head target, e.g. 829.0 metres)
	RHS_head values are stored in default_control_point_bounds (no auction_id), stamped with the import time.
	When an auction is created, its control_point_bounds are automatically populated from these defaults.
	Uploading a new .hedcon file updates the defaults; auctions created after the upload use the new bounds.
	Index NNNN is 1-based sequential across all (control_point × control_period):
		cp_number     = 1 + (NNNN - 1) // num_control_periods
		effect_period = 1 + (NNNN - 1) %  num_control_periods
	"""
	if num_control_points is None or num_control_periods is None: num_control_points, num_control_periods = _infer_hedcon_dimensions(text)

	conn = sqlite3.connect(db_path)
	imported_at = datetime.now().isoformat()
	seen: set[int] = set()
	cp_inserted = 0
	bounds_inserted = 0
	errors: list[str] = []
	for line in text.splitlines():
		m = _BON_RE.match(line.strip())
		if not m: continue
		try:
			tokens = line.split()
			idx = int(m.group(1))
			cp_num, effect_period = _decode_cp(str(idx), num_control_periods)
			if cp_num in seen: pass
			else:
				seen.add(cp_num)
				cp_id = f"gwm-cp-{cp_num}"
				gw_model_row = int(tokens[2]) if len(tokens) >= 3 and tokens[2].lstrip("-").isdigit() else None
				gw_model_column = int(tokens[3]) if len(tokens) >= 4 and tokens[3].lstrip("-").isdigit() else None
				conn.execute("INSERT OR IGNORE INTO control_points" "(control_point_id, name, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?,?,?,?,?,?)", (cp_id, f"Control point {cp_num}", gw_model_row, gw_model_column, None, None))
				conn.execute("UPDATE control_points SET gw_model_row=COALESCE(gw_model_row, ?)," " gw_model_column=COALESCE(gw_model_column, ?) WHERE control_point_id=?", (gw_model_row, gw_model_column, cp_id))
				cp_inserted += 1

			cp_id = f"gwm-cp-{cp_num}"
			rhs_head = float(tokens[5]) if len(tokens) >= 6 else None
			if rhs_head is not None:
				conn.execute("INSERT OR REPLACE INTO default_control_point_bounds" "(control_point_id, period_id, bound, imported_at) VALUES (?,?,?,?)", (cp_id, effect_period, rhs_head, imported_at))
				bounds_inserted += 1
		except Exception as exc: errors.append(str(exc))
	conn.commit()
	_set_meta(conn, "gwm_num_control_points", str(num_control_points))
	_set_meta(conn, "gwm_num_control_periods", str(num_control_periods))
	conn.commit()
	conn.close()
	return {"control_points_inserted": cp_inserted, "control_point_bounds_inserted": bounds_inserted, "num_control_points": num_control_points, "num_control_periods": num_control_periods, "errors": errors[:20],}

def import_mps(db_path: Path, text: str, period_length_hours: int) -> dict:
	"""Parse a GWM2K .mps file; insert response_matrix, control_point_bounds, and wells.

	GWM2K MPS section formats (4-token per line):
	  COLUMNS:  wellNNNN  model_name  row_index  coefficient
				(model_name at [1] is ignored; same logic as PHP GWM_files2MYSQL.php)
				Lines with [1]=="OBJ" are objective-function coefficients and are skipped.
	  RHS:      rhs_label  rhs_name  row_index  value
				(rhs_label and rhs_name at [0],[1] ignored)
	  BOUNDS:   bound_type  bnd_name  wellNNNN  value
				UP bounds are stored in trader_license as one row per well and bid period.
	"""
	conn = sqlite3.connect(db_path)
	conn.execute("PRAGMA foreign_keys = OFF")
	imported_at = datetime.now().isoformat()
	conn.execute("DELETE FROM trader_license")
	conn.execute("DELETE FROM trader_quota WHERE auction_id=0")

	max_well_idx, max_row_idx = _infer_mps_indexes(text)
	num_wells = _load_int_meta(conn, "gwm_num_wells")
	num_pump_periods = _load_int_meta(conn, "gwm_num_pump_periods")
	num_control_points = _load_int_meta(conn, "gwm_num_control_points")
	num_control_periods = _load_int_meta(conn, "gwm_num_control_periods")

	if num_wells is None:
		row = conn.execute("SELECT COUNT(*) FROM wells").fetchone()
		num_wells = int(row[0]) if row and row[0] else None
	if num_control_points is None:
		row = conn.execute("SELECT COUNT(*) FROM control_points").fetchone()
		num_control_points = int(row[0]) if row and row[0] else None

	if num_pump_periods is None and num_wells and max_well_idx > 0: num_pump_periods = int(math.ceil(max_well_idx / float(num_wells)))
	if num_control_periods is None and num_control_points and max_row_idx > 0: num_control_periods = int(math.ceil(max_row_idx / float(num_control_points)))

	if not num_pump_periods or not num_control_periods:
		conn.close()
		raise ValueError("Could not infer pump/control periods for MPS import. Import DECVAR and HEDCON first.")

	section = None
	rf_count = 0
	bound_count = 0
	license_count = 0
	wells_ensured = 0
	errors: list[str] = []
	well_trader_map: dict[int, int | None] = {}
	unassigned_wells: set[int] = set()
	for row in conn.execute("SELECT well_id, trader_id FROM wells").fetchall():
		well_id = int(row[0])
		trader_id = None if row[1] is None else int(row[1])
		well_trader_map[well_id] = trader_id

	for raw_line in text.splitlines():
		line = raw_line.rstrip()
		stripped = line.strip()
		if not stripped: continue
		tokens = stripped.split()

		# In GWM MPS, data lines in RHS also begin with "RHS".
		# Treat RHS as section header only when it appears by itself.
		if tokens[0] in ("NAME", "ROWS", "COLUMNS", "BOUNDS", "ENDATA"):
			section = tokens[0]
			continue
		if tokens[0] == "RHS" and len(tokens) == 1:
			section = tokens[0]
			continue

		if section == "COLUMNS":
			# GWM2K format per line: wellNNNN  model_label  row_index  coefficient
			# OBJ lines: wellNNNN  OBJ  price  (no row index; skip)
			if len(tokens) < 3: continue
			col_token = tokens[0]
			if not _WELL_RE.match(col_token): continue
			if tokens[1].upper() == "OBJ": continue  # objective coefficient, not a constraint row
			# Expect tokens[2] = row index, tokens[3] = coefficient
			if len(tokens) < 4: continue
			row_token = tokens[2]
			try: coef = float(tokens[3])
			except ValueError: continue
			try:
				well_num, pump_period = _decode_well(col_token, num_pump_periods)
				cp_num, effect_period = _decode_cp(row_token, num_control_periods)
				well_id = int(well_num)
				cp_id   = f"gwm-cp-{cp_num}"
				conn.execute("INSERT OR REPLACE INTO response_matrix" "(well_id, control_point_id," " pumping_period, effect_period, factor_value)" " VALUES (?,?,?,?,?)", (well_id, cp_id, pump_period, effect_period, coef))
				rf_count += 1
			except Exception as exc:
				errors.append(f"COLUMNS {col_token} row {row_token}: {exc}")

		elif section == "RHS":
			# GWM2K format: rhs_label  rhs_name  row_index  value
			# Bounds go to default_control_point_bounds (no auction_id).
			if len(tokens) < 4: continue
			row_token = tokens[2]
			try: val = float(tokens[3])
			except ValueError: continue
			try:
				cp_num, effect_period = _decode_cp(row_token, num_control_periods)
				cp_id = f"gwm-cp-{cp_num}"
				conn.execute("INSERT OR REPLACE INTO default_control_point_bounds" "(control_point_id, period_id, bound, imported_at) VALUES (?,?,?,?)", (cp_id, effect_period, val, imported_at))
				bound_count += 1
			except Exception as exc:
				errors.append(f"RHS row {row_token}: {exc}")

		elif section == "BOUNDS":
			# Format: bnd_type  bnd_name  wellNNNN  value
			if len(tokens) < 4: continue
			if str(tokens[0]).upper() != "UP": continue
			col_token = tokens[2]
			if not _WELL_RE.match(col_token): continue
			try: val = float(tokens[3])
			except ValueError: continue
			try:
				well_num, pump_period = _decode_well(col_token, num_pump_periods)
				well_id = int(well_num)
				before_changes = conn.total_changes
				conn.execute("INSERT OR IGNORE INTO wells" "(well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?,?,?,?,?,?,?,?)", (well_id, f"gwm-well-{well_num}", None, None, None, None, None, None))
				if conn.total_changes > before_changes:
					wells_ensured += 1
				if well_id not in well_trader_map:
					row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
					well_trader_map[well_id] = None if row is None or row[0] is None else int(row[0])
				trader_id = well_trader_map.get(well_id)
				if trader_id is None:
					if well_id not in unassigned_wells:
						unassigned_wells.add(well_id)
						errors.append(f"BOUNDS well{well_id}: skipped license insert because wells.trader_id is NULL. Import trader-well assignments first.")
					continue
				conn.execute("INSERT INTO trader_license(trader_id, well_id, license_quantity, license_date, bid_period) VALUES (?, ?, ?, ?, ?)", (int(trader_id), well_id, float(val), None, int(pump_period)))
				conn.execute("INSERT INTO trader_quota(trader_id, auction_id, well_id, quota_auction_start) VALUES (?, 0, ?, ?)", (int(trader_id), well_id, float(val)))
				license_count += 1
			except Exception as exc:
				errors.append(f"BOUNDS {col_token}: {exc}")

	conn.commit()
	if num_wells: _set_meta(conn, "gwm_num_wells", str(num_wells))
	if num_pump_periods: _set_meta(conn, "gwm_num_pump_periods", str(num_pump_periods))
	if num_control_points: _set_meta(conn, "gwm_num_control_points", str(num_control_points))
	if num_control_periods: _set_meta(conn, "gwm_num_control_periods", str(num_control_periods))
	conn.commit()
	conn.execute("INSERT INTO response_matrix_info(period_length_hours, rmi_loaded_date, notes) VALUES (?, ?, ?)", (float(period_length_hours), imported_at, "Imported from .mps via Programmer page",),)
	conn.commit()
	conn.close()
	return {"response_matrix_inserted": rf_count, "control_point_bounds_inserted": bound_count, "license_rows_inserted": license_count, "wells_ensured": wells_ensured, "num_wells": num_wells, "num_pump_periods": num_pump_periods, "num_control_points": num_control_points, "num_control_periods": num_control_periods, "period_length_hours": int(period_length_hours), "errors": errors[:20],}

def missing_import_data_report(db_path: Path) -> dict:
	"""Report missing gw model coordinates and lat/lon for wells and control points."""
	if not db_path.exists(): return {"db_exists": False, "wells": {}, "control_points": {},}
	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row

	well_counts = conn.execute("SELECT " "COUNT(*) AS total," "SUM(CASE WHEN gw_model_row IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_row," "SUM(CASE WHEN gw_model_column IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_column," "SUM(CASE WHEN latitude IS NULL THEN 1 ELSE 0 END) AS missing_latitude," "SUM(CASE WHEN longitude IS NULL THEN 1 ELSE 0 END) AS missing_longitude " "FROM wells" ).fetchone()
	cp_counts = conn.execute("SELECT " "COUNT(*) AS total," "SUM(CASE WHEN gw_model_row IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_row," "SUM(CASE WHEN gw_model_column IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_column," "SUM(CASE WHEN latitude IS NULL THEN 1 ELSE 0 END) AS missing_latitude," "SUM(CASE WHEN longitude IS NULL THEN 1 ELSE 0 END) AS missing_longitude " "FROM control_points" ).fetchone()
	sample_wells = [r[0] for r in conn.execute("SELECT well_id FROM wells WHERE " "gw_model_row IS NULL OR gw_model_column IS NULL OR latitude IS NULL OR longitude IS NULL " "ORDER BY well_id LIMIT 8" ).fetchall()]
	sample_cps = [r[0] for r in conn.execute("SELECT control_point_id FROM control_points WHERE " "gw_model_row IS NULL OR gw_model_column IS NULL OR latitude IS NULL OR longitude IS NULL " "ORDER BY control_point_id LIMIT 8" ).fetchall()]
	conn.close()
	return {"db_exists": True, "wells": {k: int(well_counts[k] or 0) for k in well_counts.keys()}, "control_points": {k: int(cp_counts[k] or 0) for k in cp_counts.keys()}, "sample_missing_well_ids": sample_wells, "sample_missing_control_point_ids": sample_cps,}

def import_trader_names(db_path: Path, text: str) -> dict:
	"""Import traders from a tab-delimited file.

	Expected columns: full_name, trader_first_name, trader_last_name.
	Stores trader name in name_tag and trader_loginid from trader_last_name.
	If the same last name appears more than once in the file, the first keeps the plain
	last name and subsequent ones get a numeric suffix (1, 2, …).
	"""
	lines = [l for l in text.splitlines() if l.strip()]
	if not lines: return {"traders_inserted": 0, "traders_skipped": 0, "errors": ["File is empty"]}
	header = [c.strip().lower() for c in lines[0].split("\t")]

	def col(name: str) -> int | None:
		try: return header.index(name)
		except ValueError: return None

	fn_idx = col("trader_first_name")
	ln_idx = col("trader_last_name")
	if ln_idx is None: return {"traders_inserted": 0, "traders_skipped": 0, "errors": [f"Missing column 'trader_last_name'. Found: {header}"]}

	# Collect parsed rows
	parsed: list[tuple[str, str]] = []
	for line in lines[1:]:
		cols = line.split("\t")
		if ln_idx >= len(cols): continue
		last_name = cols[ln_idx].strip()
		first_name = cols[fn_idx].strip() if fn_idx is not None and fn_idx < len(cols) else ""
		if last_name: parsed.append((first_name, last_name))

	# Assign dedup suffixes based on order of appearance
	seen: dict[str, int] = {}
	suffixes: list[str] = []
	for _, last_name in parsed:
		key = last_name.lower()
		count = seen.get(key, 0)
		suffixes.append("" if count == 0 else str(count))
		seen[key] = count + 1

	conn = sqlite3.connect(db_path)
	inserted = skipped = 0
	errors: list[str] = []
	for (first_name, last_name), suffix in zip(parsed, suffixes):
		display = last_name + suffix
		try:
			cur = conn.execute("INSERT OR IGNORE INTO traders" "(name_tag, trader_loginid, trader_first_name, trader_last_name)" " VALUES (?,?,?,?)", (display, display, first_name, last_name),)
			if cur.rowcount: inserted += 1
			else: skipped += 1
		except Exception as exc: errors.append(str(exc))
	conn.commit()
	conn.close()
	return {"traders_inserted": inserted, "traders_skipped": skipped, "errors": errors[:20]}

def import_trader_wells(db_path: Path, text: str) -> dict:
	"""Import trader-well assignments from a tab-delimited file.

	Expected columns: name (must match traders.name_tag), well_id (must match wells.well_id).
	Sets wells.trader_id for each matched row.
	A well_id can only be assigned to one trader; a trader may have multiple wells.
	"""
	lines = [l for l in text.splitlines() if l.strip()]
	if not lines: return {"wells_assigned": 0, "errors": ["File is empty"]}
	header = [c.strip().lower() for c in lines[0].split("\t")]

	def col(name: str) -> int | None:
		try: return header.index(name)
		except ValueError: return None

	name_idx = col("name")
	well_idx = col("well_id")
	if name_idx is None or well_idx is None: return {"wells_assigned": 0, "errors": [f"Missing required columns 'name' and/or 'well_id'. Found: {header}"]}

	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row
	assigned = 0
	errors: list[str] = []
	for line in lines[1:]:
		cols = line.split("\t")
		if max(name_idx, well_idx) >= len(cols): continue
		name_val = cols[name_idx].strip()
		well_id_val = cols[well_idx].strip()
		if not name_val or not well_id_val: continue
		trader_row = conn.execute("SELECT trader_id FROM traders WHERE name_tag=?", (name_val,) ).fetchone()
		if trader_row is None:
			errors.append(f"Trader name not found in traders table: {name_val!r}")
			continue
		well_row = conn.execute("SELECT well_id FROM wells WHERE well_id=? OR name=?", (well_id_val, well_id_val) ).fetchone()
		if well_row is None:
			errors.append(f"Well ID not found in wells table: {well_id_val!r}")
			continue
		conn.execute("UPDATE wells SET trader_id=? WHERE well_id=?", (trader_row["trader_id"], well_row["well_id"]),)
		assigned += 1
	conn.commit()
	conn.close()
	return {"wells_assigned": assigned, "errors": errors[:20]}

def import_well_lat_lon(db_path: Path, text: str) -> dict:
	"""Import well latitude/longitude from a tab-delimited file.

	Expected columns: well_id, latitude, longitude.
	Updates wells.latitude and wells.longitude for matching well_id rows.
	"""
	lines = [l for l in text.splitlines() if l.strip()]
	if not lines: return {"wells_updated": 0, "rows_skipped": 0, "errors": ["File is empty"]}

	delim = "\t" if "\t" in lines[0] else ","
	header = [c.strip().lower() for c in lines[0].split(delim)]

	def col(*names: str) -> int | None:
		for name in names:
			try: return header.index(name)
			except ValueError: continue
		return None

	well_idx = col("well_id", "wellid")
	lat_idx = col("latitude", "lat")
	lon_idx = col("longitude", "lon", "lng")
	if well_idx is None or lat_idx is None or lon_idx is None: return {"wells_updated": 0, "rows_skipped": 0, "errors": ["Missing required columns well_id, latitude, longitude. " f"Found: {header}"],}

	conn = sqlite3.connect(db_path)
	updated = 0
	skipped = 0
	errors: list[str] = []

	for line in lines[1:]:
		cols = line.split(delim)
		if max(well_idx, lat_idx, lon_idx) >= len(cols):
			skipped += 1
			continue

		well_id = cols[well_idx].strip()
		lat_raw = cols[lat_idx].strip()
		lon_raw = cols[lon_idx].strip()
		if not well_id or not lat_raw or not lon_raw:
			skipped += 1
			continue

		try:
			lat_val = float(lat_raw)
			lon_val = float(lon_raw)
		except ValueError:
			skipped += 1
			errors.append(f"Invalid latitude/longitude for well_id={well_id!r}")
			continue

		cur = conn.execute("UPDATE wells SET latitude=?, longitude=? WHERE well_id=? OR name=?", (lat_val, lon_val, well_id, well_id),)
		if cur.rowcount: updated += 1
		else:
			skipped += 1
			errors.append(f"Well not found: {well_id!r}")

	conn.commit()
	conn.close()
	return {"wells_updated": updated, "rows_skipped": skipped, "errors": errors[:20]}

def import_control_point_lat_lon(db_path: Path, text: str) -> dict:
	"""Import control-point latitude/longitude from a tab-delimited file.

	Expected columns: control_point_id, latitude, longitude.
	Updates control_points.latitude and control_points.longitude for matching IDs.
	"""
	lines = [l for l in text.splitlines() if l.strip()]
	if not lines: return {"control_points_updated": 0, "rows_skipped": 0, "errors": ["File is empty"],}

	delim = "\t" if "\t" in lines[0] else ","
	header = [c.strip().lower() for c in lines[0].split(delim)]

	def col(*names: str) -> int | None:
		for name in names:
			try: return header.index(name)
			except ValueError: continue
		return None

	cp_idx = col("control_point_id", "controlpoint_id", "cp_id")
	lat_idx = col("latitude", "lat")
	lon_idx = col("longitude", "lon", "lng")
	if cp_idx is None or lat_idx is None or lon_idx is None: return {"control_points_updated": 0, "rows_skipped": 0, "errors": ["Missing required columns control_point_id, latitude, longitude. " f"Found: {header}"],}

	conn = sqlite3.connect(db_path)
	updated = 0
	skipped = 0
	errors: list[str] = []

	for line in lines[1:]:
		cols = line.split(delim)
		if max(cp_idx, lat_idx, lon_idx) >= len(cols):
			skipped += 1
			continue

		cp_id = cols[cp_idx].strip()
		lat_raw = cols[lat_idx].strip()
		lon_raw = cols[lon_idx].strip()
		if not cp_id or not lat_raw or not lon_raw:
			skipped += 1
			continue

		try:
			lat_val = float(lat_raw)
			lon_val = float(lon_raw)
		except ValueError:
			skipped += 1
			errors.append(f"Invalid latitude/longitude for control_point_id={cp_id!r}")
			continue

		cur = conn.execute("UPDATE control_points SET latitude=?, longitude=? WHERE control_point_id=? OR name=?", (lat_val, lon_val, cp_id, cp_id),)
		if cur.rowcount: updated += 1
		else:
			skipped += 1
			errors.append(f"Control point not found: {cp_id!r}")

	conn.commit()
	conn.close()
	return {"control_points_updated": updated, "rows_skipped": skipped, "errors": errors[:20],}
