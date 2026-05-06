# AuctionController.py. Claude guided by JFR, 2026 05 04.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Coordinate setup, execution, and view state for auctions.

from __future__ import annotations
from collections import defaultdict
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
from ForeverFairClasses import Auction, AuctionCase
from ForeverFairClasses import AcceptedBid, ControlPointResult, MarketResult, TraderPeriodResult
from services.ForeverFairData import ForeverFairData

def _constraint_name(control_point_id: int, period_id: int) -> str: return f"cp_{control_point_id}_{period_id}".replace("-", "_")

def runCurrentAuction(foreverFairData_instance: ForeverFairData, auction_id: int) -> MarketResult:
	clearing_start_time = foreverFairData_instance.the_time_at_the_tone_is().isoformat(timespec="minutes")
	# auction_case = foreverFairData_instance.load(auction_id)
	auction_model = LpProblem("groundwater_smart_market", LpMaximize)

	# 1. Scale quota. 
	# alphas = _compute_alphas(auction_case)
	# foreverFairData_instance.set_cp_alphas(auction_id, alphas)

	# 1. To do: If foreverfair.db has custom head bounds, use them. Otherwise uses default bounds.
	# Maybe: default_bounds = foreverFairData_instance.get_default_cp_bounds()
	# Define bid variables with their upper bounds. Bid decision variables exist only for bid periods.
	# Accepted bids are constrained by allowed drawdown in later periods.
	bid_variables = {bid.id: LpVariable(f"accept_{bid.id}", lowBound=0, upBound=bid.quantity) for bid in auction_case.bids}
	
	# Define quantity variables: aggregate pumping per (well_id, bid_period).
	# Equality constraint q[w,p] = sum(bids[w,p,step]) yields dual = clearing price for (well, period).
	def _qty_var_name(well_id, period_id): return f"qty_{well_id}_{period_id}".replace("-", "_").replace(" ", "_")
	well_periods = {(bid.well_id, bid.period_id) for bid in auction_case.bids}
	quantity_vars = {(w, p): LpVariable(_qty_var_name(w, p), lowBound=None) for w, p in well_periods}
	
	# Objective function. All bid variables positive, so this is a gross pool model.
	auction_model += lpSum(bid.price * bid_variables[bid.id] for bid in auction_case.bids)

	# Quantity is the sum of the bids. The dual variable on each constraint is the clearing price for that well_id and bid period.
	def _qty_eq_name(well_id, period_id): return f"qty_eq_{well_id}_{period_id}".replace("-", "_").replace(" ", "_")
	for (w, p), qvar in quantity_vars.items():
		bids_wp = [bid for bid in auction_case.bids if bid.well_id == w and bid.period_id == p]
		auction_model += qvar == lpSum(bid_variables[bid.id] for bid in bids_wp), _qty_eq_name(w, p)

	# Get the response matrix.
	response_lookup = defaultdict(list)
	for factor in auction_case.response_factors: response_lookup[(factor.control_point_id, factor.effect_period)].append(factor)

	# Constrain all effect periods in the response matrix.
	for control_point in auction_case.control_points:
		for effect_period_id in control_point.bound_by_period.keys():
			auction_model += lpSum(factor.value * quantity_vars[(factor.well_id, factor.pumping_period)] for factor in response_lookup.get((control_point.id, effect_period_id), []) if (factor.well_id, factor.pumping_period) in quantity_vars) <= control_point.bound_by_period[effect_period_id], _constraint_name(control_point.id, effect_period_id)

	auction_model.writeLP(f"auctionClearingModel_{auction_case.auction.id}.lpt")
	solve_status = LpStatus[auction_model.solve(PULP_CBC_CMD(msg=0))]
	accepted_bids = [AcceptedBid(bid_id=bid.id, accepted_quantity = bid_variables[bid.id].value()) for bid in auction_case.bids]
	accepted_lookup = {item.bid_id: item.accepted_quantity for item in accepted_bids}

	trader_period_totals = defaultdict(float)
	for bid in auction_case.bids: trader_period_totals[(bid.participant_id, bid.period_id)] += accepted_lookup[bid.id]

	trader_period_results = []
	for participant in auction_case.participants:
		for period in auction_case.auction.periods:
			trader_period_results.append(TraderPeriodResult(participant_id=participant.id, period_id=period.id, accepted_quantity=trader_period_totals[(participant.id, period.id)], initial_allocation=participant.allocation_by_period.get(period.id, 0.0),))

	# Clearing prices: dual of quantity equality constraints, one per [well, bid_period].
	well_period_prices: dict[tuple[int, int], float] = {}
	for (w, p) in well_periods: well_period_prices[(w, p)] = float(getattr(auction_model.constraints[_qty_eq_name(w, p)], "pi", 0.0) or 0.0)

	control_point_results = []
	for control_point in auction_case.control_points:
		for effect_period_id in control_point.bound_by_period.keys():
			bound = control_point.bound_by_period[effect_period_id]
			used = sum(factor.value * float(quantity_vars[(factor.well_id, factor.pumping_period)].value() or 0.0) for factor in response_lookup.get((control_point.id, effect_period_id), []) if (factor.well_id, factor.pumping_period) in quantity_vars)
			dual_value = float(getattr(auction_model.constraints[_constraint_name(control_point.id, effect_period_id)], "pi", 0.0) or 0.0)
			control_point_results.append(ControlPointResult(control_point_id=control_point.id, period_id=effect_period_id, used_capacity=used, bound_capacity=bound, dual_value=dual_value,))

	market_result = MarketResult(solve_status=solve_status, objective_value=float(value(auction_model.objective) or 0.0), accepted_bids=accepted_bids, trader_period_results=trader_period_results, control_point_results=control_point_results, well_period_prices=well_period_prices,)
	foreverFairData_instance.save_run_results(auction_id, market_result, clearing_start_time=clearing_start_time)
	return market_result

def SetDebugAuctionData(foreverFairData_instance: ForeverFairData): return foreverFairData_instance.use_debug_database()

# This works, but the SQL implementation in ForeverFairData.py is faster.
def compute_alphas(foreverFairData_instance: ForeverFairData, auction_id: int) -> dict[tuple[int, str], float]:
	"""Compute scaling factors alpha for each (control_point, effect_date) to enforce bounds."""
	quota = foreverFairData_instance.get_quota_by_well_pumping_period(auction_id) # well quota at auction start.
	allowable_head_change, effect_date_to_idx = foreverFairData_instance.get_allowable_head_change_by_cp_effect_date(auction_id)

	alphas_by_constraint: dict[tuple[int, str], float] = {}
	well_constraint_quota: dict[tuple[int, int, str, int], float] = {}
	for (cp_id, effect_date), allowed_change in allowable_head_change.items():
		effect_period = effect_date_to_idx[effect_date]
		factors = foreverFairData_instance.get_response_factors_for_cp_period(cp_id, effect_period)
		drawdown_with_all_quota = sum(rf.value * quota[(rf.well_id, rf.pumping_period)] for rf in factors)

		# Most, but not all, factors are < 0. A few are > 0. quota >= 0. So usually drawdown_with_all_quota < 0. 
		# Typically allowed_change < 0. So usally drawdown_with_all_quota/allowed_change > 0, but not always.
		# If it is negative, either the data has response factors > 0, or allowed_change > 0.
		# Case: factors > 0, allowed_change > 0, so allowed_change/drawdown_with_all_quota > 0. So pumping raises head and allowed change is positive? Not in Tianqiao.
		# Case: factors < 0, allowed_change > 0, so allowed_change/drawdown_with_all_quota < 0. So pumping lowers head but allowed change is positive? Not in Tianqiao.
		
		# Case: factors > 0, allowed_change < 0, so allowed_change/drawdown_with_all_quota < 0. So pumping raises head and allowed change is negative? Tianqiao has this. Set alpha = 1.
		if drawdown_with_all_quota > 0.0: alphas_by_constraint[(cp_id, effect_date)] = 1.0
		# Case: factors < 0, allowed_change < 0, so allowed_change/drawdown_with_all_quota > 0. So pumping lowers head and allowed change is negative. Most typical.
		else: alphas_by_constraint[(cp_id, effect_date)] = allowed_change/drawdown_with_all_quota

		factors_for_denominator = [rf for rf in factors if rf.pumping_period <= effect_period and (rf.well_id, rf.pumping_period) in quota]
		total_head_change_from_all_quota = sum(quota[(rf.well_id, rf.pumping_period)] * rf.value for rf in factors_for_denominator) # This is the denominator.
		for rf in factors_for_denominator: # well_constraint_quota is the allowable head change at cp_id in effect_date allocated to well_id in pumping_period. Likely negative!
			well_constraint_quota[(rf.well_id, rf.pumping_period, effect_date, cp_id)] = quota[(rf.well_id, rf.pumping_period)] * rf.value * allowed_change / total_head_change_from_all_quota

	foreverFairData_instance.set_control_point_alphas(auction_id, alphas_by_constraint)
	return alphas_by_constraint
