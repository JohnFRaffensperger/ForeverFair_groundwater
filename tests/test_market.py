# tests/test_market.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Verify market clearing, setup/reset flow, and persisted run output.

from pathlib import Path
from AuctionController import runCurrentAuction
from services.ForeverFairData import ForeverFairData

def _make_repo(tmp_path: Path) -> ForeverFairData:
	project_root = Path(__file__).resolve().parents[1]
	debug_db = project_root / "Catchment_data" / "Tianqiao" / "foreverfair.db"
	return ForeverFairData(db_path=tmp_path / "small_debug_database_temp.db", debug_db_path=debug_db)

def _seed_auction_id(foreverFairData_instance: ForeverFairData) -> int:
	next_auction = foreverFairData_instance.get_next_auction_info()
	if next_auction is None: raise ValueError("No open auction available in debug database")
	return int(next_auction["auction_id"])

def _first_period_trader_well(foreverFairData_instance: ForeverFairData, auction_id: int) -> tuple[int, int, int]:
	auction = foreverFairData_instance.get_auction_info(auction_id)
	period_key = auction.periods[0].id
	for trader in foreverFairData_instance.list_of_traders():
		wells = foreverFairData_instance.get_trader_wells(trader["id"])
		if wells:
			well = wells[0]
			return period_key, int(trader["id"]), int(well.id)
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
	updated_auction = foreverFairData_instance.add_auction()
	assert updated_auction["auction_id"] is not None
	assert len(foreverFairData_instance.list_auctions()) == initial_count + 1
	assert foreverFairData_instance.get_max_bid_steps() == 3
	with foreverFairData_instance.connect_to_db() as conn:
		row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='MAX_BID_STEPS'").fetchone()
		assert row is not None
		assert row["meta_value"] == "3"

	reset_data = ForeverFairData(db_path=tmp_path / "small_debug_database_reset.db", debug_db_path=Path(__file__).resolve().parents[1] / "Catchment_data" / "Tianqiao" / "foreverfair.db")
	assert len(reset_data.list_auctions()) == initial_count

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
	base_id = first_step.id[:-3]  # strip trailing "-s1"
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
	new_auction = foreverFairData_instance.add_auction()
	auction_id = int(new_auction["auction_id"])
	foreverFairData_instance.set_quota_for_auction(auction_id, source_auction_id=seed_auction_id)
	auction = foreverFairData_instance.get_auction_info(auction_id)
	with foreverFairData_instance.connect_to_db() as conn:
		period_label = next(p.label for p in auction.periods if p.id == period_key)
		conn.execute("INSERT INTO well_license(trader_id, well_id, license_quantity, license_date, bid_period) VALUES (?, ?, ?, ?, ?)", (trader_id, well_id, 90.0, None, 1),)
		conn.execute("UPDATE well_quota SET quota_auction_start=? WHERE trader_id=? AND auction_id=? AND well_id=? AND take_date=?", (90.0, trader_id, auction_id, well_id, period_label),)

	quota = foreverFairData_instance.get_well_start_quota(well_id=well_id, auction_id=auction_id)
	assert quota[period_key] == 90.0

def test_manager_run_rejects_auction_with_no_bids(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	with foreverFairData_instance.connect_to_db() as conn:
		conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=?", (auction_id,))
	runCurrentAuction(foreverFairData_instance, auction_id)
	latest = foreverFairData_instance.get_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"
	assert foreverFairData_instance.has_default_bids(auction_id)


