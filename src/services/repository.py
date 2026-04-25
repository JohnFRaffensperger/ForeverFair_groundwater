# services/repository.py. Claude guided by JFR, 2026 04 21.
# Purpose: Persist auction data and run results in SQLite.

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from models import (Auction, AuctionPeriod, BidSegment, ControlPoint, AuctionCase, Participant, MarketResult, ResponseFactor, RightsConversion, Well,)

class AuctionRepository:
	LEGACY_TRADER_COLUMNS = [
		("trader_loginid", "TEXT"),
		("trader_password", "TEXT"),
		("trader_first_name", "TEXT"),
		("trader_last_name", "TEXT"),
		("trader_address", "TEXT"),
		("trader_city", "TEXT"),
		("trader_phone", "TEXT"),
		("trader_email", "TEXT"),
	]

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
		if not self.db_path.exists():
			return
		with self._connect() as conn:
			conn.executescript(""" CREATE TABLE IF NOT EXISTS metadata (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL ); CREATE TABLE IF NOT EXISTS traders (trader_id TEXT PRIMARY KEY, name TEXT NOT NULL ); CREATE TABLE IF NOT EXISTS wells (well_id TEXT PRIMARY KEY, name TEXT NOT NULL, trader_id TEXT NOT NULL, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL, FOREIGN KEY (trader_id) REFERENCES traders(trader_id) ); CREATE TABLE IF NOT EXISTS periods (period_id INTEGER PRIMARY KEY, period_date TEXT, display_label TEXT NOT NULL ); CREATE TABLE IF NOT EXISTS control_points (control_point_id TEXT PRIMARY KEY, name TEXT NOT NULL, gw_model_row INTEGER, gw_model_column INTEGER, latitude REAL, longitude REAL ); CREATE TABLE IF NOT EXISTS auctions (auction_id TEXT PRIMARY KEY, label TEXT NOT NULL, bid_close_label TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, ran_at TEXT ); CREATE TABLE IF NOT EXISTS auction_periods (auction_id TEXT NOT NULL, period_id INTEGER NOT NULL, period_order INTEGER NOT NULL, PRIMARY KEY (auction_id, period_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS trader_allocations (auction_id TEXT NOT NULL, trader_id TEXT NOT NULL, period_id INTEGER NOT NULL, allocation REAL NOT NULL, PRIMARY KEY (auction_id, trader_id, period_id), FOREIGN KEY (trader_id) REFERENCES traders(trader_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS default_control_point_bounds (control_point_id TEXT NOT NULL, period_id INTEGER NOT NULL, bound REAL NOT NULL, PRIMARY KEY (control_point_id, period_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id) ); CREATE TABLE IF NOT EXISTS control_point_bounds (auction_id TEXT NOT NULL, control_point_id TEXT NOT NULL, period_id INTEGER NOT NULL, bound REAL NOT NULL, PRIMARY KEY (auction_id, control_point_id, period_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS response_factors (well_id TEXT NOT NULL, control_point_id TEXT NOT NULL, pumping_period INTEGER NOT NULL, effect_period INTEGER NOT NULL, factor_value REAL NOT NULL, PRIMARY KEY (well_id, control_point_id, pumping_period, effect_period), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id), FOREIGN KEY (pumping_period) REFERENCES periods(period_id), FOREIGN KEY (effect_period) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS bids (bid_id INTEGER PRIMARY KEY AUTOINCREMENT, auction_id TEXT NOT NULL, trader_id TEXT NOT NULL, well_id TEXT NOT NULL, period_id INTEGER NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL, submitted_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, FOREIGN KEY (trader_id) REFERENCES traders(trader_id), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS auction_runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, auction_id TEXT NOT NULL, clearing_start_time TEXT, run_at TEXT NOT NULL, solve_status TEXT NOT NULL, objective_value REAL NOT NULL ); CREATE TABLE IF NOT EXISTS accepted_bids (run_id INTEGER NOT NULL, bid_id INTEGER NOT NULL, accepted_quantity REAL NOT NULL, PRIMARY KEY (run_id, bid_id), FOREIGN KEY (run_id) REFERENCES auction_runs(run_id), FOREIGN KEY (bid_id) REFERENCES bids(bid_id) ); CREATE TABLE IF NOT EXISTS period_prices (run_id INTEGER NOT NULL, period_id INTEGER NOT NULL, price REAL NOT NULL, PRIMARY KEY (run_id, period_id), FOREIGN KEY (run_id) REFERENCES auction_runs(run_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS constraint_results (run_id INTEGER NOT NULL, control_point_id TEXT NOT NULL, period_id INTEGER NOT NULL, used_capacity REAL NOT NULL, bound_capacity REAL NOT NULL, dual_value REAL NOT NULL, PRIMARY KEY (run_id, control_point_id, period_id), FOREIGN KEY (run_id) REFERENCES auction_runs(run_id), FOREIGN KEY (control_point_id) REFERENCES control_points(control_point_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); CREATE TABLE IF NOT EXISTS well_quota_ledger (well_id TEXT NOT NULL, auction_id TEXT NOT NULL, period_id INTEGER NOT NULL, quota_auction_start REAL NOT NULL, quota_adjusted REAL, quota_auction_end REAL, clearing_price REAL, adjustment_method TEXT, PRIMARY KEY (well_id, auction_id, period_id), FOREIGN KEY (well_id) REFERENCES wells(well_id), FOREIGN KEY (auction_id) REFERENCES auctions(auction_id), FOREIGN KEY (period_id) REFERENCES periods(period_id) ); """)
			self._ensure_legacy_trader_columns(conn)
			self._ensure_spatial_columns(conn)
			try:
				conn.execute("ALTER TABLE auction_runs ADD COLUMN clearing_start_time TEXT")
			except Exception:
				pass  # column already exists
			count = conn.execute("SELECT COUNT(*) AS n FROM auctions").fetchone()["n"]
			if count == 0: self._seed_from_json(conn)

	def _ensure_legacy_trader_columns(self, conn: sqlite3.Connection) -> None:
		rows = conn.execute("PRAGMA table_info(traders)").fetchall()
		existing = {row[1] for row in rows}
		for col_name, col_type in self.LEGACY_TRADER_COLUMNS:
			if col_name not in existing:
				conn.execute(f"ALTER TABLE traders ADD COLUMN {col_name} {col_type}")

	def _ensure_spatial_columns(self, conn: sqlite3.Connection) -> None:
		table_columns = {
			"wells": [
				("gw_model_row", "INTEGER"),
				("gw_model_column", "INTEGER"),
				("latitude", "REAL"),
				("longitude", "REAL"),
			],
			"control_points": [
				("gw_model_row", "INTEGER"),
				("gw_model_column", "INTEGER"),
				("latitude", "REAL"),
				("longitude", "REAL"),
			],
		}
		for table_name, cols in table_columns.items():
			existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
			for col_name, col_type in cols:
				if col_name not in existing:
					conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")

	def _extract_row_col(self, location_note: str | None) -> tuple[int | None, int | None]:
		if not location_note:
			return None, None
		parts = location_note.replace(",", " ").split()
		try:
			row_idx = parts.index("row")
			col_idx = parts.index("col")
			return int(parts[row_idx + 1]), int(parts[col_idx + 1])
		except Exception:
			return None, None

	def _seed_from_json(self, conn: sqlite3.Connection) -> None:
		seed_payload = json.loads(self.seed_path.read_text(encoding="utf-8-sig"))
		now = datetime.now(timezone.utc).isoformat()

		meta_values = {"catchment_name": seed_payload["catchment_name"], "source_note": "Data provenance: Tianqiao groundwater archive source.", "current_trader_id": seed_payload["current_trader_id"], "rights_policy_name": seed_payload["rights_conversion"]["policy_name"], "rights_policy_summary": seed_payload["rights_conversion"]["summary"],}
		for key, value in meta_values.items():
			conn.execute("INSERT INTO metadata(meta_key, meta_value) VALUES (?, ?)", (key, value))

		for idx, period in enumerate(seed_payload["auction"]["periods"]):
			conn.execute("INSERT INTO periods(period_id, period_date, display_label) VALUES (?, NULL, ?)", (idx + 1, period["label"]))

		for trader in seed_payload["traders"]:
			conn.execute("INSERT INTO traders(trader_id, name) VALUES (?, ?)", (trader["id"], trader["name"]))

		for well in seed_payload["wells"]:
			gw_row, gw_col = self._extract_row_col(well.get("location_note"))
			conn.execute(
				"INSERT INTO wells(well_id, name, trader_id, gw_model_row, gw_model_column, latitude, longitude)"
				" VALUES (?, ?, ?, ?, ?, ?, ?)",
				(well["id"], well["name"], well["trader_id"], gw_row, gw_col, None, None),
			)

		for cp in seed_payload["control_points"]:
			conn.execute(
				"INSERT INTO control_points(control_point_id, name, gw_model_row, gw_model_column, latitude, longitude)"
				" VALUES (?, ?, ?, ?, ?, ?)",
				(cp["id"], cp["name"], None, None, None, None),
			)

		auction = seed_payload["auction"]
		auction_id = auction["id"]
		conn.execute("INSERT INTO auctions(auction_id, label, bid_close_label, status, created_at, ran_at) VALUES (?, ?, ?, 'OPEN', ?, NULL)", (auction_id, auction["label"], auction["bid_close_label"], now),)
		for idx, period in enumerate(auction["periods"]):
			conn.execute("INSERT INTO auction_periods(auction_id, period_id, period_order) VALUES (?, ?, ?)", (auction_id, idx + 1, idx + 1),)

		for trader in seed_payload["traders"]:
			for idx, (period_key, allocation) in enumerate(trader["allocation_by_period"].items()):
				conn.execute("INSERT INTO trader_allocations(auction_id, trader_id, period_id, allocation) VALUES (?, ?, ?, ?)", (auction_id, trader["id"], idx + 1, allocation),)

		for cp in seed_payload["control_points"]:
			for idx, (period_key, bound) in enumerate(cp["bound_by_period"].items()):
				conn.execute("INSERT INTO control_point_bounds(auction_id, control_point_id, period_id, bound) VALUES (?, ?, ?, ?)", (auction_id, cp["id"], idx + 1, bound),)

		for factor in seed_payload["response_factors"]:
			pump_idx = int(factor["pumping_period"].replace("W", ""))
			effect_idx = int(factor["effect_period"].replace("W", ""))
			conn.execute("INSERT INTO response_factors(well_id, control_point_id, pumping_period, effect_period, factor_value) VALUES (?, ?, ?, ?, ?)", (factor["well_id"], factor["control_point_id"], pump_idx, effect_idx, factor["value"],),)

		for bid in seed_payload["bids"]:
			bid_period_idx = int(bid["period_id"].replace("W", ""))
			conn.execute("INSERT INTO bids(auction_id, trader_id, well_id, period_id, quantity, price, submitted_at, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, bid["trader_id"], bid["well_id"], bid_period_idx, bid["quantity"], bid["price"], now),)

	def _clear_all_data(self, conn: sqlite3.Connection) -> None:
		for table_name in ["constraint_results", "period_prices", "accepted_bids", "auction_runs", "bids", "response_factors", "control_point_bounds", "trader_allocations", "auction_periods", "auctions", "control_points", "wells", "traders", "metadata", ]:
			conn.execute(f"DELETE FROM {table_name}")

	def _meta(self, conn: sqlite3.Connection, key: str) -> str:
		row = conn.execute("SELECT meta_value FROM metadata WHERE meta_key=?", (key,)).fetchone()
		return "" if row is None else row["meta_value"]

	def _default_auction_id(self, conn: sqlite3.Connection) -> str:
		row = conn.execute("SELECT auction_id FROM auctions ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, created_at DESC LIMIT 1" ).fetchone()
		if row is None: raise ValueError("No auctions are defined.")
		return row["auction_id"]

	def current_participant_id(self) -> str:
		with self._connect() as conn:
			return self._meta(conn, "current_trader_id")

	def load(self, auction_id: str | None = None) -> AuctionCase:
		with self._connect() as conn:
			auction_id = auction_id or self._default_auction_id(conn)
			auction_row = conn.execute("SELECT auction_id, label, bid_close_label, status FROM auctions WHERE auction_id=?", (auction_id,) ).fetchone()
			if auction_row is None: raise ValueError(f"Unknown auction_id: {auction_id}")

			period_rows = conn.execute("SELECT p.period_id, p.display_label FROM periods p JOIN auction_periods ap ON p.period_id=ap.period_id WHERE ap.auction_id=? ORDER BY ap.period_order", (auction_id,), ).fetchall()
			periods = [AuctionPeriod(id=str(row["period_id"]), label=row["display_label"]) for row in period_rows]

			traders = []
			for trader_row in conn.execute("SELECT trader_id, name FROM traders ORDER BY trader_id").fetchall():
				alloc_rows = conn.execute("SELECT period_id, allocation FROM trader_allocations WHERE auction_id=? AND trader_id=?", (auction_id, trader_row["trader_id"]), ).fetchall()
				alloc_map = {str(row["period_id"]): float(row["allocation"]) for row in alloc_rows}
				traders.append(Participant(id=trader_row["trader_id"], name=trader_row["name"], allocation_by_period=alloc_map,))

			wells = [
				Well(
					id=row["well_id"],
					name=row["name"],
					participant_id=row["trader_id"],
					gw_model_row=row["gw_model_row"],
					gw_model_column=row["gw_model_column"],
					latitude=row["latitude"],
					longitude=row["longitude"],
				)
				for row in conn.execute(
					"SELECT well_id, name, trader_id, gw_model_row, gw_model_column, latitude, longitude"
					" FROM wells ORDER BY well_id"
				).fetchall()
			]

			control_points = []
			for cp_row in conn.execute("SELECT control_point_id, name, gw_model_row, gw_model_column, latitude, longitude FROM control_points ORDER BY control_point_id").fetchall():
				bound_rows = conn.execute("SELECT period_id, bound FROM control_point_bounds WHERE auction_id=? AND control_point_id=?", (auction_id, cp_row["control_point_id"]), ).fetchall()
				if not bound_rows:
					bound_rows = conn.execute("SELECT period_id, bound FROM default_control_point_bounds WHERE control_point_id=?", (cp_row["control_point_id"],)).fetchall()
				bound_map = {str(row["period_id"]): float(row["bound"]) for row in bound_rows}
				control_points.append(
					ControlPoint(
						id=cp_row["control_point_id"],
						name=cp_row["name"],
						bound_by_period=bound_map,
						gw_model_row=cp_row["gw_model_row"],
						gw_model_column=cp_row["gw_model_column"],
						latitude=cp_row["latitude"],
						longitude=cp_row["longitude"],
					)
				)

			response_factors = [ResponseFactor(well_id=row["well_id"], control_point_id=row["control_point_id"], pumping_period=str(row["pumping_period"]), effect_period=str(row["effect_period"]), value=float(row["factor_value"]),) for row in conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, factor_value FROM response_factors" ).fetchall()]

			bids = [BidSegment(id=f"bid-{row['bid_id']}", participant_id=row["trader_id"], well_id=row["well_id"], period_id=str(row["period_id"]), quantity=float(row["quantity"]), price=float(row["price"]), submitted_at=row["submitted_at"],) for row in conn.execute("SELECT bid_id, trader_id, well_id, period_id, quantity, price, submitted_at FROM bids WHERE auction_id=? AND deleted=0 ORDER BY bid_id", (auction_id,), ).fetchall()]

			return AuctionCase(catchment_name=self._meta(conn, "catchment_name"), source_note=self._meta(conn, "source_note"), current_participant_id=self._meta(conn, "current_trader_id"), auction=Auction(id=auction_row["auction_id"], label=auction_row["label"], bid_close_label=auction_row["bid_close_label"], status=auction_row["status"], periods=periods), participants=traders, wells=wells, control_points=control_points, response_factors=response_factors, bids=bids, rights_conversion=RightsConversion(policy_name=self._meta(conn, "rights_policy_name"), summary=self._meta(conn, "rights_policy_summary"),),)

	def current_participant(self, auction_case: AuctionCase) -> Participant: return next(p for p in auction_case.participants if p.id == auction_case.current_participant_id)

	def wells_for_participant(self, auction_case: AuctionCase, participant: Participant) -> list[Well]: return [well for well in auction_case.wells if well.participant_id == participant.id]

	def list_participants(self) -> list[dict]:
		with self._connect() as conn:
			return [{"id": row["trader_id"], "name": row["name"]} for row in conn.execute("SELECT trader_id, name FROM traders ORDER BY name").fetchall()]

	def _parse_bid_id(self, bid_id: str) -> int: return int(bid_id.split("-")[-1])

	def add_bid(self, auction_id: str, participant_id: str, well_id: str, period_id: str, quantity: float, price: float) -> BidSegment:
		now = datetime.now(timezone.utc).isoformat()
		with self._connect() as conn:
			cursor = conn.execute("INSERT INTO bids(auction_id, trader_id, well_id, period_id, quantity, price, submitted_at, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, participant_id, well_id, period_id, quantity, price, now),)
			bid_pk = int(cursor.lastrowid)
			return BidSegment(id=f"bid-{bid_pk}", participant_id=participant_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, submitted_at=now,)

	def delete_bid(self, bid_id: str, current_participant_id: str) -> bool:
		bid_pk = self._parse_bid_id(bid_id)
		with self._connect() as conn:
			row = conn.execute("SELECT trader_id, deleted FROM bids WHERE bid_id=?", (bid_pk,)).fetchone()
			if row is None or row["deleted"] == 1 or row["trader_id"] != current_participant_id: return False
			conn.execute("UPDATE bids SET deleted=1 WHERE bid_id=?", (bid_pk,))
			return True

	def reset_runtime_to_seed(self) -> AuctionCase:
		with self._connect() as conn:
			self._clear_all_data(conn)
			self._seed_from_json(conn)
		return self.load()

	def setup_auction(self, auction_id: str, label: str, bid_close_label: str, period_labels: list[str], clear_existing_bids: bool = True) -> Auction:
		now = datetime.now(timezone.utc).isoformat()
		with self._connect() as conn:
			source_auction_id = self._default_auction_id(conn)
			conn.execute("INSERT INTO auctions(auction_id, label, bid_close_label, status, created_at, ran_at) VALUES (?, ?, ?, 'OPEN', ?, NULL)", (auction_id, label, bid_close_label, now),)
			for idx, period_label in enumerate(period_labels):
				period_id = idx + 1
				conn.execute("INSERT OR IGNORE INTO periods(period_id, period_date, display_label) VALUES (?, NULL, ?)", (period_id, period_label))
				conn.execute("INSERT INTO auction_periods(auction_id, period_id, period_order) VALUES (?, ?, ?)", (auction_id, period_id, idx + 1),)

			traders = conn.execute("SELECT trader_id FROM traders").fetchall()
			for trader_row in traders:
				for idx, _ in enumerate(period_labels):
					period_id = idx + 1
					row = conn.execute("SELECT allocation FROM trader_allocations WHERE auction_id=? AND trader_id=? AND period_id=?", (source_auction_id, trader_row["trader_id"], period_id), ).fetchone()
					allocation = float(row["allocation"]) if row else 0.0
					conn.execute("INSERT INTO trader_allocations(auction_id, trader_id, period_id, allocation) VALUES (?, ?, ?, ?)", (auction_id, trader_row["trader_id"], period_id, allocation),)

			cps = conn.execute("SELECT control_point_id FROM control_points").fetchall()
			for cp_row in cps:
				for idx, _ in enumerate(period_labels):
					period_id = idx + 1
					row = conn.execute("SELECT bound FROM control_point_bounds WHERE auction_id=? AND control_point_id=? AND period_id=?", (source_auction_id, cp_row["control_point_id"], period_id), ).fetchone()
					bound = float(row["bound"]) if row else 0.0
					conn.execute("INSERT INTO control_point_bounds(auction_id, control_point_id, period_id, bound) VALUES (?, ?, ?, ?)", (auction_id, cp_row["control_point_id"], period_id, bound),)

			for idx, _ in enumerate(period_labels):
				period_id = idx + 1
				rows = conn.execute("SELECT well_id, control_point_id, pumping_period, effect_period, factor_value FROM response_factors WHERE auction_id=? AND pumping_period=? AND effect_period=?", (source_auction_id, period_id, period_id), ).fetchall()
				for row in rows:
					conn.execute("INSERT INTO response_factors(auction_id, well_id, control_point_id, pumping_period, effect_period, factor_value) VALUES (?, ?, ?, ?, ?, ?)", (auction_id, row["well_id"], row["control_point_id"], row["pumping_period"], row["effect_period"], float(row["factor_value"]),),)

			if not clear_existing_bids:
				rows = conn.execute("SELECT trader_id, well_id, period_id, quantity, price FROM bids WHERE auction_id=? AND deleted=0", (source_auction_id,), ).fetchall()
				for row in rows:
					if int(row["period_id"]) <= len(period_labels): conn.execute("INSERT INTO bids(auction_id, trader_id, well_id, period_id, quantity, price, submitted_at, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (auction_id, row["trader_id"], row["well_id"], row["period_id"], float(row["quantity"]), float(row["price"]), now,),)

		return Auction(id=auction_id, label=label, bid_close_label=bid_close_label, periods=[AuctionPeriod(id=str(idx + 1), label=lbl) for idx, lbl in enumerate(period_labels)],)

	def list_auctions(self) -> list[dict]:
		with self._connect() as conn:
			rows = conn.execute(""" SELECT a.auction_id, a.label, a.bid_close_label, a.status, a.created_at, a.ran_at, r.solve_status, r.objective_value, r.run_at AS latest_run_at FROM auctions a LEFT JOIN auction_runs r ON r.run_id = (SELECT rr.run_id FROM auction_runs rr WHERE rr.auction_id = a.auction_id ORDER BY rr.run_id DESC LIMIT 1) ORDER BY a.created_at DESC """ ).fetchall()
			return [dict(row) for row in rows]

	def save_run_results(self, auction_id: str, market_result: MarketResult, clearing_start_time: str | None = None) -> int:
		run_at = datetime.now(timezone.utc).isoformat()
		with self._connect() as conn:
			cursor = conn.execute("INSERT INTO auction_runs(auction_id, clearing_start_time, run_at, solve_status, objective_value) VALUES (?, ?, ?, ?, ?)", (auction_id, clearing_start_time, run_at, market_result.solve_status, float(market_result.objective_value)),)
			run_id = int(cursor.lastrowid)
			conn.execute("UPDATE auctions SET status='CLOSED', ran_at=? WHERE auction_id=?", (run_at, auction_id))

			for item in market_result.accepted_bids:
				bid_pk = self._parse_bid_id(item.bid_id)
				conn.execute("INSERT INTO accepted_bids(run_id, bid_id, accepted_quantity) VALUES (?, ?, ?)", (run_id, bid_pk, float(item.accepted_quantity)),)

			for period_id, price in market_result.period_prices.items():
				period_id_int = int(period_id)
				conn.execute("INSERT INTO period_prices(run_id, period_id, price) VALUES (?, ?, ?)", (run_id, period_id_int, float(price)),)

			for item in market_result.constraint_results:
				period_id_int = int(item.period_id)
				conn.execute("INSERT INTO constraint_results(run_id, control_point_id, period_id, used_capacity, bound_capacity, dual_value) VALUES (?, ?, ?, ?, ?, ?)", (run_id, item.control_point_id, period_id_int, float(item.used_capacity), float(item.bound_capacity), float(item.dual_value),),)
			return run_id

	def latest_run_summary(self, auction_id: str) -> dict | None:
		with self._connect() as conn:
			row = conn.execute("SELECT run_id, clearing_start_time, run_at, solve_status, objective_value FROM auction_runs WHERE auction_id=? ORDER BY run_id DESC LIMIT 1", (auction_id,), ).fetchone()
			if row is None: return None
			return dict(row)

	def bid_history(self, auction_id: str, participant_id: str) -> list[dict]:
		with self._connect() as conn:
			run_row = conn.execute("SELECT run_id FROM auction_runs WHERE auction_id=? ORDER BY run_id DESC LIMIT 1", (auction_id,), ).fetchone()
			run_id = None if run_row is None else int(run_row["run_id"])
			rows = conn.execute("SELECT bid_id, period_id, quantity, price, submitted_at FROM bids WHERE auction_id=? AND trader_id=? AND deleted=0 ORDER BY bid_id DESC", (auction_id, participant_id), ).fetchall()
			result = []
			for row in rows:
				accepted = 0.0
				if run_id is not None:
					acc = conn.execute("SELECT accepted_quantity FROM accepted_bids WHERE run_id=? AND bid_id=?", (run_id, int(row["bid_id"])), ).fetchone()
					if acc is not None: accepted = float(acc["accepted_quantity"])
				result.append({"bid_id": f"bid-{row['bid_id']}", "period_id": str(row["period_id"]), "quantity": float(row["quantity"]), "price": float(row["price"]), "submitted_at": row["submitted_at"], "accepted_quantity": accepted,})
			return result

	def catchment_price_rows(self, auction_id: str) -> tuple[list[dict], list[dict]]:
		with self._connect() as conn:
			run_row = conn.execute("SELECT run_id FROM auction_runs WHERE auction_id=? ORDER BY run_id DESC LIMIT 1", (auction_id,), ).fetchone()
			if run_row is None: return [], []
			run_id = int(run_row["run_id"])

			period_prices = {str(row["period_id"]): float(row["price"]) for row in conn.execute("SELECT period_id, price FROM period_prices WHERE run_id=?", (run_id,)).fetchall()}

			well_rows = []
			for well in conn.execute("SELECT well_id, name FROM wells ORDER BY well_id").fetchall():
				for period_id, price in period_prices.items():
					well_rows.append({"well_id": well["well_id"], "well_name": well["name"], "period_id": period_id, "price": price})

			cp_rows = []
			for row in conn.execute(""" SELECT c.control_point_id, c.name, r.period_id, r.dual_value, r.used_capacity, r.bound_capacity FROM constraint_results r JOIN control_points c ON c.control_point_id = r.control_point_id WHERE r.run_id=? ORDER BY c.control_point_id, r.period_id """, (run_id,), ).fetchall():
				cp_rows.append({"control_point_id": row["control_point_id"], "control_point_name": row["name"], "period_id": str(row["period_id"]), "dual_value": float(row["dual_value"]), "used_capacity": float(row["used_capacity"]), "bound_capacity": float(row["bound_capacity"]),})
			return well_rows, cp_rows

