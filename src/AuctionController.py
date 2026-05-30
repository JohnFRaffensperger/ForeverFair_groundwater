# AuctionController.py. Claude guided by JFR, 2026 05 14.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Call for bids, clear ("run") the auction with optimization, calculate rights and cash exchanges.

from __future__ import annotations
from pathlib import Path
from typing import Callable
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
import BiddingController
from ForeverFairData import ForeverFairData

# Section 1. Setting up the auction system and creating auctions. ---------------------------

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
	previous_auction_id = ffdata.get_previous_auction_id(auction_id)
	for well_id in sorted({well_id for (well_id, _period_id) in quota_by_well_period.keys()}):
		if ffdata.has_active_bids_for_well(auction_id, well_id): continue
		if previous_auction_id is not None and ffdata.copy_active_bids_for_well(auction_id, previous_auction_id, well_id): continue
		BiddingController.create_default_bid(ffdata, auction_id, well_id)

# Section 2. Solve the market-clearing optimization. Called from the button on AuctionManager.html.
def runCurrentAuction (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float | None:

	# The debug_log strings go to the AuctionManager.html message box.
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log(f"In function runCurrentAuction, runCurrentAuction started: auction_id={auction_id}") 

	# Get data. ------------------------------------------------------------------------
	auction = ffdata.get_auction_info (auction_id) # Should always be exactly one auction scheduled.

	# Get auction calendar: first pumping date -> last constrained date.
	period_maps = ffdata.get_auction_calendar(auction_id)
	effect_date_to_idx = period_maps["effect_iso_to_idx"]
	idx_to_pumping_iso = period_maps["idx_to_pumping_iso"]

	# Define variables.  ------------------------------------------------------------------------
	# Define bid variables with their upper bounds. Bid decision variables exist only for bid periods, but are constrained by allowed drawdown in later periods.
	period_labels = [p["label"] for p in auction["periods"]]
	period_id_map = {label: effect_date_to_idx[label] for label in period_labels if label in effect_date_to_idx}
	max_bid_steps = ffdata.get_max_bid_steps()
	bid_variables: dict[tuple[int, int, int], LpVariable] = {}
	bid_prices: dict[tuple[int, int, int], float] = {}
	for bid in ffdata.get_bids (auction_id):
		period_id = period_id_map[str(bid["pumping_date"])]
		for step_num in range(1, max_bid_steps + 1):
			if bid[f"qty{step_num}"] is None or bid[f"price{step_num}"] is None: continue # Check for None in case the trader doesn't put in all 5 steps?
            # Create an LP variable for each bid step. The upper bound is the bid step quantity.
			bid_variables[(int(bid["well_id"]), period_id, step_num)] = LpVariable (f"bidwtb_{int(bid['well_id'])}_{period_id}_{step_num}", lowBound=0, upBound=bid[f"qty{step_num}"])
			bid_prices[(int(bid["well_id"]), period_id, step_num)] = float(bid[f"price{step_num}"])

	# Quantity variables: aggregate pumping per (well_id, bid_period). 
	# Since bid_variables have lower bound of 0, adding a lower bound would disrupt the dual prices.
	bid_periods: set[tuple[int, int]] = {(well_id, period_id) for (well_id, period_id, _step_num) in bid_variables.keys()}
	quantity_vars: dict[tuple[int, int], LpVariable] = {(w, t): LpVariable (f"qty_{w}_{t}", lowBound=None) for w, t in bid_periods}
	
	# Set up model.  ------------------------------------------------------------------------
	# Objective function. All bid variables positive, so this is a gross pool model.
	auction_model = LpProblem ("Forever_Fair_clearing_model", LpMaximize)
	auction_model += lpSum (bid_prices[key] * bid_variables[key] for key in bid_variables.keys())
		
	# BID CONSTRAINTS. Quantity to take is the sum of the bids: q[w,p] = sum (bids[w,p,step]).
	# The dual variable on each constraint is the clearing price for that well_id and bid period.
	for (w, t) in quantity_vars.keys ():
		auction_model += quantity_vars[(w, t)] == lpSum (bid_variables[key] for key in bid_variables.keys() if key[0] == w and key[1] == t), f"qtywt_{w}_{t}"

	# Get the response matrix. Every (cp_id, effect_period) from cp_events should have response factors, otherwise the constraint is empty.
	response_factors = ffdata.get_response_factors_for_auction(auction_id)
	
	cp_events = ffdata.get_control_point_events(auction_id)
	empty_constraints: list[tuple[int, int]] = []
	for cpe in cp_events:
		control_point_id = int(cpe["control_point_id"])
		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
		if any(key[2] == effect_period and key[3] == control_point_id for key in response_factors): continue
		empty_constraints.append((effect_period, control_point_id))
	# if empty_constraints: log(f"WARNING: cp_events with no response factors (constraint will be trivial): {empty_constraints}")
	assert not empty_constraints, f"cp_events with no response factors: {empty_constraints}"

	# HEAD CONSTRAINTS: all effect periods in the response matrix by iterating over all control_point_events. 
	# CAREFUL WITH SIGNS. Response matrix coefficients are typically negative. Allowable head change is typically negative and quantity_vars are positive.
	# We want q*F <= headchange, feeling like 100*(-0.03) <= -5, which is mathematically wrong. 
	# So we need to change signs to have "<=" constraints. The "-1.0*" is for emphasis.
	for cpe in cp_events:
		control_point_id = int(cpe["control_point_id"])
		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
		# TODO: Do you really want max(0.0,...) here?
		auction_model += (lpSum(-1.0 * response_factors.get((well_id, pumping_period, effect_period, control_point_id), 0.0) * quantity_vars[(well_id, pumping_period)]
				for well_id in ffdata.get_wells()
				for pumping_period in range(1, auction["periods"][-1]["id"] + 1)
				if (well_id, pumping_period) in quantity_vars)
					<= max(0.0, -1.0*cpe["allowable_head_change"]), f"cp_{control_point_id}_{effect_period}") 

	# Run the linear program.  ------------------------------------------------------------------------
	lpt_dir = Path (__file__).parent.parent / "Auction_lpt_files"
	lpt_dir.mkdir (exist_ok=True)
	auction_model.writeLP (str (lpt_dir / f"Forever_Fair_auction_{auction['id']}.lpt")) # You can open this with the free LP Solve IDE.

	log(f"Solving ...") 
	solve_status = LpStatus[auction_model.solve (PULP_CBC_CMD (msg=0))]
	log(f"Solve status: {solve_status}. Writing solution...")

	if solve_status != "Optimal": # If the linear program failed.
		ffdata.close_auction (auction_id, solve_status=solve_status, objective_value=None, auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"), auction_revenue=None)
		return

	quota_rows: list[tuple[int, str, float | None, float | None]] = []
	for (well_id, period_id) in bid_periods:
		quota_rows.append((well_id, idx_to_pumping_iso[period_id], quantity_vars[(well_id, period_id)].value(), auction_model.constraints[f"qtywt_{well_id}_{period_id}"].pi))
		# print ("q pi: ", auction_model.constraints[f"qtywt_{well_id}_{period_id}"].pi)

	control_point_rows: list[tuple[str, int, float | None, float | None]] = []
	committed_rows: list[tuple[str, int, float]] = []
	for cpe in cp_events:
		control_point_id = int(cpe["control_point_id"])
		effect_date = str(cpe["effect_date"])
		effect_period = effect_date_to_idx[effect_date]
		control_point_rows.append((effect_date, control_point_id, auction_model.constraints[f"cp_{control_point_id}_{effect_period}"].slack, auction_model.constraints[f"cp_{control_point_id}_{effect_period}"].pi))
		# The committed pumping is only the first pumping period in the auction scheduling. Later periods are simply plans.
		auction_period_drawdown = sum(response_factors.get((well_id, 1, effect_period, control_point_id), 0.0) * quantity_vars[(well_id, 1)].value() for well_id in ffdata.get_wells())
		# committed_rows.append((effect_date, control_point_id, cpe["allowable_head_change"] - auction_period_drawdown))
		committed_rows.append((effect_date, control_point_id, auction_period_drawdown))

	ffdata.set_auction_solution_results_bulk(auction_id, quota_rows, control_point_rows, committed_rows)
	revenue = settle_accounts (ffdata, auction_id, debug_log=log)
	ffdata.close_auction (auction_id, solve_status=solve_status, objective_value = value(auction_model.objective), auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"), auction_revenue=revenue)
	log("Auction results saved")
	return revenue

# Section 3. Settlement, calculating auction revenue. -------------------------------------------
def settle_accounts (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float:
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log("In function settle_accounts")
	
	revenue = compute_revenue_on_constraint_quota (ffdata, auction_id, debug_log=log)
	# else:
	# revenue = compute_revenue_on_well_quota (ffdata, auction_id, debug_log=log)
	# TODO: if auction_id==1 and rights policy is scaling, 
	#		convert starting constraint quota to cash-equivalent starting well quota.
	# Update ending quota and payments.
	# Update ending constraint aquifer head.
	return revenue

# 3 rights policies. Most important for the first auction of the season.
# User pays: should be revenue positive for the first auction.
# Auction manager pays: negative with overallocation.
# Auction manager scales: should always be zero. 
def compute_revenue_on_constraint_quota (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float:
	"""Compute revenue from constraint quota bought and sold.
	Revenue = sum over all causal response factors (pumping_period <= effect_period) of:
	    dual_price(k,t) * F(w,u,t,k) * allowed_change(k,t) * (q_start(w,u) - q_end(w,u)) / denom_start(k,t)
		where denom_start(k,t) = sum_{v,r: r<=t} q_start(v,r) * F(v,r,t,k).
	"""
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log("In function compute_revenue_on_constraint_quota")

	# Build auction-scoped indices over the active timeline: from first pumping period through the last constrained period.
	auction_calendar = ffdata.get_auction_calendar(auction_id)
	effect_date_to_idx = auction_calendar["effect_iso_to_idx"]
	dual_prices: dict[tuple[int, int], float] = {}
	allowable_drawdown: dict[tuple[int, int], float] = {}

	# Retrieve dual prices for control_point_events in this auction.
	for cpe in ffdata.get_control_point_events(auction_id):
		if cpe["dual_price"] is None or cpe["allowable_head_change"] is None: continue
		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
		control_point_id = int(cpe["control_point_id"])
		dual_prices[(effect_period, control_point_id)] = float(cpe["dual_price"])
		allowable_drawdown[(effect_period, control_point_id)] = float(cpe["allowable_head_change"])

	# Quota start/end keys have pumping period indices scoped to the auction.
	quota_start: dict[tuple[int, int], float] = {}
	quota_end: dict[tuple[int, int], float] = {}
	for well_id in ffdata.get_wells():
		for pumping_period, q1 in ffdata.get_well_start_quota(well_id, auction_id).items(): quota_start[(well_id, pumping_period)] = q1
		for pumping_period, q2 in ffdata.get_well_end_quota(well_id, auction_id).items(): quota_end[(well_id, pumping_period)] = q2

	# THIS IS ALPHA. It is (allowable drawdown at control point in effect period)/(total licensed demand on control point in effect period).
	# Demand is derived from starting quota quantities and the response matrix.
	alpha: dict[tuple[int, int], float] = {}
	response_factors = ffdata.get_response_factors_for_auction(auction_id)
	for effect_period in ffdata.get_auction_effect_periods(auction_id):
		for control_point_id in ffdata.get_control_point_ids():
			demand = 0.0 # summing demand over wells and pumping periods for a given effect period and control point.
			for well_id in ffdata.get_wells():
				start_quota_by_period = ffdata.get_well_start_quota(well_id, auction_id)
				for pumping_period, start_quota_quantity in start_quota_by_period.items():
					if pumping_period <= effect_period:
						factor_value = response_factors.get((well_id, pumping_period, effect_period, control_point_id))
						if factor_value is None: continue # We don't assume the response matrix is dense.
						demand += start_quota_quantity * factor_value
			alpha[(effect_period, control_point_id)] = 1.0 if 0.0 == demand else allowable_drawdown[(effect_period, control_point_id)] / demand
			
	revenue = 0.0
	for well_id in ffdata.get_wells():
		for pumping_period in range(1, len(auction_calendar["pumping_labels"]) + 1):
			for effect_period in ffdata.get_auction_effect_periods(auction_id):
				for control_point_id in ffdata.get_control_point_ids():
					factor_value = response_factors.get((well_id, pumping_period, effect_period, control_point_id))
					if factor_value is None: continue
					revenue -= factor_value * dual_prices[(effect_period, control_point_id)] * (quota_end[(well_id, pumping_period)] - alpha[(effect_period, control_point_id)] * quota_start[(well_id, pumping_period)])
	return revenue

# TODO: Unused. compute_revenue_on_constraint_quota should always work.
def compute_revenue_on_well_quota (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float:
	"""Compute revenue from well quota bought and sold.
	Revenue = sum over wells w and pumping periods t: dual_price(w,t)*(q_end(w,t)- q_start(w,t))
	"""
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log("In function compute_revenue_on_well_quota")
	# Use per-(well, pumping_period) clearing prices, i.e., duals of qtywt_{w}_{t} constraints.
	dual_prices: dict[tuple[int, int], float] = ffdata.get_well_dual_prices(auction_id)

	revenue = 0.0
	for key_w, dual_price in dual_prices.items():
		well_id, pumping_period = key_w
		start_quota = ffdata.get_well_start_quota(well_id, auction_id)
		end_quota = ffdata.get_well_end_quota(well_id, auction_id)
		term = -dual_price * (end_quota[pumping_period] - start_quota[pumping_period])
		print ("Well revenue term:", "well_period=", key_w, "dual=", dual_price, "end=", end_quota[pumping_period], "start=", start_quota[pumping_period], "term=", term)
		revenue += term
	return revenue

def SetDebugAuctionData (ffdata: ForeverFairData): return ffdata.use_debug_database ()