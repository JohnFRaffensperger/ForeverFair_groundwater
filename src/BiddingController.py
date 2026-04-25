# BiddingController.py. Claude guided by JFR, 2026 04 21.
# Purpose: Validate, submit, and manage participant bids.

from __future__ import annotations
from models import BidSegment, AuctionCase
from services.repository import AuctionRepository

def _validate_bid_submission(auction_case: AuctionCase, participant_id: str, well_id: str, period_id: str, quantity: float, price: float) -> None:
	if auction_case.auction.status != "OPEN": raise ValueError("Auction is not open for bids.")
	participant = next((item for item in auction_case.participants if item.id == participant_id), None)
	if participant is None: raise ValueError("Unknown participant.")
	well = next((w for w in auction_case.wells if w.id == well_id and w.participant_id == participant_id), None)
	if well is None: raise ValueError("The selected well does not belong to the participant.")
	if not any(period.id == period_id for period in auction_case.auction.periods): raise ValueError("Unknown auction period.")
	if quantity <= 0: raise ValueError("Bid quantity must be positive.")
	if price <= 0: raise ValueError("Bid price must be positive.")

def submitBid(repository: AuctionRepository, auction_id: str, participant_id: str, well_id: str, period_id: str, quantity: float, price: float) -> BidSegment:
	auction_case = repository.load(auction_id)
	_validate_bid_submission(auction_case, participant_id, well_id, period_id, quantity, price)
	return repository.add_bid(auction_id=auction_case.auction.id, participant_id=participant_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price)

def deleteBid(repository: AuctionRepository, bid_id: str, participant_id: str) -> bool: return repository.delete_bid(bid_id=bid_id, current_participant_id=participant_id)
