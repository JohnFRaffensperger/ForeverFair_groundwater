# AuctionController.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Coordinate setup, execution, and view state for auctions.

from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from models import Auction, AuctionPeriod
from RunAuctionModule import runAuction
from services.repository import AuctionRepository
from SetupForeverFairDB import _auction_period_keys, _resolve_existing_auction_id

def SetUpAuction( repository: AuctionRepository, closed_date: str, first_water_take_date: str, last_water_take_date: str, period_length_hours: int, auction_type: str | None = None, ):
	try:
		close_dt = datetime.fromisoformat(closed_date)
		if close_dt < datetime.now(): raise ValueError("Auction closing date/time must be in the future.")
	except ValueError as e:
		if "must be in the future" in str(e): raise  # re-raise our own validation errors only
		# unparseable closed_date (freeform string) — allow through
	if datetime.fromisoformat(last_water_take_date) < datetime.fromisoformat(first_water_take_date): raise ValueError("lastWaterTakeDate must be on or after firstWaterTakeDate")
	if int(period_length_hours) <= 0: raise ValueError("period_length_hours must be positive")
	return _setup_auction(repository, closed_date=closed_date, first_water_take_date=first_water_take_date, last_water_take_date=last_water_take_date, period_length_hours=int(period_length_hours), auction_type=auction_type,)

def ResetAuctionData(repository: AuctionRepository): return repository.reset_runtime_to_seed()

def runCurrentAuction(repository: AuctionRepository, auction_id: str):
	# Finish populating auction data (allocations, bounds, standing bids) deferred from creation.
	_prepare_auction_for_run(repository, auction_id)
	if not repository.has_active_bids(auction_id): raise ValueError("The auction cannot run because it has no bids.")
	clearing_start_time = datetime.now(timezone.utc).isoformat()
	auction_case = repository.load(auction_id)
	market_result = runAuction(auction_case)
	repository.save_run_results(auction_id, market_result, clearing_start_time=clearing_start_time)
	return market_result

def getTraderPageState(repository: AuctionRepository, auction_id: str | None = None, participant_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	current_participant = next((p for p in auction_case.participants if p.id == (participant_id or auction_case.current_participant_id)), auction_case.participants[0])
	current_wells = repository.wells_for_participant(auction_case, current_participant)
	current_well = current_wells[0] if current_wells else None
	bid_history = repository.bid_history(auction_case.auction.id, current_participant.id)
	
	period_rows = []
	for period in auction_case.auction.periods:
		period_key = str(period.id)
		quota = repository.get_quota(participant_id=current_participant.id, period_start=period_key, auction_id=auction_case.auction.id,).get(period_key, [0.0, 0.0])
		period_rows.append({"period_id": period.id, "period_key": period_key, "period_label": period.label, "allocation": quota[1],})
	return {"auction_case": auction_case, "current_participant": current_participant, "current_well": current_well, "bid_history": bid_history, "period_rows": period_rows, "auction_id": auction_case.auction.id,}

def getManagerPageState(repository: AuctionRepository) -> dict:
	period_length_hours = repository.latest_period_length_hours()
	response_period_count = repository.response_matrix_period_count()
	return {"auctions": repository.list_auctions(), "period_length_hours": period_length_hours, "response_period_count": response_period_count,}

def getCatchmentPageState(repository: AuctionRepository, auction_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	well_price_rows, control_point_rows = repository.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

def getSystemState(repository: AuctionRepository, auction_id: str | None = None) -> dict:
	auction_case = repository.load(auction_id)
	latest_run = repository.latest_run_summary(auction_case.auction.id)
	well_price_rows, control_point_rows = repository.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "rights_conversion": auction_case.rights_conversion.model_dump(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

def _compute_alphas(conn: sqlite3.Connection, auction_id: str) -> dict:
	auction_row = conn.execute("SELECT firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
	if auction_row is None: return {}
	period_hours = int(auction_row[2] or 0)
	if not auction_row[0] or not auction_row[1] or period_hours <= 0: return {}
	period_keys = _auction_period_keys(str(auction_row[0]), str(auction_row[1]), period_hours)
	period_index = {key: idx + 1 for idx, key in enumerate(period_keys)}
	quota: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT w.well_id, ta.period_id, ta.allocation FROM wells w JOIN trader_allocations ta ON ta.trader_id = w.trader_id WHERE ta.auction_id = ?", (auction_id,),).fetchall():
		key = (str(row[0]), str(row[1]))
		quota[key] = quota.get(key, 0.0) + float(row[2])
	bounds: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT control_point_id, period_id, bound FROM control_point_bounds WHERE auction_id = ?", (auction_id,),).fetchall(): bounds[(str(row[0]), str(row[1]))] = float(row[2])
	alphas: dict[tuple[str, str], float] = {}
	for (cp_id, period_key), upper_bound in bounds.items():
		t = period_index.get(period_key)
		if t is None: continue
		factors = conn.execute("SELECT well_id, pumping_period, factor_value FROM response_matrix WHERE control_point_id = ? AND effect_period = ? AND pumping_period <= ?", (cp_id, t, t),).fetchall()
		sum_Fq = sum(float(row[2]) * quota.get((str(row[0]), period_keys[int(row[1]) - 1]), 0.0) for row in factors if 1 <= int(row[1]) <= len(period_keys))
		alphas[(cp_id, period_key)] = min(1.0, upper_bound / sum_Fq) if sum_Fq > 0.0 else 1.0
	return alphas

def _compute_constraint_quotas(conn: sqlite3.Connection, auction_id: str) -> int:
	quota: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT w.well_id, ta.period_id, ta.allocation FROM wells w JOIN trader_allocations ta ON ta.trader_id = w.trader_id WHERE ta.auction_id = ?", (auction_id,),).fetchall():
		key = (str(row[0]), str(row[1]))
		quota[key] = quota.get(key, 0.0) + float(row[2])
	alpha_lookup: dict[tuple[str, str], float] = {}
	for row in conn.execute("SELECT cpb.control_point_id, cpb.period_id, cpb.alpha FROM control_point_bounds cpb WHERE cpb.auction_id = ?", (auction_id,),).fetchall():
		alpha_lookup[(str(row[0]), str(row[1]))] = float(row[2]) if row[2] is not None else 1.0
	well_ids = [str(r[0]) for r in conn.execute("SELECT well_id FROM wells").fetchall()]
	cp_ids = [str(r[0]) for r in conn.execute("SELECT control_point_id FROM control_points").fetchall()]
	bid_periods = [str(r[0]) for r in conn.execute("SELECT DISTINCT period_id FROM trader_allocations WHERE auction_id = ? ORDER BY period_id", (auction_id,),).fetchall()]
	conn.execute("DELETE FROM constraint_quota WHERE auction_id = ?", (auction_id,))
	count = 0
	for well_id in well_ids:
		for period_id in bid_periods:
			q = quota.get((well_id, period_id), 0.0)
			for cp_id in cp_ids:
				alpha = alpha_lookup.get((cp_id, period_id), 1.0)
				conn.execute("INSERT INTO constraint_quota (auction_id, well_id, control_point_id, period_id, alpha, quota_value, constraint_quota_value) VALUES (?, ?, ?, ?, ?, ?, ?)", (auction_id, well_id, cp_id, period_id, alpha, q, alpha * q),)
				count += 1
	return count

def apply_default_bounds(repository: AuctionRepository, auction_id: str) -> dict:
	with repository._connect() as conn:
		auction_id = _resolve_existing_auction_id(conn, auction_id)
		auction_row = conn.execute("SELECT firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
		if auction_row is None or not auction_row[0] or not auction_row[1] or not auction_row[2]:
			raise ValueError("Auction must have firstWaterTakeDate, lastWaterTakeDate, and period_length_hours before applying default bounds.")
		period_keys = repository._period_keys(str(auction_row[0]), str(auction_row[1]), int(auction_row[2]))
		rows = conn.execute("SELECT control_point_id, period_id, bound FROM default_control_point_bounds").fetchall()
		count = 0
		for cp_id, period_id, bound in rows:
			idx = int(period_id)
			if idx < 1 or idx > len(period_keys): continue
			conn.execute("INSERT OR REPLACE INTO control_point_bounds(auction_id, control_point_id, period_id, bound) VALUES (?,?,?,?)", (auction_id, cp_id, period_keys[idx - 1], bound),)
			count += 1
		alphas = _compute_alphas(conn, auction_id)
		for (cp_id, period_id), alpha in alphas.items():
			conn.execute("UPDATE control_point_bounds SET alpha = ? WHERE auction_id = ? AND control_point_id = ? AND period_id = ?", (alpha, auction_id, cp_id, period_id),)
		cq_count = _compute_constraint_quotas(conn, auction_id)
	return {"bounds_applied": count, "alphas_computed": len(alphas), "constraint_quotas_computed": cq_count, "auction_id": auction_id}

def _setup_auction(repository: AuctionRepository, closed_date: str, first_water_take_date: str, last_water_take_date: str, period_length_hours: int, auction_type: str | None = None,) -> Auction:
	now = datetime.now(timezone.utc).isoformat()
	period_keys = repository._period_keys(first_water_take_date, last_water_take_date, int(period_length_hours))
	if not period_keys: raise ValueError("No auction periods generated from first/last water take dates and period length")
	with repository._connect() as conn:
		cursor = conn.execute("INSERT INTO auctions(status, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type) VALUES ('OPEN', ?, ?, ?, ?, ?, ?)", (now, closed_date, first_water_take_date, last_water_take_date, int(period_length_hours), auction_type),)
		auction_id_str = str(int(cursor.lastrowid))
	return Auction(id=auction_id_str, status="OPEN", periods=[AuctionPeriod(id=pk, label=pk) for pk in period_keys], closed_date=closed_date, first_water_take_date=first_water_take_date, last_water_take_date=last_water_take_date, period_length_hours=int(period_length_hours), auction_type=auction_type,)

def _prepare_auction_for_run(repository: AuctionRepository, auction_id: str) -> None:
	now = datetime.now(timezone.utc).isoformat()
	with repository._connect() as conn:
		auction_row = conn.execute("SELECT firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
		if auction_row is None: raise ValueError(f"Auction {auction_id} not found")
		period_keys = repository._period_keys(str(auction_row["firstWaterTakeDate"]), str(auction_row["lastWaterTakeDate"]), int(auction_row["period_length_hours"]))
		source_auction_id = repository._default_auction_id(conn, exclude_auction_id=auction_id)
		source_period_keys: list[str] = []
		if source_auction_id is not None:
			try: source_period_keys = [p.id for p in repository.load(str(source_auction_id)).auction.periods]
			except Exception: source_period_keys = []
		traders = conn.execute("SELECT trader_id FROM traders").fetchall()
		for trader_row in traders:
			for idx, period_id in enumerate(period_keys):
				allocation = 0.0
				if source_auction_id is not None:
					source_key = source_period_keys[idx] if idx < len(source_period_keys) else period_id
					row = conn.execute("SELECT allocation FROM trader_allocations WHERE auction_id=? AND trader_id=? AND period_id=?", (source_auction_id, trader_row["trader_id"], source_key)).fetchone()
					allocation = float(row["allocation"]) if row else 0.0
				conn.execute("INSERT OR IGNORE INTO trader_allocations(auction_id, trader_id, period_id, allocation) VALUES (?, ?, ?, ?)", (auction_id, trader_row["trader_id"], period_id, allocation),)
		cps = conn.execute("SELECT control_point_id FROM control_points").fetchall()
		for cp_row in cps:
			for idx, period_id in enumerate(period_keys):
				bound = 0.0
				if source_auction_id is not None:
					source_key = source_period_keys[idx] if idx < len(source_period_keys) else period_id
					row = conn.execute("SELECT bound FROM control_point_bounds WHERE auction_id=? AND control_point_id=? AND period_id=?", (source_auction_id, cp_row["control_point_id"], source_key)).fetchone()
					bound = float(row["bound"]) if row else 0.0
				conn.execute("INSERT OR IGNORE INTO control_point_bounds(auction_id, control_point_id, period_id, bound) VALUES (?, ?, ?, ?)", (auction_id, cp_row["control_point_id"], period_id, bound),)
		if source_auction_id is not None:
			standing = conn.execute("SELECT trader_id, well_id, effect_date, qty1, price1 FROM trader_bids WHERE auction_id=? AND is_bid_automatic=1 AND deleted=0", (source_auction_id,)).fetchall()
			for s in standing:
				conn.execute("UPDATE trader_bids SET deleted=1 WHERE auction_id=? AND trader_id=? AND effect_date=? AND deleted=0", (auction_id, s["trader_id"], s["effect_date"]),)
				conn.execute("INSERT OR IGNORE INTO trader_bids(auction_id, trader_id, well_id, bid_date, effect_date, qty1, price1, is_bid_automatic, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)", (auction_id, s["trader_id"], s["well_id"], now, s["effect_date"], s["qty1"], s["price1"]),)
