# tests/test_market.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Verify market clearing, setup/reset flow, and persisted run output.

from pathlib import Path
from AuctionController import SetDebugAuctionData, runCurrentAuction
from services.ForeverFairData import ForeverFairData

def _make_repo(tmp_path: Path) -> ForeverFairData:
	project_root = Path(__file__).resolve().parents[1]
	debug_db = project_root / "Data_for_debugging" / "small_debug_database.db"
	return ForeverFairData(db_path=tmp_path / "small_debug_database_temp.db", debug_db_path=debug_db)

# Todo: Load auction from individual ForeverFairData accessors rather than one big dump.
def _seed_auction_id(foreverFairData_instance: ForeverFairData) -> int: 
	# return foreverFairData_instance.load().auction.id
	return None

def test_market_clears_seed_case(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	result = runCurrentAuction(foreverFairData_instance, auction_id)
	assert result.solve_status == "Optimal"
	assert result.objective_value > 0

def test_setup_and_reset_auction_data(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	updated_auction = foreverFairData_instance.add_auction()
	assert updated_auction["auction_id"] is not None
	assert len(foreverFairData_instance.list_auctions()) == 2

	reset_case = SetDebugAuctionData(foreverFairData_instance)
	assert reset_case.auction.id is not None
	assert len(foreverFairData_instance.list_auctions()) == 1

def test_manager_run_persists_results(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)

	market_result = runCurrentAuction(foreverFairData_instance, auction_id)
	assert market_result.solve_status == "Optimal"
	latest = foreverFairData_instance.latest_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"

def test_multistep_bid_creates_multiple_lp_variables(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	# auction_case = foreverFairData_instance.load(auction_id) # Get rid of .load. Use separate data accessor functions instead.
	period_key = auction_case.auction.periods[0].id
	trader_id = next(w.trader_id for w in auction_case.wells if w.trader_id)
	well_id = next(w.id for w in auction_case.wells if w.trader_id == trader_id)

	first_step = foreverFairData_instance.add_bid(auction_id=auction_id, trader_id=trader_id, well_id=well_id, period_id=period_key, quantity=6.0, price=20.0, bid_steps=[(6.0, 20.0), (4.0, 14.0), (2.0, 9.0)],)
	base_id = first_step.id[:-3]  # strip trailing "-s1"
	expected_ids = {f"{base_id}-s1", f"{base_id}-s2", f"{base_id}-s3"}

	auction_case = foreverFairData_instance.load(auction_id)
	loaded_ids = {bid.id for bid in auction_case.bids}
	assert expected_ids.issubset(loaded_ids)

	result = runCurrentAuction(foreverFairData_instance, auction_id)
	accepted_ids = {item.bid_id for item in result.accepted_bids}
	assert expected_ids.issubset(accepted_ids)

def test_get_quota_uses_licenses_and_final_quota(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	# auction_case = foreverFairData_instance.load(auction_id) # Get rid of .load. Use separate data accessor functions instead.
	period_key = auction_case.auction.periods[0].id
	trader_id = next(w.trader_id for w in auction_case.wells if w.trader_id)
	well_id = next(w.id for w in auction_case.wells if w.trader_id == trader_id)
	with foreverFairData_instance.connect_to_db() as conn:
		period_label = next(p.label for p in auction_case.auction.periods if p.id == period_key)
		conn.execute("INSERT INTO well_license(trader_id, well_id, license_quantity, license_date, bid_period) VALUES (?, ?, ?, ?, ?)", (trader_id, well_id, 90.0, None, 1),)
		conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, quota_adjusted, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (trader_id, 0, well_id, 90.0, 12.0, 12.0, 10.0, period_label),)

	quota = foreverFairData_instance.get_quota_auction_start(trader_id=trader_id, auction_id=0)
	assert quota[period_key] == 90.0

def test_manager_run_rejects_auction_with_no_bids(tmp_path):
	foreverFairData_instance = _make_repo(tmp_path)
	auction_id = _seed_auction_id(foreverFairData_instance)
	with foreverFairData_instance.connect_to_db() as conn:
		conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=?", (auction_id,))

	try:
		runCurrentAuction(foreverFairData_instance, auction_id)
		assert False, "Expected runCurrentAuction to reject no-bid auction"
	except ValueError as exc:
		assert str(exc) == "The auction cannot run because it has no bids."


