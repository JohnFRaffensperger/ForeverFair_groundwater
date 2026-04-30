# tests/test_market.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Verify market clearing, setup/reset flow, and persisted run output.

from datetime import datetime, timedelta
from pathlib import Path
from AuctionController import ResetAuctionData, SetUpAuction, runCurrentAuction
from RunAuctionModule import runAuction
from services.repository import AuctionRepository
import setup as db_setup

def _make_repo(tmp_path: Path) -> AuctionRepository:
	project_root = Path(__file__).resolve().parents[1]
	seed_path = project_root / "data" / "Tianqiao" / "forever_fair_seed.json"
	db_path = tmp_path / "groundwater_market.db"
	db_setup.create_empty_db(db_path)
	repository = AuctionRepository(seed_path=seed_path, db_path=db_path)
	repository.reset_runtime_to_seed()
	return repository

def _seed_auction_id(repository: AuctionRepository) -> str: return repository.load().auction.id

def test_market_clears_seed_case(tmp_path):
	repository = _make_repo(tmp_path)
	auction_case = repository.load()
	result = runAuction(auction_case)
	assert result.solve_status == "Optimal"
	assert result.objective_value > 0

def test_setup_and_reset_auction_data(tmp_path):
	repository = _make_repo(tmp_path)
	close_dt = (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")
	first_take = (datetime.now() + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
	last_take = (datetime.fromisoformat(first_take) + timedelta(days=28)).isoformat(timespec="minutes")
	updated_auction = SetUpAuction(repository, auction_id=None, closed_date=close_dt, first_water_take_date=first_take, last_water_take_date=last_take, period_length_hours=168,)
	assert updated_auction.id is not None
	assert len(repository.list_auctions()) == 2

	reset_case = ResetAuctionData(repository)
	assert reset_case.auction.id is not None
	assert len(repository.list_auctions()) == 1

def test_manager_run_persists_results(tmp_path):
	repository = _make_repo(tmp_path)
	auction_id = _seed_auction_id(repository)

	market_result = runCurrentAuction(repository, auction_id)
	assert market_result.solve_status == "Optimal"
	latest = repository.latest_run_summary(auction_id)
	assert latest is not None
	assert latest["solve_status"] == "Optimal"

def test_multistep_bid_creates_multiple_lp_variables(tmp_path):
	repository = _make_repo(tmp_path)
	auction_id = _seed_auction_id(repository)
	auction_case = repository.load(auction_id)
	period_key = auction_case.auction.periods[0].id
	participant_id = next(w.participant_id for w in auction_case.wells if w.participant_id)
	well_id = next(w.id for w in auction_case.wells if w.participant_id == participant_id)

	first_step = repository.add_bid(auction_id=auction_id, participant_id=participant_id, well_id=well_id, period_id=period_key, quantity=6.0, price=20.0, bid_steps=[(6.0, 20.0), (4.0, 14.0), (2.0, 9.0)],)
	base_id = first_step.id[:-3]  # strip trailing "-s1"
	expected_ids = {f"{base_id}-s1", f"{base_id}-s2", f"{base_id}-s3"}

	auction_case = repository.load(auction_id)
	loaded_ids = {bid.id for bid in auction_case.bids}
	assert expected_ids.issubset(loaded_ids)

	result = runAuction(auction_case)
	accepted_ids = {item.bid_id for item in result.accepted_bids}
	assert expected_ids.issubset(accepted_ids)

def test_get_quota_uses_licenses_and_final_quota(tmp_path):
	repository = _make_repo(tmp_path)
	auction_id = _seed_auction_id(repository)
	auction_case = repository.load(auction_id)
	period_key = auction_case.auction.periods[0].id
	participant_id = next(w.participant_id for w in auction_case.wells if w.participant_id)
	well_id = next(w.id for w in auction_case.wells if w.participant_id == participant_id)
	with repository._connect() as conn:
		conn.execute("INSERT INTO trader_license(trader_id, well_id, license_quantity, license_date, bid_period) VALUES (?, ?, ?, ?, ?)", (int(participant_id), int(well_id), 90.0, None, 1),)
		conn.execute("INSERT INTO trader_quota(trader_id, auction_id, well_id, quota_auction_start, quota_adjusted, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (int(participant_id), auction_id, int(well_id), 90.0, 12.0, 12.0, 10.0, period_key),)

	quota = repository.get_quota(participant_id=participant_id, period_start=period_key, auction_id=auction_id)
	assert quota[period_key] == [90.0, 12.0]

def test_manager_run_rejects_auction_with_no_bids(tmp_path):
	repository = _make_repo(tmp_path)
	auction_id = _seed_auction_id(repository)
	with repository._connect() as conn:
		conn.execute("UPDATE trader_bids SET deleted=1 WHERE auction_id=?", (auction_id,))

	try:
		runCurrentAuction(repository, auction_id)
		assert False, "Expected runCurrentAuction to reject no-bid auction"
	except ValueError as exc:
		assert str(exc) == "The auction cannot run because it has no bids."

