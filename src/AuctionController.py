# AuctionController.py. Claude guided by JFR, 2026 05 14.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Call for bids, clear ("run") the auction with optimization, calculate rights and cash exchanges.
# This is the business logic from the auction manager's point of view.

from __future__ import annotations
import csv
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
	# ffdata.set_first_auction_adjusted_quota()
	call_for_bids(ffdata, auction_id)
	return auction_id

def create_auction(db_path: Path) -> int:
	"""Create and initialize an auction."""
	ffdata = ForeverFairData(db_path)
	auction_id = ffdata.add_auction()["auction_id"]

	ffdata.set_control_point_events_for_auction(auction_id)
	ffdata.set_quota_for_auction(auction_id)
	
	call_for_bids(ffdata, auction_id)
	return auction_id

# Set up default bids. A trader's manual entry will overwrite these.
def call_for_bids(ffdata: ForeverFairData, auction_id: int) -> None:
	# Use quota to create default bids.
	rights_policy = ffdata.get_rights_policy()
	quota_by_well_period = ffdata.get_quota(auction_id)
	users_pay_by_well = BiddingController.get_users_pay_bid_quantities_by_well(ffdata, auction_id) if rights_policy == "Users_pay" else None
	previous_auction_id = ffdata.get_previous_auction_id(auction_id)
	for well_id in sorted({well_id for (well_id, _period_id) in quota_by_well_period.keys()}):
		if ffdata.has_active_bids_for_well(auction_id, well_id): continue
		if previous_auction_id is not None and ffdata.copy_active_bids_for_well(auction_id, previous_auction_id, well_id): continue
		BiddingController.create_default_bid(ffdata, auction_id, well_id, rights_policy=rights_policy, quota_by_well_period=quota_by_well_period, users_pay_by_well=users_pay_by_well)
	if previous_auction_id is None: return
	for trader_id in ffdata.get_trader_ids_by_type("environmental"):
		ffdata.copy_active_environmental_bids(auction_id, previous_auction_id, trader_id)

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
	environmental_bid_variables: dict[tuple[int, int, int], LpVariable] = {}
	environmental_bid_prices: dict[tuple[int, int, int], float] = {}
	for bid in ffdata.get_environmental_bids(auction_id):
		for step_num in range(1, max_bid_steps + 1):
			if bid[f"qty{step_num}"] is None or bid[f"price{step_num}"] is None: continue
			environmental_bid_variables[(int(bid["trader_id"]), int(bid["cpe_id"]), step_num)] = LpVariable(f"envbid_{int(bid['trader_id'])}_{int(bid['cpe_id'])}_{step_num}", lowBound=0, upBound=bid[f"qty{step_num}"])
			environmental_bid_prices[(int(bid["trader_id"]), int(bid["cpe_id"]), step_num)] = float(bid[f"price{step_num}"])
	environmental_positions: set[tuple[int, int]] = {(trader_id, cpe_id) for (trader_id, cpe_id, _step_num) in environmental_bid_variables.keys()}
	environmental_quantity_vars: dict[tuple[int, int], LpVariable] = {(trader_id, cpe_id): LpVariable(f"envqty_{trader_id}_{cpe_id}", lowBound=None) for trader_id, cpe_id in environmental_positions}
	
	# Set up model.  ------------------------------------------------------------------------
	# Objective function. All bid variables positive, so this is a gross pool model.
	auction_model = LpProblem ("Forever_Fair_clearing_model", LpMaximize)
	auction_model += lpSum (bid_prices[key] * bid_variables[key] for key in bid_variables.keys()) + lpSum(environmental_bid_prices[key] * environmental_bid_variables[key] for key in environmental_bid_variables.keys())
		
	# BID CONSTRAINTS. Quantity to take is the sum of the bids: q[w,p] = sum (bids[w,p,step]).
	# The dual variable on each constraint is the clearing price for that well_id and bid period.
	for (w, t) in quantity_vars.keys ():
		auction_model += quantity_vars[(w, t)] == lpSum (bid_variables[key] for key in bid_variables.keys() if key[0] == w and key[1] == t), f"qtywt_{w}_{t}"
	for (trader_id, cpe_id) in environmental_quantity_vars.keys():
		auction_model += environmental_quantity_vars[(trader_id, cpe_id)] == lpSum(environmental_bid_variables[key] for key in environmental_bid_variables.keys() if key[0] == trader_id and key[1] == cpe_id), f"envqty_{trader_id}_{cpe_id}"

	# Get the response matrix. Every (cp_id, effect_period) from cp_events should have response factors, otherwise the constraint is empty.
	response_factors = ffdata.get_response_factors_for_auction(auction_id)
	
	cp_events = ffdata.get_control_point_events(auction_id)
	empty_constraints: list[tuple[int, int]] = []
	for cpe in cp_events:
		cpe_id = int(cpe["cpe_id"])
		control_point_id = int(cpe["control_point_id"])
		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
		if any(key[2] == effect_period and key[3] == control_point_id for key in response_factors): continue
		if any(cpe_id2 == cpe_id for (_trader_id, cpe_id2) in environmental_quantity_vars.keys()): continue
		empty_constraints.append((effect_period, control_point_id))
	# if empty_constraints: log(f"WARNING: cp_events with no response factors (constraint will be trivial): {empty_constraints}")
	assert not empty_constraints, f"cp_events with no response factors: {empty_constraints}"

	# HEAD CONSTRAINTS: all effect periods in the response matrix by iterating over all control_point_events. 
	# CAREFUL WITH SIGNS. Response matrix coefficients are typically negative. Allowable head change is typically negative and quantity_vars are positive.
	# We want q*F <= headchange, feeling like 100*(-0.03) <= -5, which is mathematically wrong. 
	# So we need to change signs to have "<=" constraints. The "-1.0*" is for emphasis.
	# Previously settled environmental head protection (from the prior auction) tightens the RHS.
	_prior_auction_id = ffdata.get_previous_auction_id(auction_id)
	prior_env_head_by_control_point: dict[tuple[int, str], float] = ffdata.get_environmental_head_protection(_prior_auction_id) if _prior_auction_id is not None else {}
	for cpe in cp_events:
		control_point_id = int(cpe["control_point_id"])
		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
		cpe_id = int(cpe["cpe_id"])
		prior_env_head_by_cpe = prior_env_head_by_control_point.get((control_point_id, str(cpe["effect_date"])), 0.0)
		auction_model += (lpSum(-1.0 * response_factors.get((well_id, pumping_period, effect_period, control_point_id), 0.0) * quantity_vars[(well_id, pumping_period)]
				for well_id in ffdata.get_wells()
				for pumping_period in range(1, auction["periods"][-1]["id"] + 1)
				if (well_id, pumping_period) in quantity_vars)
				+ lpSum(environmental_quantity_vars[(trader_id, cpe_id)] for (trader_id, cpe_id2) in environmental_quantity_vars.keys() if cpe_id2 == cpe_id)
					<= -1.0*(prior_env_head_by_cpe + cpe["allowable_head_change"])), f"cp_{control_point_id}_{effect_period}" 
					# <= max(0.0, -1.0*(prior_env_head_by_cpe + cpe["allowable_head_change"])), f"cp_{control_point_id}_{effect_period}") 

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
	environmental_rows: list[tuple[int, int, float | None, float | None, float | None]] = []
	for cpe in cp_events:
		control_point_id = int(cpe["control_point_id"])
		effect_date = str(cpe["effect_date"])
		effect_period = effect_date_to_idx[effect_date]
		cpe_id = int(cpe["cpe_id"])
		control_point_rows.append((effect_date, control_point_id, auction_model.constraints[f"cp_{control_point_id}_{effect_period}"].slack, auction_model.constraints[f"cp_{control_point_id}_{effect_period}"].pi))
		# The committed pumping is only the first pumping period in the auction scheduling. Later periods are simply plans.
		auction_period_drawdown = sum(response_factors.get((well_id, 1, effect_period, control_point_id), 0.0) * quantity_vars[(well_id, 1)].value() for well_id in ffdata.get_wells())
		# committed_rows.append((effect_date, control_point_id, cpe["allowable_head_change"] - auction_period_drawdown))
		committed_rows.append((effect_date, control_point_id, auction_period_drawdown))
		for trader_id, cpe_id2 in environmental_quantity_vars.keys():
			if cpe_id2 != cpe_id: continue
			environmental_rows.append((trader_id, cpe_id, 0.0, environmental_quantity_vars[(trader_id, cpe_id)].value(), auction_model.constraints[f"cp_{control_point_id}_{effect_period}"].pi))

	ffdata.set_auction_solution_results_bulk(auction_id, quota_rows, control_point_rows, committed_rows, environmental_rows)
	revenue = settle_accounts (ffdata, auction_id, debug_log=log)
	ffdata.close_auction (auction_id, solve_status=solve_status, objective_value = value(auction_model.objective), auction_close_time=ffdata.the_time_at_the_tone_is ().isoformat (timespec="minutes"), auction_revenue=revenue)
	log("Auction results saved")
	return revenue

# Section 3. Settlement, calculating auction revenue. -------------------------------------------
def settle_accounts (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float:
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log("In function settle_accounts")

	revenue = 0.0
	if ffdata.get_rights_policy() == "Quota_scaled": # revenue neutral
		ffdata.calculate_and_set_constraint_alphas(auction_id)
		set_quota_scaled_start(ffdata, auction_id, debug_log=log)
		revenue = compute_revenue_on_constraint_quota (ffdata, auction_id, debug_log=log)

	elif ffdata.get_rights_policy() == "Auction_manager_pays": # likely revenue negative, please please don't do this.
		# In the first auction of the season, the auction manager reimburses traders for lost quota to bring over-allocation to match the environmental constraints.
		if 1 == auction_id: revenue = compute_revenue_on_well_quota (ffdata, auction_id, debug_log)

		# If the hydrologist upload new head constraints, the auction manager is not responsible for a change in weather. Quota will get scaled.
		# If the hydrologist does not upload new head constraints, later auctions should be revenue neutral anyway.
		else: 
			ffdata.calculate_and_set_constraint_alphas(auction_id)
			set_quota_scaled_start(ffdata, auction_id, debug_log=log)
			revenue = compute_revenue_on_constraint_quota (ffdata, auction_id, debug_log=log) 

	elif ffdata.get_rights_policy() == "Users_pay": # revenue positive, great policy but users hate it.
		# Hydrologist and programmer are responsible for loading initial rights of zero for each well.
		revenue = compute_revenue_on_well_quota (ffdata, auction_id, debug_log)

	# TODO: Update payments on traders' pages.
	return revenue + ffdata.compute_environmental_revenue_sql(auction_id)

# Especially for auction 1, the auction manager scales quota (or license) to the available water by control point event.
# The auction manager calculates an alpha for each control point event, then calculates constraint quota for each well.
# To have this make sense to the traders after auction clearing, the auction manager converts that portfolio of constraint quota
# to a cash-equivalent well quota called quota_scaled_start. 
# The auction manager calculates cash settlement based on (quota_scaled_start - quota_auction_end)*well_quota.price for the take_date.
# Again, this should matter only with a new aquifer head. After a scaling exercise, all future quota_auction_start should equal the quota_scaled_start.
def set_quota_scaled_start(ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> dict[tuple[int, int], float]:
	"""Set well_quota.quota_scaled_start using response factors, alpha, and clearing prices."""
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log("In function set_quota_scaled_start")

	auction_calendar = ffdata.get_auction_calendar(auction_id)
	effect_date_to_idx = auction_calendar["effect_iso_to_idx"]
	response_factors = ffdata.get_response_factors_for_auction(auction_id)
	quota_auction_start = ffdata.get_quota(auction_id)
	dual_prices = ffdata.get_well_dual_prices(auction_id)
	alpha: dict[tuple[int, int], float] = {}
	cp_dual_prices: dict[tuple[int, int], float] = {}
	for cpe in ffdata.get_control_point_events(auction_id):
		if cpe["alpha"] is None: continue
		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
		control_point_id = int(cpe["control_point_id"])
		alpha[(effect_period, control_point_id)] = float(cpe["alpha"])
		cp_dual_prices[(effect_period, control_point_id)] = float(cpe["dual_price"] or 0.0)

	quota_scaled_start: dict[tuple[int, int], float] = {}
	for (well_id, pumping_period), start_quota in quota_auction_start.items():
		scaled_demand = 0.0
		for (effect_period, control_point_id), alpha_value in alpha.items():
			response = response_factors.get((well_id, pumping_period, effect_period, control_point_id))
			if response is None: continue
			constraint_dual_price = cp_dual_prices[(effect_period, control_point_id)]
			# Typically, but not always, start_quota >= 0, response <= 0, alpha >= 0, constraint_dual_price <= 0. Scaled_demand should be positive.
			# Tianqiao has positive response factors and negative alpha_values.
			scaled_demand += start_quota * response * alpha_value * constraint_dual_price

		# Typically, scaled demand < 0 (it is demand for change in head) and dual_price < 0, so quota_scaled_start > 0.
		# But this is not guaranteed. Sometimes response_factors > 0, so demand > 0. Worse with large alpha.
		dual_price = dual_prices[(well_id, pumping_period)]
		quota_scaled_start[(well_id, pumping_period)] = 0.0 if 0.0 == dual_price else scaled_demand / dual_price

	ffdata.set_quota_scaled_start_bulk(auction_id, quota_scaled_start)
	return quota_scaled_start

def compute_revenue_on_constraint_quota (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float:
	"""Compute revenue from constraint quota bought and sold.
	
	Revenue = sum over all causal response factors (pumping_period <= effect_period) of:
	    dual_price(k,t) * F(w,u,t,k) * allowed_change(k,t) * (q_start(w,u) - q_end(w,u)) / denom_start(k,t)
		where denom_start(k,t) = sum_{v,r: r<=t} q_start(v,r) * F(v,r,t,k).
	SQL like SUM(-1.0 * rm.response * cpe.dual_price * (q.quota_end - cpe.alpha * q.quota_start)), 0.0) AS revenue FROM response_matrix.
	"""
	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
	log("In function compute_revenue_on_constraint_quota")
	return ffdata.compute_constraint_quota_revenue_sql(auction_id)

# Here is a pure Python version of compute_revenue_on_constraint_quota. I'll leave it here for explanation. Claude's SQL is much faster but inscrutable.
# def compute_revenue_on_constraint_quota (ffdata: ForeverFairData, auction_id: int, debug_log: Callable[[str], None] | None = None) -> float:
# 	"""Compute revenue from constraint quota bought and sold.
# 	Revenue = sum over all causal response factors (pumping_period <= effect_period) of:
# 	    dual_price(k,t) * F(w,u,t,k) * allowed_change(k,t) * (q_start(w,u) - q_end(w,u)) / denom_start(k,t)
# 		where denom_start(k,t) = sum_{v,r: r<=t} q_start(v,r) * F(v,r,t,k).
# 	"""
# 	log: Callable[[str], None] = debug_log if debug_log is not None else (lambda _message: None)
# 	log("In function compute_revenue_on_constraint_quota")

# 	# Build auction-scoped indices over the active timeline: from first pumping period through the last constrained period.
# 	auction_calendar = ffdata.get_auction_calendar(auction_id)
# 	effect_date_to_idx = auction_calendar["effect_iso_to_idx"]
# 	dual_prices: dict[tuple[int, int], float] = {}

# 	# Retrieve dual prices for control_point_events in this auction.
# 	for cpe in ffdata.get_control_point_events(auction_id):
# 		if cpe["dual_price"] is None or cpe["allowable_head_change"] is None: continue
# 		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
# 		control_point_id = int(cpe["control_point_id"])
# 		dual_prices[(effect_period, control_point_id)] = float(cpe["dual_price"])

# 	# Quota start/end keys have pumping period indices scoped to the auction.
# 	quota_start = ffdata.get_quota(auction_id)
# 	quota_end: dict[tuple[int, int], float] = {}
# 	for well_id in ffdata.get_wells():
# 		for pumping_period, q2 in ffdata.get_well_end_quota(well_id, auction_id).items(): quota_end[(well_id, pumping_period)] = q2

# 	alpha: dict[tuple[int, int], float] = {}
# 	for cpe in ffdata.get_control_point_events(auction_id):
# 		if cpe["alpha"] is None: continue
# 		effect_period = effect_date_to_idx[str(cpe["effect_date"])]
# 		control_point_id = int(cpe["control_point_id"])
# 		alpha[(effect_period, control_point_id)] = float(cpe["alpha"])

# 	response_factors = ffdata.get_response_factors_for_auction(auction_id)
			
# 	revenue = 0.0
# 	for well_id in ffdata.get_wells():
# 		for pumping_period in range(1, len(auction_calendar["pumping_labels"]) + 1):
# 			for effect_period in ffdata.get_auction_effect_periods(auction_id):
# 				for control_point_id in ffdata.get_control_point_ids():
# 					response = response_factors.get((well_id, pumping_period, effect_period, control_point_id))
# 					if response is None: continue
# 					dual_price = dual_prices.get((effect_period, control_point_id))
# 					if dual_price is None: continue
# 					alpha_value = alpha.get((effect_period, control_point_id), 1.0)
# 					start_quota = quota_start.get((well_id, pumping_period), 0.0)
# 					end_quota = quota_end.get((well_id, pumping_period), 0.0)
# 					revenue -= response * dual_price * (end_quota - alpha_value * start_quota)
# 	return revenue

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

# Research ---------------------------------------------------------------

def create_synthetic_new_head(ffdata: ForeverFairData, change_in_forecast: list[float] | None = None, output_csv_path: Path | None = None) -> Path:
	"""Export a synthetic aquifer-head CSV from control_point_events for the first open auction."""
	auction_id, control_point_events = ffdata.get_first_open_auction_control_point_events()
	if not control_point_events: raise ValueError(f"No control_point_events rows for open auction {auction_id}")

	effect_dates = sorted({str(row["effect_date"]) for row in control_point_events})
	if change_in_forecast is None: change_in_forecast = [1.0, 1.0, 1.0, 1.0]
	CHANGE_IN_FORECAST = {effect_date: (change_in_forecast[idx] if idx < len(change_in_forecast) else 1.0) for idx, effect_date in enumerate(effect_dates)}

	csv_path = output_csv_path or (Path(__file__).parent.parent / "new_aquifer_head_limits.csv")
	rows_for_csv: list[dict[str, float | int | str]] = []
	for row in control_point_events:
		control_point_id = int(row["control_point_id"])
		effect_date = str(row["effect_date"])
		committed_head_auction_start = row["committed_head_auction_start"]
		committed_allowable_head_change = row["committed_allowable_head_change"]
		if committed_head_auction_start is None or committed_allowable_head_change is None:
			raise ValueError(f"Missing committed head values for control_point_id={control_point_id}, effect_date={effect_date}")

		actual_start_head = CHANGE_IN_FORECAST[effect_date] * committed_head_auction_start
		rows_for_csv.append({"control_point_id": control_point_id, "effect_date": effect_date, "actual_start_head": actual_start_head,})

	# fieldnames = ["auction_id", "control_point_id", "effect_date", "actual_start_head", "minimum_head", "allowable_head_change", "committed_allowable_head_change", "change_in_forecast"]
	fieldnames = ["control_point_id", "effect_date", "actual_start_head"]
	with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
		writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows_for_csv)

	return csv_path