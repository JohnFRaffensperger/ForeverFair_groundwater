# BiddingController.py. Claude guided by JFR, 2026 05 14.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Validate, submit, and manage trader bids.

from __future__ import annotations
from ForeverFairData import ForeverFairData

def create_default_bid(foreverFairData_instance: ForeverFairData, auction_id: int, well_id: int) -> None:
	quota_by_well_period = foreverFairData_instance.get_quota (auction_id) # sets a bid for each quota record.
	license_by_period = foreverFairData_instance.get_well_license_quantity(well_id)
	# Side effect: fewer bid steps results in a lower maximum bid price.
	price_steps = [0.01, 0.02, 0.04, 0.08, 0.16][:foreverFairData_instance.get_max_bid_steps()] # $/(cubic meter per week).
	created_bids: list[dict] = []
	for (quota_well_id, period_id), _quota_auction_start in quota_by_well_period.items():
		if quota_well_id != well_id: continue
		license_quantity = license_by_period[period_id]
		step_size = 2.0 * license_quantity / len(price_steps)
		# Bid step quantities are all the same.
		bid_steps = [(step_size, price_steps[step_num - 1]) for step_num in range(1, len(price_steps) + 1)]
		created_bids.append (foreverFairData_instance.add_bid (auction_id = auction_id, well_id=well_id, period_id=period_id, quantity=bid_steps[0][0], price=bid_steps[0][1], is_default=True, bid_steps=bid_steps))
	return None 

def submitBid(foreverFairData_instance: ForeverFairData, well_id: int, this_trader_id: int, auction_id: int, period_id: int,
			quantity: float, price: float, is_bid_default: bool = False, bid_steps: list[tuple[float, float]] | None = None,) -> dict[str, int | str | float | None]:
	foreverFairData_instance.set_quota_for_auction(auction_id)
	# auction_case = foreverFairData_instance.load(auction_id)
	# if auction_case.auction.status != "OPEN": raise ValueError("Auction is not open for bids.")
	# F.3/C.11: Reject bids after the closing datetime even if status is still OPEN.
	# try:
	# 	close_text = str(auction_case.auction.closed_date or "").strip()
	# 	if close_text:
	# 		close_dt = datetime.fromisoformat(close_text)
	# 		if close_dt < datetime.now(): raise ValueError("Auction bidding period has closed.")
	# except ValueError as e:
	# 	if "bidding period has closed" in str(e): raise
		# unparseable label (freeform string) — allow through
	# trader = next((item for item in auction_case.traders if item.id == this_trader_id), None)
	# if trader is None: raise ValueError("Unknown trader.")
	# F.5: Trader must own at least one well.
	# trader_wells = [w for w in auction_case.wells if w.trader_id == this_trader_id]
	# if not trader_wells: raise ValueError("No well is registered for your account.")
	# well = next((w for w in trader_wells if w.id == well_id), None)
	# if well is None: raise ValueError("The selected well does not belong to the trader.")
	# F.4/C.9: Period must exist in this auction.
	# if not any(period.id == period_id for period in auction_case.auction.periods): raise ValueError("Unknown auction period.")
	# if quantity <= 0: raise ValueError("Bid quantity must be positive.")
	# if price <= 0: raise ValueError("Bid price must be positive.")
	# steps = bid_steps if bid_steps is not None else [(quantity, price)]
	# if not steps: raise ValueError("At least one bid step is required.")
	# if len(steps) > MAX_BID_STEPS: raise ValueError(f"At most {MAX_BID_STEPS} bid steps are supported.")
	# if bid_steps is not None:  # validate step values only if explicitly provided
	# 	for step_qty, step_price in steps:
	# 		if step_qty <= 0: raise ValueError("Bid quantity must be positive.")
	# 		if step_price <= 0: raise ValueError("Bid price must be positive.")
	steps = bid_steps if bid_steps is not None else [(quantity, price)]
	return foreverFairData_instance.add_bid(auction_id=auction_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_default=is_bid_default, bid_steps=steps)

def deleteBid(foreverFairData_instance: ForeverFairData, bid_id: int, trader_id: int) -> bool: 
	return foreverFairData_instance.delete_bid(bid_id=bid_id, current_trader_id=trader_id)
