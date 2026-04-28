# setup.py. JFR / Claude, 2026-04-24.
# Purpose: Database creation, deletion, and GWM2K file import utilities.
# Keep SCHEMA_DDL in sync with the executescript() call in services/repository.py.

from __future__ import annotations
import math
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS metadata (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL );
CREATE TABLE IF NOT EXISTS traders (trader_id TEXT PRIMARY KEY, name TEXT NOT NULL, trader_loginid TEXT, trader_password TEXT, trader_first_name TEXT, trader_last_name TEXT, trader_address TEXT, trader_city TEXT, trader_phone TEXT, trader_email TEXT );
CREATE TABLE IF NOT EXISTS wells (well_id TEXT PRIMARY KEY, name TEXT NOT NULL, trader_id TEXT NOT NULL, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL, FOREIGN KEY (trader_id) REFERENCES traders(trader_id) );
CREATE TABLE IF NOT EXISTS control_points (control_point_id TEXT PRIMARY KEY, name TEXT NOT NULL, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL );
CREATE TABLE IF NOT EXISTS auctions (auction_id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, created_date TEXT NOT NULL, closed_date TEXT, firstWaterTakeDate TEXT, lastWaterTakeDate TEXT, period_length_hours INTEGER, auction_type TEXT, solve_status TEXT, objective_value REAL );
CREATE TABLE IF NOT EXISTS trader_allocations (auction_id TEXT NOT NULL, trader_id TEXT NOT NULL, period_id TEXT NOT NULL, allocation REAL NOT NULL, PRIMARY KEY (auction_id, trader_id, period_id), FOREIGN KEY (trader_id) REFERENCES traders(trader_id) );
CREATE TABLE IF NOT EXISTS default_control_point_bounds (control_point_id TEXT NOT NULL, period_id INTEGER NOT NULL, bound REAL NOT NULL, imported_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (control_point_id, period_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS control_point_bounds (auction_id TEXT NOT NULL, control_point_id TEXT NOT NULL, period_id TEXT NOT NULL, bound REAL NOT NULL, alpha REAL, PRIMARY KEY (auction_id, control_point_id, period_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS response_matrix (well_id TEXT NOT NULL, control_point_id TEXT NOT NULL, pumping_period INTEGER NOT NULL, effect_period INTEGER NOT NULL, factor_value REAL NOT NULL, PRIMARY KEY (well_id, control_point_id, pumping_period, effect_period), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS bids (bid_id INTEGER PRIMARY KEY AUTOINCREMENT, auction_id TEXT NOT NULL, trader_id TEXT NOT NULL, well_id TEXT NOT NULL, period_id TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL, submitted_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, FOREIGN KEY (trader_id) REFERENCES traders(trader_id), FOREIGN KEY (well_id) REFERENCES wells(well_id) );
CREATE TABLE IF NOT EXISTS accepted_bids (auction_id INTEGER NOT NULL, bid_id INTEGER NOT NULL, accepted_quantity REAL NOT NULL, PRIMARY KEY (auction_id, bid_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (bid_id) REFERENCES bids(bid_id) );
CREATE TABLE IF NOT EXISTS constraint_results (auction_id INTEGER NOT NULL, control_point_id TEXT NOT NULL, period_id TEXT NOT NULL, used_capacity REAL NOT NULL, bound_capacity REAL NOT NULL, dual_value REAL NOT NULL, PRIMARY KEY (auction_id, control_point_id, period_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS well_quota_ledger (well_id TEXT NOT NULL, auction_id TEXT NOT NULL, period_id TEXT NOT NULL, quota_auction_start REAL NOT NULL, quota_adjusted REAL, quota_auction_end REAL, clearing_price REAL, adjustment_method TEXT, PRIMARY KEY (well_id, auction_id, period_id), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id) );
CREATE TABLE IF NOT EXISTS constraint_quota (auction_id TEXT NOT NULL, well_id TEXT NOT NULL, control_point_id TEXT NOT NULL, period_id TEXT NOT NULL, alpha REAL NOT NULL, quota_value REAL NOT NULL, constraint_quota_value REAL NOT NULL, PRIMARY KEY (auction_id, well_id, control_point_id, period_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) );
CREATE TABLE IF NOT EXISTS configs (id INTEGER PRIMARY KEY, currDate TEXT, currAucSolve TEXT );
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, message_type TEXT, message TEXT );
CREATE TABLE IF NOT EXISTS response_matrix_info (RMI_id INTEGER PRIMARY KEY AUTOINCREMENT, RMI_hours_period_length NUMERIC, RMI_creation_date TEXT, RMI_cubic_meters_per_impulse_unit INTEGER, RMI_notes TEXT );
CREATE TABLE IF NOT EXISTS traderconsents (consentid TEXT PRIMARY KEY, traderID TEXT, wellID TEXT, consentQuantity REAL, startDate TEXT, expiryDate TEXT );
CREATE TABLE IF NOT EXISTS traderquota (quotaid INTEGER PRIMARY KEY AUTOINCREMENT, traderid TEXT, auctionid TEXT, wellID TEXT, quotaAuctionStart REAL, quotaAdjusted REAL, quotaAuctionEnd REAL, price REAL, lprowName TEXT, takeDate TEXT );
CREATE TABLE IF NOT EXISTS control_point_event (CPE_id INTEGER PRIMARY KEY AUTOINCREMENT, RMI_id INTEGER, CP_id TEXT, auction_id TEXT, CPE_effect_date TEXT, CPE_equality_constraint_right_hand_side REAL, CPE_head REAL, CPE_constraint_lower_bound REAL, CPE_head_constraint_upper_bound REAL, CPE_LProw_name TEXT, CPE_range_lower REAL, CPE_range_upper REAL, CPE_slack REAL, CPE_dual_price REAL, CPE_alpha REAL, FOREIGN KEY (RMI_id) REFERENCES response_matrix_info(RMI_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id) );
CREATE TABLE IF NOT EXISTS traderbids (bidid INTEGER PRIMARY KEY AUTOINCREMENT, well_id TEXT, traderID TEXT, auctionID TEXT, bidDate TEXT, effectDate TEXT, expiryDate TEXT, isBidAutomatic INTEGER DEFAULT 0, qty1 REAL, price1 REAL, qty2 REAL, price2 REAL, qty3 REAL, price3 REAL, qty4 REAL, price4 REAL, qty5 REAL, price5 REAL, deleted INTEGER NOT NULL DEFAULT 0 );
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
			counts["wells_with_trader_id"] = conn.execute("SELECT COUNT(*) FROM wells WHERE trader_id != '' AND trader_id IS NOT NULL").fetchone()[0]
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
	_ensure_spatial_columns(conn)
	conn.close()

def _ensure_spatial_columns(conn: sqlite3.Connection) -> None:
	"""Ensure spatial metadata columns exist for wells and control_points."""
	table_columns = {"wells": [("gw_model_row", "INTEGER"), ("gw_model_column", "INTEGER"), ("latitude", "REAL"), ("longitude", "REAL"),], "control_points": [("gw_model_row", "INTEGER"), ("gw_model_column", "INTEGER"), ("latitude", "REAL"), ("longitude", "REAL"),],}
	for table, columns in table_columns.items():
		existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
		for col_name, col_type in columns:
			if col_name not in existing: conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")

def delete_db(db_path: Path) -> None:
	"""Delete the database file if it exists."""
	if db_path.exists(): db_path.unlink()

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
	Wells are inserted with trader_id='' (unassigned; FK checks off during import).
	gw_model_row and gw_model_column are populated from the first pump-period entry.
	latitude and longitude remain NULL until georeferenced data is supplied.
	"""
	num_wells, num_pump_periods = _infer_decvar_dimensions(text)

	conn = sqlite3.connect(db_path)
	conn.execute("PRAGMA foreign_keys = OFF")
	_ensure_spatial_columns(conn)
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
			# tokens[3]=ROW, tokens[4]=COL give the grid location of this physical well
			gw_model_row = int(tokens[3]) if len(tokens) >= 4 and tokens[3].lstrip("-").isdigit() else None
			gw_model_column = int(tokens[4]) if len(tokens) >= 5 and tokens[4].lstrip("-").isdigit() else None
			well_id = f"gwm-well-{well_num}"
			conn.execute("INSERT OR IGNORE INTO wells" "(well_id, name, trader_id, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?,?,?,?,?,?,?)", (well_id, f"Well {well_num}", "", gw_model_row, gw_model_column, None, None))
			conn.execute("UPDATE wells SET gw_model_row=COALESCE(gw_model_row, ?)," " gw_model_column=COALESCE(gw_model_column, ?) WHERE well_id=?", (gw_model_row, gw_model_column, well_id))
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
	_ensure_spatial_columns(conn)
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
				Only the first pump-period entry per well is stored as an initial right.
	"""
	conn = sqlite3.connect(db_path)
	conn.execute("PRAGMA foreign_keys = OFF")
	_ensure_spatial_columns(conn)
	imported_at = datetime.now().isoformat()

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
	right_count = 0
	prev_well_num = 0
	errors: list[str] = []

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
				well_id = f"gwm-well-{well_num}"
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
			col_token = tokens[2]
			if not _WELL_RE.match(col_token): continue
			try: val = float(tokens[3])
			except ValueError: continue
			try:
				well_num, _ = _decode_well(col_token, num_pump_periods)
				# PHP logic: store only the first pump-period entry per well as initial right
				if well_num == prev_well_num + 1:
					well_id = f"gwm-well-{well_num}"
					conn.execute("INSERT OR IGNORE INTO wells" "(well_id, name, trader_id, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?,?,?,?,?,?,?)", (well_id, f"Well {well_num}", "", None, None, None, None))
					prev_well_num = well_num
					right_count += 1
			except Exception as exc:
				errors.append(f"BOUNDS {col_token}: {exc}")

	conn.commit()
	if num_wells: _set_meta(conn, "gwm_num_wells", str(num_wells))
	if num_pump_periods: _set_meta(conn, "gwm_num_pump_periods", str(num_pump_periods))
	if num_control_points: _set_meta(conn, "gwm_num_control_points", str(num_control_points))
	if num_control_periods: _set_meta(conn, "gwm_num_control_periods", str(num_control_periods))
	conn.commit()
	conn.execute("INSERT INTO response_matrix_info(RMI_hours_period_length, RMI_creation_date, RMI_notes) VALUES (?, ?, ?)", (float(period_length_hours), imported_at, "Imported from .mps via Programmer page",),)
	conn.commit()
	conn.close()
	return {"response_matrix_inserted": rf_count, "control_point_bounds_inserted": bound_count, "well_rights_inserted": right_count, "num_wells": num_wells, "num_pump_periods": num_pump_periods, "num_control_points": num_control_points, "num_control_periods": num_control_periods, "period_length_hours": int(period_length_hours), "errors": errors[:20],}

def _compute_alphas(conn: sqlite3.Connection, auction_id: str) -> dict:
	"""Compute alpha for each (control_point_id, period_id) for the given auction.

	alpha(CP, t) = upper_bound(CP, t) / sum_{well w, pumping period u <= t} F(w, CP, u, t) * quota(w, u)

	Alpha is capped at 1.0.  If the denominator is zero (no response or no quota), alpha = 1.0.
	Returns {(cp_id, period_id): alpha}.
	"""
	# Build quota: {(well_id, period_id): sum of allocations for the well's trader, that period}
	auction_row = conn.execute("SELECT firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
	if auction_row is None: return {}
	period_hours = int(auction_row[2] or 0)
	if not auction_row[0] or not auction_row[1] or period_hours <= 0: return {}
	period_keys = _auction_period_keys(str(auction_row[0]), str(auction_row[1]), period_hours)
	period_index = {key: idx + 1 for idx, key in enumerate(period_keys)}

	quota: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT w.well_id, ta.period_id, ta.allocation FROM wells w JOIN trader_allocations ta ON ta.trader_id = w.trader_id WHERE ta.auction_id = ?", (auction_id,),).fetchall():
		key = (str(row[0]), str(row[1]))
		quota[key] = quota.get(key, 0.0) + float(row[2])

	# Load all bounds for this auction
	bounds: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT control_point_id, period_id, bound FROM control_point_bounds WHERE auction_id = ?", (auction_id,), ).fetchall(): bounds[(str(row[0]), str(row[1]))] = float(row[2])

	alphas: dict[tuple[str, str], float] = {}
	for (cp_id, period_key), upper_bound in bounds.items():
		t = period_index.get(period_key)
		if t is None: continue
		factors = conn.execute("SELECT well_id, pumping_period, factor_value FROM response_matrix WHERE control_point_id = ? AND effect_period = ? AND pumping_period <= ?", (cp_id, t, t), ).fetchall()
		sum_Fq = sum(float(row[2]) * quota.get((str(row[0]), period_keys[int(row[1]) - 1]), 0.0) for row in factors if 1 <= int(row[1]) <= len(period_keys))
		alphas[(cp_id, period_key)] = min(1.0, upper_bound / sum_Fq) if sum_Fq > 0.0 else 1.0
	return alphas

def _compute_constraint_quotas(conn: sqlite3.Connection, auction_id: str) -> int:
	"""Compute ConstraintQuota for each (well, CP, bid_period) for the given auction.

	constraint_quota(well, CP, t) = alpha(CP, t) * quota(well, t)
	  where quota(well, t) = trader_allocations for the well's trader at bid period t,
	  and alpha(CP, t) is read from control_point_bounds (set by apply_default_bounds).

	Covers only the auction's bid periods (rows in trader_allocations) because quota is
	defined for bid periods and traders pump only during bid periods.

	Deletes and replaces all constraint_quota rows for this auction.
	Returns the count of rows written.
	"""
	# quota: {(well_id, period_id): allocation} for wells whose trader has an allocation
	quota: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT w.well_id, ta.period_id, ta.allocation FROM wells w JOIN trader_allocations ta ON ta.trader_id = w.trader_id WHERE ta.auction_id = ?", (auction_id,), ).fetchall():
		key = (str(row[0]), str(row[1]))
		quota[key] = quota.get(key, 0.0) + float(row[2])

	# alpha: {(cp_id, period_id): alpha} restricted to bid periods
	alpha_lookup: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT cpb.control_point_id, cpb.period_id, cpb.alpha FROM control_point_bounds cpb WHERE cpb.auction_id = ?", (auction_id,), ).fetchall():
		alpha_lookup[(str(row[0]), str(row[1]))] = float(row[2]) if row[2] is not None else 1.0

	well_ids = [str(r[0]) for r in conn.execute("SELECT well_id FROM wells").fetchall()]
	cp_ids = [str(r[0]) for r in conn.execute("SELECT control_point_id FROM control_points").fetchall()]
	bid_periods = [str(r[0]) for r in conn.execute("SELECT DISTINCT period_id FROM trader_allocations WHERE auction_id = ? ORDER BY period_id", (auction_id,), ).fetchall()]

	conn.execute("DELETE FROM constraint_quota WHERE auction_id = ?", (auction_id,))
	count = 0
	for well_id in well_ids:
		for period_id in bid_periods:
			q = quota.get((well_id, period_id), 0.0)
			for cp_id in cp_ids:
				alpha = alpha_lookup.get((cp_id, period_id), 1.0)
				conn.execute("INSERT INTO constraint_quota (auction_id, well_id, control_point_id, period_id, alpha, quota_value, constraint_quota_value) VALUES (?, ?, ?, ?, ?, ?, ?)", (auction_id, well_id, cp_id, period_id, alpha, q, alpha * q),)
				count += 1
	return count

def apply_default_bounds(db_path: Path, auction_id: str) -> dict:
	"""Copy default_control_point_bounds into control_point_bounds for a specific auction.

	Raises ValueError if the auction does not exist.
	Uses INSERT OR REPLACE so repeated calls are safe.
	Returns the number of rows written.
	"""
	conn = sqlite3.connect(db_path)
	auction_id = _resolve_existing_auction_id(conn, auction_id)
	auction_row = conn.execute("SELECT firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
	if auction_row is None or not auction_row[0] or not auction_row[1] or not auction_row[2]:
		conn.close()
		raise ValueError("Auction must have firstWaterTakeDate, lastWaterTakeDate, and period_length_hours before applying default bounds.")
	period_keys = _auction_period_keys(str(auction_row[0]), str(auction_row[1]), int(auction_row[2]))
	rows = conn.execute("SELECT control_point_id, period_id, bound FROM default_control_point_bounds" ).fetchall()
	count = 0
	for cp_id, period_id, bound in rows:
		idx = int(period_id)
		if idx < 1 or idx > len(period_keys): continue
		conn.execute("INSERT OR REPLACE INTO control_point_bounds" "(auction_id, control_point_id, period_id, bound) VALUES (?,?,?,?)", (auction_id, cp_id, period_keys[idx - 1], bound),)
		count += 1
	# D.2: compute and store alpha for each (CP, period) now that bounds are set
	alphas = _compute_alphas(conn, auction_id)
	for (cp_id, period_id), alpha in alphas.items():
		conn.execute("UPDATE control_point_bounds SET alpha = ? WHERE auction_id = ? AND control_point_id = ? AND period_id = ?", (alpha, auction_id, cp_id, period_id),)
	# D.4: compute ConstraintQuota rows now that alphas are available
	cq_count = _compute_constraint_quotas(conn, auction_id)
	conn.commit()
	conn.close()
	return {"bounds_applied": count, "alphas_computed": len(alphas), "constraint_quotas_computed": cq_count, "auction_id": auction_id}

def missing_import_data_report(db_path: Path) -> dict:
	"""Report missing gw model coordinates and lat/lon for wells and control points."""
	if not db_path.exists(): return {"db_exists": False, "wells": {}, "control_points": {},}
	conn = sqlite3.connect(db_path)
	_ensure_spatial_columns(conn)
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
	Derives trader_id, name, and trader_loginid from trader_last_name.
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
		trader_id = "trader-" + last_name.lower() + suffix
		try:
			cur = conn.execute("INSERT OR IGNORE INTO traders" "(trader_id, name, trader_loginid, trader_first_name, trader_last_name)" " VALUES (?,?,?,?,?)", (trader_id, display, display, first_name, last_name),)
			if cur.rowcount: inserted += 1
			else: skipped += 1
		except Exception as exc: errors.append(str(exc))
	conn.commit()
	conn.close()
	return {"traders_inserted": inserted, "traders_skipped": skipped, "errors": errors[:20]}

def import_trader_wells(db_path: Path, text: str) -> dict:
	"""Import trader-well assignments from a tab-delimited file.

	Expected columns: name (must match traders.name), well_id (must match wells.well_id).
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
		trader_row = conn.execute("SELECT trader_id FROM traders WHERE name=?", (name_val,) ).fetchone()
		if trader_row is None:
			errors.append(f"Trader name not found in traders table: {name_val!r}")
			continue
		well_row = conn.execute("SELECT well_id FROM wells WHERE well_id=?", (well_id_val,) ).fetchone()
		if well_row is None:
			errors.append(f"Well ID not found in wells table: {well_id_val!r}")
			continue
		conn.execute("UPDATE wells SET trader_id=? WHERE well_id=?", (trader_row["trader_id"], well_id_val),)
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
	_ensure_spatial_columns(conn)
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

		cur = conn.execute("UPDATE wells SET latitude=?, longitude=? WHERE well_id=?", (lat_val, lon_val, well_id),)
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
	_ensure_spatial_columns(conn)
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

		cur = conn.execute("UPDATE control_points SET latitude=?, longitude=? WHERE control_point_id=?", (lat_val, lon_val, cp_id),)
		if cur.rowcount: updated += 1
		else:
			skipped += 1
			errors.append(f"Control point not found: {cp_id!r}")

	conn.commit()
	conn.close()
	return {"control_points_updated": updated, "rows_skipped": skipped, "errors": errors[:20],}
