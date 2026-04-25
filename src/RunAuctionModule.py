# RunAuctionModule.py. Claude guided by JFR, 2026 04 21.
# Purpose: Solve the auction optimization model and compute result tables.

from __future__ import annotations
from collections import defaultdict
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum, value
from models import AcceptedBid, ConstraintResult, AuctionCase, MarketResult, TraderPeriodResult

def _constraint_name(control_point_id: str, period_id: str) -> str: return f"cp_{control_point_id}_{period_id}".replace("-", "_")

def runAuction(auction_case: AuctionCase) -> MarketResult:
	auction_model = LpProblem("groundwater_smart_market", LpMaximize)
	bid_variables = {bid.id: LpVariable(f"accept_{bid.id}", lowBound=0, upBound=bid.quantity) for bid in auction_case.bids}
	auction_model += lpSum(bid.price * bid_variables[bid.id] for bid in auction_case.bids)

	response_lookup = defaultdict(list)
	for factor in auction_case.response_factors:
		response_lookup[(factor.control_point_id, factor.effect_period)].append(factor)

	for control_point in auction_case.control_points:
		for period in auction_case.auction.periods:
			bound = control_point.bound_by_period.get(period.id)
			if bound is None: continue
			factors = response_lookup.get((control_point.id, period.id), [])
			expression = lpSum(factor.value * bid_variables[bid.id] for factor in factors for bid in auction_case.bids if bid.well_id == factor.well_id and bid.period_id == factor.pumping_period)
			auction_model += expression <= bound, _constraint_name(control_point.id, period.id)

	auction_model.writeLP("auctionClearingModel.lpt")
	solve_status = LpStatus[auction_model.solve(PULP_CBC_CMD(msg=0))]
	accepted_bids = [AcceptedBid(bid_id=bid.id, accepted_quantity=max(0.0, float(bid_variables[bid.id].value() or 0.0))) for bid in auction_case.bids]
	accepted_lookup = {item.bid_id: item.accepted_quantity for item in accepted_bids}

	trader_period_totals = defaultdict(float)
	for bid in auction_case.bids:
		trader_period_totals[(bid.participant_id, bid.period_id)] += accepted_lookup[bid.id]

	trader_period_results = []
	for participant in auction_case.participants:
		for period in auction_case.auction.periods:
			trader_period_results.append(TraderPeriodResult(participant_id=participant.id, period_id=period.id, accepted_quantity=round(trader_period_totals[(participant.id, period.id)], 3), initial_allocation=round(participant.allocation_by_period.get(period.id, 0.0), 3),))

	constraint_results = []
	period_prices = defaultdict(float)
	for control_point in auction_case.control_points:
		for period in auction_case.auction.periods:
			bound = control_point.bound_by_period.get(period.id)
			if bound is None: continue
			used = 0.0
			for factor in response_lookup.get((control_point.id, period.id), []):
				for bid in auction_case.bids:
					if bid.well_id == factor.well_id and bid.period_id == factor.pumping_period: used += factor.value * accepted_lookup[bid.id]
			constraint = auction_model.constraints[_constraint_name(control_point.id, period.id)]
			dual_value = float(getattr(constraint, "pi", 0.0) or 0.0)
			period_prices[period.id] = max(period_prices[period.id], dual_value)
			constraint_results.append(ConstraintResult(control_point_id=control_point.id, period_id=period.id, used_capacity=round(used, 3), bound_capacity=round(bound, 3), dual_value=round(dual_value, 3),))

	return MarketResult(solve_status=solve_status, objective_value=round(float(value(auction_model.objective) or 0.0), 3), accepted_bids=accepted_bids, trader_period_results=trader_period_results, constraint_results=constraint_results, period_prices={period_id: round(price, 3) for period_id, price in period_prices.items()},)
