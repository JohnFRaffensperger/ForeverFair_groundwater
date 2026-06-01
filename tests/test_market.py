# tests/test_market.py. Claude guided by JFR, 2026 05 31.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Verify market clearing, setup/reset flow, and persisted run output.

import sqlite3
from pathlib import Path
import pytest
from AuctionController import create_auction, runCurrentAuction
import SetupForeverFairDB
from ForeverFairData import ForeverFairData

def _make_repo(tmp_path: Path) -> ForeverFairData:
	project_root = Path(__file__).resolve().parents[1]
	data_dir = project_root / "Catchment_data" / "Tianqiao"
	db_path = tmp_path / "small_debug_database_temp.db"
	SetupForeverFairDB.create_empty_db(db_path)
	# Simulate Programmer.html Section 1: auction calendar settings must precede import_hedcon.
	_conn = sqlite3.connect(db_path)
	SetupForeverFairDB.save_catchment_info(_conn, "period_length_hours", 168)
	_conn.commit(); _conn.close()
	SetupForeverFairDB.import_decvar(db_path, (data_dir / "tianqiao.decvar").read_text())
	SetupForeverFairDB.import_hedcon(db_path, (data_dir / "tianqiao.hedcon").read_text())
	SetupForeverFairDB.import_trader_names(db_path, (data_dir / "Trader_names.csv").read_text())
	SetupForeverFairDB.import_trader_wells(db_path, (data_dir / "Trader_wells.csv").read_text())
	SetupForeverFairDB.import_well_lat_lon(db_path, (data_dir / "Tianqiao_well_lat_lon_estimated.tsv").read_text())
	SetupForeverFairDB.import_control_point_lat_lon(db_path, (data_dir / "Tianqiao_control_point_lat_lon_estimated.tsv").read_text())
	SetupForeverFairDB.import_mps(db_path, (data_dir / "tianqiao.mps").read_text(), period_length_hours=168)
	create_auction(db_path)
	return ForeverFairData(db_path=db_path)

def _seed_auction_id(foreverFairData_instance: ForeverFairData) -> int:
	next_auction = foreverFairData_instance.get_next_auction_info()
	if next_auction is None: raise ValueError("No open auction available in debug database")
	return int(next_auction["auction_id"])

def _first_period_trader_well(foreverFairData_instance: ForeverFairData, auction_id: int) -> tuple[int, int, int]:
	auction = foreverFairData_instance.get_auction_info(auction_id)
	period_key = auction["periods"][0]["id"]
	for trader in foreverFairData_instance.list_of_traders():
		wells = foreverFairData_instance.get_trader_wells(trader["id"])
		if wells:
			well = wells[0]
			return period_key, int(trader["id"]), int(well["id"])
	raise ValueError("No trader well found in debug database")

def test_market_clears_seed_case(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	runCurrentAuction(foreverFairData_instance, auction_id)
	latest = foreverFairData_instance.get_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"
	assert latest["objective_value"] > 0

def test_setup_and_reset_auction_data(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	initial_count = len(foreverFairData_instance.list_auctions())
	auction_id = create_auction(foreverFairData_instance.db_path)
	assert auction_id is not None
	assert len(foreverFairData_instance.list_auctions()) == initial_count + 1
	assert foreverFairData_instance.get_max_bid_steps() == 3
	with foreverFairData_instance.connect_to_db() as conn:
		row = conn.execute("SELECT integer_value FROM Catchment_info WHERE meta_key='MAX_BID_STEPS'").fetchone()
		assert row is not None
		assert row["integer_value"] == 3

	reset_data = _make_repo(tmp_path / "reset")
	assert len(reset_data.list_auctions()) == initial_count

def test_auction_creation_stops_after_schedule_limit(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	with foreverFairData_instance.connect_to_db() as conn:
		conn.execute("UPDATE Catchment_info SET integer_value=1 WHERE meta_key='num_bidding_periods'")
	assert len(foreverFairData_instance.list_auctions()) == 1
	assert foreverFairData_instance.get_remaining_auctions_for_auction(1) == 1
	assert foreverFairData_instance.get_remaining_auctions_for_auction(2) == 0
	with pytest.raises(ValueError, match="No auctions remain in this schedule."):
		create_auction(foreverFairData_instance.db_path)

def test_demonstration_db_uses_causal_shifted_response_matrix(tmp_path):
	db_path = tmp_path / "demo" / "foreverfair.db"
	summary = SetupForeverFairDB.create_tiny_demonstration(db_path, period_length_hours=168)
	assert summary["response_matrix_inserted"] == 83
	with sqlite3.connect(db_path) as conn:
		conn.row_factory = sqlite3.Row
		noncausal = conn.execute(
			"SELECT COUNT(*) AS n FROM response_matrix WHERE pumping_period > effect_period"
		).fetchone()
		assert noncausal is not None
		assert noncausal["n"] == 0

		period_counts = conn.execute(
			"SELECT pumping_period, COUNT(*) AS row_count FROM response_matrix GROUP BY pumping_period ORDER BY pumping_period"
		).fetchall()
		assert [row["row_count"] for row in period_counts] == [29, 24, 18, 12]
		sample_rows = conn.execute(
			"SELECT pumping_period, response FROM response_matrix WHERE well_id=1 AND control_point_id=1 AND effect_period=4 ORDER BY pumping_period"
		).fetchall()
		assert [row["pumping_period"] for row in sample_rows] == [1, 2, 3, 4]
		assert [row["response"] for row in sample_rows] == [-1.0, -1.4, -2.1, -3.0]

def test_manager_run_persists_results(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)

	runCurrentAuction(foreverFairData_instance, auction_id)
	latest = foreverFairData_instance.get_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"

def test_multistep_bid_creates_multiple_lp_variables(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	period_key, _, well_id = _first_period_trader_well(foreverFairData_instance, auction_id)

	first_step = foreverFairData_instance.add_bid(auction_id=auction_id, well_id=well_id, period_id=period_key, quantity=6.0, price=20.0, bid_steps=[(6.0, 20.0), (4.0, 14.0), (2.0, 9.0)],)
	base_id = first_step["id"][:-3]  # strip trailing "-s1"
	expected_ids = {f"{base_id}-s1", f"{base_id}-s2", f"{base_id}-s3"}

	rows = foreverFairData_instance.get_bids(auction_id)
	loaded_ids: set[str] = set()
	for row in rows:
		for step_num in range(1, 6):
			if row[f"qty{step_num}"] is not None and row[f"price{step_num}"] is not None:
				loaded_ids.add(f"bid-{row['bid_id']}-s{step_num}")
	assert expected_ids.issubset(loaded_ids)

	runCurrentAuction(foreverFairData_instance, auction_id)
	latest = foreverFairData_instance.get_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"

def test_get_quota_uses_licenses_and_final_quota(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	seed_auction_id = _seed_auction_id(foreverFairData_instance)
	period_key, trader_id, well_id = _first_period_trader_well(foreverFairData_instance, seed_auction_id)
	auction_id = create_auction(foreverFairData_instance.db_path)
	foreverFairData_instance.set_quota_for_auction(auction_id, source_auction_id=seed_auction_id)
	auction = foreverFairData_instance.get_auction_info(auction_id)
	with foreverFairData_instance.connect_to_db() as conn:
		period_label = next(p["label"] for p in auction["periods"] if p["id"] == period_key)
		conn.execute("INSERT INTO well_license(trader_id, well_id, license_quantity, license_date, bid_period) VALUES (?, ?, ?, ?, ?)", (trader_id, well_id, 90.0, None, 1),)
		conn.execute("UPDATE well_quota SET quota_auction_start=? WHERE trader_id=? AND auction_id=? AND well_id=? AND take_date=?", (90.0, trader_id, auction_id, well_id, period_label),)

	quota = foreverFairData_instance.get_well_start_quota(well_id=well_id, auction_id=auction_id)
	assert quota[period_key] == 90.0

def test_open_auction_has_default_bids_before_run(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	assert foreverFairData_instance.has_default_bids(auction_id)
	runCurrentAuction(foreverFairData_instance, auction_id)
	latest = foreverFairData_instance.get_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"
	assert foreverFairData_instance.has_default_bids(auction_id)

def test_new_auction_copies_previous_well_bids_before_generating_defaults(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_1_id = _seed_auction_id(foreverFairData_instance)
	manual_well_id, fallback_well_id = sorted(foreverFairData_instance.get_wells())[:2]
	manual_bid_steps = [(11.0, 21.0), (7.0, 15.0), (3.0, 9.0)]
	for period_id in sorted(foreverFairData_instance.get_well_start_quota(manual_well_id, auction_1_id)):
		foreverFairData_instance.add_bid(auction_id=auction_1_id, well_id=manual_well_id, period_id=period_id, quantity=manual_bid_steps[0][0], price=manual_bid_steps[0][1], bid_steps=manual_bid_steps)
	with foreverFairData_instance.connect_to_db() as conn:
		conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=? AND well_id=?", (auction_1_id, fallback_well_id))
	auction_2_id = create_auction(foreverFairData_instance.db_path)
	auction_2_dates = [period["label"] for period in foreverFairData_instance.get_auction_info(auction_2_id)["periods"]]
	with foreverFairData_instance.connect_to_db() as conn:
		source_rows = conn.execute(
			"SELECT pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM well_bids WHERE auction_id=? AND well_id=? AND deleted=0 AND pumping_date IN ({}) ORDER BY pumping_date".format(", ".join("?" for _ in auction_2_dates)),
			(auction_1_id, manual_well_id, *auction_2_dates),
		).fetchall()
		target_rows = conn.execute(
			"SELECT pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default FROM well_bids WHERE auction_id=? AND well_id=? AND deleted=0 ORDER BY pumping_date",
			(auction_2_id, manual_well_id),
		).fetchall()
		fallback_source_count = conn.execute("SELECT COUNT(*) AS n FROM well_bids WHERE auction_id=? AND well_id=? AND deleted=0", (auction_1_id, fallback_well_id)).fetchone()["n"]
		fallback_target_rows = conn.execute(
			"SELECT pumping_date, is_bid_default FROM well_bids WHERE auction_id=? AND well_id=? AND deleted=0 ORDER BY pumping_date",
			(auction_2_id, fallback_well_id),
		).fetchall()
	assert [tuple(row[:-1]) for row in target_rows] == [tuple(row) for row in source_rows]
	assert all(row["is_bid_default"] == 1 for row in target_rows)
	assert fallback_source_count == 0
	assert len(fallback_target_rows) == len(auction_2_dates)
	assert all(row["is_bid_default"] == 1 for row in fallback_target_rows)

def test_set_license_demand_populates_aquifer_limits(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	changes = foreverFairData_instance.set_license_demand_on_aquifer()
	assert changes > 0
	with foreverFairData_instance.connect_to_db() as conn:
		row = conn.execute("SELECT COUNT(*) AS total_rows, SUM(CASE WHEN license_demand IS NOT NULL THEN 1 ELSE 0 END) AS populated_rows, MAX(license_demand) AS max_license_demand FROM aquifer_head_limits").fetchone()
		assert row is not None
		assert row["total_rows"] > 0
		assert row["populated_rows"] == row["total_rows"]
		assert row["max_license_demand"] > 0.0


