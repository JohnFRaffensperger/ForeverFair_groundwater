# services/ForeverFairData.py. Claude guided by JFR, 2026 06 07.
# Copyright 2026 John F. Raffensperger. Licensed under the Forever Fair Public Interest License v1.0. See LICENSE for terms.
# Purpose: Persist auction data and run results in SQLite.
# This is the business logic from the database manager's point of view. Some auction logic appears here for speed, since SQL is sometimes faster than Python.

from __future__ import annotations
import csv
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict, cast
from SetupForeverFairDB import SCHEMA_DDL

MAX_BID_STEPS = 5 
DEFAULT_BID_STEPS = 3

class ResponseFactor(TypedDict):
	well_id: int
	control_point_id: int
	pumping_period: int
	effect_period: int
	value: float

class ForeverFairData:
	def __init__(self, db_path: Path, debug_db_path: Path | None = None):
		self.db_path = db_path
		self.debug_db_path = debug_db_path
		self.does_db_exist()

	# 1. Database connect, existence, setup.  --------------------------------------------------------------------------------------
	def connect_to_db(self) -> sqlite3.Connection:
		conn = sqlite3.connect(self.db_path)
		conn.row_factory = sqlite3.Row
		conn.execute("PRAGMA foreign_keys = ON")
		return conn

	def does_db_exist(self) -> None:
		self.db_path.parent.mkdir(parents=True, exist_ok=True)
		if not self.db_path.exists() and self.debug_db_path and self.debug_db_path.exists():
			shutil.copy(self.debug_db_path, self.db_path)
		with self.connect_to_db() as conn:
			conn.executescript(SCHEMA_DDL)
			try: conn.execute("ALTER TABLE auctions ADD COLUMN auction_revenue REAL")
			except sqlite3.OperationalError: pass
			try: conn.execute("ALTER TABLE traders ADD COLUMN trader_type TEXT NOT NULL DEFAULT 'well'")
			except sqlite3.OperationalError: pass
			conn.execute("INSERT OR IGNORE INTO Catchment_info(meta_key, integer_value) VALUES ('MAX_BID_STEPS', ?)", (DEFAULT_BID_STEPS,))
			conn.execute("INSERT OR IGNORE INTO Catchment_info(meta_key, text_value) VALUES ('rights_policy_name', 'Unspecified rights policy')")
			conn.execute("INSERT OR IGNORE INTO Catchment_info(meta_key, text_value) VALUES ('rights_policy_summary', 'No rights policy summary has been configured yet.')")

	def get_meta_data(self, conn: sqlite3.Connection, key: str) -> str:
		row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key=?", (key,)).fetchone()
		if row is None: raise ValueError(f"Missing Catchment_info row for meta_key={key}")
		return str(row[0] or "")

	def get_catchment_name(self) -> str:
		with self.connect_to_db() as conn: return self.get_meta_data(conn, "Catchment_name")

	def get_rights_conversion_dict(self) -> dict[str, str]:
		with self.connect_to_db() as conn: return {"policy_name": self.get_meta_data(conn, "rights_policy_name"), "summary": self.get_meta_data(conn, "rights_policy_summary")}

	def get_max_bid_steps(self) -> int:
		with self.connect_to_db() as conn: row = conn.execute("SELECT integer_value FROM Catchment_info WHERE meta_key='MAX_BID_STEPS'").fetchone()
		return row[0] 

	def get_rights_policy(self) -> str:
		with self.connect_to_db() as conn: row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key='Rights_policy'").fetchone()
		return row[0]

	# 2. Timing, calendar --------------------------------------------------------------------------------------
	def the_time_at_the_tone_is(self) -> datetime:
		"""Return the current synthetic simulation date/time from the database."""
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key='synthetic_current_date'").fetchone()
			return datetime.fromisoformat(row[0])

	def next_friday_at_4pm(self, base: datetime) -> datetime:
		"""Return the next Friday 16:00 at or after the given synthetic datetime."""
		days_ahead = (4 - base.weekday()) % 7
		target = (base + timedelta(days=days_ahead)).replace(hour=16, minute=0, second=0, microsecond=0)
		if target < base: target += timedelta(days=7)
		return target

# 	def next_monday(self, base: datetime) -> datetime:
# 		days_ahead = (0 - base.weekday()) % 7
# 		target = (base + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
# 		if target <= base: target += timedelta(days=7)
# 		return target
# 
	def latest_period_length_hours(self) -> int:
		"""Return period_length_hours from Catchment_info. Fails if not configured."""
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT integer_value FROM Catchment_info WHERE meta_key='period_length_hours'").fetchone()
			return row[0]

	# 3. Auction management --------------------------------------------------------------------------------
	def add_auction(self, auction_type: str | None = "final") -> dict[str, Any]:
		now_dt = self.the_time_at_the_tone_is()
		period_length_hours = self.latest_period_length_hours()
		with self.connect_to_db() as conn:
			next_auction_id = int(conn.execute("SELECT COALESCE(MAX(auction_id), 0) + 1 FROM auctions").fetchone()[0])
			if next_auction_id > self.get_number_of_bidding_periods():
				raise ValueError("No auctions remain in this schedule.")
			bidding_periods = self.get_bidding_periods_for_auction(next_auction_id)
			close_dt, first_take_dt, last_take_dt = self.get_auction_close_first_last_dates(now_dt, period_length_hours, bidding_periods)
			cursor = conn.execute("INSERT INTO auctions(status, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type) VALUES ('OPEN', ?, ?, ?, ?, ?, ?)", (now_dt.isoformat(timespec="minutes"), close_dt.isoformat(timespec="minutes"), first_take_dt.isoformat(timespec="minutes"), last_take_dt.isoformat(timespec="minutes"), period_length_hours, auction_type),)
			row = conn.execute("SELECT * FROM auctions WHERE auction_id=?", (cursor.lastrowid,)).fetchone()
			return dict(row)

	# Save auction optimization results to the database. --------------------------------------------
	def close_auction(self, auction_id: int, solve_status: str, objective_value: float | None, auction_close_time: str, auction_revenue: float | None = None) -> int:
		with self.connect_to_db() as conn:
			conn.execute("UPDATE auctions SET status='Closed', closed_date=COALESCE(closed_date, ?), solve_status=?, objective_value=?, auction_revenue=? WHERE auction_id=?", 
				(auction_close_time, solve_status, objective_value, auction_revenue, auction_id))

			# Advance synthetic clock by one week when a final auction completes.
			is_auction_final = conn.execute("SELECT auction_type FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
			if "final" == is_auction_final["auction_type"]:
				# Update the date to the next period.
				date_and_periodlength = conn.execute("SELECT meta_key, text_value, integer_value FROM Catchment_info WHERE meta_key IN ('synthetic_current_date', 'period_length_hours')").fetchall()
				meta = {row[0]: (row[2] if row[2] is not None else row[1]) for row in date_and_periodlength}
				period_length_hours = int(meta["period_length_hours"])
				conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value) VALUES ('synthetic_current_date', ?)",
				  ((datetime.fromisoformat(meta["synthetic_current_date"]) + timedelta(hours=period_length_hours)).isoformat(timespec="minutes"),))
			return auction_id

	def get_auction_close_first_last_dates(self, now_dt: datetime, period_length_hours: int, bidding_periods: int) -> tuple[datetime, datetime, datetime]:
		close_dt = self.next_friday_at_4pm(now_dt)
		first_take_dt = (close_dt + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
		window = timedelta(hours=period_length_hours * bidding_periods) - timedelta(minutes=1)
		last_take_dt = first_take_dt + window
		return close_dt, first_take_dt, last_take_dt

	def get_remaining_auctions_for_auction(self, auction_id: int) -> int:
		return max(0, self.get_number_of_bidding_periods() - auction_id + 1)

	def get_bidding_periods_for_auction(self, auction_id: int) -> int:
		return max(1, self.get_number_of_bidding_periods() - max(0, auction_id - 1))

	def _iter_period_axis(self, first_dt: datetime, last_dt: datetime, period_length_hours: int):
		if period_length_hours <= 0 or last_dt < first_dt: return
		current = first_dt
		idx = 1
		while current <= last_dt:
			yield idx, current.date().toordinal(), current.isoformat(timespec="minutes"), current
			idx += 1
			current += timedelta(hours=period_length_hours)

	# JFR: Claude wants to build maps on the basis that some period and date lookups are random rather than sequential.
	def _get_period_maps(self, auction_id: int) -> dict[str, Any]:
		auction = self.get_auction_info(auction_id)
		period_length_hours = int(auction["period_length_hours"])
		first_dt = datetime.fromisoformat(str(auction["first_water_take_date"]))
		last_constrained_iso = auction["last_constrained_date"] or auction["last_water_take_date"]
		last_constrained_dt = datetime.fromisoformat(str(last_constrained_iso))
		period_maps: dict[str, Any] = {"pumping_labels": [], "effect_labels": [], "pumping_iso_to_idx": {},
			"effect_iso_to_idx": {}, "pumping_ordinal_to_idx": {}, "effect_ordinal_to_idx": {}, "idx_to_pumping_iso": {},
			"idx_to_effect_iso": {},}
		
		for period in auction["periods"]:
			idx, iso_label = period["id"], period["label"]
			period_maps["pumping_labels"].append(iso_label)
			period_maps["pumping_iso_to_idx"][iso_label] = idx
			period_maps["pumping_ordinal_to_idx"][datetime.fromisoformat(iso_label).date().toordinal()] = idx
			period_maps["idx_to_pumping_iso"][idx] = iso_label

		for idx, ordinal, iso_label, _ in self._iter_period_axis(first_dt, last_constrained_dt, period_length_hours):
			period_maps["effect_labels"].append(iso_label)
			period_maps["effect_iso_to_idx"][iso_label] = idx
			period_maps["effect_ordinal_to_idx"][ordinal] = idx
			period_maps["idx_to_effect_iso"][idx] = iso_label

		return period_maps

	def get_auction_calendar(self, auction_id: int) -> dict[str, Any]:
		return self._get_period_maps(auction_id)

# 	def get_auction_effect_periods(self, auction_id: int) -> list[int]:
# 		"""Return the auction's effect periods in chronological order."""
# 		return list(self._get_period_maps(auction_id)["effect_iso_to_idx"].values())

# 	# When you know the auction_id.
	def get_auction_info(self, auction_id: int) -> dict[str, Any]:
		auction_info_dict: dict[str, Any] = {"id": auction_id, "periods": []}
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT a.auction_id, a.status, a.closed_date, a.firstWaterTakeDate, a.lastWaterTakeDate, a.period_length_hours, a.auction_type, a.created_date, a.solve_status, a.objective_value, a.auction_revenue, (SELECT MAX(cpe.effect_date) FROM control_point_events cpe WHERE cpe.auction_id = a.auction_id) AS last_constrained_date FROM auctions a WHERE a.auction_id=?", (auction_id,)).fetchone()
			auction_info_dict["status"] = str(row["status"])
			auction_info_dict["closed_date"] = row["closed_date"]
			auction_info_dict["first_water_take_date"] = row["firstWaterTakeDate"]
			auction_info_dict["last_water_take_date"] = row["lastWaterTakeDate"]
			auction_info_dict["first_pumping_date"] = row["firstWaterTakeDate"]
			auction_info_dict["last_pumping_date"] = row["lastWaterTakeDate"]
			auction_info_dict["period_length_hours"] = int(row["period_length_hours"]) if row["period_length_hours"] is not None else 0
			auction_info_dict["auction_type"] = row["auction_type"]
			auction_info_dict["created_date"] = row["created_date"]
			auction_info_dict["solve_status"] = row["solve_status"]
			auction_info_dict["objective_value"] = row["objective_value"]
			auction_info_dict["auction_revenue"] = row["auction_revenue"]
			auction_info_dict["last_constrained_date"] = str(row["last_constrained_date"]) if row["last_constrained_date"] is not None else None
		start_dt = datetime.fromisoformat(str(auction_info_dict["first_water_take_date"]))
		end_dt = datetime.fromisoformat(str(auction_info_dict["last_water_take_date"]))
		for idx, _, iso_label, _ in self._iter_period_axis(start_dt, end_dt, auction_info_dict["period_length_hours"]):
			auction_info_dict["periods"].append({"id": idx, "label": iso_label})
		return auction_info_dict

	# When you don't know the auction_id.
	def get_next_auction_info(self) -> dict[str, Any] | None:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT * FROM auctions WHERE status='OPEN' ORDER BY created_date ASC LIMIT 1").fetchone()
			if row is None: return None
			return dict(row)

	def get_run_summary(self, auction_id: int) -> dict[str, Any] | None:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT auction_id, solve_status, objective_value, closed_date FROM auctions WHERE auction_id=? AND solve_status IS NOT NULL", (auction_id,)).fetchone()
			if row is None: return None
			return dict(row)

	def get_latest_auction_with_catchment_results(self) -> int | None:
		"""Return most recent auction_id that has catchment output rows (well price or control-point dual/slack)."""
		with self.connect_to_db() as conn:
			row = conn.execute("""SELECT a.auction_id FROM auctions a WHERE a.status != 'DELETED'
						AND (EXISTS (SELECT 1 FROM well_quota wq WHERE wq.auction_id = a.auction_id AND wq.price IS NOT NULL)
							OR EXISTS (SELECT 1 FROM control_point_events cpe WHERE cpe.auction_id = a.auction_id AND (cpe.dual_price IS NOT NULL OR cpe.slack IS NOT NULL)))
					ORDER BY CAST(a.auction_id AS INTEGER) DESC LIMIT 1""").fetchone()
			return int(row["auction_id"]) if row is not None else None

	def list_auctions(self) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT auction_id, status, auction_type, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, solve_status, objective_value, auction_revenue FROM auctions WHERE status != 'DELETED' ORDER BY CAST(auction_id AS INTEGER) ASC").fetchall()
			return [dict(row) for row in rows]

	def list_open_auctions(self) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT auction_id, closed_date FROM auctions WHERE status='OPEN' ORDER BY created_date DESC").fetchall()
			return [dict(row) for row in rows]

# 	def make_auction_final(self) -> int:
# 		next_auction = self.get_next_auction_info()
# 		if next_auction is None: raise ValueError("No open auction available")
# 		auction_id = int(next_auction["auction_id"])
# 		with self.connect_to_db() as conn:
# 			conn.execute("UPDATE auctions SET auction_type='final' WHERE auction_id=?", (auction_id,))
# 		return auction_id
# 
# 	def make_auction_tentative(self) -> int:
# 		next_auction = self.get_next_auction_info()
# 		if next_auction is None: raise ValueError("No open auction available")
# 		auction_id = int(next_auction["auction_id"])
# 		with self.connect_to_db() as conn:
# 			conn.execute("UPDATE auctions SET auction_type='tentative' WHERE auction_id=?", (auction_id,))
# 		return auction_id
# 		
# 	# 4. Traders, wells. -------------------------------------------------------------------------------------------------
	def list_of_traders(self) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			return [{"id": int(row["trader_id"]), "name": row["name_tag"], "trader_type": str(row["trader_type"] or "well")}
				for row in conn.execute("SELECT trader_id, name_tag, trader_type FROM traders ORDER BY name_tag").fetchall()]

	def get_trader_type(self, trader_id: int) -> str:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT trader_type FROM traders WHERE trader_id=?", (trader_id,)).fetchone()
			return str(row["trader_type"] or "well") if row is not None else "well"

	def get_trader_ids_by_type(self, trader_type: str) -> list[int]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT trader_id FROM traders WHERE trader_type=? ORDER BY trader_id", (trader_type,)).fetchall()
			return [int(row["trader_id"]) for row in rows]

	def get_trader_wells(self, trader_id: int) -> list[dict[str, int | str | float | None]]:
		"""Retrieve all wells for a trader as dictionaries."""
		with self.connect_to_db() as conn:
			return [{"id": int(row["well_id"]), "name": row["name"], "trader_id": int(row["trader_id"]) if row["trader_id"] is not None else 0, "gw_model_layer": row["gw_model_layer"], "gw_model_row": row["gw_model_row"], "gw_model_column": row["gw_model_column"], "latitude": row["latitude"], "longitude": row["longitude"]} for row in conn.execute("SELECT well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude FROM wells WHERE trader_id=? ORDER BY well_id", (trader_id,)).fetchall()]

	def get_wells(self) -> set[int]:
		"""Return a set of all well_id integers in the wells table."""
		with self.connect_to_db() as conn:
			return {int(row["well_id"]) for row in conn.execute("SELECT well_id FROM wells ORDER BY well_id").fetchall()}

# 	def get_all_period_dates(self) -> list[str]: # TODO: should be get_auction_dates, constrained to auction_id. Should need only from control_point_events.
# 		"""All distinct period dates (pumping + effect) sorted. Gives the global period date sequence
# 		that aligns with response_matrix.pumping_period / effect_period integers (1-based).
# 		Effect periods can extend beyond the last pumping date, so both tables are needed."""
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""SELECT DISTINCT date_val FROM (
# 					SELECT take_date AS date_val FROM well_quota WHERE take_date IS NOT NULL
# 					UNION
# 					SELECT effect_date AS date_val FROM control_point_events) ORDER BY date_val""").fetchall()
# 			return [str(row[0]) for row in rows]
# 
# 	# 5. Bids -------------------------------------------------------------------------------------------------
	def add_bid(self, auction_id: int, well_id: int, period_id: int, quantity: float, price: float, is_default: bool = False, 
			 bid_steps: list[tuple[float, float]] | None = None) -> dict[str, int | str | float | None]:
		"""Add a bid and return it as a dictionary."""
		now = datetime.now(timezone.utc).isoformat()
		max_bid_steps = self.get_max_bid_steps()
		steps = bid_steps if bid_steps is not None else [(quantity, price)]
		if len(steps) > max_bid_steps: raise ValueError(f"At most {max_bid_steps} bid steps are supported.")
		qty_values: list[float | None] = [None] * MAX_BID_STEPS
		price_values: list[float | None] = [None] * MAX_BID_STEPS
		for idx, (step_qty, step_price) in enumerate(steps):
			qty_values[idx] = float(step_qty)
			price_values[idx] = float(step_price)
		period_maps = self._get_period_maps(auction_id)
		period_label = period_maps["idx_to_pumping_iso"][period_id]
		with self.connect_to_db() as conn:
			trader_row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
			trader_id = int(trader_row["trader_id"]) if trader_row and trader_row["trader_id"] is not None else 0
			# One active bid per (well, auction, period) - soft-delete any existing before inserting.
			conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=? AND well_id=? AND pumping_date=? AND deleted=0", (auction_id, well_id, period_label),)
			cursor = conn.execute("INSERT INTO well_bids(" "auction_id, trader_id, well_id, bid_date, pumping_date, " "qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, " "is_bid_default, deleted" ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, trader_id, well_id, now, period_label, qty_values[0], price_values[0], qty_values[1], price_values[1], qty_values[2], price_values[2], qty_values[3], price_values[3], qty_values[4], price_values[4], 1 if is_default else 0,),)
			bid_pk = cursor.lastrowid or 0
			return {"id": f"bid-{bid_pk}-s1", "well_id": well_id, "period_id": period_id, "quantity": float(steps[0][0]), "price": float(steps[0][1]), "submitted_at": now}

	def delete_bid(self, bid_id: int, current_trader_id: int) -> bool:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT trader_id, deleted FROM well_bids WHERE bid_id=?", (bid_id,)).fetchone()
			if row is None: return False
			if row["deleted"] == 1 or row["trader_id"] != current_trader_id: return False
			conn.execute("UPDATE well_bids SET deleted=1 WHERE bid_id=?", (bid_id,))
			return True
	
	def get_bids (self, auction_id: int) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT bid_id, well_id, pumping_date, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM well_bids WHERE auction_id=? AND deleted=0 ORDER BY bid_id", (auction_id,), ).fetchall()
			return [dict(row) for row in rows]

	def get_bid_count(self, auction_id: int) -> tuple[int, int]:
		max_bid_steps = self.get_max_bid_steps()
		step_count_sql = " + ".join([f"CASE WHEN qty{step_num} IS NOT NULL AND price{step_num} IS NOT NULL THEN 1 ELSE 0 END" for step_num in range(1, max_bid_steps + 1)])
		with self.connect_to_db() as conn:
			row = conn.execute(f"SELECT COALESCE(SUM(CASE WHEN is_bid_default = 0 THEN ({step_count_sql}) ELSE 0 END), 0) AS real_bid_count, COALESCE(SUM(CASE WHEN is_bid_default = 1 THEN ({step_count_sql}) ELSE 0 END), 0) AS default_bid_count FROM well_bids WHERE auction_id=? AND deleted=0", (auction_id,)).fetchone()
			return int(row["real_bid_count"]), int(row["default_bid_count"])

	def get_bid_history (self, auction_id: int, trader_id: int) -> list[dict[str, Any]]:
		"""Fetch bid history from well_bids joined to well_quota for final allocation and traded price."""
		max_bid_steps = self.get_max_bid_steps()
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT bid_id, pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, bid_date, is_bid_default FROM well_bids " "WHERE auction_id=? AND trader_id=? AND deleted=0 ORDER BY bid_id DESC", (auction_id, trader_id), ).fetchall()
			# Pre-fetch well_quota rows keyed by takeDate for this trader/auction
			quota_rows = conn.execute("SELECT take_date, quota_auction_end, price FROM well_quota WHERE auction_id=? AND trader_id=?", (auction_id, trader_id), ).fetchall()
			quota_by_date = {str(r["take_date"]): r for r in quota_rows}
		period_maps = self._get_period_maps(auction_id)
		period_idx_by_iso = period_maps["pumping_iso_to_idx"]
		bid_history_list: list[dict[str, Any]] = []
		for row in rows:
			pumping_date = str(row["pumping_date"] or "")
			period_id = period_idx_by_iso[pumping_date]
			quota = quota_by_date[pumping_date]
			final_allocation = float(quota["quota_auction_end"]) if quota and quota["quota_auction_end"] is not None else None
			traded_price = float(quota["price"]) if quota and quota["price"] is not None else None
			for step_num in range(1, max_bid_steps + 1):
				qty = row[f"qty{step_num}"]
				price = row[f"price{step_num}"]
				if qty is None or price is None: continue
				bid_history_list.append({"bid_id": int(row["bid_id"]), "period_id": period_id, "quantity": float(qty), "price": float(price), "submitted_at": row["bid_date"], "final_allocation": final_allocation, "traded_price": traded_price, "is_default": bool(row["is_bid_default"]) if row["is_bid_default"] is not None else False, })
		return bid_history_list

	def add_environmental_bid(self, auction_id: int, trader_id: int, cpe_id: int, quantity: float, price: float,
			is_default: bool = False, bid_steps: list[tuple[float, float]] | None = None,) -> dict[str, int | str | float | None]:
		now = datetime.now(timezone.utc).isoformat()
		max_bid_steps = self.get_max_bid_steps()
		steps = bid_steps if bid_steps is not None else [(quantity, price)]
		if len(steps) > max_bid_steps: raise ValueError(f"At most {max_bid_steps} bid steps are supported.")
		qty_values: list[float | None] = [None] * MAX_BID_STEPS
		price_values: list[float | None] = [None] * MAX_BID_STEPS
		for idx, (step_qty, step_price) in enumerate(steps):
			qty_values[idx] = float(step_qty)
			price_values[idx] = float(step_price)
		with self.connect_to_db() as conn:
			conn.execute("UPDATE environmental_bids SET deleted=1 WHERE auction_id=? AND trader_id=? AND cpe_id=? AND deleted=0", (auction_id, trader_id, cpe_id))
			cursor = conn.execute("INSERT INTO environmental_bids(auction_id, trader_id, cpe_id, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
				(auction_id, trader_id, cpe_id, now, qty_values[0], price_values[0], qty_values[1], price_values[1], qty_values[2], price_values[2], qty_values[3], price_values[3], qty_values[4], price_values[4], 1 if is_default else 0))
			env_bid_id = cursor.lastrowid or 0
			return {"id": f"env-bid-{env_bid_id}-s1", "cpe_id": cpe_id, "quantity": float(steps[0][0]), "price": float(steps[0][1]), "submitted_at": now}

	def get_environmental_bids(self, auction_id: int) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT env_bid_id, trader_id, cpe_id, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM environmental_bids WHERE auction_id=? AND deleted=0 ORDER BY env_bid_id", (auction_id,)).fetchall()
			return [dict(row) for row in rows]

	def get_environmental_bid_rows(self, auction_id: int, trader_id: int) -> list[dict[str, Any]]:
		previous_auction_id = self.get_previous_auction_id(auction_id)
		max_bid_steps = self.get_max_bid_steps()
		with self.connect_to_db() as conn:
			current_rows = conn.execute("""SELECT cpe.cpe_id, cpe.control_point_id, cp.name AS control_point_name, cpe.effect_date,
				cpe.committed_head_auction_start, cpe.committed_head_auction_end, cpe.planned_head_auction_start, cpe.planned_head_auction_end,
				(ahl.minimum_head - cpe.committed_head_auction_start) AS allowable_head_change
				FROM control_point_events cpe
				JOIN control_points cp ON cp.control_point_id = cpe.control_point_id
				JOIN aquifer_head_limits ahl ON ahl.control_point_id = cpe.control_point_id AND ahl.effect_date = cpe.effect_date
				WHERE cpe.auction_id=? ORDER BY cpe.effect_date, cpe.control_point_id""", (auction_id,)).fetchall()
			latest_rows = conn.execute("SELECT cpe_id, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM environmental_bids WHERE auction_id=? AND trader_id=? AND deleted=0 ORDER BY env_bid_id DESC", (auction_id, trader_id)).fetchall()
			previous_price_by_key: dict[tuple[int, str], float | None] = {}
			if previous_auction_id is not None:
				previous_rows = conn.execute("SELECT control_point_id, effect_date, dual_price FROM control_point_events WHERE auction_id=?", (previous_auction_id,)).fetchall()
				previous_price_by_key = {(int(row["control_point_id"]), str(row["effect_date"])): (float(row["dual_price"]) if row["dual_price"] is not None else None) for row in previous_rows}
		latest_by_cpe_id: dict[int, sqlite3.Row] = {}
		for row in latest_rows:
			cpe_id = int(row["cpe_id"])
			if cpe_id not in latest_by_cpe_id: latest_by_cpe_id[cpe_id] = row
		result: list[dict[str, Any]] = []
		for row in current_rows:
			cpe_id = int(row["cpe_id"])
			latest = latest_by_cpe_id.get(cpe_id)
			latest_bids: list[dict[str, float]] = []
			if latest is not None:
				for step_num in range(1, max_bid_steps + 1):
					qty = latest[f"qty{step_num}"]
					step_price = latest[f"price{step_num}"]
					if qty is None or step_price is None: continue
					latest_bids.append({"quantity": float(qty), "price": float(step_price)})
			result.append({"cpe_id": cpe_id, "control_point_id": int(row["control_point_id"]), "control_point_name": str(row["control_point_name"] or ""),
				"effect_date": str(row["effect_date"] or ""), "committed_head_auction_start": row["committed_head_auction_start"], "committed_head_auction_end": row["committed_head_auction_end"],
				"planned_head_auction_start": row["planned_head_auction_start"], "planned_head_auction_end": row["planned_head_auction_end"], "allowable_head_change": float(row["allowable_head_change"] or 0.0),
				"latest_bids": latest_bids, "clearing_price": previous_price_by_key.get((int(row["control_point_id"]), str(row["effect_date"])))})
		return result

	def get_environmental_bid_history(self, auction_id: int, trader_id: int) -> list[dict[str, Any]]:
		max_bid_steps = self.get_max_bid_steps()
		with self.connect_to_db() as conn:
			rows = conn.execute("""SELECT eb.env_bid_id, eb.cpe_id, eb.bid_date, eb.is_bid_default, eb.qty1, eb.price1, eb.qty2, eb.price2, eb.qty3, eb.price3, eb.qty4, eb.price4, eb.qty5, eb.price5,
				cpe.control_point_id, cpe.effect_date, cp.name AS control_point_name, ep.traded_head_end, ep.price
				FROM environmental_bids eb
				JOIN control_point_events cpe ON cpe.cpe_id = eb.cpe_id
				JOIN control_points cp ON cp.control_point_id = cpe.control_point_id
				LEFT JOIN environmental_position ep ON ep.auction_id = eb.auction_id AND ep.trader_id = eb.trader_id AND ep.cpe_id = eb.cpe_id
				WHERE eb.auction_id=? AND eb.trader_id=? AND eb.deleted=0 ORDER BY eb.env_bid_id DESC""", (auction_id, trader_id)).fetchall()
		bid_history_list: list[dict[str, Any]] = []
		for row in rows:
			traded_head_end = float(row["traded_head_end"]) if row["traded_head_end"] is not None else None
			traded_price = float(row["price"]) if row["price"] is not None else None
			for step_num in range(1, max_bid_steps + 1):
				qty = row[f"qty{step_num}"]
				step_price = row[f"price{step_num}"]
				if qty is None or step_price is None: continue
				bid_history_list.append({"env_bid_id": int(row["env_bid_id"]), "cpe_id": int(row["cpe_id"]), "control_point_id": int(row["control_point_id"]),
					"control_point_name": str(row["control_point_name"] or ""), "effect_date": str(row["effect_date"] or ""), "quantity": float(qty), "price": float(step_price),
					"submitted_at": row["bid_date"], "traded_head_end": traded_head_end, "traded_price": traded_price,
					"is_default": bool(row["is_bid_default"]) if row["is_bid_default"] is not None else False})
		return bid_history_list

# 	def get_environmental_positions(self, auction_id: int, trader_id: int) -> list[dict[str, Any]]:
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""SELECT ep.env_position_id, ep.auction_id, ep.trader_id, ep.cpe_id, ep.traded_head_start, ep.traded_head_end, ep.price,
# 				cpe.control_point_id, cpe.effect_date, cp.name AS control_point_name
# 				FROM environmental_position ep
# 				JOIN control_point_events cpe ON cpe.cpe_id = ep.cpe_id
# 				JOIN control_points cp ON cp.control_point_id = cpe.control_point_id
# 				WHERE ep.auction_id=? AND ep.trader_id=?
# 				ORDER BY cpe.effect_date, cpe.control_point_id, ep.env_position_id""", (auction_id, trader_id)).fetchall()
# 			return [{"env_position_id": int(row["env_position_id"]), "auction_id": int(row["auction_id"]), "trader_id": int(row["trader_id"]),
# 				"cpe_id": int(row["cpe_id"]), "control_point_id": int(row["control_point_id"]), "control_point_name": str(row["control_point_name"] or ""),
# 				"effect_date": str(row["effect_date"] or ""), "traded_head_start": row["traded_head_start"], "traded_head_end": row["traded_head_end"],
# 				"price": row["price"]} for row in rows]
# 
	def get_environmental_head_protection(self, auction_id: int) -> dict[tuple[int, str], float]:
		"""Return total traded_head_end per (control_point_id, effect_date) for the given auction across all traders."""
		with self.connect_to_db() as conn:
			rows = conn.execute("""SELECT cpe.control_point_id, cpe.effect_date, COALESCE(SUM(ep.traded_head_end), 0.0) AS total_head
				FROM environmental_position ep
				JOIN control_point_events cpe ON cpe.cpe_id = ep.cpe_id
				WHERE ep.auction_id=?
				GROUP BY cpe.control_point_id, cpe.effect_date""", (auction_id,)).fetchall()
			return {(int(row["control_point_id"]), str(row["effect_date"])): float(row["total_head"]) for row in rows}

# 	def has_active_environmental_bids(self, auction_id: int, trader_id: int, cpe_id: int) -> bool:
# 		with self.connect_to_db() as conn:
# 			row = conn.execute("SELECT 1 FROM environmental_bids WHERE auction_id=? AND trader_id=? AND cpe_id=? AND deleted=0 LIMIT 1", (auction_id, trader_id, cpe_id)).fetchone()
# 			return row is not None
# 
	def copy_active_environmental_bids(self, auction_id: int, source_auction_id: int, trader_id: int) -> int:
		now = datetime.now(timezone.utc).isoformat()
		with self.connect_to_db() as conn:
			target_rows = conn.execute("SELECT cpe_id, control_point_id, effect_date FROM control_point_events WHERE auction_id=?", (auction_id,)).fetchall()
			target_cpe_by_key = {(int(row["control_point_id"]), str(row["effect_date"])): int(row["cpe_id"]) for row in target_rows}
			source_rows = conn.execute("""SELECT eb.qty1, eb.price1, eb.qty2, eb.price2, eb.qty3, eb.price3, eb.qty4, eb.price4, eb.qty5, eb.price5,
				cpe.control_point_id, cpe.effect_date
				FROM environmental_bids eb JOIN control_point_events cpe ON cpe.cpe_id = eb.cpe_id
				WHERE eb.auction_id=? AND eb.trader_id=? AND eb.deleted=0 ORDER BY eb.env_bid_id""", (source_auction_id, trader_id)).fetchall()
			inserted = 0
			for row in source_rows:
				key = (int(row["control_point_id"]), str(row["effect_date"]))
				if key not in target_cpe_by_key: continue
				target_cpe_id = target_cpe_by_key[key]
				conn.execute("UPDATE environmental_bids SET deleted=1 WHERE auction_id=? AND trader_id=? AND cpe_id=? AND deleted=0", (auction_id, trader_id, target_cpe_id))
				conn.execute("INSERT INTO environmental_bids(auction_id, trader_id, cpe_id, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)",
					(auction_id, trader_id, target_cpe_id, now, row["qty1"], row["price1"], row["qty2"], row["price2"], row["qty3"], row["price3"], row["qty4"], row["price4"], row["qty5"], row["price5"]))
				inserted += 1
			return inserted

	# Reads default bids from the previous auction and posts them to the new auction.
	# To do: this should be incorporated in get_bids
	# UNUSED
# 	def get_default_bids(self, auction_id: int, source_auction_id: int, now: str) -> None:
# 		with self.connect_to_db() as conn:
# 			standing = conn.execute("SELECT trader_id, well_id, pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM well_bids WHERE auction_id=? AND is_bid_default=1 AND deleted=0", (source_auction_id,)).fetchall()
# 			for s in standing:
# 				conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=? AND well_id=? AND pumping_date=? AND deleted=0", (auction_id, s["well_id"], s["pumping_date"]),)
# 				conn.execute("INSERT OR IGNORE INTO well_bids(auction_id, trader_id, well_id, bid_date, pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)", (auction_id, s["trader_id"], s["well_id"], now, s["pumping_date"], s["qty1"], s["price1"], s["qty2"], s["price2"], s["qty3"], s["price3"], s["qty4"], s["price4"], s["qty5"], s["price5"]),)
# 
	def get_number_of_bidding_periods(self) -> int:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT integer_value FROM Catchment_info WHERE meta_key='num_bidding_periods'").fetchone()
			return row[0]

	def has_default_bids(self, auction_id: int) -> bool:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT 1 FROM well_bids WHERE auction_id=? AND is_bid_default=1 AND deleted=0 LIMIT 1", (auction_id,)).fetchone()
			return row is not None

	def get_previous_auction_id(self, auction_id: int) -> int | None:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT MAX(auction_id) AS auction_id FROM auctions WHERE auction_id < ? AND status != 'DELETED'", (auction_id,)).fetchone()
			if row is None or row["auction_id"] is None: return None
			return int(row["auction_id"])

	def has_active_bids_for_well(self, auction_id: int, well_id: int) -> bool:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT 1 FROM well_bids WHERE auction_id=? AND well_id=? AND deleted=0 LIMIT 1", (auction_id, well_id)).fetchone()
			return row is not None

	def copy_active_bids_for_well(self, auction_id: int, source_auction_id: int, well_id: int) -> int:
		target_pumping_dates = self._get_period_maps(auction_id)["pumping_labels"]
		if not target_pumping_dates: return 0
		now = datetime.now(timezone.utc).isoformat()
		placeholders = ", ".join("?" for _ in target_pumping_dates)
		with self.connect_to_db() as conn:
			rows = conn.execute(f"SELECT trader_id, pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM well_bids WHERE auction_id=? AND well_id=? AND deleted=0 AND pumping_date IN ({placeholders}) ORDER BY pumping_date, bid_id",
				(source_auction_id, well_id, *target_pumping_dates),).fetchall()
			inserted = 0
			for row in rows:
				conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=? AND well_id=? AND pumping_date=? AND deleted=0", (auction_id, well_id, row["pumping_date"]))
				conn.execute("INSERT INTO well_bids(auction_id, trader_id, well_id, bid_date, pumping_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)",
					(auction_id, row["trader_id"], well_id, now, row["pumping_date"], row["qty1"], row["price1"], row["qty2"], row["price2"], row["qty3"], row["price3"], row["qty4"], row["price4"], row["qty5"], row["price5"]))
				inserted += 1
			return inserted

# 	def has_real_bids_for_well(self, auction_id: int, well_id: int) -> bool:
# 		with self.connect_to_db() as conn:
# 			row = conn.execute("SELECT 1 FROM well_bids WHERE auction_id=? AND well_id=? AND is_bid_default=0 AND deleted=0 LIMIT 1", (auction_id, well_id)).fetchone()
# 			return row is not None

# 	# 6. Quota -----------------------------------------
	def get_well_start_quota(self, well_id: int, auction_id: int) -> dict[int, float]:
		"""Return {bid_period: quota_auction_start} from well_quota for the given auction_id, ordered by take_date."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT take_date, quota_auction_start FROM well_quota WHERE well_id=? AND auction_id=? ORDER BY take_date", (well_id, auction_id)).fetchall()
		period_maps = self._get_period_maps(auction_id)
		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
		result: dict[int, float] = {}
		for row in rows:
			period_idx = pumping_iso_to_idx[str(row["take_date"] or "")]
			result[period_idx] = float(row["quota_auction_start"] or 0.0)
		return result
	
	def get_well_end_quota(self, well_id: int, auction_id: int) -> dict[int, float]:
		"""Return {bid_period: quota_auction_end} from well_quota for the given auction_id, ordered by take_date."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT take_date, quota_auction_end FROM well_quota WHERE well_id=? AND auction_id=? ORDER BY take_date", (well_id, auction_id)).fetchall()
		period_maps = self._get_period_maps(auction_id)
		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
		result: dict[int, float] = {}
		for row in rows:
			period_idx = pumping_iso_to_idx[str(row["take_date"] or "")]
			result[period_idx] = float(row["quota_auction_end"] or 0.0)
		return result

	def get_well_scaled_start_quota(self, well_id: int, auction_id: int) -> dict[int, float]:
		"""Return {bid_period: quota_scaled_start} from well_quota for the given auction_id, ordered by take_date."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT take_date, quota_scaled_start FROM well_quota WHERE well_id=? AND auction_id=? ORDER BY take_date", (well_id, auction_id)).fetchall()
		period_maps = self._get_period_maps(auction_id)
		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
		result: dict[int, float] = {}
		for row in rows:
			period_idx = pumping_iso_to_idx[str(row["take_date"] or "")]
			result[period_idx] = float(row["quota_scaled_start"] or 0.0)
		return result

	def get_well_clearing_price_for_current_rows(self, well_id: int, auction_id: int) -> dict[int, float | None]:
		"""Return {bid_period: price} mapped onto the current auction rows from source well_quota.price.
		For auction>1 this uses the previous auction and applies the same one-period shift as set_quota_for_auction.
		"""
		if auction_id <= 1: return {}
		period_labels = self._get_period_maps(auction_id)["pumping_labels"]
		with self.connect_to_db() as conn:
			source_rows = conn.execute("SELECT take_date, price FROM well_quota WHERE well_id=? AND auction_id=? ORDER BY take_date", (well_id, auction_id - 1)).fetchall()
		take_dates = [str(r["take_date"] or "") for r in source_rows]
		source_period_idx = {take_date: idx + 1 for idx, take_date in enumerate(take_dates[1:1 + len(period_labels)])}
		result: dict[int, float | None] = {}
		for row in source_rows:
			take_date = str(row["take_date"] or "")
			if take_date not in source_period_idx: continue
			period_id = source_period_idx[take_date]
			result[period_id] = float(row["price"]) if row["price"] is not None else None
		return result

	def get_well_license_quantity(self, well_id: int) -> dict[int, float]:
		"""Return {bid_period: license_quantity} from well_license for the given well_id."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT bid_period, license_quantity FROM well_license WHERE well_id=? ORDER BY bid_period", (well_id,)).fetchall()
			return {int(row["bid_period"]): float(row["license_quantity"] or 0.0) for row in rows if row["bid_period"] is not None}
	
	def get_quota(self, auction_id: int) -> dict[tuple[int, int], float]:
		"""Return {(well_id, period_idx): quota_auction_start} for all wells in the auction."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT well_id, take_date, quota_auction_start FROM well_quota WHERE auction_id=?", (auction_id,)).fetchall()
		period_maps = self._get_period_maps(auction_id)
		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
		result: dict[tuple[int, int], float] = {}
		for row in rows:
			well_id = int(row["well_id"]) if row["well_id"] is not None else 0
			take_date = str(row["take_date"] or "").strip()
			period_idx = pumping_iso_to_idx[take_date]
			if well_id <= 0 or period_idx <= 0: continue
			result[(well_id, period_idx)] = float(row["quota_auction_start"] or 0.0)
		return result

	def get_well_dual_prices(self, auction_id: int) -> dict[tuple[int, int], float]:
		"""Return {(well_id, period_idx): price} for all well_quota rows in the auction."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT well_id, take_date, price FROM well_quota WHERE auction_id=?", (auction_id,)).fetchall()
		period_maps = self._get_period_maps(auction_id)
		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
		result: dict[tuple[int, int], float] = {}
		for row in rows:
			well_id = int(row["well_id"]) if row["well_id"] is not None else 0
			take_date = str(row["take_date"] or "").strip()
			period_idx = pumping_iso_to_idx[take_date]
			if well_id <= 0 or period_idx <= 0: continue
			result[(well_id, period_idx)] = float(row["price"] or 0.0)
		return result

	def set_quota_for_auction (self, auction_id: int, source_auction_id: int | None = None) -> int:
		period_labels = self._get_period_maps(auction_id)["pumping_labels"]
		with self.connect_to_db() as conn:
			if conn.execute("SELECT 1 FROM well_quota WHERE auction_id=? LIMIT 1", (auction_id,)).fetchone() is not None: return 0
			inserted = 0
			if auction_id == 1: # For auction 1: populate quota from well_license
				for row in conn.execute("""SELECT wl.trader_id, wl.well_id, wl.license_quantity, wl.bid_period 
						FROM well_license wl ORDER BY wl.bid_period, wl.well_id""").fetchall():
					period_id = row["bid_period"]
					if period_id < 1 or period_id > len(period_labels): continue
					conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, take_date) VALUES (?, ?, ?, ?, ?)",
								(row["trader_id"], 1, row["well_id"], row["license_quantity"], period_labels[period_id - 1]))
					inserted += 1
			else: # For auction >= 2: populate quota from previous auction's quota_auction_end (fallback: quota_scaled_start, then quota_auction_start)
				if source_auction_id is not None:
					source_id = source_auction_id
				else:
					row = conn.execute("SELECT MAX(auction_id) FROM well_quota WHERE auction_id < ?", (auction_id,)).fetchone()
					source_id = int(row[0]) if row and row[0] is not None else 0
				if source_id <= 0: return 0
				take_dates = [str(r["take_date"] or "") for r in conn.execute("SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL ORDER BY take_date", (source_id,)).fetchall()]
				source_period_idx = {take_date: idx + 1 for idx, take_date in enumerate(take_dates[1:1 + len(period_labels)])}
				for row in conn.execute("SELECT trader_id, well_id, quota_auction_start, quota_auction_end, quota_scaled_start, take_date FROM well_quota WHERE auction_id=? ORDER BY take_date, well_id", (source_id,)).fetchall():
					take_date = str(row["take_date"] or "")
					if take_date not in source_period_idx: continue
					period_id = source_period_idx[take_date]
					if period_id < 1 or period_id > len(period_labels): continue
					quota_start = row["quota_auction_end"] if row["quota_auction_end"] is not None else (row["quota_scaled_start"] if row["quota_scaled_start"] is not None else row["quota_auction_start"])
					conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, quota_scaled_start, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
								(row["trader_id"], auction_id, row["well_id"], quota_start, row["quota_scaled_start"], period_labels[period_id - 1]))
					inserted += 1
			return inserted

# 	def set_quota_auction_end(self, auction_id: int, well_id: int, take_date: str, quota_auction_end: float | None, price: float | None) -> None:
# 		with self.connect_to_db() as conn:
# 			cursor = conn.execute("UPDATE well_quota SET quota_auction_end=?, price=? WHERE auction_id=? AND well_id=? AND take_date=?", (quota_auction_end, price, auction_id, well_id, take_date))
# 			if cursor.rowcount:
# 				return
# 			base_row = conn.execute("SELECT trader_id, quota_auction_start, quota_scaled_start FROM well_quota WHERE auction_id IN (?, 0) AND well_id=? AND take_date=? ORDER BY CASE WHEN auction_id=? THEN 0 ELSE 1 END LIMIT 1", (auction_id, well_id, take_date, auction_id)).fetchone()
# 			trader_row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
# 			trader_id = trader_row["trader_id"] if trader_row is not None else None
# 			quota_auction_start = base_row["quota_auction_start"] if base_row is not None else None
# 			quota_scaled_start = base_row["quota_scaled_start"] if base_row is not None else None
# 			if base_row is not None and base_row["trader_id"] is not None:
# 				trader_id = base_row["trader_id"]
# 			conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, quota_scaled_start, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (trader_id, auction_id, well_id, quota_auction_start, quota_scaled_start, quota_auction_end, price, take_date))

	def set_quota_scaled_start_bulk(self, auction_id: int, quota_scaled_start_by_period: dict[tuple[int, int], float]) -> int:
		"""Update well_quota.quota_scaled_start for one auction using (well_id, pumping_period) keys."""
		period_labels = self._get_period_maps(auction_id)["pumping_labels"]
		with self.connect_to_db() as conn:
			updated = 0
			for (well_id, pumping_period), quota_scaled_start in quota_scaled_start_by_period.items():
				take_date = period_labels[pumping_period - 1]
				cursor = conn.execute("UPDATE well_quota SET quota_scaled_start=? WHERE auction_id=? AND well_id=? AND take_date=?", (quota_scaled_start, auction_id, well_id, take_date))
				if 1 != cursor.rowcount: raise ValueError(f"Expected one well_quota row for auction_id={auction_id}, well_id={well_id}, take_date={take_date}; updated={cursor.rowcount}")
				updated += 1
			return updated

	# For each well_quota, set quota_auction_start to min {over all control point events} (well_license*(available head)/(licensed_demand))
# 	def set_first_auction_adjusted_quota (self) -> None:
# 		with self.connect_to_db() as conn:
# 			conn.execute("DROP TABLE IF EXISTS temp.tmp_first_auction_periods")
# 			conn.execute("DROP TABLE IF EXISTS temp.tmp_first_auction_alpha")
# 			conn.execute("CREATE TEMP TABLE tmp_first_auction_periods (well_id INTEGER NOT NULL, pumping_period INTEGER NOT NULL, PRIMARY KEY (well_id, pumping_period))")
# 			conn.execute("CREATE TEMP TABLE tmp_first_auction_alpha (well_id INTEGER NOT NULL, pumping_period INTEGER NOT NULL, min_alpha REAL NOT NULL, PRIMARY KEY (well_id, pumping_period))")
# 			conn.execute("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
# 					FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=1 AND take_date IS NOT NULL))
# 				INSERT INTO tmp_first_auction_periods(well_id, pumping_period)
# 				SELECT DISTINCT wq.well_id, tdi.pumping_period
# 				FROM well_quota wq
# 				JOIN take_date_idx tdi ON tdi.take_date = wq.take_date
# 				WHERE wq.auction_id=1""")
# 			conn.execute("""WITH effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
# 					FROM (SELECT DISTINCT effect_date FROM aquifer_head_limits WHERE effect_date IS NOT NULL))
# 				INSERT INTO tmp_first_auction_alpha(well_id, pumping_period, min_alpha)
# 				SELECT periods.well_id, periods.pumping_period, MIN(ahl.allowable_head_change / ahl.license_demand) AS min_alpha
# 				FROM tmp_first_auction_periods periods
# 				JOIN response_matrix rm ON rm.well_id = periods.well_id AND rm.pumping_period = periods.pumping_period
# 				JOIN effect_date_idx edi ON edi.effect_period = rm.effect_period
# 				JOIN aquifer_head_limits ahl ON ahl.control_point_id = rm.control_point_id AND ahl.effect_date = edi.effect_date
# 				WHERE rm.pumping_period <= rm.effect_period AND ahl.allowable_head_change < 0.0 AND ahl.license_demand < 0.0
# 				GROUP BY periods.well_id, periods.pumping_period""")
# 			conn.execute("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
# 					FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=1 AND take_date IS NOT NULL))
# 				UPDATE well_quota
# 				SET quota_auction_start = quota_auction_start * (SELECT tmp_first_auction_alpha.min_alpha
# 					FROM take_date_idx tdi
# 					JOIN tmp_first_auction_alpha ON tmp_first_auction_alpha.well_id = well_quota.well_id AND tmp_first_auction_alpha.pumping_period = tdi.pumping_period
# 					WHERE tdi.take_date = well_quota.take_date)
# 				WHERE auction_id=1 AND EXISTS (SELECT 1 FROM take_date_idx tdi
# 					JOIN tmp_first_auction_alpha ON tmp_first_auction_alpha.well_id = well_quota.well_id AND tmp_first_auction_alpha.pumping_period = tdi.pumping_period
# 					WHERE tdi.take_date = well_quota.take_date)""")
# 			conn.execute("DROP TABLE IF EXISTS temp.tmp_first_auction_periods")
# 			conn.execute("DROP TABLE IF EXISTS temp.tmp_first_auction_alpha")
# 			return

# 	# 7. Response matrix, control points  --------------------------------------------------------
	def bounds_imported_at(self) -> str | None:
		try:
			with self.connect_to_db() as conn:
				row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key='mps_loaded_date'").fetchone()
				if row and row[0]: return str(row[0])
				return None
		except Exception: return None

	def get_quota_by_well_pumping_period(self, auction_id: int) -> dict[tuple[int, int], float]:
		return self.get_quota(auction_id)

	def set_license_demand_on_aquifer(self) -> int:
		"""Write aquifer_head_limits.license_demand from well_license and response factors."""
		with self.connect_to_db() as conn:
			conn.execute("DROP TABLE IF EXISTS temp.tmp_license_demand")
			conn.execute("CREATE TEMP TABLE tmp_license_demand (control_point_id INTEGER NOT NULL, effect_date TEXT NOT NULL, license_demand REAL NOT NULL, PRIMARY KEY (control_point_id, effect_date))")
			conn.execute("""WITH effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
					FROM (SELECT DISTINCT effect_date FROM aquifer_head_limits WHERE effect_date IS NOT NULL)),
				license_period_values AS (SELECT rm.control_point_id, rm.effect_period, SUM(wl.license_quantity * rm.response) AS license_demand
					FROM well_license wl
					JOIN response_matrix rm ON rm.well_id = wl.well_id AND rm.pumping_period = wl.bid_period
					WHERE rm.pumping_period <= rm.effect_period
					GROUP BY rm.control_point_id, rm.effect_period)
				INSERT INTO tmp_license_demand(control_point_id, effect_date, license_demand)
				SELECT license_period_values.control_point_id, effect_date_idx.effect_date, license_period_values.license_demand
				FROM license_period_values
				JOIN effect_date_idx ON effect_date_idx.effect_period = license_period_values.effect_period""")
			before_changes = conn.total_changes
			conn.execute("UPDATE aquifer_head_limits SET license_demand = 0.0 WHERE effect_date IS NOT NULL")
			conn.execute("""UPDATE aquifer_head_limits
				SET license_demand = (SELECT tmp_license_demand.license_demand FROM tmp_license_demand
					WHERE tmp_license_demand.control_point_id = aquifer_head_limits.control_point_id
					  AND tmp_license_demand.effect_date = aquifer_head_limits.effect_date)
				WHERE EXISTS (SELECT 1 FROM tmp_license_demand
					WHERE tmp_license_demand.control_point_id = aquifer_head_limits.control_point_id
					  AND tmp_license_demand.effect_date = aquifer_head_limits.effect_date)""")
			conn.execute("DROP TABLE IF EXISTS temp.tmp_license_demand")
			return conn.total_changes - before_changes

# 	def get_license_demand_by_cp_effect_period(self, auction_id: int) -> dict[tuple[int, int], float]:
# 		"""Return {(control_point_id, effect_period): license_demand} from aquifer_head_limits."""
# 		effect_date_to_idx = self._get_period_maps(auction_id)["effect_iso_to_idx"]
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("SELECT control_point_id, effect_date, license_demand FROM aquifer_head_limits WHERE license_demand IS NOT NULL").fetchall()
# 		return {(int(row["control_point_id"]), effect_date_to_idx[str(row["effect_date"])]): float(row["license_demand"]) for row in rows if str(row["effect_date"]) in effect_date_to_idx}
# 
# 	def get_allowable_head_change_by_cp_effect_date(self, auction_id: int) -> tuple[dict[tuple[int, str], float], dict[str, int]]:
# 		effect_date_to_idx = self._get_period_maps(auction_id)["effect_iso_to_idx"]
# 		with self.connect_to_db() as conn:
# 			# We typically expect allowable_head_change < 0. Minimum head is something like 830, head at auction start like 833, so allowable_head_change = -3.0.
# 			rows = conn.execute("""SELECT cpe.control_point_id, cpe.effect_date, (ahl.minimum_head - cpe.committed_head_auction_start) AS allowable_head_change
# 				FROM control_point_events cpe
# 				JOIN aquifer_head_limits ahl ON ahl.control_point_id = cpe.control_point_id AND ahl.effect_date = cpe.effect_date
# 				WHERE cpe.auction_id=?""", (auction_id,)).fetchall()
# 			allowable_head_change: dict[tuple[int, str], float] = {}
# 			for row in rows:
# 				effect_date = str(row["effect_date"] or "")
# 				if effect_date not in effect_date_to_idx: continue
# 				allowable_head_change[(int(row["control_point_id"]), effect_date)] = float(row["allowable_head_change"] or 0.0)
# 			return allowable_head_change, effect_date_to_idx

	def get_control_point_events(self, auction_id: int) -> list[dict[str, Any]]:
		"""Return all control_point_events rows for the given auction_id as dicts with control_point_id, effect_date, allowable_head_change, dual_price, slack, alpha."""
		with self.connect_to_db() as conn:
			rows = conn.execute("""SELECT cpe.cpe_id, cpe.control_point_id, cpe.effect_date, (ahl.minimum_head - cpe.committed_head_auction_start) AS allowable_head_change, cpe.dual_price, cpe.slack, cpe.alpha
				FROM control_point_events cpe
				JOIN aquifer_head_limits ahl ON ahl.control_point_id = cpe.control_point_id AND ahl.effect_date = cpe.effect_date
				WHERE cpe.auction_id=?""", (auction_id,)).fetchall()
			return [dict(row) for row in rows]

# 	def get_committed_heads_by_cp_effect_date(self, auction_id: int) -> dict[tuple[int, str], tuple[float | None, float | None]]:
# 		"""Return {(control_point_id, effect_date): (committed_head_auction_start, committed_head_auction_end)}."""
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""SELECT control_point_id, effect_date, committed_head_auction_start, committed_head_auction_end
# 				FROM control_point_events WHERE auction_id=?""", (auction_id,)).fetchall()
# 			return {(int(row["control_point_id"]), str(row["effect_date"])): (row["committed_head_auction_start"], row["committed_head_auction_end"]) for row in rows}

	def get_first_open_auction_control_point_events(self) -> tuple[int, list[dict[str, Any]]]:
		"""Return (auction_id, rows) from control_point_events for the first open auction.

		If no auction is open, fall back to the latest auction that has control_point_events rows.
		"""
		with self.connect_to_db() as conn:
			next_auction = self.get_next_auction_info()
			if next_auction is not None: auction_id = int(next_auction["auction_id"])
			else:
				row = conn.execute("""SELECT cpe.auction_id FROM control_point_events cpe ORDER BY cpe.auction_id DESC LIMIT 1""").fetchone()
				if row is None: raise ValueError("No auction control-point events available")
				auction_id = int(row["auction_id"])

			rows = conn.execute("""SELECT control_point_id, effect_date, committed_head_auction_start, committed_allowable_head_change
				FROM control_point_events WHERE auction_id=? ORDER BY control_point_id, effect_date""", (auction_id,)).fetchall()
			if not rows: raise ValueError(f"No control_point_events rows for auction {auction_id}")
			return auction_id, [dict(row) for row in rows]

# 	def get_control_points_for_auction(self, auction_id: int) -> list[dict[str, int | str | dict[int, float] | None]]:
# 		period_label_to_idx = self._get_period_maps(auction_id)["effect_iso_to_idx"]
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""SELECT c.control_point_id, c.name, c.gw_model_row, c.gw_model_column, c.latitude, c.longitude, e.effect_date, (ahl.minimum_head - e.committed_head_auction_start) AS allowable_head_change
# 				FROM control_points c
# 				JOIN control_point_events e ON e.control_point_id = c.control_point_id
# 				JOIN aquifer_head_limits ahl ON ahl.control_point_id = e.control_point_id AND ahl.effect_date = e.effect_date
# 				WHERE e.auction_id=? ORDER BY c.control_point_id, e.effect_date""", (auction_id,)).fetchall()
# 			control_points: dict[int, dict[str, Any]] = {}
# 			for row in rows:
# 				control_point_id = int(row["control_point_id"])
# 				try: control_point = control_points[control_point_id]
# 				except KeyError:
# 					control_point = cast(dict[str, Any], {"id": control_point_id, "name": str(row["name"]), "bound_by_period": {}, "gw_model_row": row["gw_model_row"], "gw_model_column": row["gw_model_column"], "latitude": row["latitude"], "longitude": row["longitude"]})
# 					control_points[control_point_id] = control_point
# 				period_id = period_label_to_idx[str(row["effect_date"] or "")]
# 				bound_by_period = cast(dict[int, float], control_point["bound_by_period"])
# 				bound_by_period[period_id] = float(row["allowable_head_change"] or 0.0)
# 			return list(control_points.values())
# 
# 	def get_control_point_ids(self) -> list[int]:
# 		"""Return control point ids in ascending order."""
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("SELECT control_point_id FROM control_points ORDER BY control_point_id").fetchall()
# 		return [int(row["control_point_id"]) for row in rows]
# 
# 	def get_control_point_rhs(self, auction_id: int) -> dict[tuple[int, int], float]:
# 		"""Return {(control_point_id, period_idx): allowable_head_change} for the given auction."""
# 		period_label_to_idx = self._get_period_maps(auction_id)["effect_iso_to_idx"]
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""SELECT cpe.control_point_id, cpe.effect_date, (ahl.minimum_head - cpe.committed_head_auction_start) AS allowable_head_change
# 				FROM control_point_events cpe
# 				JOIN aquifer_head_limits ahl ON ahl.control_point_id = cpe.control_point_id AND ahl.effect_date = cpe.effect_date
# 				WHERE cpe.auction_id=?""", (auction_id,)).fetchall()
# 			result: dict[tuple[int, int], float] = {}
# 			for row in rows:
# 				period_idx = period_label_to_idx[str(row["effect_date"] or "")]
# 				result[(row["control_point_id"], period_idx)] = float(row["allowable_head_change"] or 0.0)
# 			return result

	def get_all_response_factors(self) -> list[ResponseFactor]:
		"""Retrieve all response factors as dictionaries."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, response FROM response_matrix ORDER BY control_point_id, effect_period, pumping_period, well_id").fetchall()
			return [{"well_id": int(row["well_id"]), "control_point_id": int(row["control_point_id"]), "pumping_period": int(row["pumping_period"]), "effect_period": int(row["effect_period"]), "value": float(row["response"])} for row in rows]

	def get_response_factors_for_auction(self, auction_id: int) -> dict[tuple[int, int, int, int], float]:
		"""Return response coefficients remapped from global periods to auction-local periods.

		Assumption: response_matrix is calendar-independent. Period 1 in response_matrix
		always means the first pumping period of the hydrology model, regardless of auction date.
		This accessor translates those global period indices to the current auction's local timeline
		and returns a flat dict keyed by (well_id, pumping_period, effect_period, control_point_id).
		"""
		period_maps = self._get_period_maps(auction_id)
		auction_effect_period_count = len(period_maps["effect_labels"])
		auction_pumping_period_count = len(period_maps["pumping_labels"])
		global_effect_period_count = self.response_matrix_period_count()
		period_offset = max(0, global_effect_period_count - auction_effect_period_count)
		mapped: dict[tuple[int, int, int, int], float] = {}
		for factor in self.get_all_response_factors():
			local_effect_period = int(factor["effect_period"]) - period_offset
			local_pumping_period = int(factor["pumping_period"]) - period_offset
			if local_effect_period < 1 or local_effect_period > auction_effect_period_count: continue
			if local_pumping_period < 1 or local_pumping_period > auction_pumping_period_count: continue
			mapped[(int(factor["well_id"]), local_pumping_period, local_effect_period, int(factor["control_point_id"]))] = float(factor["value"])
		return mapped

# 	def get_response_factors_for_cp_period(self, control_point_id: int, effect_period: int) -> dict[tuple[int, int], float]:
# 		"""Retrieve response coefficients for a control point and effect period as a flat dict keyed by (well_id, pumping_period)."""
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, response FROM response_matrix WHERE control_point_id=? AND effect_period=? ORDER BY pumping_period, well_id", (control_point_id, effect_period)).fetchall()
# 			return {(int(row["well_id"]), int(row["pumping_period"])): float(row["response"]) for row in rows}
# 
	def response_matrix_period_count(self) -> int: # Should be the same as Catchment_info.gwm_num_control_periods.
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT COALESCE(MAX(effect_period), 0) AS n FROM response_matrix").fetchone()
			return int(row["n"] or 0)

	def set_control_point_events_for_auction(self, auction_id: int, source_auction_id: int | None = None) -> int:
		with self.connect_to_db() as conn:
			if conn.execute("SELECT 1 FROM control_point_events WHERE auction_id=? LIMIT 1", (auction_id,)).fetchone() is not None: return 0
			auction_row = conn.execute("SELECT firstWaterTakeDate FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
			first_take_date = str(auction_row["firstWaterTakeDate"]) if auction_row else None
			if first_take_date is None: return 0
			cur = conn.execute("""INSERT INTO control_point_events(control_point_id, auction_id, effect_date, planned_head_auction_start, committed_head_auction_start, committed_allowable_head_change)
				SELECT ahl.control_point_id, ?, ahl.effect_date,
					COALESCE((SELECT prev.planned_head_auction_end FROM control_point_events prev
						 WHERE prev.control_point_id = ahl.control_point_id AND prev.effect_date = ahl.effect_date
						   AND prev.auction_id = (SELECT MAX(cpe2.auction_id) FROM control_point_events cpe2
						                          WHERE cpe2.control_point_id = ahl.control_point_id AND cpe2.effect_date = ahl.effect_date AND cpe2.auction_id < ?)),
						ahl.actual_start_head),
					COALESCE((SELECT prev.committed_head_auction_end FROM control_point_events prev
						 WHERE prev.control_point_id = ahl.control_point_id AND prev.effect_date = ahl.effect_date
						   AND prev.auction_id = (SELECT MAX(cpe2.auction_id) FROM control_point_events cpe2
						                          WHERE cpe2.control_point_id = ahl.control_point_id AND cpe2.effect_date = ahl.effect_date AND cpe2.auction_id < ?)),
						ahl.actual_start_head),
					ahl.minimum_head - COALESCE(
						(SELECT prev.committed_head_auction_end FROM control_point_events prev
						 WHERE prev.control_point_id = ahl.control_point_id AND prev.effect_date = ahl.effect_date
						   AND prev.auction_id = (SELECT MAX(cpe2.auction_id) FROM control_point_events cpe2
						                          WHERE cpe2.control_point_id = ahl.control_point_id AND cpe2.effect_date = ahl.effect_date AND cpe2.auction_id < ?)),
						ahl.actual_start_head)
				FROM aquifer_head_limits ahl WHERE ahl.effect_date >= ?
				ORDER BY ahl.control_point_id, ahl.effect_date""", (auction_id, auction_id, auction_id, auction_id, first_take_date))
			return cur.rowcount

# 	def set_control_point_event_results(self, auction_id: int, control_point_id: int, effect_date: str, slack: float | None, dual_price: float | None) -> None:
# 		with self.connect_to_db() as conn:
# 			conn.execute("""UPDATE control_point_events SET slack=?, dual_price=?, 
# 				planned_head_auction_end = (SELECT ahl.minimum_head FROM aquifer_head_limits ahl WHERE ahl.control_point_id=? AND ahl.effect_date=?) + ? 
# 				WHERE auction_id=? AND control_point_id=? AND effect_date=?""",
# 				(slack, dual_price, control_point_id, effect_date, slack, auction_id, control_point_id, effect_date))
# 
# 	def set_committed_head_change(self, auction_id: int, control_point_id: int, effect_date: str, committed_head_change: float) -> None:
# 		with self.connect_to_db() as conn:
# 			conn.execute("""UPDATE control_point_events
# 				SET committed_head_auction_end = committed_head_auction_start + ?,
# 					committed_allowable_head_change = (SELECT ahl.minimum_head FROM aquifer_head_limits ahl WHERE ahl.control_point_id=? AND ahl.effect_date=?) - (committed_head_auction_start + ?)
# 				WHERE auction_id=? AND control_point_id=? AND effect_date=?""",
# 				(committed_head_change, control_point_id, effect_date, committed_head_change, auction_id, control_point_id, effect_date))

	def set_auction_solution_results_bulk(self, auction_id: int, quota_rows: list[tuple[int, str, float | None, float | None]], control_point_rows: list[tuple[str, int, float | None, float | None]],
		committed_rows: list[tuple[str, int, float]], environmental_rows: list[tuple[int, int, float | None, float | None, float | None]] | None = None,) -> None:
		"""Persist all post-solve rows using one DB connection/transaction."""
		with self.connect_to_db() as conn:
			for well_id, take_date, quota_auction_end, price in quota_rows:
				cursor = conn.execute("UPDATE well_quota SET quota_auction_end=?, price=? WHERE auction_id=? AND well_id=? AND take_date=?", (quota_auction_end, price, auction_id, well_id, take_date),)
				if cursor.rowcount: continue
				base_row = conn.execute("SELECT trader_id, quota_auction_start, quota_scaled_start FROM well_quota WHERE auction_id IN (?, 0) AND well_id=? AND take_date=? ORDER BY CASE WHEN auction_id=? THEN 0 ELSE 1 END LIMIT 1",
					(auction_id, well_id, take_date, auction_id),).fetchone()
				trader_row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
				trader_id = trader_row["trader_id"] # if trader_row is not None else None
				quota_auction_start = base_row["quota_auction_start"] # if base_row is not None else None
				quota_scaled_start = base_row["quota_scaled_start"] # if base_row is not None else None
				if base_row is not None and base_row["trader_id"] is not None: trader_id = base_row["trader_id"]
				conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, quota_scaled_start, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
					(trader_id, auction_id, well_id, quota_auction_start, quota_scaled_start, quota_auction_end, price, take_date),)

			conn.executemany("""UPDATE control_point_events SET slack=?, dual_price=?,
					planned_head_auction_end = (SELECT ahl.minimum_head FROM aquifer_head_limits ahl WHERE ahl.control_point_id=? AND ahl.effect_date=?) + ?
					WHERE auction_id=? AND control_point_id=? AND effect_date=?""",
				[(slack, dual_price, control_point_id, effect_date, slack, auction_id, control_point_id, effect_date) for effect_date, control_point_id, slack, dual_price in control_point_rows],)

			conn.executemany("""UPDATE control_point_events
				SET committed_head_auction_end = committed_head_auction_start + ?,
					committed_allowable_head_change = (SELECT ahl.minimum_head FROM aquifer_head_limits ahl WHERE ahl.control_point_id=? AND ahl.effect_date=?) - (committed_head_auction_start + ?)
				WHERE auction_id=? AND control_point_id=? AND effect_date=?""",
				[(committed_head_change, control_point_id, effect_date, committed_head_change, auction_id, control_point_id, effect_date) for effect_date, control_point_id, committed_head_change in committed_rows],)

			if environmental_rows:
				for trader_id, cpe_id, traded_head_start, traded_head_end, price in environmental_rows:
					cursor = conn.execute("UPDATE environmental_position SET traded_head_start=?, traded_head_end=?, price=? WHERE auction_id=? AND trader_id=? AND cpe_id=?",
						(traded_head_start, traded_head_end, price, auction_id, trader_id, cpe_id))
					if cursor.rowcount: continue
					conn.execute("INSERT INTO environmental_position(auction_id, trader_id, cpe_id, traded_head_start, traded_head_end, price) VALUES (?, ?, ?, ?, ?, ?)",
						(auction_id, trader_id, cpe_id, traded_head_start, traded_head_end, price))

	def latest_aquifer_head_limits_upload_date(self) -> str | None:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT MAX(upload_date) AS upload_date FROM aquifer_head_limits").fetchone()
			if row is None or row["upload_date"] is None: return None
			return str(row["upload_date"])

	def latest_aquifer_head_adjustment_notice(self) -> dict[str, Any] | None:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT text_value FROM Catchment_info WHERE meta_key='latest_aquifer_head_adjustment_notice'").fetchone()
			if row is None or not row["text_value"]: return None
			return cast(dict[str, Any], json.loads(str(row["text_value"])))

	def update_aquifer_head_limits(self, csv_text: str) -> int:
		rows = list(csv.DictReader(csv_text.splitlines()))
		if not rows: raise ValueError("CSV is empty")
		required_columns = {"control_point_id", "effect_date", "actual_start_head"}
		if not required_columns.issubset(set(rows[0].keys())): raise ValueError("CSV must contain control_point_id, effect_date, actual_start_head")

		next_auction = self.get_next_auction_info()
		if next_auction is None: raise ValueError("No open auction available")
		auction_id = int(next_auction["auction_id"])
		first_pumping_date = str(self.get_auction_info(auction_id)["first_pumping_date"])

		parsed_rows: list[tuple[int, str, float]] = []
		for row in rows: parsed_rows.append((int(row["control_point_id"]), str(row["effect_date"]), float(row["actual_start_head"])))

		first_effect_date = min(effect_date for _control_point_id, effect_date, _actual_start_head in parsed_rows)
		if first_effect_date != first_pumping_date:
			raise ValueError(f"First effect_date {first_effect_date} does not match first pumping date {first_pumping_date}")

		csv_keys = {(control_point_id, effect_date) for control_point_id, effect_date, _actual_start_head in parsed_rows}
		with self.connect_to_db() as conn:
			expected_rows = conn.execute("SELECT control_point_id, effect_date FROM control_point_events WHERE auction_id=?", (auction_id,)).fetchall()
			expected_keys = {(int(row["control_point_id"]), str(row["effect_date"])) for row in expected_rows}
			if csv_keys != expected_keys: raise ValueError("CSV control_point_id/effect_date rows must exactly match control_point_events for the first open auction")

			minimum_head_rows = conn.execute("SELECT control_point_id, effect_date, minimum_head FROM aquifer_head_limits").fetchall()
			minimum_head_by_key = {(int(row["control_point_id"]), str(row["effect_date"])): row["minimum_head"] for row in minimum_head_rows}
			control_point_rows = conn.execute("SELECT control_point_id, name FROM control_points").fetchall()
			control_point_name_by_id = {int(row["control_point_id"]): str(row["name"] or "") for row in control_point_rows}
			upload_date = datetime.now().isoformat(timespec="minutes")
			updates: list[tuple[str, float, float, int, str]] = []
			raised_rows: list[dict[str, int | str | float]] = []
			adjusted_start_head_by_key: dict[tuple[int, str], float] = {}
			for control_point_id, effect_date, actual_start_head in parsed_rows:
				minimum_head = minimum_head_by_key[(control_point_id, effect_date)]
				if minimum_head is None: raise ValueError(f"Missing minimum_head for control_point_id={control_point_id}, effect_date={effect_date}")
				minimum_head_value = float(minimum_head)
				adjusted_actual_start_head = actual_start_head
				if minimum_head_value > actual_start_head:
					adjusted_actual_start_head = minimum_head_value
					raised_rows.append({"control_point_id": control_point_id, "control_point_name": control_point_name_by_id.get(control_point_id, ""), "effect_date": effect_date,
						"uploaded_actual_start_head": actual_start_head, "raised_to": adjusted_actual_start_head, "difference": adjusted_actual_start_head - actual_start_head})
				allowable_head_change = minimum_head_value - adjusted_actual_start_head
				updates.append((upload_date, adjusted_actual_start_head, allowable_head_change, control_point_id, effect_date))
				adjusted_start_head_by_key[(control_point_id, effect_date)] = adjusted_actual_start_head

			top_raised_rows: list[dict[str, int | str | float]] = sorted(raised_rows, key=lambda row: float(row["difference"]), reverse=True)[:3]
			notice_payload: dict[str, Any] = {"upload_date": upload_date, "adjusted_count": len(raised_rows), "top_rows": top_raised_rows}

			conn.executemany("""UPDATE aquifer_head_limits
				SET upload_date=?, actual_start_head=?, allowable_head_change=?, license_demand=NULL, head_constraint_upper_bound=NULL
				WHERE control_point_id=? AND effect_date=?""", updates)
			conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value) VALUES ('latest_aquifer_head_adjustment_notice', ?)", (json.dumps(notice_payload),))

			conn.executemany("""UPDATE control_point_events
				SET committed_head_auction_start=?,
					committed_allowable_head_change=(SELECT ahl.minimum_head FROM aquifer_head_limits ahl WHERE ahl.control_point_id=? AND ahl.effect_date=?) - ?,
					planned_head_auction_start=?,
					committed_head_auction_end=NULL,
					planned_head_auction_end=NULL,
					slack=NULL,
					dual_price=NULL,
					alpha=NULL
				WHERE auction_id=? AND control_point_id=? AND effect_date=?""",
				[(adjusted_start_head_by_key[(control_point_id, effect_date)], control_point_id, effect_date,
					adjusted_start_head_by_key[(control_point_id, effect_date)], adjusted_start_head_by_key[(control_point_id, effect_date)],
					auction_id, control_point_id, effect_date) for control_point_id, effect_date, _actual_start_head in parsed_rows],
			)
			return len(updates)

	# 8. Rights -------------------------------------------------------------------------
	# Amazing query by Claude, though I gave it a lot of guidance. The same as AuctionController.compute_alphas, but for just one well. SQL is faster in this case than Python.
	# Used for showing the well constraint quota on Trader.html.
	# UNUSED
# 	def get_well_constraint_quota(self, well_id: int, auction_id: int) -> dict[tuple[int, int, int], float]:
# 		"""Return {(pumping_period, effect_period, control_point_id): constraint_quota} for one well.
# 		well_constraint_quota is the allowable head change in effect_date at cp_id allocated to well_id in pumping_period. Likely negative!
# 
# 		The SQL reconstructs pumping_period from ordered well_quota.take_date and effect_period from ordered control_point_events.effect_date,
# 		then computes constraint_quota(w,u,t,k) = q(w,u) * F(w,u,t,k) * allowable_head_change(t,k) / denom(k,t)
# 		where denom(k,t) = SUM(q(v,r) * F(v,r,t,k)) over all wells v and pumping periods r <= t. 
# 		denom(k,t) is the drawdown from all quota_auction_start at control_point_id k in effect_period t.
# 		In other places, I define alpha (k,t) = allowable_head_change(t,k) / denom(k,t), which is the fraction of allocated change in head. 
# 		So alpha (k,t) > 1 means people can take more, alpha (k,t) > 1 means must take less.
# 
# 		Requires a valid auction_id with timeline metadata.
# 		"""
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
# 						FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL)),
# 					effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
# 						FROM (SELECT DISTINCT effect_date FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL)),
# 					q AS (SELECT wq.well_id, wq.take_date, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq
# 						JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=?),
# 					cpe AS (SELECT cp.control_point_id, edi.effect_period, ahl.allowable_head_change FROM control_point_events cp
# 						JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date
# 						JOIN aquifer_head_limits ahl ON ahl.control_point_id = cp.control_point_id AND ahl.effect_date = cp.effect_date
# 						WHERE cp.auction_id=?),
# 					denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.response) AS total_load FROM response_matrix rm
# 						JOIN q ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period WHERE rm.pumping_period <= rm.effect_period
# 						GROUP BY rm.control_point_id, rm.effect_period)
# 				SELECT q.take_date, rm.effect_period, rm.control_point_id, q.quota_auction_start * rm.response * cpe.allowable_head_change / denom.total_load AS constraint_quota
# 				FROM response_matrix rm
# 				JOIN q     ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period
# 				JOIN cpe   ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
# 				JOIN denom ON denom.control_point_id=rm.control_point_id AND denom.effect_period=rm.effect_period
# 				WHERE rm.well_id=? AND denom.total_load != 0.0
# 				ORDER BY q.take_date, rm.effect_period, rm.control_point_id
# 			""", (auction_id, auction_id, auction_id, auction_id, well_id)).fetchall()
# 		period_maps = self._get_period_maps(auction_id)
# 		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
# 		result: dict[tuple[int, int, int], float] = {}
# 		for r in rows:
# 			pumping_period = pumping_iso_to_idx[str(r["take_date"] or "")]
# 			result[(pumping_period, int(r["effect_period"]), int(r["control_point_id"]))] = float(r["constraint_quota"] or 0.0)
# 		return result
# 
# 	# UNUSED
# 	def get_well_constraint_quota1(self, well_id: int, auction_id: int) -> dict[tuple[int, int, int], float]:
# 		"""Return {(pumping_period, effect_period, control_point_id): constraint_quota} using precomputed cpe.alpha.
# 
# 		Assumes calculate_and_set_constraint_alphas has already populated control_point_events.alpha
# 		for the provided auction_id.
# 		"""
# 		with self.connect_to_db() as conn:
# 			rows = conn.execute("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
# 						FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL)),
# 					q AS (SELECT wq.well_id, wq.take_date, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq
# 						JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=?),
# 					effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
# 						FROM (SELECT DISTINCT effect_date FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL)),
# 					cpe AS (SELECT cp.control_point_id, cp.effect_date, cp.alpha, edi.effect_period FROM control_point_events cp
# 						JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date WHERE cp.auction_id=?)
# 				SELECT q.take_date, rm.effect_period, rm.control_point_id, q.quota_auction_start * rm.response * cpe.alpha AS constraint_quota
# 				FROM response_matrix rm JOIN q   ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period JOIN cpe ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
# 				WHERE rm.well_id=?
# 				ORDER BY q.take_date, rm.effect_period, rm.control_point_id
# 			""", (auction_id, auction_id, auction_id, auction_id, well_id)).fetchall()
# 		period_maps = self._get_period_maps(auction_id)
# 		pumping_iso_to_idx = period_maps["pumping_iso_to_idx"]
# 		result: dict[tuple[int, int, int], float] = {}
# 		for r in rows:
# 			pumping_period = pumping_iso_to_idx[str(r["take_date"] or "")]
# 			result[(pumping_period, int(r["effect_period"]), int(r["control_point_id"]))] = float(r["constraint_quota"] or 0.0)
# 		return result

# 	# Key function! Claude wrote this, wrong at first. Now verified against function AuctionController.py compute_alphas().
	def calculate_and_set_constraint_alphas(self, auction_id: int) -> int:
		"""Compute and persist alpha on control_point_events for one auction.

		Source rows:
		- Quota inputs come from get_quota_by_well_pumping_period(auction_id).
		- Event rows to update are selected from control_point_events for auction_id.

		Computation (SQL CTE):
		- effect_period is reconstructed by ordering distinct effect_date values.
		- Local auction periods are shifted to global response_matrix periods using:
		  period_offset = max(0, max(response_matrix.effect_period) - auction_effect_period_count).
		- denom(control_point_id, effect_period) = SUM(quota_auction_start * response)
		  across response_matrix rows where pumping_period <= effect_period.
		- alpha assignment follows the current implementation exactly:
		  * If denom.total_load is NULL or 0.0, then alpha = NULL
		  * Otherwise alpha = allowable_head_change / denom.total_load

		The method uses a temporary table (tmp_quota_alpha) and returns the number of updated
		rows measured by conn.total_changes before/after the UPDATE.
		"""
		quota = self.get_quota_by_well_pumping_period(auction_id)
		with self.connect_to_db() as conn:
			source_auction_id = auction_id

			conn.execute("DROP TABLE IF EXISTS temp.tmp_quota_alpha")
			conn.execute("CREATE TEMP TABLE tmp_quota_alpha (well_id INTEGER NOT NULL, pumping_period INTEGER NOT NULL, quota_auction_start REAL NOT NULL, PRIMARY KEY (well_id, pumping_period))")
			if quota:
				conn.executemany("INSERT INTO tmp_quota_alpha(well_id, pumping_period, quota_auction_start) VALUES (?, ?, ?)",
					[(well_id, pumping_period, quota_value) for (well_id, pumping_period), quota_value in quota.items()],)

			before_changes = conn.total_changes
			conn.execute("""
				WITH effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period FROM (SELECT DISTINCT effect_date FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL)),
					auction_effect_count AS (SELECT COUNT(*) AS n FROM effect_date_idx),
					global_effect_count AS (SELECT COALESCE(MAX(effect_period), 0) AS n FROM response_matrix),
					period_offset AS (SELECT CASE WHEN global_effect_count.n > auction_effect_count.n THEN global_effect_count.n - auction_effect_count.n ELSE 0 END AS offset
						FROM global_effect_count, auction_effect_count),
				q AS (SELECT well_id, pumping_period, quota_auction_start FROM tmp_quota_alpha),
					q_global AS (SELECT q.well_id, q.quota_auction_start, q.pumping_period + period_offset.offset AS pumping_period_global
						FROM q JOIN period_offset),
				denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.response) AS total_load FROM response_matrix rm
					JOIN q_global q ON q.well_id = rm.well_id AND q.pumping_period_global = rm.pumping_period WHERE rm.pumping_period <= rm.effect_period GROUP BY rm.control_point_id, rm.effect_period),
				alpha_values AS (SELECT cpe.control_point_id, cpe.effect_date,
							CASE WHEN denom.total_load IS NULL OR denom.total_load = 0.0 THEN NULL ELSE (ahl.minimum_head - cpe.committed_head_auction_start) / denom.total_load
							END AS alpha FROM control_point_events cpe
						JOIN aquifer_head_limits ahl ON ahl.control_point_id = cpe.control_point_id AND ahl.effect_date = cpe.effect_date
					JOIN effect_date_idx edi ON edi.effect_date = cpe.effect_date
					JOIN period_offset ON 1=1
					LEFT JOIN denom ON denom.control_point_id = cpe.control_point_id AND denom.effect_period = edi.effect_period + period_offset.offset WHERE cpe.auction_id=?)
				UPDATE control_point_events
				SET alpha = (SELECT alpha_values.alpha FROM alpha_values WHERE alpha_values.control_point_id = control_point_events.control_point_id
					  AND alpha_values.effect_date = control_point_events.effect_date)
				WHERE auction_id=? AND EXISTS (SELECT 1 FROM alpha_values WHERE alpha_values.control_point_id = control_point_events.control_point_id AND alpha_values.effect_date = control_point_events.effect_date)
			""", (source_auction_id, source_auction_id, source_auction_id))
			conn.execute("DROP TABLE IF EXISTS temp.tmp_quota_alpha")
			return conn.total_changes - before_changes

	# Used only by compute_alphas. 
# 	def set_constraint_alphas(self, auction_id: int, alphas: dict[tuple[int, int | str], float]) -> None:
# 		period_labels = self._get_period_maps(auction_id)["pumping_labels"]
# 		with self.connect_to_db() as conn:
# 			for (cp_id, period_ref), alpha in alphas.items():
# 				if isinstance(period_ref, str):
# 					period_label = period_ref
# 				else:
# 					period_label = period_labels[period_ref - 1] if 1 <= period_ref <= len(period_labels) else None
# 				if not period_label: continue
# 				conn.execute("UPDATE control_point_events SET alpha = ? WHERE auction_id = ? AND control_point_id = ? AND effect_date = ?", (alpha, auction_id, cp_id, period_label),)

	def compute_constraint_quota_revenue_sql(self, auction_id: int) -> float:
		"""Compute constraint-quota revenue in SQL using response_matrix, well_quota, and control_point_events."""
		with self.connect_to_db() as conn:
			row = conn.execute("""WITH
					take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
						FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL)),
					effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
						FROM (SELECT DISTINCT effect_date FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL)),
					auction_effect_count AS (SELECT COUNT(*) AS n FROM effect_date_idx),
					global_effect_count AS (SELECT COALESCE(MAX(effect_period), 0) AS n FROM response_matrix),
					period_offset AS (SELECT CASE WHEN global_effect_count.n > auction_effect_count.n THEN global_effect_count.n - auction_effect_count.n ELSE 0 END AS offset
						FROM global_effect_count, auction_effect_count),
					q AS (SELECT wq.well_id, tdi.pumping_period, COALESCE(wq.quota_auction_start, 0.0) AS quota_start, COALESCE(wq.quota_auction_end, 0.0) AS quota_end
						FROM well_quota wq JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=?),
					q_global AS (SELECT q.well_id, q.quota_start, q.quota_end, q.pumping_period + period_offset.offset AS pumping_period_global
						FROM q JOIN period_offset),
					cpe AS (SELECT cpe.control_point_id, edi.effect_period, cpe.dual_price, cpe.alpha AS alpha
						FROM control_point_events cpe JOIN effect_date_idx edi ON edi.effect_date = cpe.effect_date WHERE cpe.auction_id=? AND cpe.dual_price IS NOT NULL),
					cpe_global AS (SELECT cpe.control_point_id, cpe.dual_price, cpe.alpha, cpe.effect_period + period_offset.offset AS effect_period_global
						FROM cpe JOIN period_offset)
				SELECT COALESCE(SUM(-1.0 * rm.response * cpe.dual_price * (q.quota_end - cpe.alpha * q.quota_start)), 0.0) AS revenue
				FROM response_matrix rm
				JOIN q_global q ON q.well_id = rm.well_id AND q.pumping_period_global = rm.pumping_period
				JOIN cpe_global cpe ON cpe.control_point_id = rm.control_point_id AND cpe.effect_period_global = rm.effect_period""", 
				(auction_id, auction_id, auction_id, auction_id)).fetchone()
			return float(row["revenue"] if row is not None and row["revenue"] is not None else 0.0)

	def compute_environmental_revenue_sql(self, auction_id: int) -> float:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT COALESCE(SUM(COALESCE(traded_head_end, 0.0) * ABS(COALESCE(price, 0.0))), 0.0) AS revenue FROM environmental_position WHERE auction_id=?", (auction_id,)).fetchone()
			return float(row["revenue"] if row is not None and row["revenue"] is not None else 0.0)

	def get_map_background_settings(self) -> dict[str, Any]:
		keys = {"map_background_filename": None, "map_bbox_west": None, "map_bbox_south": None, "map_bbox_east": None, "map_bbox_north": None, }
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT meta_key, text_value FROM Catchment_info WHERE meta_key IN ('map_background_filename','map_bbox_west','map_bbox_south','map_bbox_east','map_bbox_north')").fetchall()
			for row in rows: keys[str(row["meta_key"])] = row["text_value"]
		filename = str(keys["map_background_filename"] or "").strip()
		bbox_values = [keys["map_bbox_west"], keys["map_bbox_south"], keys["map_bbox_east"], keys["map_bbox_north"]]
		bbox: tuple[float, float, float, float] | None = None
		if all(value is not None and str(value).strip() != "" for value in bbox_values):
			bbox = (float(str(bbox_values[0]).strip()), float(str(bbox_values[1]).strip()), float(str(bbox_values[2]).strip()), float(str(bbox_values[3]).strip()))
		return {"filename": filename, "bbox": bbox}

	def save_map_background_settings(self, filename: str, west: float, south: float, east: float, north: float) -> None:
		with self.connect_to_db() as conn:
			conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value, integer_value) VALUES ('map_background_filename', ?, NULL)", (filename,))
			conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value, integer_value) VALUES ('map_bbox_west', ?, NULL)", (str(west),))
			conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value, integer_value) VALUES ('map_bbox_south', ?, NULL)", (str(south),))
			conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value, integer_value) VALUES ('map_bbox_east', ?, NULL)", (str(east),))
			conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, text_value, integer_value) VALUES ('map_bbox_north', ?, NULL)", (str(north),))
			conn.commit()

	# 9. Get price output. ---------------------------------------------
	def catchment_price_rows(self, auction_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
		"""Fetch price and constraint results from well_quota and control_point_events."""
		period_maps = self._get_period_maps(auction_id)
		pumping_period_id_map = period_maps["pumping_iso_to_idx"]
		effect_period_id_map = period_maps["effect_iso_to_idx"]
		with self.connect_to_db() as conn:
			# Read per-well prices from well_quota (well_id + take_date + price per row)
			well_rows: list[dict[str, Any]] = []
			for row in conn.execute("""SELECT wq.well_id, w.name, w.latitude, w.longitude, wq.take_date, wq.price
					FROM well_quota wq JOIN wells w ON w.well_id = wq.well_id WHERE wq.auction_id=? AND wq.well_id IS NOT NULL AND wq.take_date IS NOT NULL
					ORDER BY wq.well_id, wq.take_date""", (auction_id,)).fetchall():
				well_id = int(row["well_id"])
				period_id = pumping_period_id_map[str(row["take_date"])]
				well_rows.append({"well_id": well_id, "well_name": row["name"], "latitude": row["latitude"], "longitude": row["longitude"], "period_id": period_id, "price": (float(row["price"]) if row["price"] is not None else None)})

			# Read control point results from control_point_events
			cp_rows: list[dict[str, Any]] = []
			for row in conn.execute("""SELECT c.control_point_id, c.name, c.latitude, c.longitude, e.effect_date, e.dual_price,
					ahl.allowable_head_change, e.slack FROM control_point_events e JOIN control_points c ON c.control_point_id = e.control_point_id
					JOIN aquifer_head_limits ahl ON ahl.control_point_id = e.control_point_id AND ahl.effect_date = e.effect_date
					WHERE e.auction_id=? ORDER BY c.control_point_id, e.effect_date""", (auction_id,)).fetchall():

					slack = float(row["slack"]) if row["slack"] is not None else None
					bound = float(row["allowable_head_change"]) if row["allowable_head_change"] is not None else None
					period_id = effect_period_id_map[str(row["effect_date"] or "")]
					used_capacity = None if slack is None or bound is None else bound + slack
					cp_rows.append({"control_point_id": int(row["control_point_id"]), "control_point_name": row["name"], "latitude": row["latitude"], "longitude": row["longitude"], "period_id": period_id, "dual_value": (float(row["dual_price"]) if row["dual_price"] is not None else None), "used_capacity": used_capacity, "bound_capacity": bound, "slack": slack,})
			return well_rows, cp_rows

# If you got this far, you're amazing.
