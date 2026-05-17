# web/ForeverFairPages.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Define FastAPI routes and wire web dependencies.

from __future__ import annotations
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import AuctionController
import BiddingController
from ForeverFairData import ForeverFairData
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import SetupForeverFairDB

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATCHMENT_ROOT = PROJECT_ROOT / "Catchment_data"
DEBUG_DB_PATH = CATCHMENT_ROOT / "Data_for_debugging" / "small_debug_database.db"
ACTIVE_CATCHMENT_ENV_VAR = "FOREVER_FAIR_CATCHMENT"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

def _available_catchment_dirs() -> list[Path]:
	if not CATCHMENT_ROOT.exists(): return []
	return sorted((path for path in CATCHMENT_ROOT.iterdir() if path.is_dir() and path.name != "Data_for_debugging"), key=lambda path: path.name.lower())

def _resolve_initial_catchment_name() -> str:
	configured = os.environ.get(ACTIVE_CATCHMENT_ENV_VAR, "").strip()
	available = _available_catchment_dirs()
	if configured and any(path.name == configured for path in available): return configured
	if available: return available[0].name
	return "Undefined"

def _build_repository(catchment_name: str) -> tuple[Path, ForeverFairData]:
	data_dir = CATCHMENT_ROOT / catchment_name
	return data_dir, ForeverFairData(db_path=data_dir / "foreverfair.db", debug_db_path=DEBUG_DB_PATH,)

_active_catchment_name = _resolve_initial_catchment_name()
DATA_DIR, foreverFairData_instance = _build_repository(_active_catchment_name)

def set_active_catchment(catchment_name: str) -> None:
	global DATA_DIR, foreverFairData_instance, _active_catchment_name
	selected_name = catchment_name.strip()
	available_names = {path.name for path in _available_catchment_dirs()}
	if selected_name not in available_names: raise ValueError(f"Unknown catchment: {selected_name}")
	_active_catchment_name = selected_name
	DATA_DIR, foreverFairData_instance = _build_repository(selected_name)

app = FastAPI(title="Forever Fair 2026")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

_auctionmanager_debug_log: list[str] = []
_auctionmanager_run_active = False

def add_auctionmanager_debug(message: str) -> None:
	_auctionmanager_debug_log.append(str(message))

def clear_auctionmanager_debug() -> None:
	_auctionmanager_debug_log.clear()

def get_auctionmanager_debug_text() -> str:
	return "\n".join(_auctionmanager_debug_log[-400:])

def set_auctionmanager_run_active(is_active: bool) -> None:
	global _auctionmanager_run_active
	_auctionmanager_run_active = is_active

def get_auctionmanager_run_active() -> bool:
	return _auctionmanager_run_active

def _flash_redirect(url: str, msg: str, status_code: int = 303) -> RedirectResponse:
	r = RedirectResponse(url=url, status_code=status_code)
	r.set_cookie("flash", msg, max_age=60, httponly=True, samesite="lax")
	return r

def _common_template_context() -> dict[str, Any]:
	return {"active_catchment_name": _active_catchment_name}

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
	traders = foreverFairData_instance.list_of_traders()
	now_iso = foreverFairData_instance.the_time_at_the_tone_is().isoformat(timespec="minutes")
	upcoming = foreverFairData_instance.list_auctions()
	next_final = next((a for a in reversed(upcoming) if a.get("status") == "OPEN" and a.get("auction_type") != "tentative" and (a.get("closed_date") or "") > now_iso), None)
	next_tentative = next((a for a in reversed(upcoming) if a.get("status") == "OPEN" and a.get("auction_type") == "tentative" and (a.get("closed_date") or "") > now_iso), None)
	context = _common_template_context()
	context.update({"traders": traders, "next_final": next_final, "next_tentative": next_tentative, })
	return templates.TemplateResponse(request, "LoginPage.html", context)

@app.post("/login")
def do_login(trader_id: int = Form(...)):
	response = RedirectResponse(url="/trader", status_code=303)
	response.set_cookie("trader_id", str(trader_id), max_age=86400, httponly=True)
	return response

@app.get("/researcher", response_class=HTMLResponse)
def researcher_page(request: Request):
	traders = foreverFairData_instance.list_of_traders()
	context = _common_template_context()
	context.update({"traders": traders})
	return templates.TemplateResponse(request, "Researcher.html", context)

@app.get("/database-documentation", response_class=HTMLResponse)
def database_documentation_page(request: Request):
	return templates.TemplateResponse(request, "Database_documentation.html", _common_template_context())

@app.get("/hydrologist", response_class=HTMLResponse)
def doc_hydrologist(request: Request):
	notice = request.cookies.get("flash")
	context = _common_template_context()
	context.update({"bounds_imported_at": foreverFairData_instance.bounds_imported_at(), "notice": notice, })
	resp = templates.TemplateResponse(request, "Hydrologist.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/programmer", response_class=HTMLResponse)
def doc_programmer(request: Request):
	report = SetupForeverFairDB.missing_import_data_report(DATA_DIR / "foreverfair.db")
	notice = request.cookies.get("flash")
	context = _common_template_context()
	period_length_hours = foreverFairData_instance.latest_period_length_hours()
	bidding_periods = foreverFairData_instance.get_number_of_bidding_periods()
	now_dt = foreverFairData_instance.the_time_at_the_tone_is()
	context["today_display"] = now_dt.strftime("%d %b %Y")
	context["today_weekday"] = now_dt.strftime("%A")
	context["period_length_hours"] = period_length_hours
	context["bidding_periods"] = bidding_periods
	if period_length_hours is not None:
		close_dt, _, _ = foreverFairData_instance.get_auction_close_first_last_dates(now_dt, period_length_hours, bidding_periods)
		period_td = timedelta(hours=period_length_hours)
		context["first_three_closes"] = [( close_dt + i * period_td).strftime("%d %b %Y %H:%M") for i in range(3)]
	else:
		context["first_three_closes"] = None
	context.update({"notice": notice, "missing_report": report, "available_catchments": [path.name for path in _available_catchment_dirs()], "max_bid_steps": foreverFairData_instance.get_max_bid_steps(), })
	resp = templates.TemplateResponse(request, "Programmer.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/auctionmanager", response_class=HTMLResponse)
def doc_auctionmanager(request: Request):
	next_auction = foreverFairData_instance.get_next_auction_info()
	if next_auction is None:
		AuctionController.create_auction(DATA_DIR / "foreverfair.db")
		next_auction = foreverFairData_instance.get_next_auction_info()

	next_real_bid_count, next_default_bid_count = foreverFairData_instance.get_bid_count(next_auction["auction_id"])
	period_length_hours = foreverFairData_instance.latest_period_length_hours()
	bidding_periods = foreverFairData_instance.get_number_of_bidding_periods()
	now_dt = foreverFairData_instance.the_time_at_the_tone_is()
	close_dt, default_first, default_last = foreverFairData_instance.get_auction_close_first_last_dates(now_dt, period_length_hours or 168, bidding_periods)

	response_period_count = foreverFairData_instance.response_matrix_period_count()
	auctions = foreverFairData_instance.list_auctions()
	context: dict[str, Any] = {"auction": next_auction, "auctions": auctions, "period_length_hours": period_length_hours, "response_period_count": response_period_count, "bidding_periods": bidding_periods, "next_auction_id": next_auction["auction_id"], "next_real_bid_count": next_real_bid_count, "next_default_bid_count": next_default_bid_count,}
	notice = request.cookies.get("flash")
	context["notice"] = notice
	context["now"] = now_dt.isoformat(timespec="minutes")
	context["today_display"] = now_dt.strftime("%d %b %Y")
	context["today_weekday"] = now_dt.strftime("%A")
	context["scheduled_close_display"] = close_dt.strftime("%d %b %Y %H:%M")
	context["default_first_display"] = default_first.strftime("%d %b %Y")
	context["default_last_display"] = default_last.strftime("%d %b %Y")
	context["default_close_time"] = close_dt.isoformat(timespec="minutes")
	context["default_first_water_take"] = default_first.isoformat(timespec="minutes")
	context["default_last_water_take"] = default_last.isoformat(timespec="minutes")
	context["debug_text"] = get_auctionmanager_debug_text()
	context.update(_common_template_context())
	constraint_revenues: dict[int, float] = {}
	for a in auctions:
		if a.get("solve_status") == "Optimal":
			try: constraint_revenues[int(a["auction_id"])] = AuctionController.compute_revenue_on_constraint_quota(foreverFairData_instance, int(a["auction_id"]))
			except Exception as e: add_auctionmanager_debug(f"Constraint revenue error for auction {a.get('auction_id')}: {e}")
	context["constraint_revenues"] = constraint_revenues
	if period_length_hours is not None and response_period_count:
		context["default_last_constrained"] = (default_first + timedelta(hours=period_length_hours * response_period_count)).strftime("%d %b %Y")
	else: context["default_last_constrained"] = ""
	resp = templates.TemplateResponse(request, "AuctionManager.html", context)
	resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
	resp.headers["Pragma"] = "no-cache"
	resp.headers["Expires"] = "0"
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/", response_class=HTMLResponse)
def home(): return RedirectResponse(url="/researcher", status_code=303)

@app.get("/trader", response_class=HTMLResponse)
def trader_page(request: Request):

	trader_cookie = request.cookies.get("trader_id")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	next_auction = foreverFairData_instance.get_next_auction_info()
	if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
	auction_id = next_auction["auction_id"]
	auction_info = foreverFairData_instance.get_auction_info(auction_id)
	current_wells = foreverFairData_instance.get_trader_wells(trader_id)
	current_well = current_wells[0]
	bid_history = foreverFairData_instance.get_bid_history(auction_id, trader_id)
	quota_by_period = foreverFairData_instance.get_well_start_quota(well_id=current_well["id"], auction_id=auction_id)
	max_bid_steps = foreverFairData_instance.get_max_bid_steps()
	period_rows: list[dict[str, Any]] = []
	for period in auction_record.periods:
		period_rows.append({"period_id": period.id, "period_key": period.id, "period_label": period.label, "allocation": quota_by_period.get(period.id, 0.0),})
	current_trader = next((t for t in foreverFairData_instance.list_of_traders() if t["id"] == trader_id), {"id": trader_id, "name": ""})
	context: dict[str, Any] = {"current_trader": current_trader, "current_well": current_well, "bid_history": bid_history, "period_rows": period_rows, "auction_id": auction_id, "auction_case": {"auction": auction_info}, "max_bid_steps": max_bid_steps, "optional_bid_step_numbers": list(range(2, max_bid_steps + 1)),}
	notice = request.cookies.get("flash")
	context["notice"] = notice
	context.update(_common_template_context())
	resp = templates.TemplateResponse(request, "Trader.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.post("/bids/new")
async def create_bid(request: Request):
	form = await request.form()
	try:
		auction_id = int(str(form.get("auction_id", "")).strip())
		well_id = int(str(form.get("well_id", "")).strip())
		period_id = int(str(form.get("period_id", "")).strip())
		quantity = float(str(form.get("quantity", "")).strip())
		price = float(str(form.get("price", "")).strip())
	except Exception:
		return _flash_redirect("/trader", "Error: invalid bid form values")
	is_default = str(form.get("is_default", "")).strip().lower() in {"true", "on", "1", "yes"}

	trader_cookie = request.cookies.get("trader_id")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	bid_steps: list[tuple[float, float]] = [(quantity, price)]
	for step_num in range(2, foreverFairData_instance.get_max_bid_steps() + 1):
		quantity_text = str(form.get(f"quantity{step_num}", "") or "").strip()
		price_text = str(form.get(f"price{step_num}", "") or "").strip()
		if not quantity_text and not price_text:
			continue
		if not quantity_text or not price_text:
			return _flash_redirect(f"/trader?auction_id={auction_id}", f"Error: Step {step_num} requires both quantity and price")
		try:
			bid_steps.append((float(quantity_text), float(price_text)))
		except ValueError:
			return _flash_redirect(f"/trader?auction_id={auction_id}", f"Error: Step {step_num} has invalid quantity or price")
	try: BiddingController.submitBid(foreverFairData_instance, auction_id=auction_id, this_trader_id=trader_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_bid_default=is_default, bid_steps=bid_steps,)
	except ValueError as e:
		return _flash_redirect(f"/trader?auction_id={auction_id}", f"Error: {e}")
	return _flash_redirect(f"/trader?auction_id={auction_id}", "Bid saved")

@app.post("/bids/{bid_id}/delete")
def delete_bid(request: Request, bid_id: int):
	trader_cookie = request.cookies.get("trader_id")
	trader_id = int(trader_cookie) if trader_cookie and trader_cookie.isdigit() else 0
	deleted = BiddingController.deleteBid(foreverFairData_instance, bid_id, trader_id)
	return _flash_redirect("/trader", "Bid deleted" if deleted else "Bid not found")

@app.post("/auctionmanager/run-auction")
async def manager_run_auction(request: Request):
	try:
		form = await request.form()
		auction_id = int(str(form.get("auction_id", "")).strip())
	except (ValueError, KeyError):
		return JSONResponse({"ok": False, "message": "Error: Missing or invalid auction_id"}, status_code=400)
	
	# Guard: do not run an auction that has already closed by time.
	target = next((a for a in foreverFairData_instance.list_auctions() if a.get("auction_id") == auction_id), None)
	if target is None:
		if request.headers.get("x-requested-with") == "fetch": return JSONResponse({"ok": False, "message": "Error: Auction not found"}, status_code=404)
		return _flash_redirect("/auctionmanager", "Error: Auction not found")

	bid_close = target.get("closed_date") or ""
	if target.get("status") == "CLOSED" or (bid_close and bid_close < foreverFairData_instance.the_time_at_the_tone_is().isoformat(timespec="minutes")):
		if request.headers.get("x-requested-with") == "fetch": return JSONResponse({"ok": False, "message": "Error: Cannot run a closed auction"}, status_code=400)
		return _flash_redirect("/auctionmanager", "Error: Cannot run a closed auction")
	try:
		clear_auctionmanager_debug()
		set_auctionmanager_run_active(True)
		add_auctionmanager_debug(f"Run requested for auction_id={auction_id}")
		# Apply the latest default head-constraint bounds before running.
		# try: AuctionController.apply_default_bounds(foreverFairData_instance, auction_id)
		# except Exception: pass  # No default bounds yet; continue.
		AuctionController.runCurrentAuction(foreverFairData_instance, auction_id=auction_id, debug_log=add_auctionmanager_debug)
		add_auctionmanager_debug("Auction run completed")
	except Exception as e:
		add_auctionmanager_debug(f"Error: {e}")
		if request.headers.get("x-requested-with") == "fetch":
			set_auctionmanager_run_active(False)
			if str(e) == "The auction cannot run because it has no bids.": return JSONResponse({"ok": False, "message": "The auction cannot run because it has no bids."}, status_code=400)
			return JSONResponse({"ok": False, "message": f"Error: {e}"}, status_code=500)
		set_auctionmanager_run_active(False)
		if str(e) == "The auction cannot run because it has no bids.": return _flash_redirect("/auctionmanager", "The auction cannot run because it has no bids.")
		return _flash_redirect("/auctionmanager", f"Error: {e}")
	set_auctionmanager_run_active(False)
	if request.headers.get("x-requested-with") == "fetch": return JSONResponse({"ok": True, "message": "Auction run completed"})
	return _flash_redirect("/auctionmanager", "Auction run completed")

@app.get("/api/auctionmanager-debug")
def api_auctionmanager_debug() -> JSONResponse:
	return JSONResponse({"debug_text": get_auctionmanager_debug_text(), "run_active": get_auctionmanager_run_active()}, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})

@app.get("/catchment", response_class=HTMLResponse)
def catchment_page(request: Request, auction_id: int | None = None):
	if auction_id is None:
		next_auction = foreverFairData_instance.get_next_auction_info()
		if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
		auction_id = next_auction["auction_id"]
	well_price_rows, control_point_rows = foreverFairData_instance.catchment_price_rows(auction_id)
	context: dict[str, Any] = {"catchment_name": foreverFairData_instance.get_catchment_name(), "auction": foreverFairData_instance.get_auction_info(auction_id), "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}
	context.update(_common_template_context())
	notice = request.cookies.get("flash")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "CatchmentPage.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/api/system-state")
def system_state_api(auction_id: int | None = None) -> dict[str, Any]:
	if auction_id is None:
		next_auction = foreverFairData_instance.get_next_auction_info()
		if next_auction is None: return {"error": "No open auction. Ask the auction manager to create one."}
		auction_id = next_auction["auction_id"]
	latest_run = foreverFairData_instance.get_run_summary(auction_id)
	well_price_rows, control_point_rows = foreverFairData_instance.catchment_price_rows(auction_id)
	return {"catchment_name": foreverFairData_instance.get_catchment_name(), "auction": foreverFairData_instance.get_auction_info(auction_id), "rights_conversion": foreverFairData_instance.get_rights_conversion_dict(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

@app.get("/api/open-auctions")
def api_open_auctions():
	import sqlite3 as _sqlite3
	try:
		conn = _sqlite3.connect(DATA_DIR / "foreverfair.db") # TODO: change this SQL to an accessor.
		rows = conn.execute("SELECT auction_id, closed_date FROM auctions WHERE status='OPEN' ORDER BY created_date DESC").fetchall()
		conn.close()
		return JSONResponse([{"id": r[0], "closed_date": r[1]} for r in rows])
	except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/setup/db-status") # TODO: this file should never know the db status.
def setup_db_status(): return JSONResponse(SetupForeverFairDB.db_status(DATA_DIR / "foreverfair.db"), headers={"Cache-Control": "no-store"})

@app.post("/setup/create-db")
def setup_create_db():
	try:
		SetupForeverFairDB.create_empty_db(DATA_DIR / "foreverfair.db")
		return _flash_redirect("/programmer", "Empty database created")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error creating database: {e}")

@app.post("/setup/select-catchment")
def setup_select_catchment(catchment_name: str = Form(...)):
	try:
		set_active_catchment(catchment_name)
		return _flash_redirect("/programmer", f"Active catchment set to {catchment_name}")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error selecting catchment: {e}")

@app.post("/setup/delete-db")
def setup_delete_db():
	db_path = DATA_DIR / "foreverfair.db"
	try:
		SetupForeverFairDB.delete_db(db_path)
		if db_path.exists(): return _flash_redirect("/programmer", "Error deleting database: database file still exists")
		SetupForeverFairDB.create_empty_db(db_path)
		return _flash_redirect("/programmer", "Database deleted")
	except Exception as e:
		import logging
		logging.getLogger("uvicorn.error").exception("Delete-db failed for %s", db_path)
		return _flash_redirect("/programmer", f"Error deleting database: {e}")

@app.post("/setup/import-decvar")
async def setup_import_decvar(file: UploadFile = File(...),):
	status = SetupForeverFairDB.db_status(DATA_DIR / "foreverfair.db") # TODO: database name should not be hardwired.
	tables = status.get("tables", {}) if isinstance(status, dict) else {}
	if not status.get("exists") or "wells" not in tables:
		return _flash_redirect("/programmer", "Create new dataase first")
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_decvar(DATA_DIR / "foreverfair.db", text)
	notice = (f"DECVAR import complete: {result['wells_inserted']} wells inserted"
	          f" (inferred: {result['num_wells']} wells, {result['num_pump_periods']} pump periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-hedcon")
async def setup_import_hedcon(file: UploadFile = File(...),):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		result = SetupForeverFairDB.import_hedcon(DATA_DIR / "foreverfair.db", text)
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing HEDCON: {e}")
	notice = (f"HEDCON import complete: {result['control_points_inserted']} control points,"
	          f" {result['control_point_rows_inserted']} minimum-head rows inserted"
	          f" (inferred: {result['num_control_points']} control points, {result['num_control_periods']} control periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-mps")
async def setup_import_mps(file: UploadFile = File(...), period_unit: str = Form(...),):
	try:
		unit_hours = {"hour": 1, "day": 24, "week": 168}.get(str(period_unit).strip().lower())
		if unit_hours is None: return _flash_redirect("/programmer", "Error importing MPS: invalid period unit")
		text = (await file.read()).decode("utf-8", errors="replace")
		db_path = DATA_DIR / "foreverfair.db"
		result = SetupForeverFairDB.import_mps(db_path, text, period_length_hours=unit_hours)
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing MPS: {e}")
	notice = (f"MPS import complete: {result['response_matrix_inserted']} response factors,"
		      f" {result['aquifer_head_rows_inserted']} aquifer-head rows,"
		      f" {result['license_rows_inserted']} trader-license rows"
		      f" ({result['wells_ensured']} wells ensured)"
		      f" (using: {result['num_wells']} wells, {result['num_pump_periods']} pump periods,"
		      f" {result['num_control_points']} control points, {result['num_control_periods']} control periods)")
	notice += f"; period length set to {result['period_length_hours']} hours"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.get("/setup/current-period-unit")
def setup_current_period_unit() -> dict[str, Any]:
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists(): return {"period_unit": None, "period_length_hours": None}
	hours = foreverFairData_instance.latest_period_length_hours()
	if hours is None: return {"period_unit": None, "period_length_hours": None}
	if hours == 1: unit = "hour"
	elif hours == 24: unit = "day"
	elif hours == 168: unit = "week"
	else: unit = "day"
	return {"period_unit": unit, "period_length_hours": hours}

@app.post("/setup/set-period-unit")
async def setup_set_period_unit(request: Request) -> dict[str, Any]:
	body = await request.json()
	unit = str(body.get("period_unit", "")).strip().lower()
	unit_hours = {"hour": 1, "day": 24, "week": 168}.get(unit)
	if unit_hours is None: return {"ok": False, "error": "invalid period unit"}
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "period_length_hours", unit_hours)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "period_length_hours": unit_hours}

@app.get("/setup/current-bidding-periods")
def setup_current_bidding_periods():
	return {"num_bidding_periods": foreverFairData_instance.get_number_of_bidding_periods()}

@app.get("/setup/current-max-bid-steps")
def setup_current_max_bid_steps() -> dict[str, Any]:
	return {"max_bid_steps": foreverFairData_instance.get_max_bid_steps()}

@app.get("/setup/current-rights-policy")
def setup_current_rights_policy() -> dict[str, Any]:
	return {"rights_policy": foreverFairData_instance.get_rights_policy()}

@app.post("/setup/set-rights-policy")
async def setup_set_rights_policy(request: Request) -> dict[str, Any]:
	VALID_POLICIES = {"Users_pay", "Auction_manager_pays", "Quota_scaled"}
	body = await request.json()
	value = body.get("rights_policy", "")
	if value not in VALID_POLICIES:
		return {"ok": False, "error": f"invalid rights policy: {value}"}
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists():
		return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "Rights_policy", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "rights_policy": value}

@app.post("/setup/set-bidding-periods")
async def setup_set_bidding_periods(request: Request) -> dict[str, Any]:
	body = await request.json()
	try:
		value = int(body.get("num_bidding_periods", 4)) # TODO: Why "4"? Why int?
	except Exception:
		return {"ok": False, "error": "invalid number of bidding periods"}
	if value < 1 or value > 52: return {"ok": False, "error": "number of bidding periods must be 1..52"}
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "num_bidding_periods", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "num_bidding_periods": value}

@app.post("/setup/set-max-bid-steps")
async def setup_set_max_bid_steps(request: Request) -> dict[str, Any]:
	body = await request.json()
	try:
		value = int(body.get("max_bid_steps", 3))
	except Exception:
		return {"ok": False, "error": "invalid maximum bid steps"}
	if value < 1 or value > 5:
		return {"ok": False, "error": "maximum bid steps must be 1..5"}
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists():
		return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "MAX_BID_STEPS", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "max_bid_steps": value}

@app.post("/setup/import-trader-names")
async def setup_import_trader_names(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_trader_names(DATA_DIR / "foreverfair.db", text)
	notice = (f"Trader names import: {result['traders_inserted']} inserted,"
	          f" {result['traders_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-trader-wells")
async def setup_import_trader_wells(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_trader_wells(DATA_DIR / "foreverfair.db", text)
	notice = f"Trader-well assignments: {result['wells_assigned']} wells assigned"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-well-lat-lon")
async def setup_import_well_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_well_lat_lon(DATA_DIR / "foreverfair.db", text)
	notice = (f"Well lat-lon import: {result['wells_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-control-point-lat-lon")
async def setup_import_control_point_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_control_point_lat_lon(DATA_DIR / "foreverfair.db", text)
	notice = (f"Control-point lat-lon import: {result['control_points_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/setup-first-auction")
def setup_first_auction():
	try:
		auction_id = AuctionController.create_auction(DATA_DIR / "foreverfair.db")
		return _flash_redirect("/programmer", f"Auction system set up: auction_id={auction_id}")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error setting up auction system: {e}")
