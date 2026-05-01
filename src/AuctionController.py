# AuctionController.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Coordinate setup, execution, and view state for auctions.

from __future__ import annotations
from datetime import datetime, timezone
from AuctionObjects import Auction, AuctionCase, ResponseFactor
from RunAuctionModule import runAuction
from services.ForeverFairData import ForeverFairData

def SetUpAuction( foreverFairData_instance: ForeverFairData, closed_date: str, first_water_take_date: str, last_water_take_date: str, period_length_hours: int, auction_type: str | None = None, ) -> Auction:
	if int(period_length_hours) <= 0: raise ValueError("period_length_hours must be positive")
	auction_id_str, period_keys = foreverFairData_instance.add_auction(closed_date, first_water_take_date, last_water_take_date, int(period_length_hours), auction_type)
	return foreverFairData_instance.get_auction(auction_id_str)

def ResetAuctionData(foreverFairData_instance: ForeverFairData): return foreverFairData_instance.reset_runtime_to_seed()

def prepare_auction_for_run(foreverFairData_instance: ForeverFairData, auction_id: str) -> None:
	now = datetime.now(timezone.utc).isoformat()
	period_keys = [p.id for p in foreverFairData_instance.get_auction(auction_id).periods]
	source_auction_id = foreverFairData_instance.get_next_auction(exclude_auction_id=auction_id)
	sourceget_water_take_date_list: list[str] = []
	if source_auction_id is not None:
		try: sourceget_water_take_date_list = [p.id for p in foreverFairData_instance.load(str(source_auction_id)).auction.periods]
		except Exception: sourceget_water_take_date_list = []
	foreverFairData_instance.update_trader_allocations(auction_id, period_keys, source_auction_id, sourceget_water_take_date_list)
	foreverFairData_instance.load_automatic_cp_bounds(auction_id, period_keys, source_auction_id, sourceget_water_take_date_list)
	if source_auction_id is not None:
		foreverFairData_instance.load_automatic_bids(auction_id, source_auction_id, now)

def runCurrentAuction(foreverFairData_instance: ForeverFairData, auction_id: str):
	prepare_auction_for_run(foreverFairData_instance, auction_id)
	if not foreverFairData_instance.has_active_bids(auction_id): raise ValueError("The auction cannot run because it has no bids.")
	clearing_start_time = datetime.now(timezone.utc).isoformat()
	auction_case = foreverFairData_instance.load(auction_id)
	market_result = runAuction(auction_case)
	foreverFairData_instance.save_run_results(auction_id, market_result, clearing_start_time=clearing_start_time)
	return market_result

def _compute_alphas(auction_case: AuctionCase) -> dict[tuple[str, str], float]:
	period_keys = [p.id for p in auction_case.auction.periods]
	period_index = {key: idx + 1 for idx, key in enumerate(period_keys)}
	quota: dict[tuple[str, str], float] = {}
	for trader in auction_case.participants:
		for well in auction_case.wells:
			if well.participant_id != trader.id: continue
			for period_id, alloc in trader.allocation_by_period.items():
				key = (well.id, period_id)
				quota[key] = quota.get(key, 0.0) + alloc
	bounds: dict[tuple[str, str], float] = {}
	for cp in auction_case.control_points:
		for period_id, bound in cp.bound_by_period.items():
			bounds[(cp.id, period_id)] = bound
	rf_by_cp_effect: dict[tuple[str, str], list[ResponseFactor]] = {}
	for rf in auction_case.response_factors:
		rf_by_cp_effect.setdefault((rf.control_point_id, rf.effect_period), []).append(rf)
	alphas: dict[tuple[str, str], float] = {}
	for (cp_id, period_key), upper_bound in bounds.items():
		t = period_index.get(period_key)
		if t is None: continue
		factors = [rf for rf in rf_by_cp_effect.get((cp_id, period_key), []) if period_index.get(rf.pumping_period, 0) <= t]
		sum_Fq = sum(rf.value * quota.get((rf.well_id, rf.pumping_period), 0.0) for rf in factors)
		alphas[(cp_id, period_key)] = min(1.0, upper_bound / sum_Fq) if sum_Fq > 0.0 else 1.0
	return alphas

def _compute_constraint_quotas(auction_case: AuctionCase, alphas: dict[tuple[str, str], float]) -> list[tuple[str, str, str, float, float, float]]:
	quota: dict[tuple[str, str], float] = {}
	for trader in auction_case.participants:
		for well in auction_case.wells:
			if well.participant_id != trader.id: continue
			for period_id, alloc in trader.allocation_by_period.items():
				key = (well.id, period_id)
				quota[key] = quota.get(key, 0.0) + alloc
	well_ids = [w.id for w in auction_case.wells]
	cp_ids = [cp.id for cp in auction_case.control_points]
	bid_periods = sorted({period_id for trader in auction_case.participants for period_id in trader.allocation_by_period})
	rows: list[tuple[str, str, str, float, float, float]] = []
	for well_id in well_ids:
		for period_id in bid_periods:
			q = quota.get((well_id, period_id), 0.0)
			for cp_id in cp_ids:
				alpha = alphas.get((cp_id, period_id), 1.0)
				rows.append((well_id, cp_id, period_id, alpha, q, alpha * q))
	return rows

def apply_default_bounds(foreverFairData_instance: ForeverFairData, auction_id: str) -> dict:
	auction = foreverFairData_instance.get_auction(auction_id)
	period_keys = [p.id for p in auction.periods]
	default_bounds = foreverFairData_instance.get_default_cp_bounds()
	bounds_to_write: list[tuple[str, str, float]] = []
	for cp_id, period_idx_str, bound in default_bounds:
		idx = int(period_idx_str)
		if idx < 1 or idx > len(period_keys): continue
		bounds_to_write.append((cp_id, period_keys[idx - 1], bound))
	foreverFairData_instance.set_cp_bounds_for_auction(auction_id, bounds_to_write)
	auction_case = foreverFairData_instance.load(auction_id)
	alphas = _compute_alphas(auction_case)
	foreverFairData_instance.set_cp_alphas(auction_id, alphas)
	auction_case = foreverFairData_instance.load(auction_id)
	cq_rows = _compute_constraint_quotas(auction_case, alphas)
	foreverFairData_instance.replace_constraint_quotas(auction_id, cq_rows)
	return {"bounds_applied": len(bounds_to_write), "alphas_computed": len(alphas), "constraint_quotas_computed": len(cq_rows), "auction_id": auction_id}
