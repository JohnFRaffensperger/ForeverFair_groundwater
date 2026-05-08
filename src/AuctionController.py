# AuctionController.py. Claude guided by JFR, 2026 05 04.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Coordinate setup, execution, and view state for auctions.

from __future__ import annotations
from collections import defaultdict
from typing import Any, Callable
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
from ForeverFairClasses import ResponseFactor, ControlPoint
import BiddingController
from services.ForeverFairData import ForeverFairData

def call_for_bids(foreverFairData_instance: ForeverFairData, auction_id: int) -> None:
	if foreverFairData_instance.has_automatic_bids(auction_id): return
	quota_by_well_period = foreverFairData_instance.get_all_well_quota_by_period(auction_id)
	for well_id in sorted({well_id for (well_id, _period_id) in quota_by_well_period.keys()}):
		BiddingController.create_default_bid(foreverFairData_instance, auction_id, well_id)

def runCurrentAuction (foreverFairData_instance: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> None:
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log(f"runCurrentAuction started: auction_id={auction_id}")
	auction = foreverFairData_instance.get_auction_info (auction_id) # Should always be exactly one auction scheduled.
	
	call_for_bids(foreverFairData_instance, auction_id)
	foreverFairData_instance.ensure_control_point_event_rows_for_auction(auction_id)
	log("Default bids loaded")
	
	auction_model = LpProblem ("Forever_Fair_clearing_model", LpMaximize)

	# Define bid variables with their upper bounds. Bid decision variables exist only for bid periods, but are constrained by allowed drawdown in later periods.
	period_labels = [p.label for p in auction.periods]
	period_id_map = {period_label: idx + 1 for idx, period_label in enumerate(period_labels)}
	bid_variables: dict[tuple[int, int, int], LpVariable] = {}
	objective_terms: list[Any] = []
	for row in foreverFairData_instance.get_active_bid_segments (auction_id):
		period_id = period_id_map.get(str(row.get("effect_date") or ""), 0)
		if period_id == 0: continue
		for step_num in range(1, 6):
			if (qty := row.get(f"qty{step_num}")) is None or (price := row.get(f"price{step_num}")) is None: continue
			key = (int(row["well_id"]), period_id, step_num)
			bid_variables[key] = LpVariable (f"bidwtb_{key[0]}_{key[1]}_{key[2]}", lowBound=0, upBound=qty)
			objective_terms.append(float(price) * bid_variables[key])
	log(f"Active bid segments: {len(bid_variables)}")
		
	bid_periods: set[tuple[int, int]] = {(well_id, period_id) for (well_id, period_id, _step_num) in bid_variables.keys()}

	print("Bid length ", len(bid_variables))
	log(f"Bid periods: {len(bid_periods)}")
	
	# Define quantity variables: aggregate pumping per (well_id, bid_period).
	quantity_vars: dict[tuple[int, int], LpVariable] = {(w, t): LpVariable (f"qty_{w}_{t}", lowBound=None) for w, t in bid_periods}
	
	# Objective function. All bid variables positive, so this is a gross pool model.
	auction_model += lpSum (objective_terms)
		
	# Quantity to take is the sum of the bids: q[w,p] = sum (bids[w,p,step]).
	# The dual variable on each constraint is the clearing price for that well_id and bid period.
	for (w, t) in quantity_vars.keys ():
		auction_model += quantity_vars[(w, t)] == lpSum (bid_variables[key] for key in bid_variables.keys() if key[0] == w and key[1] == t), f"qtywt_{w}_{t}"

	# Get the response matrix.
	response_lookup: defaultdict[tuple[int, int], list[ResponseFactor]] = defaultdict (list)
	for factor in foreverFairData_instance.get_all_response_factors (): response_lookup [(factor.control_point_id, factor.effect_period)].append (factor)

	# Constrain all effect periods in the response matrix. 
	# CAREFUL WITH SIGNS. Response matrix coefficients are typically negative. Allowable head change is typically negative. qvars are positive.
	# Want q*F <= headchange, like 100*(-0.03) <= -5, which is false. So we need to change signs to have a "<=" constraints.
	control_points: list[ControlPoint] = foreverFairData_instance.get_control_points_for_auction (auction_id)
	for control_point in control_points:
		for effect_period_id in control_point.bound_by_period.keys ():
			auction_model += (lpSum (-1.0*factor.value * quantity_vars [(factor.well_id, factor.pumping_period)]
					for factor in response_lookup.get ((control_point.id, effect_period_id), []) if (factor.well_id, factor.pumping_period) in quantity_vars)
						<= -1.0*control_point.bound_by_period[effect_period_id], f"cp_{control_point.id}_{effect_period_id}")

	auction_model.writeLP (f"Forever_Fair_auction_{auction.id}.lpt")
	solve_status = LpStatus[auction_model.solve (PULP_CBC_CMD (msg=0))]
	log(f"Solve status: {solve_status}")
	
	# Save the resulting quota and prices.
	for (well_id, period_id) in bid_periods:
		foreverFairData_instance.set_quota_auction_end (auction_id, well_id, period_labels[period_id - 1], 
			quantity_vars [(well_id, period_id)].value (), 
			auction_model.constraints [f"qtywt_{well_id}_{period_id}"].pi)

	# Save control point slack and dual prices.
	for control_point in control_points:
		for effect_period_id in control_point.bound_by_period.keys ():
			foreverFairData_instance.set_control_point_event_results (auction_id, control_point.id, period_labels[effect_period_id - 1], 
				auction_model.constraints[f"cp_{control_point.id}_{effect_period_id}"].slack, 
				auction_model.constraints[f"cp_{control_point.id}_{effect_period_id}"].pi)

	foreverFairData_instance.close_auction (auction_id, solve_status=solve_status, objective_value = float (value (auction_model.objective)), auction_close_time=foreverFairData_instance.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
	log("Auction results saved")
	return # market_result

def SetDebugAuctionData (foreverFairData_instance: ForeverFairData): return foreverFairData_instance.use_debug_database ()

# This works, but the SQL implementation in ForeverFairData.get_well_constraint_quota () is faster.
# Other differences: this works for all wells while the SQL implementation works for only one well.
def compute_alphas (foreverFairData_instance: ForeverFairData, auction_id: int) -> dict[tuple[int, str], float]:
	"""Compute scaling factors alpha for each (control_point, effect_date) to enforce bounds."""
	quota = foreverFairData_instance.get_quota_by_well_pumping_period (auction_id) # well quota at auction start.
	allowable_head_change, effect_date_to_idx = foreverFairData_instance.get_allowable_head_change_by_cp_effect_date (auction_id)

	alphas_by_constraint: dict[tuple[int, str], float] = {}
	well_constraint_quota: dict[tuple[int, int, str, int], float] = {}
	for (cp_id, effect_date), allowed_change in allowable_head_change.items ():
		effect_period = effect_date_to_idx[effect_date]
		factors = foreverFairData_instance.get_response_factors_for_cp_period (cp_id, effect_period)
		drawdown_with_all_quota = sum (rf.value * quota [(rf.well_id, rf.pumping_period)] for rf in factors)

		# Most, but not all, factors are < 0. A few are > 0. quota >= 0. So usually drawdown_with_all_quota < 0. 
		# Typically allowed_change < 0. So usally drawdown_with_all_quota/allowed_change > 0, but not always.
		# If it is negative, either the data has response factors > 0, or allowed_change > 0.
		# Case: factors > 0, allowed_change > 0, so allowed_change/drawdown_with_all_quota > 0. So pumping raises head and allowed change is positive? Not in Tianqiao.
		# Case: factors < 0, allowed_change > 0, so allowed_change/drawdown_with_all_quota < 0. So pumping lowers head but allowed change is positive? Not in Tianqiao.
		
		# Case: factors > 0, allowed_change < 0, so allowed_change/drawdown_with_all_quota < 0. So pumping raises head and allowed change is negative? Tianqiao has this. Set alpha = 1.
		if drawdown_with_all_quota > 0.0: alphas_by_constraint [(cp_id, effect_date)] = 1.0
		# Case: factors < 0, allowed_change < 0, so allowed_change/drawdown_with_all_quota > 0. So pumping lowers head and allowed change is negative. Most typical.
		else: alphas_by_constraint [(cp_id, effect_date)] = allowed_change/drawdown_with_all_quota

		# Not saved anywhere yet. Better to use the SQL.
		factors_for_denominator = [rf for rf in factors if rf.pumping_period <= effect_period and (rf.well_id, rf.pumping_period) in quota]
		total_head_change_from_all_quota = sum (quota [(rf.well_id, rf.pumping_period)] * rf.value for rf in factors_for_denominator) # This is the denominator.
		for rf in factors_for_denominator: # well_constraint_quota is the allowable head change at cp_id in effect_date allocated to well_id in pumping_period. Likely negative!
			well_constraint_quota [(rf.well_id, rf.pumping_period, effect_date, cp_id)] = quota [(rf.well_id, rf.pumping_period)] * rf.value * allowed_change / total_head_change_from_all_quota

	foreverFairData_instance.set_control_point_alphas (auction_id, alphas_by_constraint)
	return alphas_by_constraint
