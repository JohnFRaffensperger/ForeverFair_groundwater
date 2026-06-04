# BiddingController.py. Claude guided by JFR, 2026 05 31.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Validate, submit, and manage trader bids. This is the business logic from the trader's and well's point of view.

from __future__ import annotations
from ForeverFairData import ForeverFairData

# Precompute Users_pay bid quantities for all wells in one auction-level pass.
def get_users_pay_bid_quantities_by_well(foreverFairData_instance: ForeverFairData, auction_id: int) -> dict[int, dict[int, float]]:
	response_factors = foreverFairData_instance.get_response_factors_for_auction(auction_id)
	auction_calendar = foreverFairData_instance.get_auction_calendar(auction_id)
	effect_date_to_idx = auction_calendar["effect_iso_to_idx"]
	control_point_events = foreverFairData_instance.get_control_point_events(auction_id)

	cp_allowable: dict[tuple[int, int], float] = {(effect_date_to_idx[str(cpe["effect_date"])], int(cpe["control_point_id"])): float(cpe["allowable_head_change"] or 0.0)
		for cpe in control_point_events}

	# 1) For each (u,k), Fsum_u_k(u,k) = sum_{w,t} min(0, F(w,t,u,k)).
	Fsum_u_k: dict[tuple[int, int], float] = {key: 0.0 for key in cp_allowable.keys()}
	min_response_by_well_pumping_period: dict[tuple[int, int], float] = {}
	for (well_id, pumping_period, effect_period, control_point_id), response in response_factors.items():
		well_period = (well_id, pumping_period)
		if well_period not in min_response_by_well_pumping_period or response < min_response_by_well_pumping_period[well_period]:
			min_response_by_well_pumping_period[well_period] = response
		if response > 0.0: continue
		cp_key = (effect_period, control_point_id)
		if cp_key in Fsum_u_k: Fsum_u_k[cp_key] += response

	# 2) For each (u,k), alpha(u,k) = allowable_head_change(u,k) / Fsum_u_k(u,k), with alpha=0 if denom(u,k)=0.
	alpha_k_u = {cp_key: (0.0 if Fsum_u_k[cp_key] == 0.0 else cp_allowable[cp_key] / Fsum_u_k[cp_key]) for cp_key in cp_allowable.keys()}

	# 3) Gross Users_pay quantity by pumping period t for each well w.
	gross_weighted_by_well_period: dict[tuple[int, int], float] = {}
	for (well_id, pumping_period, effect_period, control_point_id), response in response_factors.items():
		if pumping_period > effect_period: continue
		gross_weighted_by_well_period.setdefault((well_id, pumping_period), 0.0)
		gross_weighted_by_well_period[(well_id, pumping_period)] += (response if response <= 0.0 else 0.0) * alpha_k_u.get((effect_period, control_point_id), 0.0)

	users_pay_by_well: dict[int, dict[int, float]] = {}
	for (well_id, pumping_period), gross_weighted in gross_weighted_by_well_period.items():
		users_pay_by_well.setdefault(well_id, {})[pumping_period] = gross_weighted * (0.04 / min_response_by_well_pumping_period[(well_id, pumping_period)])
	return users_pay_by_well

# For the Users_pay rights policy, we can create some plausible bid quantities following the quota scaling formula.
def get_users_pay_bid_quantities(foreverFairData_instance: ForeverFairData, auction_id: int, well_id: int) -> dict[int, float]:
	return get_users_pay_bid_quantities_by_well(foreverFairData_instance, auction_id).get(well_id, {})

# Called for the first auction.
def create_default_bid(foreverFairData_instance: ForeverFairData, auction_id: int, well_id: int,
		rights_policy: str | None = None, quota_by_well_period: dict[tuple[int, int], float] | None = None,
		users_pay_by_well: dict[int, dict[int, float]] | None = None) -> None:
	rights_policy = rights_policy if rights_policy is not None else foreverFairData_instance.get_rights_policy()
	quota_by_well_period = quota_by_well_period if quota_by_well_period is not None else foreverFairData_instance.get_quota(auction_id) # sets a bid for each quota record.

	if rights_policy == "Users_pay":
		users_pay_quantities = users_pay_by_well.get(well_id, {}) if users_pay_by_well is not None else get_users_pay_bid_quantities(foreverFairData_instance, auction_id, well_id)
		quantity_by_period = {period_id: users_pay_quantities.get(period_id, 0.0) for (quota_well_id, period_id), _quota_auction_start in quota_by_well_period.items() if quota_well_id == well_id}
	else: quantity_by_period = foreverFairData_instance.get_well_license_quantity(well_id)

	# Side effect: fewer bid steps results in a lower maximum bid price.
	# Also, zero license results in bids with zero bid quantities. So you would have to enter your own bids manually.
	price_steps = [0.01, 0.02, 0.04, 0.08, 0.16][:foreverFairData_instance.get_max_bid_steps()] # $/(cubic meter per week).
	for (quota_well_id, period_id), _quota_auction_start in quota_by_well_period.items():
		if quota_well_id != well_id: continue
		step_size = 2.0 * quantity_by_period[period_id] / len(price_steps)
		# Bid step quantities are all the same.
		bid_steps = [(step_size, price_steps[step_num - 1]) for step_num in range(1, len(price_steps) + 1)]
		foreverFairData_instance.add_bid (auction_id = auction_id, well_id=well_id, period_id=period_id, quantity=bid_steps[0][0], price=bid_steps[0][1], is_default=True, bid_steps=bid_steps)
	return None 

def submitBid(foreverFairData_instance: ForeverFairData, well_id: int, this_trader_id: int, auction_id: int, period_id: int,
			quantity: float, price: float, is_bid_default: bool = False, bid_steps: list[tuple[float, float]] | None = None,) -> dict[str, int | str | float | None]:
	foreverFairData_instance.set_quota_for_auction(auction_id)
	target_auction = next((auction for auction in foreverFairData_instance.list_auctions() if int(auction["auction_id"]) == auction_id), None)
	if target_auction is None: raise ValueError("Auction not found.")
	now_iso = foreverFairData_instance.the_time_at_the_tone_is().isoformat(timespec="minutes")
	bid_close = str(target_auction.get("closed_date") or "")
	is_closed = str(target_auction.get("status") or "").upper() == "CLOSED"
	if is_closed or (bid_close and bid_close < now_iso): raise ValueError("Bid submission is closed for this auction.")

	trader_wells = foreverFairData_instance.get_trader_wells(this_trader_id)
	if not trader_wells: raise ValueError("No well is registered for your account.")
	if not any(well["id"] == well_id for well in trader_wells): raise ValueError("The selected well does not belong to the trader.")

	auction_info = foreverFairData_instance.get_auction_info(auction_id)
	if not any(int(period["id"]) == period_id for period in auction_info["periods"]): raise ValueError("Unknown auction period.")

	steps = bid_steps if bid_steps is not None else [(quantity, price)]
	if not steps: raise ValueError("At least one bid step is required.")
	if quantity <= 0: raise ValueError("Bid quantity must be positive.")
	if price <= 0: raise ValueError("Bid price must be positive.")
	for step_qty, step_price in steps:
		if step_qty <= 0: raise ValueError("Bid quantity must be positive.")
		if step_price <= 0: raise ValueError("Bid price must be positive.")
	return foreverFairData_instance.add_bid(auction_id=auction_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_default=is_bid_default, bid_steps=steps)

def deleteBid(foreverFairData_instance: ForeverFairData, bid_id: int, trader_id: int) -> bool: 
	return foreverFairData_instance.delete_bid(bid_id=bid_id, current_trader_id=trader_id)
