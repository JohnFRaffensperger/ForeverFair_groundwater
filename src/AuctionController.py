# AuctionController.py. Claude guided by JFR, 2026 05 14.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Call for bids, clear ("run") the auction with optimization, calculate rights and cash exchanges.

from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from typing import Callable
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
import BiddingController
from ForeverFairData import ForeverFairData

def create_auction(db_path: Path) -> int:
	"""Create and initialize an auction."""
	ffdata = ForeverFairData(db_path)
	auction_id = ffdata.add_auction()["auction_id"]
	ffdata.set_control_point_events_for_auction(auction_id)
	ffdata.set_quota_for_auction(auction_id)
	# Scale license to quota only on the first auction. TODO: Should also scale quota on reload of aquifer head limits, and for a new period in a rolling auction horizon.
	if 1 == auction_id: ffdata.calculate_and_set_constraint_alphas(auction_id)
	call_for_bids(ffdata, auction_id)
	return auction_id

def compute_revenue_on_constraint_quota (ffdata: ForeverFairData, auction_id: int) -> float:
	"""Compute revenue from constraint quota changes across all wells.

	Revenue = sum over all causal response factors (pumping_period <= effect_period) of:
	    dual_price(k,t) * F(w,u,t,k) * allowed_change(k,t) * (q_start(w,u) - q_end(w,u)) / denom_start(k,t)

	where denom_start(k,t) = sum_{v,r: r<=t} q_start(v,r) * F(v,r,t,k).
	"""
	# Build auction-scoped indices over the active timeline: first pumping -> last constrained.
	period_maps = ffdata.get_period_maps(auction_id)
	effect_date_to_idx = period_maps["effect_iso_to_idx"]
	dual_prices: dict[tuple[int, int], float] = {}
	allowed_changes: dict[tuple[int, int], float] = {}
	for row in ffdata.get_control_point_events(auction_id):
		effect_date = str(row["effect_date"])
		ep = effect_date_to_idx.get(effect_date, 0)
		if ep <= 0: continue
		cp_id = int(row["control_point_id"])
		dual_prices[(cp_id, ep)] = float(row["dual_price"])
		allowed_changes[(cp_id, ep)] = float(row["allowable_head_change"])

	# Quota start/end keys already use auction-scoped pumping period indices.
	quota_start: dict[tuple[int, int], float] = {}
	quota_end: dict[tuple[int, int], float] = {}
	for well_id in ffdata.get_wells():
		for pumping_period, q in ffdata.get_well_start_quota(well_id, auction_id).items():
			quota_start[(well_id, pumping_period)] = q
		for pumping_period, q in ffdata.get_well_end_quota(well_id, auction_id).items():
			quota_end[(well_id, pumping_period)] = q

	# denom_start(k,t) = sum_{v,r: r<=t} q_start(v,r) * F(v,r,t,k).
	all_factors = ffdata.get_all_response_factors()
	denom_start: dict[tuple[int, int], float] = defaultdict(float)
	for rf in all_factors:
		cp_id = int(rf["control_point_id"])
		effect_period = int(rf["effect_period"])
		pumping_period = int(rf["pumping_period"])
		if (cp_id, effect_period) not in allowed_changes: continue
		if pumping_period <= effect_period:
			denom_start[(cp_id, effect_period)] += quota_start.get((int(rf["well_id"]), pumping_period), 0.0) * float(rf["value"])

	revenue = 0.0
	for rf in all_factors:
		cp_id = int(rf["control_point_id"])
		effect_period = int(rf["effect_period"])
		pumping_period = int(rf["pumping_period"])
		if pumping_period > effect_period: continue
		key_cp = (cp_id, effect_period)
		key_well = (int(rf["well_id"]), pumping_period)
		if key_cp not in dual_prices or key_well not in quota_start or key_well not in quota_end: continue
		if denom_start[key_cp] == 0.0: continue
		# Use quota scaled by constraint alpha.
		revenue += (float(rf["value"])*dual_prices[key_cp]*(quota_start[key_well] - quota_end[key_well])
			*allowed_changes[key_cp] / denom_start[key_cp])
		# Use raw initial quota.
		# revenue += (rf.value*dual_prices[key_cp]*(quota_start[key_well] - quota_end[key_well]))	
	return revenue

# This works, but the SQL implementation in ForeverFairData.get_well_constraint_quota () is faster.
# Other differences: this works for all wells while the SQL implementation works for only one well.
def compute_alphas (ffdata: ForeverFairData, auction_id: int) -> dict[tuple[int, str], float]:
	"""Compute scaling factors alpha for each (control_point, effect_date) to enforce bounds."""
	quota = ffdata.get_quota_by_well_pumping_period (auction_id) # well quota at auction start.
	allowable_head_change, effect_date_to_idx = ffdata.get_allowable_head_change_by_cp_effect_date (auction_id)

	alphas_by_constraint: dict[tuple[int, str], float] = {}
	well_constraint_quota: dict[tuple[int, int, str, int], float] = {}
	for (cp_id, effect_date), allowed_change in allowable_head_change.items ():
		effect_period = effect_date_to_idx[effect_date]
		factors = ffdata.get_response_factors_for_cp_period (cp_id, effect_period)
		factors_for_denominator = [rf for rf in factors if rf.pumping_period <= effect_period and (rf.well_id, rf.pumping_period) in quota]
		drawdown_with_all_quota = sum (rf.value * quota [(rf.well_id, rf.pumping_period)] for rf in factors_for_denominator)

		# Most, but not all, factors are < 0. A few are > 0. quota >= 0. So usually drawdown_with_all_quota < 0. 
		# Typically allowed_change < 0. So usally drawdown_with_all_quota/allowed_change > 0, but not always.
		# If it is negative, either the data has response factors > 0, or allowed_change > 0.
		# Case: factors > 0, allowed_change > 0, so allowed_change/drawdown_with_all_quota > 0. So pumping raises head and allowed change is positive? Not in Tianqiao.
		# Case: factors < 0, allowed_change > 0, so allowed_change/drawdown_with_all_quota < 0. So pumping lowers head but allowed change is positive? Not in Tianqiao.
		
		# Case: factors > 0, allowed_change < 0, so allowed_change/drawdown_with_all_quota < 0. So pumping raises head and allowed change is negative? Tianqiao has this. Set alpha = 1.
		if drawdown_with_all_quota > 0.0: alphas_by_constraint [(cp_id, effect_date)] = 1.0
		# Case: factors < 0, allowed_change < 0, so allowed_change/drawdown_with_all_quota > 0. So pumping lowers head and allowed change is negative. Most typical.
		else: alphas_by_constraint [(cp_id, effect_date)] = allowed_change/drawdown_with_all_quota

		# From alphas, calculate well constraint quota. Not saved anywhere yet. Better to use the SQL.
		total_head_change_from_all_quota = sum (quota [(rf.well_id, rf.pumping_period)] * rf.value for rf in factors_for_denominator) # This is the denominator.
		for rf in factors_for_denominator: # well_constraint_quota is the allowable head change at cp_id in effect_date allocated to well_id in pumping_period. Likely negative!
			well_constraint_quota [(rf.well_id, rf.pumping_period, effect_date, cp_id)] = quota [(rf.well_id, rf.pumping_period)] * rf.value * allowed_change / total_head_change_from_all_quota

	ffdata.set_constraint_alphas (auction_id, alphas_by_constraint)
	return alphas_by_constraint

# Set up default bids. A trader's manual entry will overwrite these.
def call_for_bids(ffdata: ForeverFairData, auction_id: int) -> None:
	if ffdata.has_default_bids(auction_id): return
	quota_by_well_period = ffdata.get_quota(auction_id)
	for well_id in sorted({well_id for (well_id, _period_id) in quota_by_well_period.keys()}):
		BiddingController.create_default_bid(ffdata, auction_id, well_id)

# Solve the market-clearing optimization. The debug_log strings go to the AuctionManager.html message box.
def runCurrentAuction (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> None:
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log(f"runCurrentAuction started: auction_id={auction_id}") 
	auction = ffdata.get_auction_info (auction_id) # Should always be exactly one auction scheduled.

	# Use auction-scoped timeline maps: first pumping date -> last constrained date.
	period_maps = ffdata.get_period_maps(auction_id)
	cp_events = ffdata.get_control_point_events(auction_id)
	effect_date_to_idx = period_maps["effect_iso_to_idx"]
	idx_to_pumping_iso = period_maps["idx_to_pumping_iso"]

	auction_model = LpProblem ("Forever_Fair_clearing_model", LpMaximize)

	# Define bid variables with their upper bounds. Bid decision variables exist only for bid periods, but are constrained by allowed drawdown in later periods.
	period_labels = [p["label"] for p in auction["periods"]]
	period_id_map = {label: effect_date_to_idx[label] for label in period_labels if label in effect_date_to_idx}
	max_bid_steps = ffdata.get_max_bid_steps()
	bid_variables: dict[tuple[int, int, int], LpVariable] = {}
	bid_prices: dict[tuple[int, int, int], float] = {}
	for row in ffdata.get_bids (auction_id):
		period_id = period_id_map.get(str(row.get("effect_date") or ""), 0)
		if period_id == 0: continue
		for step_num in range(1, max_bid_steps + 1):
			# Need to check for None in case the trader doesn't put in all 5 steps?
			if (qty := row.get(f"qty{step_num}")) is None or (price := row.get(f"price{step_num}")) is None: continue
			key = (int(row["well_id"]), period_id, step_num)

			# Create an LP variable for each bid step. The upper bound is the bid step quantity.
			bid_variables[key] = LpVariable (f"bidwtb_{key[0]}_{key[1]}_{key[2]}", lowBound=0, upBound=qty)
			bid_prices[key] = float(price)
	log(f"Active bids: {len(bid_variables)}")
		
	bid_periods: set[tuple[int, int]] = {(well_id, period_id) for (well_id, period_id, _step_num) in bid_variables.keys()}

	# Quantity variables: aggregate pumping per (well_id, bid_period). Since bid_variables have lower bound of 0, 
	# quantity_vars don't need that lower bound. In fact, adding a lower bound would disrupt the dual prices.
	quantity_vars: dict[tuple[int, int], LpVariable] = {(w, t): LpVariable (f"qty_{w}_{t}", lowBound=None) for w, t in bid_periods}
	
	# Objective function. All bid variables positive, so this is a gross pool model.
	auction_model += lpSum (bid_prices[key] * bid_variables[key] for key in bid_variables.keys())
		
	# Quantity to take is the sum of the bids: q[w,p] = sum (bids[w,p,step]).
	# The dual variable on each constraint is the clearing price for that well_id and bid period.
	for (w, t) in quantity_vars.keys ():
		auction_model += quantity_vars[(w, t)] == lpSum (bid_variables[key] for key in bid_variables.keys() if key[0] == w and key[1] == t), f"qtywt_{w}_{t}"

	# Get the response matrix.
	response_lookup: defaultdict[tuple[int, int], list[dict[str, int | float]]] = defaultdict (list)
	for factor in ffdata.get_all_response_factors (): response_lookup [(factor["control_point_id"], factor["effect_period"])].append (factor)

	# Constrain all effect periods in the response matrix by iterating over all control_point_events. 
	# CAREFUL WITH SIGNS. Response matrix coefficients are typically negative. Allowable head change is typically negative. qvars are positive.
	# Want q*F <= headchange, like 100*(-0.03) <= -5, which is false. So we need to change signs (with -1.0* for emphasis) to have "<=" constraints.
	# (cp_events, effect_dates, effect_date_to_idx already built above)

	# Guard: every (cp_id, effect_period) from cp_events must have response factors, otherwise the constraint is empty.
	empty_constraints = [(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])]) for row in cp_events if not response_lookup[(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])])]]
	if empty_constraints: log(f"WARNING: cp_events with no response factors (constraint will be trivial): {empty_constraints}")
	assert not empty_constraints, f"cp_events with no response factors: {empty_constraints}"

	for row in cp_events:
		cp_id = int(row["control_point_id"])
		effect_period = effect_date_to_idx[str(row["effect_date"])]
		auction_model += (lpSum (-1.0*factor["value"] * quantity_vars [(factor["well_id"], factor["pumping_period"])]
				for factor in response_lookup.get ((cp_id, effect_period), []) if (factor["well_id"], factor["pumping_period"]) in quantity_vars)
					<= -1.0*float(row["allowable_head_change"]), f"cp_{cp_id}_{effect_period}")

	lpt_dir = Path (__file__).parent.parent / "Auction_lpt_files"
	lpt_dir.mkdir (exist_ok=True)
	auction_model.writeLP (str (lpt_dir / f"Forever_Fair_auction_{auction["id"]}.lpt"))
	solve_status = LpStatus[auction_model.solve (PULP_CBC_CMD (msg=0))]
	log(f"Solve status: {solve_status}")
	if solve_status != "Optimal":
		ffdata.close_auction (auction_id, solve_status=solve_status, objective_value=None, auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
		return

	# Save the resulting quota and prices.
	for (well_id, period_id) in bid_periods:
		ffdata.set_quota_auction_end (auction_id, well_id, idx_to_pumping_iso[period_id], 
			quantity_vars [(well_id, period_id)].value (), 
			auction_model.constraints [f"qtywt_{well_id}_{period_id}"].pi)

	# Save control point slack and dual prices.
	for row in cp_events:
		cp_id = int(row["control_point_id"])
		effect_date = str(row["effect_date"])
		effect_period = effect_date_to_idx[effect_date]
		constraint_name = f"cp_{cp_id}_{effect_period}"
		if constraint_name in auction_model.constraints:
			ffdata.set_control_point_event_results (auction_id, cp_id, effect_date, 
				auction_model.constraints[constraint_name].slack, 
				auction_model.constraints[constraint_name].pi)

	ffdata.close_auction (auction_id, solve_status=solve_status, objective_value = float (value (auction_model.objective) or 0.0), auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
	log("Auction results saved")
	return # market_result

def SetDebugAuctionData (ffdata: ForeverFairData): return ffdata.use_debug_database ()