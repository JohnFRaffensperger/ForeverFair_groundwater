# AuctionController.py. Claude guided by JFR, 2026 04 21.
# Purpose: Coordinate setup, execution, and view state for auctions.

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from RunAuctionModule import runAuction
from services.repository import AuctionRepository

def _compute_effect_date(auction_date: str | None, days_in_period: int | None, offset: int) -> str:
	"""Convert auction_date + offset * days_in_period to ISO date string.
	If auction_date or days_in_period missing, return offset as string (integer fallback)."""
	if not auction_date or not days_in_period:
		return str(offset)
	try:
		base = datetime.fromisoformat(auction_date).date()
		effect_date = base + timedelta(days=offset * days_in_period)
		return effect_date.isoformat()
	except Exception:
		return str(offset)

def SetUpAuction( repository: AuctionRepository, auction_id: str | None, label: str, bid_close_label: str, period_labels: list[str], clear_existing_bids: bool = True, auction_date: str | None = None, days_in_period: int | None = None, number_of_periods: int | None = None, auction_type: str | None = None, ):
	clean_labels = [label.strip() for label in period_labels if label.strip()]
	if len(clean_labels) == 0: raise ValueError("At least one period label is required.")
	return repository.setup_auction(auction_id=auction_id, label=label, bid_close_label=bid_close_label, period_labels=clean_labels, clear_existing_bids=clear_existing_bids, auction_date=auction_date, days_in_period=days_in_period, number_of_periods=number_of_periods, auction_type=auction_type,)

def ResetAuctionData(repository: AuctionRepository): return repository.reset_runtime_to_seed()

def runCurrentAuction(repository: AuctionRepository, auction_id: str):
	clearing_start_time = datetime.now(timezone.utc).isoformat()
	auction_case = repository.load(auction_id)
	market_result = runAuction(auction_case)
	repository.save_run_results(auction_id, market_result, clearing_start_time=clearing_start_time)
	return market_result

def getTraderPageState(repository: AuctionRepository, auction_id: str | None = None, participant_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	current_participant = next((p for p in auction_case.participants if p.id == (participant_id or auction_case.current_participant_id)), auction_case.participants[0])
	current_wells = repository.wells_for_participant(auction_case, current_participant)
	current_well = current_wells[0] if current_wells else None
	bid_history = repository.bid_history(auction_case.auction.id, current_participant.id)
	# Compute effect dates for periods if auction_date and days_in_period are set
	period_rows = []
	for idx, period in enumerate(auction_case.auction.periods):
		effect_date = _compute_effect_date(auction_case.auction.auction_date, auction_case.auction.days_in_period, idx)
		period_rows.append({
			"period_id": period.id,
			"period_key": effect_date,  # Use effect_date as key for date-based periods
			"period_label": period.label,
			"allocation": round(current_participant.allocation_by_period.get(period.id, 0.0), 3),
		})
	return {"auction_case": auction_case, "current_participant": current_participant, "current_well": current_well, "bid_history": bid_history, "period_rows": period_rows, "auction_id": auction_case.auction.id,}

def getManagerPageState(repository: AuctionRepository) -> dict: return {"auctions": repository.list_auctions()}

def getCatchmentPageState(repository: AuctionRepository, auction_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	well_price_rows, control_point_rows = repository.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

def getSystemState(repository: AuctionRepository, auction_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	latest_run = repository.latest_run_summary(auction_case.auction.id)
	well_price_rows, control_point_rows = repository.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "rights_conversion": auction_case.rights_conversion.model_dump(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}
