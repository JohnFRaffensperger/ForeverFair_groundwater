# tests/test_market.py. Claude guided by JFR, 2026 04 21.
# Purpose: Verify market clearing, setup/reset flow, and persisted run output.

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

def test_market_clears_seed_case(tmp_path):
	repository = _make_repo(tmp_path)
	auction_case = repository.load("auction-001")
	result = runAuction(auction_case)
	assert result.solve_status == "Optimal"
	assert result.objective_value > 0

def test_setup_and_reset_auction_data(tmp_path):
	repository = _make_repo(tmp_path)

	updated_auction = SetUpAuction(repository, auction_id="auction-002", label="Reset test auction", bid_close_label="Bids close Monday noon", period_labels=["Trial Week 1", "Trial Week 2"],)
	assert updated_auction.id == "auction-002"
	assert len(repository.list_auctions()) == 2

	reset_case = ResetAuctionData(repository)
	assert reset_case.auction.id == "auction-001"
	assert len(repository.list_auctions()) == 1

def test_manager_run_persists_results(tmp_path):
	repository = _make_repo(tmp_path)

	market_result = runCurrentAuction(repository, "auction-001")
	assert market_result.solve_status == "Optimal"
	latest = repository.latest_run_summary("auction-001")
	assert latest is not None
	assert latest["solve_status"] == "Optimal"

