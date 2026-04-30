# RunAuctionModule.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Solve the auction optimization model and compute result tables.

from __future__ import annotations
from collections import defaultdict
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
from models import AcceptedBid, ConstraintResult, AuctionCase, MarketResult, TraderPeriodResult

def _constraint_name(control_point_id: str, period_id: str) -> str: return f"cp_{control_point_id}_{period_id}".replace("-", "_")

def runAuction(auction_case: AuctionCase) -> MarketResult:
	auction_model = LpProblem("groundwater_smart_market", LpMaximize)

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
	well_period_prices: dict[str, float] = {}
	for (w, p) in well_periods: well_period_prices[f"{w}_{p}"] = float(getattr(auction_model.constraints[_qty_eq_name(w, p)], "pi", 0.0) or 0.0)

	constraint_results = []
	for control_point in auction_case.control_points:
		for effect_period_id in control_point.bound_by_period.keys():
			bound = control_point.bound_by_period[effect_period_id]
			used = sum(factor.value * float(quantity_vars[(factor.well_id, factor.pumping_period)].value() or 0.0) for factor in response_lookup.get((control_point.id, effect_period_id), []) if (factor.well_id, factor.pumping_period) in quantity_vars)
			dual_value = float(getattr(auction_model.constraints[_constraint_name(control_point.id, effect_period_id)], "pi", 0.0) or 0.0)
			constraint_results.append(ConstraintResult(control_point_id=control_point.id, period_id=effect_period_id, used_capacity=used, bound_capacity=bound, dual_value=dual_value,))

	return MarketResult(solve_status=solve_status, objective_value=float(value(auction_model.objective) or 0.0), accepted_bids=accepted_bids, trader_period_results=trader_period_results, constraint_results=constraint_results, well_period_prices=well_period_prices,)
