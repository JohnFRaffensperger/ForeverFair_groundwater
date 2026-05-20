# AuctionController.py. Claude guided by JFR, 2026 05 14.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Call for bids, clear ("run") the auction with optimization, calculate rights and cash exchanges.

from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from typing import Callable
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
import BiddingController
from ForeverFairData import ForeverFairData, ResponseFactor

# 1. Setting up the auction system and creating auctions. ---------------------------

def set_up_auction_system(db_path: Path) -> int:
	ffdata = ForeverFairData(db_path)
	ffdata.set_license_demand_on_aquifer() 
	auction_id = create_auction(db_path)
	ffdata.set_control_point_events_for_auction(auction_id)
	ffdata.set_quota_for_auction(auction_id)
	ffdata.set_first_auction_adjusted_quota()
	call_for_bids(ffdata, auction_id)
		# Should depend on rights policy.
	# if ffdata.get_rights_policy() == "Quota_scaled": # not "Auction_manager_pays" nor "Users_pay"
		# estimate quota based on minimum constraint alpha; tell trader "this is an estimate".
		# Problem is that the database needs a fourth quota: starting quota, estimated scaled start quota, true scaled start quota, end quota.
		# Unless I can show that the estimated scaled start quota == true scaled start quota.
		# write estimated quota to quota_scaled
	# elif traders have zero rights:
	#		write zero quota.
	# else (auction manager compensates traders for lost quota) copy license to quota.
	# End with create first auction.
	
	return auction_id

def create_auction(db_path: Path) -> int:
	"""Create and initialize an auction."""
	ffdata = ForeverFairData(db_path)
	auction_id = ffdata.add_auction()["auction_id"]

	# set_control_point_events_for_auction should now set the aquifer limits for each next auction
	# based on the original limits and the previous auction.
	ffdata.set_control_point_events_for_auction(auction_id)

	# Should dep
	ffdata.set_quota_for_auction(auction_id)
	# Scale license to quota only on the first auction. TODO: Should also scale quota on reload of aquifer head limits, and for a new period in a rolling auction horizon.
	# if 1 == auction_id: ffdata.calculate_and_set_constraint_alphas(auction_id)
	call_for_bids(ffdata, auction_id)
	return auction_id

# Set up default bids. A trader's manual entry will overwrite these.
def call_for_bids(ffdata: ForeverFairData, auction_id: int) -> None:
	# Use quota to create default bids.
	quota_by_well_period = ffdata.get_quota(auction_id)
	for well_id in sorted({well_id for (well_id, _period_id) in quota_by_well_period.keys()}):
		BiddingController.create_default_bid(ffdata, auction_id, well_id)

# 2. Solve the market-clearing optimization. The debug_log strings go to the AuctionManager.html message box.
def runCurrentAuction (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> None:
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log(f"runCurrentAuction started: auction_id={auction_id}") 

	auction = ffdata.get_auction_info (auction_id) # Should always be exactly one auction scheduled.

	# Get auction calendar: first pumping date -> last constrained date.
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
		period_id = period_id_map[str(row["effect_date"])]
		for step_num in range(1, max_bid_steps + 1):
			if row[f"qty{step_num}"] is None or row[f"price{step_num}"] is None: continue # Check for None in case the trader doesn't put in all 5 steps?
            # Create an LP variable for each bid step. The upper bound is the bid step quantity.
			bid_variables[(int(row["well_id"]), period_id, step_num)] = LpVariable (f"bidwtb_{int(row['well_id'])}_{period_id}_{step_num}", lowBound=0, upBound=row[f"qty{step_num}"])
			bid_prices[(int(row["well_id"]), period_id, step_num)] = float(row[f"price{step_num}"])
	
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
	# Assumption: response_matrix timing is calendar-independent. Period 1 in response_matrix
	# is always the first pumping period, so we remap global periods to auction-local periods.
	for factor in ffdata.get_response_factors_for_auction(auction_id):
		response_lookup[(factor["control_point_id"], factor["effect_period"])].append(factor)

	# Guard: every (cp_id, effect_period) from cp_events must have response factors, otherwise the constraint is empty.
	empty_constraints = [(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])]) for row in cp_events if not response_lookup[(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])])]]
	if empty_constraints: log(f"WARNING: cp_events with no response factors (constraint will be trivial): {empty_constraints}")
	assert not empty_constraints, f"cp_events with no response factors: {empty_constraints}"

	# Constrain all effect periods in the response matrix by iterating over all control_point_events. 
	# CAREFUL WITH SIGNS. Response matrix coefficients are typically negative. Allowable head change is typically negative. qvars are positive.
	# Want q*F <= headchange, like 100*(-0.03) <= -5, which is false. So we need to change signs (with -1.0* for emphasis) to have "<=" constraints.
	# (cp_events, effect_dates, effect_date_to_idx already built above)
	for row in cp_events:
		auction_model += (lpSum (-1.0*factor["value"] * quantity_vars [(factor["well_id"], factor["pumping_period"])]
				for factor in response_lookup[(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])])] if (factor["well_id"], factor["pumping_period"]) in quantity_vars)
					<= -1.0*float(row["allowable_head_change"]), f"cp_{int(row["control_point_id"])}_{effect_date_to_idx[str(row["effect_date"])]}") 

	# Run the linear program.
	lpt_dir = Path (__file__).parent.parent / "Auction_lpt_files"
	lpt_dir.mkdir (exist_ok=True)
	auction_model.writeLP (str (lpt_dir / f"Forever_Fair_auction_{auction["id"]}.lpt")) # You can open this with the free LP Solve IDE.
	solve_status = LpStatus[auction_model.solve (PULP_CBC_CMD (msg=0))]
	log(f"Solve status: {solve_status}")

	if solve_status != "Optimal": # If the linear program failed.
		ffdata.close_auction (auction_id, solve_status=solve_status, objective_value=None, auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
		return

	# Save the resulting quota and prices.
	for (well_id, period_id) in bid_periods:
		ffdata.set_quota_auction_end (auction_id, well_id, idx_to_pumping_iso[period_id], 
			quantity_vars [(well_id, period_id)].value (), 
			auction_model.constraints [f"qtywt_{well_id}_{period_id}"].pi)

	# Save control point slack and dual prices.
	for row in cp_events:
		constraint_name = f"cp_{int(row["control_point_id"])}_{effect_date_to_idx[str(row["effect_date"])]}"
		if constraint_name in auction_model.constraints: 
			# Passing date, not index, to save the price into the database.
			ffdata.set_control_point_event_results (auction_id, int(row["control_point_id"]), str(row["effect_date"]), 
				auction_model.constraints[constraint_name].slack, 
				auction_model.constraints[constraint_name].pi)

	ffdata.close_auction (auction_id, solve_status=solve_status, objective_value = float (value (auction_model.objective) or 0.0), auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"))
	log("Auction results saved")
	return # market_result

# 3. Settlement, calculating auction revenue. -------------------------------------------
def settle_accounts ():
	# if auction_id==1 and rights policy is scaling, 
	#		convert starting constraint quota to cash-equivalent starting well quota.
	# Update ending quota and payments.
	# Update ending constraint aquifer head.
	return

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
	allowable_drawdown: dict[tuple[int, int], float] = {}

	# Retrieve dual prices for control_point_events in this auction.
	for row in ffdata.get_control_point_events(auction_id):
		dual_prices[(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])])] = float(row["dual_price"])
		allowable_drawdown[(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])])] = float(row["allowable_head_change"])

	# Quota start/end keys already use auction-scoped pumping period indices.
	quota_start: dict[tuple[int, int], float] = {}
	quota_end: dict[tuple[int, int], float] = {}
	for well_id in ffdata.get_wells():
		for pumping_period, q1 in ffdata.get_well_start_quota(well_id, auction_id).items(): quota_start[(well_id, pumping_period)] = q1
		for pumping_period, q2 in ffdata.get_well_end_quota(well_id, auction_id).items(): quota_end[(well_id, pumping_period)] = q2

	# THIS IS ALPHA. It is (allowable drawdown)/demand. 
	# If allowable drawdown < demand, alpha < 1 and the auction manager should scale constraint quota down.
	# If allowable drawdown > demand, alpha > 1 and the auction manager can scale constraint quota up.
	demand_on_cpe: dict[tuple[int, int], float] = ffdata.get_license_demand_by_cp_effect_period(auction_id)
	alpha: dict[tuple[int, int], float] = {}
	for key_cp in demand_on_cpe:
		if key_cp in allowable_drawdown: alpha[key_cp] = allowable_drawdown[key_cp] / demand_on_cpe[key_cp]

	revenue = 0.0
	for rf in ffdata.get_all_response_factors():
		key_cp = (rf["control_point_id"], rf["effect_period"])
		if key_cp not in alpha: continue
		key_w = (rf["well_id"], rf["pumping_period"])
		if key_w not in quota_start: continue
		# Use quota scaled by constraint alpha.
		revenue += (alpha[key_cp]*rf["value"]*dual_prices[key_cp]*(quota_start[key_w] - quota_end[key_w]))
	return revenue

def compute_revenue_on_well_quota (ffdata: ForeverFairData, auction_id: int) -> float:

	revenue = 0.0
	return revenue

def SetDebugAuctionData (ffdata: ForeverFairData): return ffdata.use_debug_database ()