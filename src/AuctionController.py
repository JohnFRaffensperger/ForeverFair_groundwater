# AuctionController.py. Claude guided by JFR, 2026 04 21.
# Purpose: Coordinate setup, execution, and view state for auctions.

from __future__ import annotations
from datetime import datetime, timezone
from RunAuctionModule import runAuction
from services.repository import AuctionRepository

def SetUpAuction( repository: AuctionRepository, auction_id: str | None, closed_date: str, first_water_take_date: str, last_water_take_date: str, period_length_hours: int, clear_existing_bids: bool = True, auction_type: str | None = None, ):
	# A.5: forbid editing a closed auction.
	if auction_id:
		existing = next((a for a in repository.list_auctions() if str(a.get("auction_id")) == str(auction_id)), None)
		if existing is not None:
			now_iso = datetime.now().isoformat(timespec="minutes")
			bid_close = existing.get("closed_date") or ""
			if existing.get("status") == "CLOSED" or (bid_close and bid_close < now_iso): raise ValueError("Cannot edit a closed auction.")
	try:
		close_dt = datetime.fromisoformat(closed_date)
		if close_dt < datetime.now(): raise ValueError("Auction closing date/time must be in the future.")
	except ValueError as e:
		if "must be in the future" in str(e) or "Cannot edit" in str(e): raise  # re-raise our own validation errors only
		# unparseable closed_date (freeform string) — allow through
	if datetime.fromisoformat(last_water_take_date) < datetime.fromisoformat(first_water_take_date): raise ValueError("lastWaterTakeDate must be on or after firstWaterTakeDate")
	if int(period_length_hours) <= 0: raise ValueError("period_length_hours must be positive")
	return repository.setup_auction(auction_id=auction_id, closed_date=closed_date, first_water_take_date=first_water_take_date, last_water_take_date=last_water_take_date, period_length_hours=int(period_length_hours), clear_existing_bids=clear_existing_bids, auction_type=auction_type,)

def ResetAuctionData(repository: AuctionRepository): return repository.reset_runtime_to_seed()

def runCurrentAuction(repository: AuctionRepository, auction_id: str):
	if not repository.has_active_bids(auction_id): raise ValueError("The auction cannot run because it has no bids.")
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
	
	period_rows = []
	for period in auction_case.auction.periods:
		period_key = str(period.id)
		quota = repository.get_quota(participant_id=current_participant.id, period_start=period_key, auction_id=auction_case.auction.id,).get(period_key, [0.0, 0.0])
		period_rows.append({"period_id": period.id, "period_key": period_key, "period_label": period.label, "allocation": quota[1],})
	return {"auction_case": auction_case, "current_participant": current_participant, "current_well": current_well, "bid_history": bid_history, "period_rows": period_rows, "auction_id": auction_case.auction.id,}

def getManagerPageState(repository: AuctionRepository) -> dict:
	period_length_hours = repository.latest_period_length_hours()
	response_period_count = repository.response_matrix_period_count()
	return {"auctions": repository.list_auctions(), "period_length_hours": period_length_hours, "response_period_count": response_period_count,}

def getCatchmentPageState(repository: AuctionRepository, auction_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	well_price_rows, control_point_rows = repository.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

def getSystemState(repository: AuctionRepository, auction_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	latest_run = repository.latest_run_summary(auction_case.auction.id)
	well_price_rows, control_point_rows = repository.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "rights_conversion": auction_case.rights_conversion.model_dump(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}
