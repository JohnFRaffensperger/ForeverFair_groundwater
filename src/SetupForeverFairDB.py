# SetupForeverFairDB.py. JFR with Claude's help, 2026-05-08.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Database creation, deletion, and GWM2K file import utilities.
# Keep SCHEMA_DDL in sync with the executescript() call in services/repository.py.

from __future__ import annotations
import gc
import math
import re
import sqlite3
# import sys
import time
from datetime import datetime, timedelta
from typing import Any
from pathlib import Path

# Database creation, status, deletion. -----------------------------------------------
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS Catchment_info (meta_key TEXT PRIMARY KEY, text_value TEXT, integer_value INTEGER);
CREATE TABLE IF NOT EXISTS control_points (control_point_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, gw_model_layer INTEGER, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL);
CREATE TABLE IF NOT EXISTS aquifer_head_limits (control_point_event_id INTEGER PRIMARY KEY AUTOINCREMENT, control_point_id INTEGER, effect_date TEXT, upload_date TEXT, actual_start_head REAL, minimum_head REAL, allowable_head_change REAL, head_constraint_upper_bound REAL, license_demand REAL, FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id));
CREATE TABLE IF NOT EXISTS response_matrix (well_id INTEGER NOT NULL, control_point_id INTEGER NOT NULL, pumping_period INTEGER NOT NULL, effect_period INTEGER NOT NULL, factor_value REAL NOT NULL, PRIMARY KEY (well_id, control_point_id, pumping_period, effect_period), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id));
CREATE TABLE IF NOT EXISTS traders (trader_id INTEGER PRIMARY KEY AUTOINCREMENT, name_tag TEXT NOT NULL, trader_loginid TEXT, trader_password TEXT, trader_first_name TEXT, trader_last_name TEXT, trader_address TEXT, trader_city TEXT, trader_phone TEXT, trader_email TEXT);
CREATE TABLE IF NOT EXISTS wells (well_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, trader_id INTEGER, gw_model_layer INTEGER, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL, FOREIGN KEY (trader_id) REFERENCES traders(trader_id));

CREATE TABLE IF NOT EXISTS auctions (auction_id INTEGER PRIMARY KEY AUTOINCREMENT, auction_type TEXT, created_date TEXT NOT NULL, closed_date TEXT, status TEXT NOT NULL, firstWaterTakeDate TEXT, lastWaterTakeDate TEXT, period_length_hours INTEGER, solve_status TEXT, objective_value REAL, auction_revenue REAL);
CREATE TABLE IF NOT EXISTS control_point_events (cpe_id INTEGER PRIMARY KEY AUTOINCREMENT, control_point_id INTEGER, auction_id INTEGER, effect_date TEXT, committed_head_auction_start REAL, committed_head_auction_end REAL, planned_head_auction_start REAL, planned_head_auction_end REAL, slack REAL, dual_price REAL, FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id));
CREATE TABLE IF NOT EXISTS well_license (license_id INTEGER PRIMARY KEY AUTOINCREMENT, trader_id INTEGER, well_id INTEGER, bid_period INTEGER, license_quantity REAL, license_date TEXT);
CREATE TABLE IF NOT EXISTS well_quota (quota_id INTEGER PRIMARY KEY AUTOINCREMENT, trader_id INTEGER, auction_id INTEGER, well_id INTEGER, quota_auction_start REAL, quota_adjusted REAL, quota_auction_end REAL, price REAL, take_date TEXT);
CREATE TABLE IF NOT EXISTS well_bids (bid_id INTEGER PRIMARY KEY AUTOINCREMENT, well_id INTEGER, trader_id INTEGER, auction_id INTEGER, bid_date TEXT, pumping_date TEXT, expiry_date TEXT, is_bid_default INTEGER DEFAULT 0, qty1 REAL, price1 REAL, qty2 REAL, price2 REAL, qty3 REAL, price3 REAL, qty4 REAL, price4 REAL, qty5 REAL, price5 REAL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_aquifer_head_limits_control_point_effect_date ON aquifer_head_limits(control_point_id, effect_date);
CREATE INDEX IF NOT EXISTS idx_aquifer_head_limits_effect_date ON aquifer_head_limits(effect_date);
CREATE INDEX IF NOT EXISTS idx_response_matrix_well_pumping_effect_control ON response_matrix(well_id, pumping_period, effect_period, control_point_id);
CREATE INDEX IF NOT EXISTS idx_well_license_well_bid_period ON well_license(well_id, bid_period);
"""

# Programmer.html, section 0. 
def create_empty_db(db_path: Path) -> None:
	"""Create all tables and seed default catchment settings."""
	db_path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(db_path)
	conn.executescript(SCHEMA_DDL)
	save_catchment_info(conn, "synthetic_current_date", "2030-01-04T16:00")
	save_catchment_info(conn, "MAX_BID_STEPS", 3) # Default. Up to 5.
	save_catchment_info(conn, "num_bidding_periods", 20) # Default. Length of your hydrological season.
	save_catchment_info(conn, "Rights_policy", "Quota_scaled") # or "Auction_manager_pays" or "Users_pay"
	conn.commit()
	conn.close()

def create_demonstration_db(db_path: Path, lp_path: Path | None = None, period_length_hours: int = 168) -> dict[str, Any]:
	"""Create a fresh demonstration DB matching tests/test.txt scenario values.
	3 traders A, B, and C, each with a well, i.e., well_A, well_B, and well_C, each with initial license of 5.
	2 control points, Coast and Stream.

	Response matrix base vectors (pumping period 1, effect periods 1..5)
			period  	1		2 		3	 	4		5
	well_A to Coast: 	-3.0	-2.1	-1.4	-1.0	-0.5
	well_B to Coast: 	-2.0	-1.4	-1.0	-0.7	-0.5
	well_C to Coast: 	-1.0	-0.7	-0.5	-0.3	-0.2
	well_A to Stream: 	-2.0	-1.0	-0.5	-0.25	-0.13
	well_B to Stream: 	-2.0	-1.0	-0.5	-0.25	-0.12
	well_C to Stream: 	-0.7	-0.35	-0.2	-0.1	0.0

	Control point	Now		Lower limit
	Coast			100		80
	Stream			120		90
	"""
	_ = lp_path

	# Create the database.
	if db_path.exists(): delete_db(db_path)
	create_empty_db(db_path)
	conn = sqlite3.connect(db_path)
	upload_date = datetime.now().isoformat(timespec="minutes")

	# Add traders, wells, control points.
	conn.execute("INSERT INTO traders(trader_id, name_tag, trader_loginid, trader_first_name) VALUES (1, 'A','A','A')")
	conn.execute("INSERT INTO traders(trader_id, name_tag, trader_loginid, trader_first_name) VALUES (2, 'B','B','B')")
	conn.execute("INSERT INTO traders(trader_id, name_tag, trader_loginid, trader_first_name) VALUES (3, 'C','C','C')")

	conn.execute("INSERT INTO wells(well_id, name, trader_id) VALUES (1, 'well_A', 1)")
	conn.execute("INSERT INTO wells(well_id, name, trader_id) VALUES (2, 'well_B', 2)")
	conn.execute("INSERT INTO wells(well_id, name, trader_id) VALUES (3, 'well_C', 3)")

	conn.execute("INSERT INTO control_points(control_point_id, name) VALUES (1, 'Coast')")
	conn.execute("INSERT INTO control_points(control_point_id, name) VALUES (2, 'Stream')")

	# Add the response matrix.
	conn.execute("DELETE FROM response_matrix")
	# Base vectors for pumping period 1 with effect periods 1..5.
	# For pumping periods 2..4, shift right by (pumping_period-1); overflow drops off.
	# Store values as negative response coefficients.
	base_response_vectors: dict[tuple[int, int], list[float]] = {
		(1, 1): [-3.0, -2.1, -1.4, -1.0, -0.5],
		(2, 1): [-2.0, -1.4, -1.0, -0.7, -0.5],
		(3, 1): [-1.0, -0.7, -0.5, -0.3, -0.2],
		(1, 2): [-2.0, -1.0, -0.5, -0.25, -0.12],
		(2, 2): [-2.0, -1.0, -0.5, -0.25, -0.12],
		(3, 2): [-0.7, -0.35, -0.2, -0.1, 0.0],}
	response_rows: list[tuple[int, int, int, int, float]] = []
	num_pumping_periods = 4
	num_effect_periods = 5

	for pumping_period in range(1, num_pumping_periods + 1):
		shift = pumping_period - 1
		for (well_id, control_point_id), vector in base_response_vectors.items():
			for effect_period in range(1, num_effect_periods + 1):
				idx = effect_period - shift - 1
				if idx < 0 or idx >= len(vector): continue
				factor_value = vector[idx]
				if 0.0 == factor_value: continue
				response_rows.append((well_id, control_point_id, pumping_period, effect_period, factor_value))
	conn.executemany("INSERT INTO response_matrix(well_id, control_point_id, pumping_period, effect_period, factor_value) VALUES (?,?,?,?,?)", response_rows)

	# Add meta data.
	synthetic_current_date = "2030-01-04T16:00"
	save_catchment_info(conn, "synthetic_current_date", synthetic_current_date)
	save_catchment_info(conn, "period_length_hours", int(period_length_hours))
	save_catchment_info(conn, "num_bidding_periods", num_pumping_periods)
	save_catchment_info(conn, "gwm_num_wells", 3)
	save_catchment_info(conn, "gwm_num_pump_periods", num_pumping_periods)
	save_catchment_info(conn, "gwm_num_control_points", 2)
	save_catchment_info(conn, "gwm_num_control_periods", num_effect_periods)

	base = datetime.fromisoformat(synthetic_current_date)
	days_ahead = (0 - base.weekday()) % 7
	start_dt = (base + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
	if start_dt <= base: start_dt += timedelta(days=7)
	step = timedelta(hours=int(period_length_hours))
	pumping_date = [(start_dt + i * step).isoformat(timespec="minutes") for i in range(num_effect_periods)]

	# Add head limits.
	conn.execute("DELETE FROM aquifer_head_limits")
	aquifer_rows: list[tuple[int, str, str, float, float]] = []
	for effect_date in pumping_date:
		aquifer_rows.append((1, effect_date, upload_date, 100.0, 80.0))
		aquifer_rows.append((2, effect_date, upload_date, 120.0, 108.0))
	conn.executemany ("INSERT INTO aquifer_head_limits(control_point_id, effect_date, upload_date, actual_start_head, minimum_head) VALUES (?,?,?,?,?)", aquifer_rows,)

	# Add well license and bids.
	conn.execute("DELETE FROM well_license")
	license_rows: list[tuple[int, int, int, float, None]] = []
	for bid_period in range(1, 5): # All 3 wells start with license of 5 m^3 per period.
		license_rows.append((1, 1, bid_period, 5.0, None))
		license_rows.append((2, 2, bid_period, 5.0, None))
		license_rows.append((3, 3, bid_period, 5.0, None))
	conn.executemany("INSERT INTO well_license(trader_id, well_id, bid_period, license_quantity, license_date) VALUES (?,?,?,?,?)", license_rows,)

	conn.execute("DELETE FROM well_bids")
	bid_rows: list[tuple[Any, ...]] = []
	#        well_id, trader_id, auction_id, bid_date, pumping_date, expiry_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default,
	bid_rows.append((1, 1, 1, upload_date, pumping_date[0], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((1, 1, 1, upload_date, pumping_date[1], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((1, 1, 1, upload_date, pumping_date[2], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((1, 1, 1, upload_date, pumping_date[3], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))

	bid_rows.append((2, 2, 1, upload_date, pumping_date[0], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((2, 2, 1, upload_date, pumping_date[1], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((2, 2, 1, upload_date, pumping_date[2], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((2, 2, 1, upload_date, pumping_date[3], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))

	bid_rows.append((3, 3, 1, upload_date, pumping_date[0], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((3, 3, 1, upload_date, pumping_date[1], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	bid_rows.append((3, 3, 1, upload_date, pumping_date[2], 0, 3.3333333, 1.0, 3.3333333, 2.0, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	# Changing step 2 from $2 to $1.8 to eliminate a dual optima.
	bid_rows.append((3, 3, 1, upload_date, pumping_date[3], 0, 3.3333333, 1.0, 3.3333333, 1.8, 3.3333333, 4.0, None, None, None, None, 0, 0, ))
	conn.executemany("INSERT INTO well_bids(well_id, trader_id, auction_id, bid_date, pumping_date, expiry_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default, deleted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", bid_rows)

	conn.commit()
	conn.close()
	return {"db_path": str(db_path), "traders_inserted": 3, "wells_inserted": 3, "control_points_inserted": 2, "response_matrix_inserted": len(response_rows),
		"aquifer_head_limits_inserted": len(aquifer_rows), "well_license_inserted": len(license_rows), "well_bids_inserted": len(bid_rows), }

def setup_demonstration() -> Path:
	"""Create a demonstration DB for the tests/test.txt scenario.
	In your directory ...\ForeverFair2026, edit your ".env" file to read:
		FOREVER_FAIR_CATCHMENT=Tiny_demonstration
	"""
	
	data_dir = Path(__file__).resolve().parents[1] / "Catchment_data" / "Tiny_demonstration"
	db_path = data_dir / "foreverfair.db"
	summary = create_demonstration_db(db_path, period_length_hours=168)
	import AuctionController
	auction_id = AuctionController.set_up_auction_system(db_path)
	print(f"Created demonstration db: {db_path}")
	print(summary)
	print(f"Set up: first auction_id={auction_id}")
	return db_path

# Populates database status table on Programmer.html.
def db_status(db_path: Path) -> dict[str, Any]:
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

def delete_db(db_path: Path) -> None:
	"""Delete the database file and any WAL/SHM sidecar files if they exist."""
	failed: list[str] = []
	for suffix in ("", "-wal", "-shm"):
		p = db_path.parent / (db_path.name + suffix)
		if not p.exists(): continue
		last_exc: OSError | None = None
		for _ in range(20):
			if not p.exists(): break
			try:
				p.unlink()
				break
			except OSError as exc:
				last_exc = exc
				# If a leaked sqlite3 handle is waiting for GC finalization, release it.
				gc.collect()
				time.sleep(0.05)
		if p.exists(): failed.append(f"{p}: {last_exc}")
	if failed: raise OSError("Failed to delete one or more database files: " + "; ".join(failed))

# Meta data. -------------------------
# def ensure_catchment_info_table(conn: sqlite3.Connection) -> None:
# 	"""Ensure Catchment_info exists with typed columns."""
# 	conn.execute("CREATE TABLE IF NOT EXISTS Catchment_info (meta_key TEXT PRIMARY KEY, text_value TEXT, integer_value INTEGER)")

def get_catchment_info(conn: sqlite3.Connection, key: str) -> str | int | None:
	# ensure_catchment_info_table(conn)
	row = conn.execute("SELECT text_value, integer_value FROM Catchment_info WHERE meta_key=?", (key,)).fetchone()
	if row is None: return None
	text_val, int_val = row[0], row[1]
	return int_val if int_val is not None else text_val

def save_catchment_info(conn: sqlite3.Connection, key: str, value: str | int | float) -> None:
	# ensure_catchment_info_table(conn)
	if conn.execute("SELECT 1 FROM Catchment_info WHERE meta_key='Catchment_name' LIMIT 1").fetchone() is None:
		row = conn.execute("PRAGMA database_list").fetchone()
		db_path = "" if row is None or len(row) < 3 else str(row[2] or "")
		case_name = Path(db_path).parent.name.strip() if db_path else ""
		if case_name: conn.execute("INSERT OR IGNORE INTO Catchment_info(meta_key, text_value) VALUES ('Catchment_name', ?)", (case_name,))
	if isinstance(value, int):
		conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, integer_value, text_value) VALUES (?, ?, NULL)", (key, value))
	else:
		conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value, integer_value) VALUES (?, ?, NULL)", (key, str(value)))

# Programmer.html, section 1, import decvar. -------------------------------------------
def import_decvar(db_path: Path, text: str) -> dict[str, Any]:
	"""Parse a GWM2K .decvar file; insert one well row per unique well number.

	DECVAR line format:  wellNNNN  NC  LAYER  ROW  COL  FTYPE  FSTAT  stress_period
	  tokens[0] = wellNNNN (sequential index across all well × pump_period combinations)
	  tokens[3] = ROW grid coordinate (used as location note)
	  tokens[4] = COL grid coordinate (used as location note)
	DECVAR has no lower/upper bounds; capacities come from the MPS BOUNDS section.
	Index NNNN is 1-based sequential across all (well × pump_period) combinations:
		well_number  = 1 + (NNNN - 1) // num_pump_periods
		pump_period  = 1 + (NNNN - 1) %  num_pump_periods
	Wells are inserted with trader_id=NULL (unassigned; FK checks off during import).
	gw_model_layer, gw_model_row, and gw_model_column are populated from the first pump-period entry.
	latitude and longitude remain NULL until georeferenced data is supplied.
	"""
	num_wells, num_pump_periods = mps_decvar_decoding(text)

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
			well_num, _pump_period = mps_well_decoding(tokens[0], num_pump_periods)
			if well_num in seen: continue
			seen.add(well_num)
			# tokens[2]=LAYER, tokens[3]=ROW, tokens[4]=COL give the grid location of this physical well
			gw_model_layer = int(tokens[2]) if len(tokens) >= 3 and tokens[2].lstrip("-").isdigit() else None
			gw_model_row = int(tokens[3]) if len(tokens) >= 4 and tokens[3].lstrip("-").isdigit() else None
			gw_model_column = int(tokens[4]) if len(tokens) >= 5 and tokens[4].lstrip("-").isdigit() else None
			well_id = int(well_num)
			conn.execute("INSERT OR IGNORE INTO wells (well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude) VALUES (?,?,?,?,?,?,?,?)", (well_id, f"gwm-well-{well_num}", None, gw_model_layer, gw_model_row, gw_model_column, None, None))
			conn.execute("UPDATE wells SET gw_model_layer=COALESCE(gw_model_layer, ?), gw_model_row=COALESCE(gw_model_row, ?), gw_model_column=COALESCE(gw_model_column, ?) WHERE well_id=?", (gw_model_layer, gw_model_row, gw_model_column, well_id))
			inserted += 1
		except Exception as exc:
			errors.append(str(exc))
	conn.commit()
	save_catchment_info(conn, "gwm_num_wells", int(num_wells))
	save_catchment_info(conn, "gwm_num_pump_periods", int(num_pump_periods)) # Unused!
	conn.commit()
	conn.close()
	return {"wells_inserted": inserted, "num_wells": num_wells, "num_pump_periods": num_pump_periods, "errors": errors[:20],}

# Programmer.html, section 2, import hedcon. -------------------------------------------
def import_hedcon(db_path: Path, text: str, num_control_points: int | None = None, num_control_periods: int | None = None) -> dict[str, Any]:
	"""Parse a GWM2K .hedcon file; insert control points and write minimum heads to aquifer_head_limits.

	HEDCON line format:  bonNNNN  LAYER  ROW  COL  SENSE  RHS_head  stress_period
	  tokens[0] = bonNNNN (sequential index across all control_point × control_period)
	  tokens[5] = RHS_head (the head target, e.g. 829.0 metres)
	RHS_head values are written directly to aquifer_head_limits.minimum_head using ISO datetime
	effect_dates computed from synthetic_current_date and period_length_hours in Catchment_info.
	import_mps augments these rows with actual_start_head and allowable_head_change.
	Uploading a new .hedcon file clears and rewrites all aquifer_head_limits rows.
	Index NNNN is 1-based sequential across all (control_point × control_period):
		cp_number     = 1 + (NNNN - 1) // num_control_periods
		effect_period = 1 + (NNNN - 1) %  num_control_periods
	"""
	if num_control_points is None or num_control_periods is None: num_control_points, num_control_periods = mps_hedcon_decoding(text)

	conn = sqlite3.connect(db_path)
	seen: set[int] = set()
	cp_inserted = 0
	bounds_inserted = 0
	errors: list[str] = []
	upload_date = datetime.now().isoformat(timespec="minutes")
	period_length_hours = get_catchment_info(conn, "period_length_hours")
	# if period_length_hours is None:
	# 	conn.close()
	# 	raise ValueError("period_length_hours not set in Catchment_info. Set the auction calendar (section 1) before importing .hedcon.")
	effect_dates: list[str] = []
	syn_row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key='synthetic_current_date'").fetchone()
	if syn_row and syn_row[0]:
		base = datetime.fromisoformat(str(syn_row[0]))
		days_ahead = (0 - base.weekday()) % 7
		start_dt = (base + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
		if start_dt <= base: start_dt += timedelta(days=7)
		step = timedelta(hours=int(period_length_hours))
		effect_dates = [(start_dt + i * step).isoformat(timespec="minutes") for i in range(max(0, int(num_control_periods)))]
	conn.execute("DELETE FROM aquifer_head_limits")
	for line in text.splitlines():
		m = _BON_RE.match(line.strip())
		if not m: continue
		try:
			tokens = line.split()
			idx = int(m.group(1))
			cp_num, effect_period = mps_control_pt_decoding(str(idx), num_control_periods)
			cp_name = f"gwm-cp-{cp_num}"
			gw_model_layer = int(tokens[1]) if len(tokens) >= 2 and tokens[1].lstrip("-").isdigit() else None
			gw_model_row = int(tokens[2]) if len(tokens) >= 3 and tokens[2].lstrip("-").isdigit() else None
			gw_model_column = int(tokens[3]) if len(tokens) >= 4 and tokens[3].lstrip("-").isdigit() else None
			if cp_num in seen:
				row = conn.execute("SELECT control_point_id FROM control_points WHERE name=?", (cp_name,)).fetchone()
				if row is None: continue
				cp_id = int(row[0])
			else:
				seen.add(cp_num)
				row = conn.execute("SELECT control_point_id FROM control_points WHERE name=?", (cp_name,)).fetchone()
				if row is None:
					cur = conn.execute("INSERT INTO control_points (name, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude) VALUES (?,?,?,?,?,?)", (cp_name, gw_model_layer, gw_model_row, gw_model_column, None, None))
					cp_id = int(cur.lastrowid or 0)
					cp_inserted += 1
				else:
					cp_id = int(row[0])
				conn.execute("UPDATE control_points SET gw_model_layer=COALESCE(gw_model_layer, ?), gw_model_row=COALESCE(gw_model_row, ?), gw_model_column=COALESCE(gw_model_column, ?) WHERE control_point_id=?", (gw_model_layer, gw_model_row, gw_model_column, cp_id))

			rhs_head = float(tokens[5]) if len(tokens) >= 6 else None
			if rhs_head is not None:
				effect_date = effect_dates[effect_period - 1] if 1 <= effect_period <= len(effect_dates) else None
				if effect_date is None: errors.append(f"effect_period {effect_period} out of range (num_control_periods={num_control_periods})"); continue
				conn.execute("DELETE FROM aquifer_head_limits WHERE control_point_id=? AND effect_date=?", (cp_id, effect_date))
				conn.execute("INSERT INTO aquifer_head_limits(control_point_id, effect_date, upload_date, minimum_head) VALUES (?, ?, ?, ?)", (cp_id, effect_date, upload_date, rhs_head))
				bounds_inserted += 1
		except Exception as exc: errors.append(str(exc))
	conn.commit()
	save_catchment_info(conn, "gwm_num_control_points", int(num_control_points))
	save_catchment_info(conn, "gwm_num_control_periods", int(num_control_periods))
	conn.commit()
	conn.close()
	return {"control_points_inserted": cp_inserted, "control_point_rows_inserted": bounds_inserted, "num_control_points": num_control_points, "num_control_periods": num_control_periods, "errors": errors[:20],}

# Programmer.html, section 3. Importing trader names.  -------------------------------------------------------------------------------
def import_trader_names(db_path: Path, text: str) -> dict[str, Any]:
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
		count = seen.setdefault(key, 0)
		suffixes.append("" if count == 0 else str(count))
		seen[key] = count + 1

	conn = sqlite3.connect(db_path)
	inserted = skipped = 0
	errors: list[str] = []
	for (first_name, last_name), suffix in zip(parsed, suffixes):
		display = last_name + suffix
		try:
			cur = conn.execute("INSERT OR IGNORE INTO traders (name_tag, trader_loginid, trader_first_name, trader_last_name) VALUES (?,?,?,?)", (display, display, first_name, last_name),)
			if cur.rowcount: inserted += 1
			else: skipped += 1
		except Exception as exc: errors.append(str(exc))
	conn.commit()
	conn.close()
	return {"traders_inserted": inserted, "traders_skipped": skipped, "errors": errors[:20]}

# Programmer.html, section 4, trader-well pairs. -------------------------------------------
def import_trader_wells(db_path: Path, text: str) -> dict[str, Any]:
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

# Programmer.html, section 5, well lat/long. -------------------------------------------
def import_well_lat_lon(db_path: Path, text: str) -> dict[str, Any]:
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

# Programmer.html, section 6, control point lat/long.-------------------------------------------
def import_control_point_lat_lon(db_path: Path, text: str) -> dict[str, Any]:
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

# Programmer.html, section 8. Reading the MPS file ----------------------------------------------------------------
_WELL_RE = re.compile(r'[Ww][Ee][Ll][Ll](\d+)', re.ASCII)
def mps_well_decoding(token: str, num_pump_periods: int) -> tuple[int, int]:
	"""wellNNNN → (well_number, pump_period), both 1-based."""
	m = _WELL_RE.match(token)
	if not m: raise ValueError(f"Cannot decode well token: {token!r}")
	idx = int(m.group(1))
	well_num   = 1 + (idx - 1) // num_pump_periods
	pump_period = 1 + (idx - 1) %  num_pump_periods
	return well_num, pump_period

_BON_RE  = re.compile(r'[Bb][Oo][Nn](\d+)', re.ASCII)
_NUM_RE  = re.compile(r'^(\d+)$')
def mps_control_pt_decoding(token: str, num_control_periods: int) -> tuple[int, int]:
	"""Numeric row index or bonNNNN → (cp_number, effect_period), both 1-based."""
	m_num = _NUM_RE.match(token.strip())
	m_bon = _BON_RE.match(token)
	if m_num: idx = int(m_num.group(1))
	elif m_bon: idx = int(m_bon.group(1))
	else: raise ValueError(f"Cannot decode CP token: {token!r}")
	cp_num        = 1 + (idx - 1) // num_control_periods
	effect_period = 1 + (idx - 1) %  num_control_periods
	return cp_num, effect_period

# MPS file decoding.
def mps_decvar_decoding(text: str) -> tuple[int, int]:
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

def mps_hedcon_decoding(text: str) -> tuple[int, int]:
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

def get_mps_indices(text: str) -> tuple[int, int]:
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

def Modify_license_for_your_scenario(raw_license: float, well_id: int, bid_period: int) -> float:
	"""Scale raw per-well license for scenario analysis before writing to well_license.
	"""
	Fudge = 0.0 # Should have large auction revenue. Fix function create_default_bid in BiddingController so you don't have zero bids.
	return raw_license * Fudge

def import_mps(db_path: Path, text: str, period_length_hours: int) -> dict[str, Any]:
	"""Parse a GWM2K .mps file; insert response_matrix, aquifer_head_limits, and well_license.

	GWM2K MPS section formats (4-token per line):
	  COLUMNS:  wellNNNN  model_name  row_index  coefficient
				(model_name at [1] is ignored; same logic as PHP GWM_files2MYSQL.php)
				Lines with [1]=="OBJ" are objective-function coefficients and are skipped.
	  RHS:      rhs_label  rhs_name  row_index  value
				value is the allowable_head_change = minimum_head (h_l) - actual_start_head (h_0).
				minimum_head is read from aquifer_head_limits rows written by import_hedcon.
				actual_start_head (h_0) = minimum_head - LP_RHS (the base-run head from GWM HDCSTATE0).
				actual_start_head and allowable_head_change are updated in aquifer_head_limits.
	  BOUNDS:   bound_type  bnd_name  wellNNNN  value
				UP bounds are stored in well_license as one row per well and bid period.
	"""
	conn = sqlite3.connect(db_path)
	conn.execute("PRAGMA foreign_keys = OFF")
	imported_at = datetime.now().isoformat()
	conn.execute("DELETE FROM well_license")

	max_well_idx, max_row_idx = get_mps_indices(text)
	num_wells = get_catchment_info(conn, "gwm_num_wells")
	num_pump_periods = get_catchment_info(conn, "gwm_num_pump_periods")
	num_control_points = get_catchment_info(conn, "gwm_num_control_points")
	num_control_periods = get_catchment_info(conn, "gwm_num_control_periods")

	if num_wells is None:
		row = conn.execute("SELECT COUNT(*) FROM wells").fetchone()
		num_wells = int(row[0]) if row is not None else None
	if num_control_points is None:
		row = conn.execute("SELECT COUNT(*) FROM control_points").fetchone()
		num_control_points = int(row[0]) if row is not None else None

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
	cp_id_by_num: dict[int, int] = {}
	well_trader_map: dict[int, int | None] = {}
	unassigned_wells: set[int] = set()
	effect_dates: list[str] = []
	row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key='synthetic_current_date'").fetchone()
	if row is not None and row[0]:
		base = datetime.fromisoformat(str(row[0]))
		days_ahead = (0 - base.weekday()) % 7
		start_dt = (base + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
		if start_dt <= base: start_dt += timedelta(days=7)
		step = timedelta(hours=period_length_hours)
		for i in range(max(0, int(num_control_periods or 0))):
			effect_dates.append((start_dt + i * step).isoformat(timespec="minutes"))
	bidding_periods = get_catchment_info(conn, "num_bidding_periods")

	for row in conn.execute("SELECT control_point_id, name FROM control_points").fetchall():
		cp_id = int(row[0])
		cp_name = str(row[1] or "")
		if cp_name.startswith("gwm-cp-"):
			try: cp_num = int(cp_name.split("gwm-cp-")[-1])
			except ValueError: continue
			cp_id_by_num[cp_num] = cp_id

	def _ensure_cp_id(cp_num: int) -> int:
		try: return cp_id_by_num[cp_num]
		except KeyError: pass
		cp_name = f"gwm-cp-{cp_num}"
		row = conn.execute("SELECT control_point_id FROM control_points WHERE name=?", (cp_name,)).fetchone()
		if row is None:
			cur = conn.execute("INSERT INTO control_points(name, gw_model_row, gw_model_column, latitude, longitude) VALUES (?,?,?,?,?)", (cp_name, None, None, None, None),)
			cp_id = int(cur.lastrowid or 0)
		else:
			cp_id = int(row[0])
		cp_id_by_num[cp_num] = cp_id
		return cp_id
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
				well_num, pump_period = mps_well_decoding(col_token, num_pump_periods)
				cp_num, effect_period = mps_control_pt_decoding(row_token, num_control_periods)
				well_id = int(well_num)
				cp_id = _ensure_cp_id(cp_num)
				conn.execute("INSERT OR REPLACE INTO response_matrix (well_id, control_point_id, pumping_period, effect_period, factor_value) VALUES (?,?,?,?,?)", (well_id, cp_id, pump_period, effect_period, coef))
				rf_count += 1
			except Exception as exc:
				errors.append(f"COLUMNS {col_token} row {row_token}: {exc}")

		elif section == "RHS":
			# GWM2K format: rhs_label  rhs_name  row_index  value
			# Compute and store aquifer head limits from MPS RHS and staged hedcon minimum heads.
			if len(tokens) < 4: continue
			row_token = tokens[2]
			try: val = float(tokens[3])
			except ValueError: continue
			try:
				cp_num, effect_period = mps_control_pt_decoding(row_token, num_control_periods)
				cp_id = _ensure_cp_id(cp_num)
				effect_date = effect_dates[effect_period - 1] if 1 <= effect_period <= len(effect_dates) else str(effect_period)
				# minimum_head from aquifer_head_limits written by import_hedcon; actual_start_head (h_0) = minimum_head - LP_RHS.
				min_row = conn.execute("SELECT minimum_head FROM aquifer_head_limits WHERE control_point_id=? AND effect_date=?", (cp_id, effect_date)).fetchone()
				minimum_head_val = float(min_row[0]) if min_row and min_row[0] is not None else None
				actual_start_head = (minimum_head_val - val) if minimum_head_val is not None else None
				conn.execute("UPDATE aquifer_head_limits SET actual_start_head=?, allowable_head_change=? WHERE control_point_id=? AND effect_date=?", (actual_start_head, val, cp_id, effect_date))
				bound_count += 1
			except Exception as exc:
				errors.append(f"RHS row {row_token}: {exc}")

		elif section == "BOUNDS":
			# Format: bnd_type  bnd_name  wellNNNN  value. Bounds go to well_license and initial quota.
			if len(tokens) < 4: continue
			if str(tokens[0]).upper() != "UP": continue # Ignoring lower bounds.
			col_token = tokens[2]
			if not _WELL_RE.match(col_token): continue
			try: raw_license = float(tokens[3])
			except ValueError: continue
			try:
				well_num, pump_period = mps_well_decoding(col_token, num_pump_periods)
				well_id = int(well_num)
				license_quantity = Modify_license_for_your_scenario(raw_license, well_id, pump_period)
				before_changes = conn.total_changes
				conn.execute("INSERT OR IGNORE INTO wells (well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude) VALUES (?,?,?,?,?,?,?,?)", (well_id, f"gwm-well-{well_num}", None, None, None, None, None, None))
				if conn.total_changes > before_changes: wells_ensured += 1
				if well_id not in well_trader_map:
					row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
					well_trader_map[well_id] = None if row is None or row[0] is None else int(row[0])
				trader_id = well_trader_map[well_id]
				if trader_id is None:
					if well_id not in unassigned_wells:
						unassigned_wells.add(well_id)
						errors.append(f"BOUNDS well{well_id}: skipped license insert because wells.trader_id is NULL. Import trader-well assignments first.")
					continue
				conn.execute("INSERT INTO well_license(trader_id, well_id, license_quantity, license_date, bid_period) VALUES (?, ?, ?, ?, ?)", (trader_id, well_id, license_quantity, None, pump_period))
				license_count += 1
			except Exception as exc:
				errors.append(f"BOUNDS {col_token}: {exc}")

	conn.commit()
	if num_wells: save_catchment_info(conn, "gwm_num_wells", int(num_wells))
	if num_pump_periods: save_catchment_info(conn, "gwm_num_pump_periods", int(num_pump_periods)) # Unused!
	if num_control_points: save_catchment_info(conn, "gwm_num_control_points", int(num_control_points))
	if num_control_periods: save_catchment_info(conn, "gwm_num_control_periods", int(num_control_periods))
	save_catchment_info(conn, "period_length_hours", period_length_hours)
	save_catchment_info(conn, "mps_loaded_date", imported_at)
	save_catchment_info(conn, "mps_notes", "Imported from .mps via Programmer page")
	conn.commit()
	conn.close()
	return {"response_matrix_inserted": rf_count, "aquifer_head_rows_inserted": bound_count, "license_rows_inserted": license_count, "wells_ensured": wells_ensured, "num_wells": num_wells, "num_pump_periods": num_pump_periods, "num_control_points": num_control_points, "num_control_periods": num_control_periods, "period_length_hours": period_length_hours, "errors": errors[:20],}

# Show info on Programmer.html. -----------------------------------------------
def missing_import_data_report(db_path: Path) -> dict[str, Any]:
	"""Report missing gw model coordinates and lat/lon for wells and control points."""
	if not db_path.exists(): return {"db_exists": False, "wells": {}, "control_points": {},}
	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row
	zero_counts = {"total": 0, "missing_gw_model_row": 0, "missing_gw_model_column": 0, "missing_latitude": 0, "missing_longitude": 0}
	try:
		tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
		if "wells" not in tables or "control_points" not in tables:
			return {"db_exists": True, "wells": dict(zero_counts), "control_points": dict(zero_counts), "sample_missing_well_ids": [], "sample_missing_control_point_ids": [],}
		well_counts = conn.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN gw_model_row IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_row, SUM(CASE WHEN gw_model_column IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_column, SUM(CASE WHEN latitude IS NULL THEN 1 ELSE 0 END) AS missing_latitude, SUM(CASE WHEN longitude IS NULL THEN 1 ELSE 0 END) AS missing_longitude FROM wells" ).fetchone()
		cp_counts = conn.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN gw_model_row IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_row, SUM(CASE WHEN gw_model_column IS NULL THEN 1 ELSE 0 END) AS missing_gw_model_column, SUM(CASE WHEN latitude IS NULL THEN 1 ELSE 0 END) AS missing_latitude, SUM(CASE WHEN longitude IS NULL THEN 1 ELSE 0 END) AS missing_longitude FROM control_points" ).fetchone()
		sample_wells = [r[0] for r in conn.execute("SELECT well_id FROM wells WHERE gw_model_row IS NULL OR gw_model_column IS NULL OR latitude IS NULL OR longitude IS NULL ORDER BY well_id LIMIT 8" ).fetchall()]
		sample_cps = [r[0] for r in conn.execute("SELECT control_point_id FROM control_points WHERE gw_model_row IS NULL OR gw_model_column IS NULL OR latitude IS NULL OR longitude IS NULL ORDER BY control_point_id LIMIT 8" ).fetchall()]
		return {"db_exists": True, "wells": {k: int(well_counts[k] or 0) for k in well_counts.keys()}, "control_points": {k: int(cp_counts[k] or 0) for k in cp_counts.keys()}, "sample_missing_well_ids": sample_wells, "sample_missing_control_point_ids": sample_cps,}
	finally:
		conn.close()

def setup_tianqiao(): # When you don't feel like using Programmer.html.
	"""Delete foreverfair.db and reload from Tianqiao data files, replicating the Programmer.html workflow."""
	data_dir = Path(__file__).resolve().parents[1] / "Catchment_data" / "Tianqiao"
	db_path = data_dir / "foreverfair.db"
	delete_db(db_path)
	print("Deleted db")
	create_empty_db(db_path)
	conn = sqlite3.connect(db_path)
	# Set auction calendar before import_hedcon.
	save_catchment_info(conn, "period_length_hours", 168)
	save_catchment_info(conn, "num_bidding_periods", 20)
	conn.commit()
	conn.close()
	import_decvar(db_path, (data_dir / "tianqiao.decvar").read_text())
	import_hedcon(db_path, (data_dir / "tianqiao.hedcon").read_text())
	import_trader_names(db_path, (data_dir / "Trader_names.csv").read_text())
	print("Imported decvar, hedcon, trader_names")

	import_trader_wells(db_path, (data_dir / "Trader_wells.csv").read_text())
	import_well_lat_lon(db_path, (data_dir / "Tianqiao_well_lat_lon_estimated.tsv").read_text())
	import_control_point_lat_lon(db_path, (data_dir / "Tianqiao_control_point_lat_lon_estimated.tsv").read_text())
	print("Imported trader_wells, well lat lon, cp lat lon. Importing mps...")
	import AuctionController
	import_mps(db_path, (data_dir / "tianqiao.mps").read_text(), period_length_hours=168)
	print("Finished mps. Creating first auction ...")
	auction_id = AuctionController.set_up_auction_system(db_path)
	print(f"Set up: first auction_id={auction_id}")

if __name__ == "__main__":
	# if len(sys.argv) > 1 and sys.argv[1].lower() == "demo":
	setup_demonstration()
	# setup_tianqiao()
