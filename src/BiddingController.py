# BiddingController.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Validate, submit, and manage participant bids.

from __future__ import annotations
from datetime import datetime
from models import BidSegment, AuctionCase
from services.repository import AuctionRepository

MAX_BID_STEPS = 5

def _validate_bid_submission(auction_case: AuctionCase, participant_id: str, well_id: str, period_id: str, quantity: float, price: float,) -> None:
	period_id = str(period_id).strip()
	if auction_case.auction.status != "OPEN": raise ValueError("Auction is not open for bids.")
	# F.3/C.11: Reject bids after the closing datetime even if status is still OPEN.
	try:
		close_text = str(auction_case.auction.closed_date or "").strip()
		if close_text:
			close_dt = datetime.fromisoformat(close_text)
			if close_dt < datetime.now(): raise ValueError("Auction bidding period has closed.")
	except ValueError as e:
		if "bidding period has closed" in str(e):
			raise  # re-raise our own validation error only
		# unparseable label (freeform string) — allow through
	participant = next((item for item in auction_case.participants if item.id == participant_id), None)
	if participant is None: raise ValueError("Unknown participant.")
	# F.5: Trader must own at least one well.
	participant_wells = [w for w in auction_case.wells if w.participant_id == participant_id]
	if not participant_wells: raise ValueError("No well is registered for your account.")
	well = next((w for w in participant_wells if w.id == well_id), None)
	if well is None: raise ValueError("The selected well does not belong to the participant.")
	# F.4/C.9: Period must exist in this auction.
	if not any(str(period.id) == period_id for period in auction_case.auction.periods): raise ValueError("Unknown auction period.")
	if quantity <= 0: raise ValueError("Bid quantity must be positive.")
	if price <= 0: raise ValueError("Bid price must be positive.")

def _validate_bid_steps(bid_steps: list[tuple[float, float]]) -> None:
	if not bid_steps: raise ValueError("At least one bid step is required.")
	if len(bid_steps) > MAX_BID_STEPS: raise ValueError(f"At most {MAX_BID_STEPS} bid steps are supported.")
	for quantity, price in bid_steps:
		if quantity <= 0: raise ValueError("Bid quantity must be positive.")
		if price <= 0: raise ValueError("Bid price must be positive.")

def submitBid(repository: AuctionRepository, auction_id: str, participant_id: str, well_id: str, period_id: str, quantity: float, price: float, is_automatic: bool = False, bid_steps: list[tuple[float, float]] | None = None,) -> BidSegment:
	auction_case = repository.load(auction_id)
	_validate_bid_submission(auction_case, participant_id, well_id, period_id, quantity, price)
	steps = bid_steps if bid_steps is not None else [(quantity, price)]
	_validate_bid_steps(steps)
	return repository.add_bid(
		auction_id=auction_case.auction.id,
		participant_id=participant_id,
		well_id=well_id,
		period_id=period_id,
		quantity=steps[0][0],
		price=steps[0][1],
		is_automatic=is_automatic,
		bid_steps=steps,
	)

def deleteBid(repository: AuctionRepository, bid_id: str, participant_id: str) -> bool: return repository.delete_bid(bid_id=bid_id, current_participant_id=participant_id)
