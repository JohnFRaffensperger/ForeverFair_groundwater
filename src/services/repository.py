# services/repository.py. Claude guided by JFR, 2026 04 21.
# Purpose: Persist auction data and run results in SQLite.

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from models import (Auction, AuctionPeriod, BidSegment, ControlPoint, AuctionCase, Participant, MarketResult, ResponseFactor, RightsConversion, Well,)
from setup import SCHEMA_DDL

class AuctionRepository:
	def __init__(self, seed_path: Path, db_path: Path | None = None, runtime_path: Path | None = None):
		self.seed_path = seed_path
		self.db_path = db_path or runtime_path or (seed_path.parent / "groundwater_market.db")
		self._ensure_db()

	def _connect(self) -> sqlite3.Connection:
		conn = sqlite3.connect(self.db_path)
		conn.row_factory = sqlite3.Row
		conn.execute("PRAGMA foreign_keys = ON")
		return conn

	def _ensure_db(self) -> None:
		if not self.db_path.exists(): self.db_path.parent.mkdir(parents=True, exist_ok=True)
		with self._connect() as conn:
			conn.executescript(SCHEMA_DDL)

	def _extract_row_col(self, location_note: str | None) -> tuple[int | None, int | None]:
		if not location_note: return None, None
		parts = location_note.replace(",", " ").split()
		try:
			row_idx = parts.index("row")
			col_idx = parts.index("col")
			return int(parts[row_idx + 1]), int(parts[col_idx + 1])
		except Exception:
			return None, None

	def _next_monday(self, base_dt: datetime) -> datetime:
		days_ahead = (7 - base_dt.weekday()) % 7
		if days_ahead == 0: days_ahead = 7
		return (base_dt + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

	def _period_keys(self, first_water_take_date: str, last_water_take_date: str, period_length_hours: int) -> list[str]:
		if int(period_length_hours) <= 0: return []
		start_dt = datetime.fromisoformat(str(first_water_take_date))
		end_dt = datetime.fromisoformat(str(last_water_take_date))
		if end_dt < start_dt: return []
		step = timedelta(hours=int(period_length_hours))
		keys: list[str] = []
		current = start_dt
		while current <= end_dt:
			keys.append(current.isoformat(timespec="minutes"))
			current += step
		return keys

	def _seed_from_json(self, conn: sqlite3.Connection) -> None:
		seed_payload = json.loads(self.seed_path.read_text(encoding="utf-8-sig"))
		now = datetime.now(timezone.utc).isoformat()

		trader_id_map: dict[str, int] = {}
		for idx, trader in enumerate(seed_payload["traders"], start=1):
			external_trader_id = str(trader.get("id", "")).strip()
			trader_id_map[external_trader_id] = int(idx)
			conn.execute("INSERT INTO traders(trader_id, name_tag) VALUES (?, ?)", (int(idx), trader["name"]))

		meta_values = {"catchment_name": seed_payload["catchment_name"], "source_note": "Data provenance: Tianqiao groundwater archive source.", "current_trader_id": str(trader_id_map.get(str(seed_payload.get("current_trader_id", "")).strip(), 1)), "rights_policy_name": seed_payload["rights_conversion"]["policy_name"], "rights_policy_summary": seed_payload["rights_conversion"]["summary"],}
		for key, value in meta_values.items():
			conn.execute("INSERT INTO metadata(meta_key, meta_value) VALUES (?, ?)", (key, value))

		well_id_map: dict[str, int] = {}
		for idx, well in enumerate(seed_payload["wells"], start=1):
			gw_row, gw_col = self._extract_row_col(well.get("location_note"))
			external_well_id = str(well.get("id", "")).strip()
			well_id_map[external_well_id] = int(idx)
			well_name = str(well.get("name") or external_well_id)
			mapped_trader_id = trader_id_map.get(str(well.get("trader_id", "")).strip())
			conn.execute("INSERT INTO wells(well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (int(idx), well_name, mapped_trader_id, None, gw_row, gw_col, None, None),)

		for cp in seed_payload["control_points"]:
			conn.execute("INSERT INTO control_points(control_point_id, name, gw_model_row, gw_model_column, latitude, longitude)" " VALUES (?, ?, ?, ?, ?, ?)", (cp["id"], cp["name"], None, None, None, None),)

		auction = seed_payload["auction"]
		period_length_hours = int(float(conn.execute("SELECT COALESCE(MAX(period_length_hours), 168) FROM response_matrix_info").fetchone()[0] or 168))
		close_dt = datetime.now() + timedelta(days=1)
		first_dt = self._next_monday(close_dt)
		last_dt = first_dt + timedelta(days=28)
		period_keys = self._period_keys(first_dt.isoformat(timespec="minutes"), last_dt.isoformat(timespec="minutes"), period_length_hours)
		cursor = conn.execute("INSERT INTO auctions(status, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type) VALUES ('OPEN', ?, ?, ?, ?, ?, ?)", (now, close_dt.isoformat(timespec="minutes"), first_dt.isoformat(timespec="minutes"), last_dt.isoformat(timespec="minutes"), period_length_hours, auction.get("auction_type")),)
		auction_id = str(int(cursor.lastrowid))

		for trader in seed_payload["traders"]:
			mapped_trader_id = trader_id_map.get(str(trader.get("id", "")).strip())
			if mapped_trader_id is None: continue
			for idx, (period_key, allocation) in enumerate(trader["allocation_by_period"].items()):
				if idx < len(period_keys): conn.execute("INSERT INTO trader_allocations(auction_id, trader_id, period_id, allocation) VALUES (?, ?, ?, ?)", (auction_id, mapped_trader_id, period_keys[idx], allocation),)

		for cp in seed_payload["control_points"]:
			for idx, (period_key, bound) in enumerate(cp["bound_by_period"].items()):
				if idx < len(period_keys): conn.execute("INSERT INTO control_point_bounds(auction_id, control_point_id, period_id, bound) VALUES (?, ?, ?, ?)", (auction_id, cp["id"], period_keys[idx], bound),)

		for factor in seed_payload["response_factors"]:
			pump_idx = int(factor["pumping_period"].replace("W", ""))
			effect_idx = int(factor["effect_period"].replace("W", ""))
			mapped_well_id = well_id_map.get(str(factor["well_id"]))
			if mapped_well_id is None: continue
			conn.execute("INSERT INTO response_matrix(well_id, control_point_id, pumping_period, effect_period, factor_value) VALUES (?, ?, ?, ?, ?)", (mapped_well_id, factor["control_point_id"], pump_idx, effect_idx, factor["value"],),)

		for bid in seed_payload["bids"]:
			bid_period_idx = int(bid["period_id"].replace("W", ""))
			if bid_period_idx < 1 or bid_period_idx > len(period_keys): continue
			mapped_trader_id = trader_id_map.get(str(bid.get("trader_id", "")).strip())
			if mapped_trader_id is None: continue
			mapped_well_id = well_id_map.get(str(bid["well_id"]))
			if mapped_well_id is None: continue
			conn.execute("INSERT INTO trader_bids(auction_id, trader_id, well_id, bid_date, effect_date, qty1, price1, deleted) " "VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, mapped_trader_id, mapped_well_id, now, period_keys[bid_period_idx - 1], bid["quantity"], bid["price"]),)
	def _clear_all_data(self, conn: sqlite3.Connection) -> None:
		for table_name in ["constraint_results", "trader_bids", "control_point_event", "trader_quota", "trader_license", "response_matrix", "response_matrix_info", "control_point_bounds", "default_control_point_bounds", "trader_allocations", "auctions", "control_points", "wells", "traders", "metadata", ]:
			conn.execute(f"DELETE FROM {table_name}")

	def _meta(self, conn: sqlite3.Connection, key: str) -> str:
		row = conn.execute("SELECT meta_value FROM metadata WHERE meta_key=?", (key,)).fetchone()
		return "" if row is None else row["meta_value"]

	def _default_auction_id(self, conn: sqlite3.Connection) -> str | None:
		row = conn.execute("SELECT auction_id FROM auctions ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, created_date DESC LIMIT 1" ).fetchone()
		if row is None: return None
		return row["auction_id"]

	def current_participant_id(self) -> str:
		with self._connect() as conn:
			return self._meta(conn, "current_trader_id")

	def latest_period_length_hours(self) -> int | None:
		with self._connect() as conn:
			row = conn.execute("SELECT period_length_hours FROM response_matrix_info ORDER BY rmi_id DESC LIMIT 1").fetchone()
			if row is None or row["period_length_hours"] is None: return None
			return int(float(row["period_length_hours"]))

	def response_matrix_period_count(self) -> int:
		with self._connect() as conn:
			row = conn.execute("SELECT COALESCE(MAX(effect_period), 0) AS n FROM response_matrix").fetchone()
			return int(row["n"] or 0)

	def load(self, auction_id: str | None = None) -> AuctionCase:
		with self._connect() as conn:
			auction_id = auction_id or self._default_auction_id(conn)
			auction_row = conn.execute("SELECT auction_id, status, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type, solve_status, objective_value FROM auctions WHERE auction_id=?", (auction_id,) ).fetchone()
			if auction_row is None: raise ValueError(f"Unknown auction_id: {auction_id}")

			period_keys = []
			if auction_row["firstWaterTakeDate"] and auction_row["lastWaterTakeDate"] and auction_row["period_length_hours"]: period_keys = self._period_keys(str(auction_row["firstWaterTakeDate"]), str(auction_row["lastWaterTakeDate"]), int(auction_row["period_length_hours"]))
			periods = [AuctionPeriod(id=pk, label=pk) for pk in period_keys]
			if not periods: raise ValueError("Auction has no computed periods. Set firstWaterTakeDate, lastWaterTakeDate, and period_length_hours.")
			period_map = {idx + 1: p.id for idx, p in enumerate(periods)}

			traders = []
			for trader_row in conn.execute("SELECT trader_id, name_tag FROM traders ORDER BY trader_id").fetchall():
				alloc_map = {}
				for row in conn.execute("SELECT period_id, allocation FROM trader_allocations WHERE auction_id=? AND trader_id=?", (auction_id, trader_row["trader_id"]),).fetchall(): alloc_map[str(row["period_id"])] = float(row["allocation"] or 0.0)
				traders.append(Participant(id=str(trader_row["trader_id"]), name=trader_row["name_tag"], allocation_by_period=alloc_map,))

			wells = [Well(id=str(row["well_id"]), name=row["name"], participant_id=str(row["trader_id"]) if row["trader_id"] is not None else "", gw_model_layer=row["gw_model_layer"], gw_model_row=row["gw_model_row"], gw_model_column=row["gw_model_column"], latitude=row["latitude"], longitude=row["longitude"],) for row in conn.execute("SELECT well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude" " FROM wells ORDER BY well_id" ).fetchall()]

			control_points = []
			for cp_row in conn.execute("SELECT control_point_id, name, gw_model_row, gw_model_column, latitude, longitude FROM control_points ORDER BY control_point_id").fetchall():
				# Read bounds from control_point_bounds (authoritative: written by both seed and apply_default_bounds)
				bound_map = {}
				for row in conn.execute("SELECT period_id, bound FROM control_point_bounds WHERE auction_id=? AND control_point_id=?", (auction_id, cp_row["control_point_id"]), ).fetchall():
					bound_map[str(row["period_id"])] = float(row["bound"])
				control_points.append(ControlPoint(id=cp_row["control_point_id"], name=cp_row["name"], bound_by_period=bound_map, gw_model_row=cp_row["gw_model_row"], gw_model_column=cp_row["gw_model_column"], latitude=cp_row["latitude"], longitude=cp_row["longitude"],))

			# Read response factors from response_matrix table (authoritative: written by seed and import_mps)
			response_factors = [ResponseFactor(well_id=str(row["well_id"]), control_point_id=str(row["control_point_id"]), pumping_period=period_map.get(int(row["pumping_period"]), str(row["pumping_period"])), effect_period=period_map.get(int(row["effect_period"]), str(row["effect_period"])), value=float(row["factor_value"] or 0.0),) for row in conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, factor_value FROM response_matrix" ).fetchall()]

			# Read trader_bids rows (supports qty1-qty5 steps per row)
			bids: list[BidSegment] = []
			for row in conn.execute("SELECT bid_id, trader_id, well_id, effect_date, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 " "FROM trader_bids WHERE auction_id=? AND deleted=0 ORDER BY bid_id", (auction_id,), ).fetchall():
				period_id = str(row["effect_date"] or "")
				submitted_at = row["bid_date"]
				# Create one BidSegment per active step (qty/price non-null)
				for step_num in range(1, 6):
					qty_col = f"qty{step_num}"
					price_col = f"price{step_num}"
					qty = row[qty_col]
					price = row[price_col]
					if qty is None or price is None: continue
					bids.append(BidSegment(id=f"bid-{row['bid_id']}-s{step_num}", participant_id=str(row["trader_id"]), well_id=str(row["well_id"]), period_id=period_id, quantity=float(qty), price=float(price), submitted_at=submitted_at,))

			return AuctionCase(catchment_name=self._meta(conn, "catchment_name"), source_note=self._meta(conn, "source_note"), current_participant_id=self._meta(conn, "current_trader_id"), auction=Auction(id=str(auction_row["auction_id"]), status=auction_row["status"], periods=periods, closed_date=auction_row["closed_date"], first_water_take_date=auction_row["firstWaterTakeDate"], last_water_take_date=auction_row["lastWaterTakeDate"], period_length_hours=auction_row["period_length_hours"], auction_type=auction_row["auction_type"], solve_status=auction_row["solve_status"], objective_value=auction_row["objective_value"]), participants=traders, wells=wells, control_points=control_points, response_factors=response_factors, bids=bids, rights_conversion=RightsConversion(policy_name=self._meta(conn, "rights_policy_name"), summary=self._meta(conn, "rights_policy_summary"),),)

	def current_participant(self, auction_case: AuctionCase) -> Participant: return next(p for p in auction_case.participants if p.id == auction_case.current_participant_id)

	def wells_for_participant(self, auction_case: AuctionCase, participant: Participant) -> list[Well]: return [well for well in auction_case.wells if well.participant_id == participant.id]

	def list_participants(self) -> list[dict]:
		with self._connect() as conn:
			return [{"id": str(row["trader_id"]), "name": row["name_tag"]} for row in conn.execute("SELECT trader_id, name_tag FROM traders ORDER BY name_tag").fetchall()]

	def _parse_bid_id(self, bid_id: str) -> int:
		tail = bid_id.split("-")[-1]
		if "-s" in bid_id: tail = bid_id.split("-")[-2]
		return int(tail)

	def add_bid(self, auction_id: str, participant_id: str, well_id: str, period_id: str, quantity: float, price: float, is_automatic: bool = False, bid_steps: list[tuple[float, float]] | None = None) -> BidSegment:
		now = datetime.now(timezone.utc).isoformat()
		steps = bid_steps if bid_steps is not None else [(quantity, price)]
		if not steps: raise ValueError("At least one bid step is required.")
		if len(steps) > 5: raise ValueError("At most 5 bid steps are supported.")
		qty_values: list[float | None] = [None] * 5
		price_values: list[float | None] = [None] * 5
		for idx, (step_qty, step_price) in enumerate(steps):
			qty_values[idx] = float(step_qty)
			price_values[idx] = float(step_price)
		with self._connect() as conn:
			# C.8: one active bid per (trader, auction, period) — soft-delete any existing before inserting.
			participant_id_int = int(participant_id)
			conn.execute("UPDATE trader_bids SET deleted=1 WHERE auction_id=? AND trader_id=? AND effect_date=? AND deleted=0", (auction_id, participant_id_int, str(period_id)),)
			cursor = conn.execute("INSERT INTO trader_bids(" "auction_id, trader_id, well_id, bid_date, effect_date, " "qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, " "is_bid_automatic, deleted" ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, participant_id_int, int(well_id), now, str(period_id), qty_values[0], price_values[0], qty_values[1], price_values[1], qty_values[2], price_values[2], qty_values[3], price_values[3], qty_values[4], price_values[4], 1 if is_automatic else 0,),)
			bid_pk = int(cursor.lastrowid)
			return BidSegment(id=f"bid-{bid_pk}-s1", participant_id=participant_id, well_id=well_id, period_id=period_id, quantity=float(steps[0][0]), price=float(steps[0][1]), submitted_at=now,)

	def delete_bid(self, bid_id: str, current_participant_id: str) -> bool:
		bid_pk = self._parse_bid_id(bid_id)
		with self._connect() as conn:
			row = conn.execute("SELECT trader_id, deleted FROM trader_bids WHERE bid_id=?", (bid_pk,)).fetchone()
			if row is None: return False
			if row["deleted"] == 1 or str(row["trader_id"]) != str(current_participant_id): return False
			conn.execute("UPDATE trader_bids SET deleted=1 WHERE bid_id=?", (bid_pk,))
			return True

	def reset_runtime_to_seed(self) -> AuctionCase:
		with self._connect() as conn:
			self._clear_all_data(conn)
			self._seed_from_json(conn)
		return self.load()

	def setup_auction(self, auction_id: str | None, closed_date: str, first_water_take_date: str, last_water_take_date: str, period_length_hours: int, clear_existing_bids: bool = True, auction_type: str | None = None) -> Auction:
		now = datetime.now(timezone.utc).isoformat()
		period_keys = self._period_keys(first_water_take_date, last_water_take_date, int(period_length_hours))
		if not period_keys: raise ValueError("No auction periods generated from first/last water take dates and period length")
		source_period_keys: list[str] = []
		with self._connect() as conn:
			existing_id = str(auction_id).strip() if auction_id is not None else ""
			source_auction_id = self._default_auction_id(conn)
			if source_auction_id is not None:
				try: source_period_keys = [p.id for p in self.load(str(source_auction_id)).auction.periods]
				except Exception: source_period_keys = []

			if existing_id:
				conn.execute("UPDATE auctions SET closed_date=?, firstWaterTakeDate=?, lastWaterTakeDate=?, period_length_hours=?, auction_type=? WHERE auction_id=?", (closed_date, first_water_take_date, last_water_take_date, int(period_length_hours), auction_type, existing_id),)
				auction_id_str = existing_id
			else:
				cursor = conn.execute("INSERT INTO auctions(status, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type) VALUES ('OPEN', ?, ?, ?, ?, ?, ?)", (now, closed_date, first_water_take_date, last_water_take_date, int(period_length_hours), auction_type),)
				auction_id_str = str(int(cursor.lastrowid))

			traders = conn.execute("SELECT trader_id FROM traders").fetchall()
			for trader_row in traders:
				for idx, period_id in enumerate(period_keys):
					allocation = 0.0
					if source_auction_id is not None:
						source_key = source_period_keys[idx] if idx < len(source_period_keys) else period_id
						row = conn.execute("SELECT allocation FROM trader_allocations WHERE auction_id=? AND trader_id=? AND period_id=?", (source_auction_id, trader_row["trader_id"], source_key), ).fetchone()
						allocation = float(row["allocation"]) if row else 0.0
					conn.execute("INSERT INTO trader_allocations(auction_id, trader_id, period_id, allocation) VALUES (?, ?, ?, ?)", (auction_id_str, trader_row["trader_id"], period_id, allocation),)

			cps = conn.execute("SELECT control_point_id FROM control_points").fetchall()
			for cp_row in cps:
				for idx, period_id in enumerate(period_keys):
					bound = 0.0
					if source_auction_id is not None:
						source_key = source_period_keys[idx] if idx < len(source_period_keys) else period_id
						row = conn.execute("SELECT bound FROM control_point_bounds WHERE auction_id=? AND control_point_id=? AND period_id=?", (source_auction_id, cp_row["control_point_id"], source_key), ).fetchone()
						bound = float(row["bound"]) if row else 0.0
					conn.execute("INSERT INTO control_point_bounds(auction_id, control_point_id, period_id, bound) VALUES (?, ?, ?, ?)", (auction_id_str, cp_row["control_point_id"], period_id, bound),)

			for idx, period_id in enumerate(period_keys):
				period_num = idx + 1
				rows = conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, factor_value FROM response_matrix WHERE pumping_period=? AND effect_period=?", (period_num, period_num), ).fetchall()
				for row in rows:
					conn.execute("INSERT OR REPLACE INTO response_matrix(well_id, control_point_id, pumping_period, effect_period, factor_value) VALUES (?, ?, ?, ?, ?)", (row["well_id"], row["control_point_id"], row["pumping_period"], row["effect_period"], float(row["factor_value"]),),)

			# C.7: Always carry forward standing bids (isBidAutomatic=1) from most recent previous auction.
			if source_auction_id is not None:
				standing = conn.execute("SELECT trader_id, well_id, effect_date, qty1, price1 FROM trader_bids " "WHERE auction_id=? AND is_bid_automatic=1 AND deleted=0", (source_auction_id,), ).fetchall()
				for s in standing:
					# Soft-delete any existing bid for same (trader, period) in the new auction first.
					conn.execute("UPDATE trader_bids SET deleted=1 WHERE auction_id=? AND trader_id=? AND effect_date=? AND deleted=0", (auction_id_str, s["trader_id"], s["effect_date"]),)
					conn.execute("INSERT INTO trader_bids(auction_id, trader_id, well_id, bid_date, effect_date, qty1, price1, is_bid_automatic, deleted) " "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)", (auction_id_str, s["trader_id"], s["well_id"], now, s["effect_date"], s["qty1"], s["price1"]),)

		return Auction(id=auction_id_str, status="OPEN", periods=[AuctionPeriod(id=pk, label=pk) for pk in period_keys], closed_date=closed_date, first_water_take_date=first_water_take_date, last_water_take_date=last_water_take_date, period_length_hours=int(period_length_hours), auction_type=auction_type,)

	def list_auctions(self) -> list[dict]:
		with self._connect() as conn:
			rows = conn.execute("SELECT auction_id, status, auction_type, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, solve_status, objective_value FROM auctions ORDER BY created_date DESC").fetchall()
			return [dict(row) for row in rows]

	def save_run_results(self, auction_id: str, market_result: MarketResult, clearing_start_time: str | None = None) -> str:
		run_at = datetime.now(timezone.utc).isoformat()
		with self._connect() as conn:
			conn.execute("UPDATE auctions SET status='CLOSED', closed_date=COALESCE(closed_date, ?), solve_status=?, objective_value=? WHERE auction_id=?", (run_at, market_result.solve_status, float(market_result.objective_value), auction_id),)

			# Map trader-period results to trader_quota rows (one per trader per period)
			for tpr in market_result.trader_period_results:
				participant_id = str(tpr.participant_id)
				period_id_str = str(tpr.period_id)
				initial_alloc = float(tpr.initial_allocation)
				accepted_qty = float(tpr.accepted_quantity)

				# Find first well for this trader (simplified: don't try to match per-bid wells)
				first_well = conn.execute("SELECT well_id FROM wells WHERE trader_id=? LIMIT 1", (int(participant_id),)).fetchone()
				well_id = int(first_well['well_id']) if first_well else 0
				period_price = float(market_result.well_period_prices.get(f"{well_id}_{period_id_str}", 0.0))
				conn.execute("INSERT INTO trader_quota(trader_id, auction_id, well_id, quota_auction_start, quota_adjusted, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (int(participant_id), auction_id, well_id, initial_alloc, accepted_qty, accepted_qty, period_price, period_id_str),)

			for item in market_result.constraint_results:
				period_key = str(item.period_id)
				conn.execute("INSERT INTO constraint_results(auction_id, control_point_id, period_id, used_capacity, bound_capacity, dual_value) VALUES (?, ?, ?, ?, ?, ?)", (auction_id, item.control_point_id, period_key, float(item.used_capacity), float(item.bound_capacity), float(item.dual_value),),)
				
				# D.2: read pre-computed alpha from control_point_bounds
				alpha_val = None
				alpha_row = conn.execute("SELECT alpha FROM control_point_bounds WHERE auction_id=? AND control_point_id=? AND period_id=?", (auction_id, item.control_point_id, period_key),).fetchone()
				if alpha_row and alpha_row["alpha"] is not None: alpha_val = float(alpha_row["alpha"])
				conn.execute("INSERT INTO control_point_event(auction_id, control_point_id, effect_date, head_constraint_upper_bound, slack, dual_price, alpha) VALUES (?, ?, ?, ?, ?, ?, ?)", (auction_id, item.control_point_id, str(item.period_id), float(item.bound_capacity), float(item.bound_capacity) - float(item.used_capacity), float(item.dual_value), alpha_val),)
			return auction_id

	def latest_run_summary(self, auction_id: str) -> dict | None:
		with self._connect() as conn:
			row = conn.execute("SELECT auction_id, solve_status, objective_value, closed_date FROM auctions WHERE auction_id=? AND solve_status IS NOT NULL", (auction_id,)).fetchone()
			if row is None: return None
			return dict(row)

	def has_active_bids(self, auction_id: str) -> bool:
		"""Return True if auction has at least one non-deleted trader_bids row."""
		with self._connect() as conn:
			row = conn.execute("SELECT 1 FROM trader_bids WHERE auction_id=? AND deleted=0 LIMIT 1", (str(auction_id),),).fetchone()
			return row is not None

	def bid_history(self, auction_id: str, participant_id: str) -> list[dict]:
		"""Fetch bid history from trader_bids joined to trader_quota for final allocation and traded price."""
		with self._connect() as conn:
			rows = conn.execute("SELECT bid_id, effect_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, bid_date, is_bid_automatic FROM trader_bids " "WHERE auction_id=? AND trader_id=? AND deleted=0 ORDER BY bid_id DESC", (auction_id, int(participant_id)), ).fetchall()
			# Pre-fetch trader_quota rows keyed by takeDate for this trader/auction
			quota_rows = conn.execute("SELECT take_date, quota_auction_end, price FROM trader_quota WHERE auction_id=? AND trader_id=?", (auction_id, int(participant_id)), ).fetchall()
			quota_by_date = {str(r["take_date"]): r for r in quota_rows}
			result = []
			for row in rows:
				effect_date = str(row["effect_date"] or "")
				quota = quota_by_date.get(effect_date)
				final_allocation = float(quota["quota_auction_end"]) if quota and quota["quota_auction_end"] is not None else None
				traded_price = float(quota["price"]) if quota and quota["price"] is not None else None
				for step_num in range(1, 6):
					qty = row[f"qty{step_num}"]
					price = row[f"price{step_num}"]
					if qty is None or price is None: continue
					result.append({"bid_id": f"bid-{row['bid_id']}-s{step_num}", "period_id": effect_date, "quantity": float(qty), "price": float(price), "submitted_at": row["bid_date"], "final_allocation": final_allocation, "traded_price": traded_price, "is_automatic": bool(row["is_bid_automatic"]) if row["is_bid_automatic"] is not None else False, })
			return result

	def get_quota(self, participant_id: str, period_start: str, period_end: str | None = None, auction_id: str | None = None) -> dict[str, list[float]]:
		"""Return {period: [licensed_allocation, final_quota]} for the participant.

		Implemented against trader_license and trader_quota. This implementation assumes
		one license row and one final-quota row per participant-period; if duplicate
		rows exist, the first row for that period is used.
		"""
		period_end = period_end if period_end is not None else period_start
		result: dict[str, list[float]] = {}
		with self._connect() as conn:
			auction_id = auction_id or self._default_auction_id(conn)
			period_keys: list[str] = []
			if auction_id:
				auction_row = conn.execute("SELECT firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions WHERE auction_id=?", (str(auction_id),)).fetchone()
				if auction_row and auction_row["firstWaterTakeDate"] and auction_row["lastWaterTakeDate"] and auction_row["period_length_hours"]:
					period_keys = self._period_keys(str(auction_row["firstWaterTakeDate"]), str(auction_row["lastWaterTakeDate"]), int(auction_row["period_length_hours"]))

			def _period_index(period_value: str) -> int | None:
				value = str(period_value or "").strip()
				if not value: return None
				if value.isdigit(): return int(value)
				if value.upper().startswith("W") and value[1:].isdigit(): return int(value[1:])
				if period_keys:
					try: return period_keys.index(value) + 1
					except ValueError: return None
				return None

			start_idx = _period_index(str(period_start))
			end_idx = _period_index(str(period_end))
			if start_idx is not None and end_idx is not None and end_idx < start_idx:
				start_idx, end_idx = end_idx, start_idx

			# Licensed allocation by period from trader_license (one row expected).
			if start_idx is not None and end_idx is not None:
				license_rows = conn.execute("SELECT bid_period, license_quantity FROM trader_license WHERE trader_id=? AND bid_period>=? AND bid_period<=? ORDER BY bid_period, license_id", (int(participant_id), int(start_idx), int(end_idx)), ).fetchall()
			else:
				license_rows = []
			for row in license_rows:
				period_idx = int(row["bid_period"] or 0)
				if period_idx <= 0: continue
				if period_keys and period_idx <= len(period_keys): period_key = str(period_keys[period_idx - 1])
				else: period_key = str(period_idx)
				if not period_key or period_key in result: continue
				licensed = float(row["license_quantity"] or 0.0)
				result[period_key] = [licensed, licensed]

			# Final quota by period from trader_quota (one row expected per participant-period).
			if auction_id:
				quota_rows = conn.execute("SELECT take_date, quota_auction_end FROM trader_quota WHERE trader_id=? AND auction_id=? AND take_date>=? AND take_date<=? ORDER BY take_date, quota_id", (int(participant_id), str(auction_id), str(period_start), str(period_end)), ).fetchall()
				for row in quota_rows:
					period_key = str(row["take_date"] or "").strip()
					if not period_key: continue
					final_quota = float(row["quota_auction_end"] or 0.0)
					if period_key not in result: result[period_key] = [0.0, final_quota]
					else: result[period_key][1] = final_quota

		return result

	def catchment_price_rows(self, auction_id: str) -> tuple[list[dict], list[dict]]:
		"""Fetch price and constraint results from trader_quota and control_point_event."""
		with self._connect() as conn:
			# Read per-well prices from trader_quota (well_id + take_date + price per row)
			well_name_map = {row["well_id"]: row["name"] for row in conn.execute("SELECT well_id, name FROM wells").fetchall()}
			well_rows = []
			for row in conn.execute("SELECT well_id, take_date, price FROM trader_quota WHERE auction_id=? AND well_id IS NOT NULL AND take_date IS NOT NULL ORDER BY well_id, take_date", (auction_id,)).fetchall():
				well_id = str(row["well_id"])
				well_rows.append({"well_id": well_id, "well_name": well_name_map.get(well_id, well_id), "period_id": str(row["take_date"]), "price": float(row["price"] or 0.0)})

			# Read control point results from control_point_event
			cp_rows = []
			for row in conn.execute(""" SELECT c.control_point_id, c.name, e.effect_date, e.dual_price, e.head_constraint_upper_bound, e.slack FROM control_point_event e JOIN control_points c ON c.control_point_id = e.control_point_id WHERE e.auction_id=? ORDER BY c.control_point_id, e.effect_date """, (auction_id,)).fetchall():
					slack = float(row["slack"] or 0.0)
					bound = float(row["head_constraint_upper_bound"] or 0.0)
					cp_rows.append({"control_point_id": row["control_point_id"], "control_point_name": row["name"], "period_id": str(row["effect_date"] or ""), "dual_value": float(row["dual_price"] or 0.0), "used_capacity": bound - slack, "bound_capacity": bound,})
			return well_rows, cp_rows

