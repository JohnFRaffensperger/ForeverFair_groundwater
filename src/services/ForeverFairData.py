# services/ForeverFairData.py. Claude guided by JFR, 2026 05 08.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Persist auction data and run results in SQLite.

from __future__ import annotations
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from ForeverFairClasses import (Auction, AuctionPeriod, BidSegment, ControlPoint, ResponseFactor, Well,)
from SetupForeverFairDB import SCHEMA_DDL

MAX_BID_STEPS = 5 
DEFAULT_BID_STEPS = 3

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
			conn.execute("INSERT OR IGNORE INTO Catchment_info(meta_key, meta_value) VALUES ('MAX_BID_STEPS', ?)", (str(DEFAULT_BID_STEPS),))

	def get_meta_data(self, conn: sqlite3.Connection, key: str) -> str:
		row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key=?", (key,)).fetchone()
		return "" if row is None else row["meta_value"]

	def get_catchment_name(self) -> str:
		with self.connect_to_db() as conn: return self.get_meta_data(conn, "Catchment_name")

	def get_rights_conversion_dict(self) -> dict[str, str]:
		with self.connect_to_db() as conn: return {"policy_name": self.get_meta_data(conn, "rights_policy_name"), "summary": self.get_meta_data(conn, "rights_policy_summary")}

	def get_max_bid_steps(self) -> int: # If you didn't set up the database properly, this should crash. Don't pretend you have the right data.
		# try:
			with self.connect_to_db() as conn: row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='MAX_BID_STEPS'").fetchone()
			# if row is None or row["meta_value"] is None: return DEFAULT_BID_STEPS
		# 	try: value = int(row["meta_value"])
		# 	except Exception: return DEFAULT_BID_STEPS
		# 	return min(MAX_BID_STEPS, max(1, value))
		# except sqlite3.OperationalError: 
			return DEFAULT_BID_STEPS

	def get_rights_policy(self) -> str: # If you didn't set up the database properly, this should crash. Don't pretend you have the right data.
		# try:
			with self.connect_to_db() as conn: row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='Rights_policy'").fetchone()
			return row["meta_value"] # if row and row["meta_value"] else "Users_pay"
		# except sqlite3.OperationalError:
		# 	return "Users_pay"

	# 2. Timing, calendar --------------------------------------------------------------------------------------
	def the_time_at_the_tone_is(self) -> datetime:
		"""Return the current synthetic simulation date/time from the database."""
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='synthetic_current_date'").fetchone()
			return datetime.fromisoformat(row["meta_value"])

	def _next_friday_at_4pm(self, base: datetime) -> datetime:
		"""Return the next Friday 16:00 at or after the given synthetic datetime."""
		days_ahead = (4 - base.weekday()) % 7
		target = (base + timedelta(days=days_ahead)).replace(hour=16, minute=0, second=0, microsecond=0)
		if target < base: target += timedelta(days=7)
		return target

	def next_friday_at_4pm(self, base: datetime) -> datetime: return self._next_friday_at_4pm(base)

	def _next_monday(self, base: datetime) -> datetime:
		days_ahead = (0 - base.weekday()) % 7
		target = (base + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
		if target <= base: target += timedelta(days=7)
		return target

	def latest_period_length_hours(self) -> int | None:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='period_length_hours'").fetchone()
			if row is None or row["meta_value"] is None: return None
			try: return int(float(row["meta_value"]))
			except Exception: return None

	# 3. Auction management --------------------------------------------------------------------------------
	def add_auction(self, auction_type: str | None = "final") -> dict[str, Any]:
		now_dt = self.the_time_at_the_tone_is()
		period_length_hours = self.latest_period_length_hours() or 168
		bidding_periods = self.get_number_of_bidding_periods()
		close_dt, first_take_dt, last_take_dt = self.get_auction_close_first_last_dates(now_dt, period_length_hours, bidding_periods)
		with self.connect_to_db() as conn:
			cursor = conn.execute("INSERT INTO auctions(status, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type) VALUES ('OPEN', ?, ?, ?, ?, ?, ?)", (now_dt.isoformat(timespec="minutes"), close_dt.isoformat(timespec="minutes"), first_take_dt.isoformat(timespec="minutes"), last_take_dt.isoformat(timespec="minutes"), period_length_hours, auction_type),)
			row = conn.execute("SELECT * FROM auctions WHERE auction_id=?", (cursor.lastrowid,)).fetchone()
			return dict(row)

	# Save auction optimization results to the database. --------------------------------------------
	# UNUSED
	def close_auction(self, auction_id: int, solve_status: str, objective_value: float, auction_close_time: str) -> int:
		with self.connect_to_db() as conn:
			conn.execute("UPDATE auctions SET status='Closed', closed_date=COALESCE(closed_date, ?), solve_status=?, objective_value=? WHERE auction_id=?", 
				(auction_close_time, solve_status, objective_value, auction_id))

			# Advance synthetic clock by one week when a final auction completes.
			is_auction_final = conn.execute("SELECT auction_type FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
			if "final" == is_auction_final["auction_type"]:
				# Update the date to the next period.
				date_and_periodlength = conn.execute("SELECT meta_key, meta_value FROM Catchment_info WHERE meta_key IN ('synthetic_current_date', 'period_length_hours')").fetchall()
				meta = {row["meta_key"]: row["meta_value"] for row in date_and_periodlength}
				period_length_hours = int(meta["period_length_hours"])
				conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, meta_value) VALUES ('synthetic_current_date', ?)",
				  ((datetime.fromisoformat(meta["synthetic_current_date"]) + timedelta(hours=period_length_hours)).isoformat(timespec="minutes"),))
			return auction_id

	def get_auction_close_first_last_dates(self, now_dt: datetime, period_length_hours: int, bidding_periods: int) -> tuple[datetime, datetime, datetime]:
		close_dt = self.next_friday_at_4pm(now_dt)
		first_take_dt = (close_dt + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
		window = timedelta(hours=period_length_hours * bidding_periods) - timedelta(minutes=1)
		last_take_dt = first_take_dt + window
		return close_dt, first_take_dt, last_take_dt

	# When you know the auction_id.
	def get_auction_info(self, auction_id: int) -> Auction:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT auction_id, status, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, auction_type, created_date, solve_status, objective_value FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
			if row is None: raise ValueError(f"Auction {auction_id} not found")
			else:
				# auction_pk = int(row["auction_id"])
				status = str(row["status"])
				closed_date = row["closed_date"]
				first_water_take_date = row["firstWaterTakeDate"]
				last_water_take_date = row["lastWaterTakeDate"]
				raw_period_length = row["period_length_hours"]
				period_length_hours = int(raw_period_length) if raw_period_length is not None else 0
				auction_type = row["auction_type"]
				created_date = row["created_date"]
				solve_status = row["solve_status"]
				objective_value = row["objective_value"]
			period_labels: list[str] = []
			if period_length_hours > 0 and first_water_take_date and last_water_take_date:
				start_dt = datetime.fromisoformat(str(first_water_take_date))
				end_dt = datetime.fromisoformat(str(last_water_take_date))
				if end_dt >= start_dt:
					current = start_dt
					while current <= end_dt:
						period_labels.append(current.isoformat(timespec="minutes"))
						current += timedelta(hours=period_length_hours)
			return Auction(id=auction_id, status=status, periods=[AuctionPeriod(id=idx + 1, label=period_label) for idx, period_label in enumerate(period_labels)], closed_date=closed_date, first_water_take_date=first_water_take_date, last_water_take_date=last_water_take_date, period_length_hours=period_length_hours, auction_type=auction_type, created_date=created_date, solve_status=solve_status, objective_value=objective_value,)

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

	# TODO: should have exactly one open auction. I guess we could implement an auction history page.
	def list_auctions(self) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT auction_id, status, auction_type, created_date, closed_date, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, solve_status, objective_value FROM auctions WHERE status != 'DELETED' ORDER BY CAST(auction_id AS INTEGER) ASC").fetchall()
			return [dict(row) for row in rows]

	# UNUSED
	def make_auction_final(self) -> int:
		next_auction = self.get_next_auction_info()
		if next_auction is None: raise ValueError("No open auction available")
		auction_id = int(next_auction["auction_id"])
		with self.connect_to_db() as conn:
			conn.execute("UPDATE auctions SET auction_type='final' WHERE auction_id=?", (auction_id,))
		return auction_id

	# UNUSED
	def make_auction_tentative(self) -> int:
		next_auction = self.get_next_auction_info()
		if next_auction is None: raise ValueError("No open auction available")
		auction_id = int(next_auction["auction_id"])
		with self.connect_to_db() as conn:
			conn.execute("UPDATE auctions SET auction_type='tentative' WHERE auction_id=?", (auction_id,))
		return auction_id
		
	# 4. Traders, wells. -------------------------------------------------------------------------------------------------
	def list_of_traders(self) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			return [{"id": int(row["trader_id"]), "name": row["name_tag"]} for row in conn.execute("SELECT trader_id, name_tag FROM traders ORDER BY name_tag").fetchall()]

	def get_trader_wells(self, trader_id: int) -> list[Well]:
		with self.connect_to_db() as conn:
			return [Well(id=int(row["well_id"]), name=row["name"], trader_id=int(row["trader_id"]) if row["trader_id"] is not None else 0, gw_model_layer=row["gw_model_layer"], gw_model_row=row["gw_model_row"], gw_model_column=row["gw_model_column"], latitude=row["latitude"], longitude=row["longitude"],) for row in conn.execute("SELECT well_id, name, trader_id, gw_model_layer, gw_model_row, gw_model_column, latitude, longitude FROM wells WHERE trader_id=? ORDER BY well_id", (trader_id,)).fetchall()]

	def get_wells(self) -> set[int]:
		"""Return a set of all well_id integers in the wells table."""
		with self.connect_to_db() as conn:
			return {int(row["well_id"]) for row in conn.execute("SELECT well_id FROM wells ORDER BY well_id").fetchall()}

	def get_all_period_dates(self) -> list[str]:
		"""All distinct take_dates across all auctions, sorted. Gives the global period date sequence
		that aligns with response_matrix.pumping_period / effect_period integers (1-based)."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT DISTINCT take_date FROM well_quota WHERE take_date IS NOT NULL ORDER BY take_date").fetchall()
			return [str(row["take_date"]) for row in rows]

	# 5. Bids -------------------------------------------------------------------------------------------------
	def add_bid(self, auction_id: int, well_id: int, period_id: int, quantity: float, price: float, is_default: bool = False, 
			 bid_steps: list[tuple[float, float]] | None = None) -> BidSegment:
		now = datetime.now(timezone.utc).isoformat()
		max_bid_steps = self.get_max_bid_steps()
		steps = bid_steps if bid_steps is not None else [(quantity, price)]
		if len(steps) > max_bid_steps: raise ValueError(f"At most {max_bid_steps} bid steps are supported.")
		qty_values: list[float | None] = [None] * MAX_BID_STEPS
		price_values: list[float | None] = [None] * MAX_BID_STEPS
		for idx, (step_qty, step_price) in enumerate(steps):
			qty_values[idx] = float(step_qty)
			price_values[idx] = float(step_price)
		period_labels = [p.label for p in self.get_auction_info(auction_id).periods]
		period_label = period_labels[period_id - 1] if 1 <= period_id <= len(period_labels) else None
		with self.connect_to_db() as conn:
			trader_row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
			trader_id = int(trader_row["trader_id"]) if trader_row and trader_row["trader_id"] is not None else 0
			if not period_label: raise ValueError(f"Unknown period_id: {period_id}")
			# One active bid per (well, auction, period) - soft-delete any existing before inserting.
			# TODO. The update seems dumb. 
			conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=? AND well_id=? AND effect_date=? AND deleted=0", (auction_id, well_id, period_label),)
			cursor = conn.execute("INSERT INTO well_bids(" "auction_id, trader_id, well_id, bid_date, effect_date, " "qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, " "is_bid_default, deleted" ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, trader_id, well_id, now, period_label, qty_values[0], price_values[0], qty_values[1], price_values[1], qty_values[2], price_values[2], qty_values[3], price_values[3], qty_values[4], price_values[4], 1 if is_default else 0,),)
			bid_pk = cursor.lastrowid or 0
			return BidSegment(id=f"bid-{bid_pk}-s1", well_id=well_id, period_id=period_id, quantity=float(steps[0][0]), price=float(steps[0][1]), submitted_at=now,)

	def delete_bid(self, bid_id: int, current_trader_id: int) -> bool:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT trader_id, deleted FROM well_bids WHERE bid_id=?", (bid_id,)).fetchone()
			if row is None: return False
			if row["deleted"] == 1 or row["trader_id"] != current_trader_id: return False
			conn.execute("UPDATE well_bids SET deleted=1 WHERE bid_id=?", (bid_id,))
			return True
	
	def get_bids (self, auction_id: int) -> list[dict[str, Any]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT bid_id, well_id, effect_date, bid_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM well_bids WHERE auction_id=? AND deleted=0 ORDER BY bid_id", (auction_id,), ).fetchall()
			return [dict(row) for row in rows]

	def get_bid_count(self, auction_id: int) -> tuple[int, int]:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT COALESCE(SUM(CASE WHEN is_bid_default = 0 THEN 1 ELSE 0 END), 0) AS real_bid_count, COALESCE(SUM(CASE WHEN is_bid_default = 1 THEN 1 ELSE 0 END), 0) AS default_bid_count FROM well_bids WHERE auction_id=?", (auction_id,)).fetchone()
			return int(row["real_bid_count"]), int(row["default_bid_count"])

	# TODO. Way too complicated, should be just the SQL.
	def get_bid_history (self, auction_id: int, trader_id: int) -> list[dict[str, Any]]:
		"""Fetch bid history from well_bids joined to well_quota for final allocation and traded price."""
		max_bid_steps = self.get_max_bid_steps()
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT bid_id, effect_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, bid_date, is_bid_default FROM well_bids " "WHERE auction_id=? AND trader_id=? AND deleted=0 ORDER BY bid_id DESC", (auction_id, trader_id), ).fetchall()
			# Pre-fetch well_quota rows keyed by takeDate for this trader/auction
			quota_rows = conn.execute("SELECT take_date, quota_auction_end, price FROM well_quota WHERE auction_id=? AND trader_id=?", (auction_id, trader_id), ).fetchall()
			quota_by_date = {str(r["take_date"]): r for r in quota_rows}
		period_labels = [p.label for p in self.get_auction_info(auction_id).periods]
		bid_history_list: list[dict[str, Any]] = []
		for row in rows:
			effect_date = str(row["effect_date"] or "")
			period_id = (period_labels.index(effect_date) + 1) if effect_date in period_labels else None
			if period_id is None: continue
			quota = quota_by_date.get(effect_date)
			final_allocation = float(quota["quota_auction_end"]) if quota and quota["quota_auction_end"] is not None else None
			traded_price = float(quota["price"]) if quota and quota["price"] is not None else None
			for step_num in range(1, max_bid_steps + 1):
				qty = row[f"qty{step_num}"]
				price = row[f"price{step_num}"]
				if qty is None or price is None: continue
				bid_history_list.append({"bid_id": int(row["bid_id"]), "period_id": period_id, "quantity": float(qty), "price": float(price), "submitted_at": row["bid_date"], "final_allocation": final_allocation, "traded_price": traded_price, "is_default": bool(row["is_bid_default"]) if row["is_bid_default"] is not None else False, })
		return bid_history_list

	# Reads default bids from the previous auction and posts them to the new auction.
	# To do: this should be incorporated in get_bids
	# UNUSED
	def get_default_bids(self, auction_id: int, source_auction_id: int, now: str) -> None:
		with self.connect_to_db() as conn:
			standing = conn.execute("SELECT trader_id, well_id, effect_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5 FROM well_bids WHERE auction_id=? AND is_bid_default=1 AND deleted=0", (source_auction_id,)).fetchall()
			for s in standing:
				# TODO. The update seems dumb. 
				conn.execute("UPDATE well_bids SET deleted=1 WHERE auction_id=? AND well_id=? AND effect_date=? AND deleted=0", (auction_id, s["well_id"], s["effect_date"]),)
				conn.execute("INSERT OR IGNORE INTO well_bids(auction_id, trader_id, well_id, bid_date, effect_date, qty1, price1, qty2, price2, qty3, price3, qty4, price4, qty5, price5, is_bid_default, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)", (auction_id, s["trader_id"], s["well_id"], now, s["effect_date"], s["qty1"], s["price1"], s["qty2"], s["price2"], s["qty3"], s["price3"], s["qty4"], s["price4"], s["qty5"], s["price5"]),)

	def get_number_of_bidding_periods(self) -> int:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='num_bidding_periods'").fetchone()
			return int(row["meta_value"])

	def has_default_bids(self, auction_id: int) -> bool:
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT 1 FROM well_bids WHERE auction_id=? AND is_bid_default=1 AND deleted=0 LIMIT 1", (auction_id,)).fetchone()
			return row is not None

	# 6. Quota -----------------------------------------
	def get_well_start_quota(self, well_id: int, auction_id: int) -> dict[int, float]:
		"""Return {bid_period: quota_auction_start} from well_quota for the given auction_id, ordered by take_date."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT quota_auction_start FROM well_quota WHERE well_id=? AND auction_id=? ORDER BY take_date", (well_id, auction_id)).fetchall()
			return {i + 1: float(row["quota_auction_start"] or 0.0) for i, row in enumerate(rows)}
	
	def get_well_end_quota(self, well_id: int, auction_id: int) -> dict[int, float]:
		"""Return {bid_period: quota_auction_end} from well_quota for the given auction_id, ordered by take_date."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT quota_auction_end FROM well_quota WHERE well_id=? AND auction_id=? ORDER BY take_date", (well_id, auction_id)).fetchall()
			return {i + 1: float(row["quota_auction_end"] or 0.0) for i, row in enumerate(rows)}

	def get_well_license_quantity(self, well_id: int) -> dict[int, float]:
		"""Return {bid_period: license_quantity} from well_license for the given well_id."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT bid_period, license_quantity FROM well_license WHERE well_id=? ORDER BY bid_period", (well_id,)).fetchall()
			return {int(row["bid_period"]): float(row["license_quantity"] or 0.0) for row in rows if row["bid_period"] is not None}
	
	# TODO: need to fix Programmer.html to have a button to set up the first auction. Then no auction is auction 0. So this function would be simpler.
	def get_quota(self, auction_id: int) -> dict[tuple[int, int], float]:
		"""Return {(well_id, period_idx): quota_auction_start} for all wells in the auction."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT well_id, take_date, quota_auction_start FROM well_quota WHERE auction_id=?", (auction_id,)).fetchall()
			period_labels = [p.label for p in self.get_auction_info(auction_id).periods]
			if period_labels:
				period_label_to_idx = {label: idx + 1 for idx, label in enumerate(period_labels)}
			else:
				take_dates = sorted({str(row["take_date"] or "").strip() for row in rows if row["take_date"] is not None})
				period_label_to_idx = {label: idx + 1 for idx, label in enumerate(take_dates)}
			result: dict[tuple[int, int], float] = {}
			for row in rows:
				well_id = int(row["well_id"]) if row["well_id"] is not None else 0
				take_date = str(row["take_date"] or "").strip()
				period_idx = period_label_to_idx.get(take_date, 0)
				if well_id <= 0 or period_idx <= 0: continue
				result[(well_id, period_idx)] = float(row["quota_auction_start"] or 0.0)
			return result

	def set_quota_for_auction (self, auction_id: int, source_auction_id: int | None = None) -> int:
		period_labels = [p.label for p in self.get_auction_info(auction_id).periods]
		with self.connect_to_db() as conn:
			if conn.execute("SELECT 1 FROM well_quota WHERE auction_id=? LIMIT 1", (auction_id,)).fetchone() is not None: return 0
			inserted = 0
			if auction_id == 1:
				# For auction 1: populate from well_license
				for row in conn.execute("""SELECT wl.trader_id, wl.well_id, wl.license_quantity, wl.bid_period 
						FROM well_license wl ORDER BY wl.bid_period, wl.well_id""").fetchall():
					period_id = row["bid_period"]
					if period_id < 1 or period_id > len(period_labels): continue
					conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, take_date) VALUES (?, ?, ?, ?, ?)",
								(row["trader_id"], 1, row["well_id"], row["license_quantity"], period_labels[period_id - 1]))
					inserted += 1
			else:
				# For auction >= 2: populate from previous auction's quota_auction_end (fallback: quota_adjusted, then quota_auction_start)
				if source_auction_id is not None:
					source_id = source_auction_id
				else:
					row = conn.execute("SELECT MAX(auction_id) FROM well_quota WHERE auction_id < ?", (auction_id,)).fetchone()
					source_id = int(row[0]) if row and row[0] is not None else 0
				if source_id <= 0: return 0
				take_dates = [str(r["take_date"] or "") for r in conn.execute("SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL ORDER BY take_date", (source_id,)).fetchall()]
				source_period_idx = {take_date: idx + 1 for idx, take_date in enumerate(take_dates)}
				for row in conn.execute("SELECT trader_id, well_id, quota_auction_start, quota_auction_end, quota_adjusted, take_date FROM well_quota WHERE auction_id=? ORDER BY take_date, well_id", (source_id,)).fetchall():
					period_id = source_period_idx.get(str(row["take_date"] or ""), 0)
					if period_id < 1 or period_id > len(period_labels): continue
					quota_start = row["quota_auction_end"] if row["quota_auction_end"] is not None else (row["quota_adjusted"] if row["quota_adjusted"] is not None else row["quota_auction_start"])
					conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, quota_adjusted, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
								(row["trader_id"], auction_id, row["well_id"], quota_start, row["quota_adjusted"], period_labels[period_id - 1]))
					inserted += 1
			return inserted

	def set_quota_auction_end(self, auction_id: int, well_id: int, take_date: str, quota_auction_end: float | None, price: float | None) -> None:
		with self.connect_to_db() as conn:
			cursor = conn.execute("UPDATE well_quota SET quota_auction_end=?, price=? WHERE auction_id=? AND well_id=? AND take_date=?", (quota_auction_end, price, auction_id, well_id, take_date))
			if cursor.rowcount:
				return
			base_row = conn.execute("SELECT trader_id, quota_auction_start, quota_adjusted FROM well_quota WHERE auction_id IN (?, 0) AND well_id=? AND take_date=? ORDER BY CASE WHEN auction_id=? THEN 0 ELSE 1 END LIMIT 1", (auction_id, well_id, take_date, auction_id)).fetchone()
			trader_row = conn.execute("SELECT trader_id FROM wells WHERE well_id=?", (well_id,)).fetchone()
			trader_id = trader_row["trader_id"] if trader_row is not None else None
			quota_auction_start = base_row["quota_auction_start"] if base_row is not None else None
			quota_adjusted = base_row["quota_adjusted"] if base_row is not None else None
			if base_row is not None and base_row["trader_id"] is not None:
				trader_id = base_row["trader_id"]
			conn.execute("INSERT INTO well_quota(trader_id, auction_id, well_id, quota_auction_start, quota_adjusted, quota_auction_end, price, take_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (trader_id, auction_id, well_id, quota_auction_start, quota_adjusted, quota_auction_end, price, take_date))

	# 7. Response matrix, control points  --------------------------------------------------------
	def bounds_imported_at(self) -> str | None:
		try:
			with self.connect_to_db() as conn:
				row = conn.execute("SELECT meta_value FROM Catchment_info WHERE meta_key='mps_loaded_date'").fetchone()
				if row and row[0]: return str(row[0])
				return None
		except Exception: return None

	def get_quota_by_well_pumping_period(self, auction_id: int) -> dict[tuple[int, int], float]:
		return self.get_quota(auction_id)

	def get_allowable_head_change_by_cp_effect_date(self, auction_id: int) -> tuple[dict[tuple[int, str], float], dict[str, int]]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT control_point_id, effect_date, allowable_head_change FROM control_point_events WHERE auction_id=?", (auction_id,)).fetchall()
			effect_dates = sorted({str(row["effect_date"] or "") for row in rows if row["effect_date"] is not None})
			effect_date_to_idx = {effect_date: idx + 1 for idx, effect_date in enumerate(effect_dates)}
			allowable_head_change: dict[tuple[int, str], float] = {}
			for row in rows:
				effect_date = str(row["effect_date"] or "")
				if effect_date not in effect_date_to_idx: continue
				allowable_head_change[(int(row["control_point_id"]), effect_date)] = float(row["allowable_head_change"] or 0.0)
			return allowable_head_change, effect_date_to_idx

	def get_control_point_events(self, auction_id: int) -> list[dict[str, Any]]:
		"""Return all control_point_events rows for the given auction_id as dicts with control_point_id, effect_date, allowable_head_change, dual_price, slack."""
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT control_point_id, effect_date, allowable_head_change, dual_price, slack FROM control_point_events WHERE auction_id=?", (auction_id,)).fetchall()
			return [dict(row) for row in rows]

	def get_control_points_for_auction(self, auction_id: int) -> list[ControlPoint]:
		auction = self.get_auction_info(auction_id)
		period_labels = [p.label for p in auction.periods]
		period_label_to_idx = {label: idx + 1 for idx, label in enumerate(period_labels)}
		with self.connect_to_db() as conn:
			rows = conn.execute("""SELECT c.control_point_id, c.name, c.gw_model_row, c.gw_model_column, c.latitude, c.longitude, e.effect_date, e.allowable_head_change
				FROM control_points c JOIN control_point_events e ON e.control_point_id = c.control_point_id
				WHERE e.auction_id=? ORDER BY c.control_point_id, e.effect_date""", (auction_id,)).fetchall()
			control_points: dict[int, ControlPoint] = {}
			for row in rows:
				control_point_id = int(row["control_point_id"])
				control_point = control_points.get(control_point_id)
				if control_point is None:
					control_point = ControlPoint(id=control_point_id, name=str(row["name"]), bound_by_period={}, gw_model_row=row["gw_model_row"], gw_model_column=row["gw_model_column"], latitude=row["latitude"], longitude=row["longitude"])
					control_points[control_point_id] = control_point
				period_id = period_label_to_idx.get(str(row["effect_date"] or ""), 0)
				if period_id > 0: control_point.bound_by_period[period_id] = float(row["allowable_head_change"] or 0.0)
			return list(control_points.values())

	def get_control_point_rhs(self, auction_id: int) -> dict[tuple[int, int], float]:
		"""Return {(control_point_id, period_idx): allowable_head_change} for the given auction."""
		auction = self.get_auction_info(auction_id)
		period_labels = [p.label for p in auction.periods]
		period_label_to_idx = {label: idx + 1 for idx, label in enumerate(period_labels)}
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT control_point_id, effect_date, allowable_head_change FROM control_point_events WHERE auction_id=?", (auction_id,)).fetchall()
			result: dict[tuple[int, int], float] = {}
			for row in rows: result[(row["control_point_id"], period_label_to_idx.get(row["effect_date"], 0))] = float(row["allowable_head_change"] or 0.0)
			return result

	def get_all_response_factors(self) -> list[ResponseFactor]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, factor_value FROM response_matrix ORDER BY control_point_id, effect_period, pumping_period, well_id").fetchall()
			return [ResponseFactor(well_id=int(row["well_id"]), control_point_id=int(row["control_point_id"]), pumping_period=int(row["pumping_period"]), effect_period=int(row["effect_period"]), value=float(row["factor_value"])) for row in rows]

	def get_response_factors_for_cp_period(self, control_point_id: int, effect_period: int) -> list[ResponseFactor]:
		with self.connect_to_db() as conn:
			rows = conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, factor_value FROM response_matrix WHERE control_point_id=? AND effect_period=? ORDER BY pumping_period, well_id", (control_point_id, effect_period)).fetchall()
			return [ResponseFactor(well_id=int(row["well_id"]), control_point_id=int(row["control_point_id"]), pumping_period=int(row["pumping_period"]), effect_period=int(row["effect_period"]), value=float(row["factor_value"])) for row in rows]

	def response_matrix_period_count(self) -> int: # Should be the same as Catchment_info.gwm_num_control_periods.
		with self.connect_to_db() as conn:
			row = conn.execute("SELECT COALESCE(MAX(effect_period), 0) AS n FROM response_matrix").fetchone()
			return int(row["n"] or 0)

	def set_control_point_events_for_auction (self, auction_id: int, source_auction_id: int | None = None) -> int:
		with self.connect_to_db() as conn:
			if conn.execute("SELECT 1 FROM control_point_events WHERE auction_id=? LIMIT 1", (auction_id,)).fetchone() is not None: return 0
			if source_auction_id is not None:
				source_id = source_auction_id
			else:
				row = conn.execute("SELECT MAX(auction_id) FROM control_point_events WHERE auction_id < ? AND effect_date NOT LIKE 'stg:%'", (auction_id,)).fetchone()
				source_id = int(row[0]) if row and row[0] is not None else 0

			# Copy only effect periods on or after this auction's firstWaterTakeDate.
			# effect_date_to_idx in the LP is built from get_all_period_dates() (globally stable),
			# so past-auction effect dates are not needed for LP alignment.
			auction_row = conn.execute("SELECT firstWaterTakeDate FROM auctions WHERE auction_id=?", (auction_id,)).fetchone()
			first_take_date = str(auction_row["firstWaterTakeDate"]) if auction_row else None
			if first_take_date is None: return 0
			inserted = 0
			for row in conn.execute("SELECT control_point_id, effect_date, actual_start_head, minimum_head, allowable_head_change, head_constraint_upper_bound FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL AND effect_date NOT LIKE 'stg:%' AND effect_date >= ? ORDER BY control_point_id, effect_date", (source_id, first_take_date)).fetchall():
				conn.execute("INSERT INTO control_point_events(control_point_id, auction_id, effect_date, actual_start_head, minimum_head, allowable_head_change, head_constraint_upper_bound) VALUES (?, ?, ?, ?, ?, ?, ?)", (row["control_point_id"], auction_id, str(row["effect_date"]), row["actual_start_head"], row["minimum_head"], row["allowable_head_change"], row["head_constraint_upper_bound"],))
				inserted += 1
			return inserted

	def set_control_point_event_results(self, auction_id: int, control_point_id: int, effect_date: str, slack: float | None, dual_price: float | None) -> None:
		with self.connect_to_db() as conn:
			conn.execute("UPDATE control_point_events SET slack=?, dual_price=? WHERE auction_id=? AND control_point_id=? AND effect_date=?", (slack, dual_price, auction_id, control_point_id, effect_date))

	# 8. Rights -------------------------------------------------------------------------
	# Amazing query by Claude, though I gave it a lot of guidance. The same as AuctionController.compute_alphas, but for just one well. SQL is faster in this case than Python.
	# Used for showing the well constraint quota on Trader.html.
	# UNUSED
	def get_well_constraint_quota(self, well_id: int, auction_id: int = 0) -> dict[tuple[str, int, int], float]:
		"""Return {(take_date, effect_period, control_point_id): constraint_quota} for one well.
		well_constraint_quota is the allowable head change in effect_date at cp_id allocated to well_id in pumping_period. Likely negative!

		The SQL reconstructs pumping_period from ordered well_quota.take_date and effect_period from ordered control_point_events.effect_date,
		then computes constraint_quota(w,u,t,k) = q(w,u) * F(w,u,t,k) * allowable_head_change(t,k) / denom(k,t)
		where denom(k,t) = SUM(q(v,r) * F(v,r,t,k)) over all wells v and pumping periods r <= t. 
		denom(k,t) is the drawdown from all quota_auction_start at control_point_id k in effect_period t.
		In other places, I define alpha (k,t) = allowable_head_change(t,k) / denom(k,t), which is the fraction of allocated change in head. 
		So alpha (k,t) > 1 means people can take more, alpha (k,t) > 1 means must take less.

		If the requested auction_id has no well_quota rows, the query falls back to auction_id=0, matching the behavior of other quota accessors.
		"""
		with self.connect_to_db() as conn:
			source_auction_id = auction_id
			row = conn.execute("SELECT 1 FROM well_quota WHERE auction_id=? LIMIT 1", (auction_id,)).fetchone()
			if row is None and auction_id != 0: source_auction_id = 0

			rows = conn.execute("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
						FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL)),
					effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
						FROM (SELECT DISTINCT effect_date FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL)),
					q AS (SELECT wq.well_id, wq.take_date, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq
						JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=?),
					cpe AS (SELECT cp.control_point_id, edi.effect_period, cp.allowable_head_change FROM control_point_events cp
						JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date WHERE cp.auction_id=?),
					denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.factor_value) AS total_load FROM response_matrix rm
						JOIN q ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period WHERE rm.pumping_period <= rm.effect_period
						GROUP BY rm.control_point_id, rm.effect_period)
				SELECT q.take_date, rm.effect_period, rm.control_point_id, q.quota_auction_start * rm.factor_value * cpe.allowable_head_change / denom.total_load AS constraint_quota
				FROM response_matrix rm
				JOIN q     ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period
				JOIN cpe   ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
				JOIN denom ON denom.control_point_id=rm.control_point_id AND denom.effect_period=rm.effect_period
				WHERE rm.well_id=? AND denom.total_load != 0.0
				ORDER BY q.take_date, rm.effect_period, rm.control_point_id
			""", (source_auction_id, source_auction_id, source_auction_id, source_auction_id, well_id)).fetchall()
			return {(str(r["take_date"]), int(r["effect_period"]), int(r["control_point_id"])): float(r["constraint_quota"] or 0.0) for r in rows}

	# UNUSED
	def get_well_constraint_quota1(self, well_id: int, auction_id: int = 0) -> dict[tuple[str, int, int], float]:
		"""Return {(take_date, effect_period, control_point_id): constraint_quota} using precomputed cpe.alpha.

		Assumes calculate_and_set_constraint_alphas has already populated control_point_events.alpha
		for the same source_auction_id used below.
		"""
		with self.connect_to_db() as conn:
			source_auction_id = auction_id
			row = conn.execute("SELECT 1 FROM well_quota WHERE auction_id=? LIMIT 1", (auction_id,)).fetchone()
			if row is None and auction_id != 0: source_auction_id = 0

			rows = conn.execute("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period
						FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=? AND take_date IS NOT NULL)),
					q AS (SELECT wq.well_id, wq.take_date, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq
						JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=?),
					effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period
						FROM (SELECT DISTINCT effect_date FROM control_point_events WHERE auction_id=? AND effect_date IS NOT NULL)),
					cpe AS (SELECT cp.control_point_id, cp.effect_date, cp.alpha, edi.effect_period FROM control_point_events cp
						JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date WHERE cp.auction_id=?)
				SELECT q.take_date, rm.effect_period, rm.control_point_id, q.quota_auction_start * rm.factor_value * cpe.alpha AS constraint_quota
				FROM response_matrix rm JOIN q   ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period JOIN cpe ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
				WHERE rm.well_id=?
				ORDER BY q.take_date, rm.effect_period, rm.control_point_id
			""", (source_auction_id, source_auction_id, source_auction_id, source_auction_id, well_id)).fetchall()
			return {(str(r["take_date"]), int(r["effect_period"]), int(r["control_point_id"])): float(r["constraint_quota"] or 0.0) for r in rows}

	# Key function! Claude wrote this, wrong at first. Now verified against function AuctionController.py compute_alphas().
	def calculate_and_set_constraint_alphas(self, auction_id: int) -> int:
		"""Compute and persist alpha on control_point_events for one auction.

		Source rows:
		- Quota inputs come from get_quota_by_well_pumping_period(auction_id), which already
		  applies this class's fallback-to-auction-0 behavior for missing well_quota rows.
- Event rows to update are selected from control_point_events for auction_id.

		Computation (SQL CTE):
		- effect_period is reconstructed by ordering distinct effect_date values.
		- denom(control_point_id, effect_period) = SUM(quota_auction_start * factor_value)
		  across response_matrix rows where pumping_period <= effect_period.
		- alpha assignment follows the current implementation exactly:
		  * If denom.total_load > 0.0, then alpha = 1.0
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
				q AS (SELECT well_id, pumping_period, quota_auction_start FROM tmp_quota_alpha),
				denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.factor_value) AS total_load FROM response_matrix rm
					JOIN q ON q.well_id = rm.well_id AND q.pumping_period = rm.pumping_period WHERE rm.pumping_period <= rm.effect_period GROUP BY rm.control_point_id, rm.effect_period),
				alpha_values AS (SELECT cpe.control_point_id, cpe.effect_date,
						CASE WHEN denom.total_load > 0.0 THEN 1.0 WHEN denom.total_load IS NULL OR denom.total_load = 0.0 THEN NULL ELSE cpe.allowable_head_change / denom.total_load
						END AS alpha FROM control_point_events cpe
					JOIN effect_date_idx edi ON edi.effect_date = cpe.effect_date
					LEFT JOIN denom ON denom.control_point_id = cpe.control_point_id AND denom.effect_period = edi.effect_period WHERE cpe.auction_id=?)
				UPDATE control_point_events
				SET alpha = (SELECT alpha_values.alpha FROM alpha_values WHERE alpha_values.control_point_id = control_point_events.control_point_id
					  AND alpha_values.effect_date = control_point_events.effect_date)
				WHERE auction_id=? AND EXISTS (SELECT 1 FROM alpha_values WHERE alpha_values.control_point_id = control_point_events.control_point_id AND alpha_values.effect_date = control_point_events.effect_date)
			""", (source_auction_id, source_auction_id, source_auction_id))
			conn.execute("DROP TABLE IF EXISTS temp.tmp_quota_alpha")
			return conn.total_changes - before_changes

	# Used only by compute_alphas. 
	def set_constraint_alphas(self, auction_id: int, alphas: dict[tuple[int, int | str], float]) -> None:
		period_labels = [p.label for p in self.get_auction_info(auction_id).periods]
		with self.connect_to_db() as conn:
			for (cp_id, period_ref), alpha in alphas.items():
				if isinstance(period_ref, str):
					period_label = period_ref
				else:
					period_label = period_labels[period_ref - 1] if 1 <= period_ref <= len(period_labels) else None
				if not period_label: continue
				conn.execute("UPDATE control_point_events SET alpha = ? WHERE auction_id = ? AND control_point_id = ? AND effect_date = ?", (alpha, auction_id, cp_id, period_label),)

	# 9. Get price output. ---------------------------------------------
	def catchment_price_rows(self, auction_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
		"""Fetch price and constraint results from well_quota and control_point_events."""
		period_labels = [p.label for p in self.get_auction_info(auction_id).periods]
		period_id_map = {period_label: idx + 1 for idx, period_label in enumerate(period_labels)}
		with self.connect_to_db() as conn:
			# Read per-well prices from well_quota (well_id + take_date + price per row)
			well_name_map = {row["well_id"]: row["name"] for row in conn.execute("SELECT well_id, name FROM wells").fetchall()}
			well_rows: list[dict[str, Any]] = []
			for row in conn.execute("SELECT well_id, take_date, price FROM well_quota WHERE auction_id=? AND well_id IS NOT NULL AND take_date IS NOT NULL ORDER BY well_id, take_date", (auction_id,)).fetchall():
				well_id = int(row["well_id"])
				period_id = period_id_map.get(str(row["take_date"]), 0)
				well_rows.append({"well_id": well_id, "well_name": well_name_map.get(well_id, str(well_id)), "period_id": period_id, "price": float(row["price"] or 0.0)})

			# Read control point results from control_point_events
			cp_rows: list[dict[str, Any]] = []
			for row in conn.execute(""" SELECT c.control_point_id, c.name, e.effect_date, e.dual_price, e.head_constraint_upper_bound, e.slack FROM control_point_events e JOIN control_points c ON c.control_point_id = e.control_point_id WHERE e.auction_id=? ORDER BY c.control_point_id, e.effect_date """, (auction_id,)).fetchall():
					slack = float(row["slack"] or 0.0)
					bound = float(row["head_constraint_upper_bound"] or 0.0)
					period_id = period_id_map.get(str(row["effect_date"] or ""), 0)
					cp_rows.append({"control_point_id": int(row["control_point_id"]), "control_point_name": row["name"], "period_id": period_id, "dual_value": float(row["dual_price"] or 0.0), "used_capacity": bound - slack, "bound_capacity": bound,})
			return well_rows, cp_rows