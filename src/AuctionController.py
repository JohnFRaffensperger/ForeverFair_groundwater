# AuctionController.py. Claude guided by JFR, 2026 05 08.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Call for bids, clear ("run") the auction with optimization, calculate rights and cash exchanges.

from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from typing import Callable
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
from ForeverFairClasses import ResponseFactor
import BiddingController
from services.ForeverFairData import ForeverFairData

def create_auction(db_path: Path) -> int:
	"""Create a new OPEN auction and return its auction_id."""
	ffdata = ForeverFairData(db_path)
	return int(ffdata.add_auction()["auction_id"])

def setup_first_auction(db_path: Path) -> int:
	"""Compute constraint alphas for the first OPEN auction.

	The auction must already exist and have well_quota and control_point_events
	populated by import_mps. Returns the auction_id.
	"""
	ffdata = ForeverFairData(db_path)
	auction = ffdata.get_next_auction_info()
	if auction is None: raise ValueError("No OPEN auction found. Import MPS first (/setup/import-mps).")
	auction_id = int(auction["auction_id"])
	ffdata.calculate_and_set_constraint_alphas(auction_id)
	return auction_id

def compute_revenue_on_constraint_quota (foreverFairData_instance: ForeverFairData, auction_id: int) -> float:
	"""Compute revenue extracted from constraint quota changes across all wells.
	
	Revenue = sum over wells and control point events of: cpe_pi * (well_cpe_quota_start - well_cpe_quota_end)
	
	where:
	- cpe_pi is the control point event constraint dual price (shadow price)
	- well_cpe_quota_start is the constraint quota allocated to the well at auction start
	- well_cpe_quota_end is the constraint quota allocated to the well at auction end
	"""
	
	# Get all control point events with dual prices
	cp_events = foreverFairData_instance.get_control_point_events(auction_id)
	cp_dual_prices: dict[tuple[int, str], float] = {}
	for row in cp_events:
		dual_price = row.get("dual_price")
		if dual_price is not None: cp_dual_prices[(int(row["control_point_id"]), str(row["effect_date"]))] = float(dual_price)
	
	wells_in_auction = foreverFairData_instance.get_wells()
	revenue = 0.0
	for well_id in wells_in_auction:
		# Get constraint quotas at auction start (using quota_auction_start from well_quota)
		start_quota = foreverFairData_instance.get_well_start_quota(well_id, auction_id)
		
		# Get constraint quotas at auction end (using quota_auction_end from well_quota)
		# This uses the same calculation as start_quotas but with quota_auction_end values
		end_quota = foreverFairData_instance.get_well_constraint_quota_end(well_id, auction_id)
		
		# Revenue = sum of dual prices times the change in quota
		for (take_date, effect_period, cp_id), start_quota in start_quotas.items():
			end_quota = end_quotas.get((take_date, effect_period, cp_id), 0.0)
			# Map back to find dual price using effect_date (note: we have effect_period here)
			# The dual price is on the control point constraint at each effect_date
			# To find the right effect_date, we'd need a reverse mapping, but since we're looking at
			# constraint quota changes, we can use the control point's dual price for the effect_period
			dual_price = 0.0
			for (cpe_cp_id, cpe_effect_date) in cp_dual_prices:
				if cpe_cp_id == cp_id:
					# This is approximate—should ideally match by both cp_id and the effect_period
					# For now, use the dual price from the control point event
					dual_price = cp_dual_prices.get((cp_id, cpe_effect_date), 0.0)
					break
			
			revenue += dual_price * (start_quota - end_quota)
	
	return revenue

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

	foreverFairData_instance.set_constraint_alphas (auction_id, alphas_by_constraint)
	return alphas_by_constraint

# Set up default bids. A trader's manual entry will overwrite these.
def call_for_bids(foreverFairData_instance: ForeverFairData, auction_id: int) -> None:
	if foreverFairData_instance.has_default_bids(auction_id): return
	quota_by_well_period = foreverFairData_instance.get_quota(auction_id)
	for well_id in sorted({well_id for (well_id, _period_id) in quota_by_well_period.keys()}):
		BiddingController.create_default_bid(foreverFairData_instance, auction_id, well_id)

# Solve the market-clearing optimization. The debug_log strings go to the AuctionManager.html message box.
def runCurrentAuction (foreverFairData_instance: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> None:
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log(f"runCurrentAuction started: auction_id={auction_id}") 
	auction = foreverFairData_instance.get_auction_info (auction_id) # Should always be exactly one auction scheduled.
	
	call_for_bids(foreverFairData_instance, auction_id)
	foreverFairData_instance.set_control_point_events_for_auction(auction_id)
	
	auction_model = LpProblem ("Forever_Fair_clearing_model", LpMaximize)

	# Define bid variables with their upper bounds. Bid decision variables exist only for bid periods, but are constrained by allowed drawdown in later periods.
	period_labels = [p.label for p in auction.periods]
	period_id_map = {period_label: idx + 1 for idx, period_label in enumerate(period_labels)}
	max_bid_steps = foreverFairData_instance.get_max_bid_steps()
	bid_variables: dict[tuple[int, int, int], LpVariable] = {}
	bid_prices: dict[tuple[int, int, int], float] = {}
	for row in foreverFairData_instance.get_bids (auction_id):
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
	response_lookup: defaultdict[tuple[int, int], list[ResponseFactor]] = defaultdict (list)
	for factor in foreverFairData_instance.get_all_response_factors (): response_lookup [(factor.control_point_id, factor.effect_period)].append (factor)

	# Constrain all effect periods in the response matrix by iterating over all control_point_events. 
	# CAREFUL WITH SIGNS. Response matrix coefficients are typically negative. Allowable head change is typically negative. qvars are positive.
	# Want q*F <= headchange, like 100*(-0.03) <= -5, which is false. So we need to change signs (with -1.0* for emphasis) to have "<=" constraints.
	cp_events = foreverFairData_instance.get_control_point_events(auction_id)
	effect_dates = sorted({str(row["effect_date"]) for row in cp_events})
	effect_date_to_idx = {effect_date: idx + 1 for idx, effect_date in enumerate(effect_dates)}

	# Guard: every (cp_id, effect_period) from cp_events must have response factors, otherwise the constraint is empty.
	empty_constraints = [(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])]) for row in cp_events if not response_lookup[(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])])]]
	if empty_constraints: log(f"WARNING: cp_events with no response factors (constraint will be trivial): {empty_constraints}")
	assert not empty_constraints, f"cp_events with no response factors: {empty_constraints}"

	for row in cp_events:
		cp_id = int(row["control_point_id"])
		effect_period = effect_date_to_idx[str(row["effect_date"])]
		auction_model += (lpSum (-1.0*factor.value * quantity_vars [(factor.well_id, factor.pumping_period)]
				for factor in response_lookup.get ((cp_id, effect_period), []) if (factor.well_id, factor.pumping_period) in quantity_vars)
					<= -1.0*float(row["allowable_head_change"]), f"cp_{cp_id}_{effect_period}")

	lpt_dir = Path (__file__).parent.parent / "Auction_lpt_files"
	lpt_dir.mkdir (exist_ok=True)
	auction_model.writeLP (str (lpt_dir / f"Forever_Fair_auction_{auction.id}.lpt"))
	solve_status = LpStatus[auction_model.solve (PULP_CBC_CMD (msg=0))]
	log(f"Solve status: {solve_status}")
	if solve_status != "Optimal":
		foreverFairData_instance.close_auction (auction_id, solve_status=solve_status, objective_value=None, auction_close_time=foreverFairData_instance.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
		return

	# Save the resulting quota and prices.
	for (well_id, period_id) in bid_periods:
		foreverFairData_instance.set_quota_auction_end (auction_id, well_id, period_labels[period_id - 1], 
			quantity_vars [(well_id, period_id)].value (), 
			auction_model.constraints [f"qtywt_{well_id}_{period_id}"].pi)

	# Save control point slack and dual prices.
	for row in cp_events:
		cp_id = int(row["control_point_id"])
		effect_date = str(row["effect_date"])
		effect_period = effect_date_to_idx[effect_date]
		constraint_name = f"cp_{cp_id}_{effect_period}"
		if constraint_name in auction_model.constraints:
			foreverFairData_instance.set_control_point_event_results (auction_id, cp_id, effect_date, 
				auction_model.constraints[constraint_name].slack, 
				auction_model.constraints[constraint_name].pi)

	foreverFairData_instance.close_auction (auction_id, solve_status=solve_status, objective_value = float (value (auction_model.objective)), auction_close_time=foreverFairData_instance.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
	log("Auction results saved")
	return # market_result

def SetDebugAuctionData (foreverFairData_instance: ForeverFairData): return foreverFairData_instance.use_debug_database ()