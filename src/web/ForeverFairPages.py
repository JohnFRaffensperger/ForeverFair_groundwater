# web/ForeverFairPages.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Define FastAPI routes and wire web dependencies.

from __future__ import annotations
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
from services.ForeverFairData import ForeverFairData
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import SetupForeverFairDB

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "Tianqiao"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

foreverFairData_instance = ForeverFairData(db_path=DATA_DIR / "foreverfair.db", debug_db_path=PROJECT_ROOT / "Data_for_debugging" / "small_debug_database.db",)

app = FastAPI(title="Forever Fair 2026")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

def _flash_redirect(url: str, msg: str, status_code: int = 303) -> RedirectResponse:
	r = RedirectResponse(url=url, status_code=status_code)
	r.set_cookie("flash", msg, max_age=60, httponly=True, samesite="lax")
	return r

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
	traders = foreverFairData_instance.list_of_traders()
	now_iso = foreverFairData_instance.the_time_at_the_tone_is().isoformat(timespec="minutes")
	upcoming = foreverFairData_instance.list_auctions()
	next_final = next((a for a in reversed(upcoming) if a.get("status") == "OPEN" and a.get("auction_type") != "tentative" and (a.get("closed_date") or "") > now_iso), None)
	next_tentative = next((a for a in reversed(upcoming) if a.get("status") == "OPEN" and a.get("auction_type") == "tentative" and (a.get("closed_date") or "") > now_iso), None)
	return templates.TemplateResponse(request, "LoginPage.html", {"traders": traders, "next_final": next_final, "next_tentative": next_tentative, })

@app.post("/login")
def do_login(trader_id: int = Form(...)):
	response = RedirectResponse(url="/trader", status_code=303)
	response.set_cookie("trader_id", str(trader_id), max_age=86400, httponly=True)
	return response

@app.get("/researcher", response_class=HTMLResponse)
def researcher_page(request: Request):
	traders = foreverFairData_instance.list_of_traders()
	return templates.TemplateResponse(request, "Researcher.html", {"traders": traders})

@app.get("/database-documentation", response_class=HTMLResponse)
def database_documentation_page(request: Request):
	return templates.TemplateResponse(request, "Database_documentation.html", {})

@app.get("/hydrologist", response_class=HTMLResponse)
def doc_hydrologist(request: Request):
	notice = request.cookies.get("flash")
	resp = templates.TemplateResponse(request, "Hydrologist.html", {"bounds_imported_at": foreverFairData_instance.bounds_imported_at(), "notice": notice, })
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/programmer", response_class=HTMLResponse)
def doc_programmer(request: Request):
	report = SetupForeverFairDB.missing_import_data_report(DATA_DIR / "foreverfair.db")
	notice = request.cookies.get("flash")
	resp = templates.TemplateResponse(request, "Programmer.html", {"notice": notice, "missing_report": report, })
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/auctionmanager", response_class=HTMLResponse)
def doc_auctionmanager(request: Request):
	next_auction = foreverFairData_instance.get_next_auction_info()
	if next_auction is None: next_auction = foreverFairData_instance.add_auction()
	next_real_bid_count, next_default_bid_count = foreverFairData_instance.get_bid_count(next_auction["auction_id"])
	period_length_hours = foreverFairData_instance.latest_period_length_hours()
	bidding_periods = foreverFairData_instance.number_of_bidding_periods()
	# period_length_hours = period_length_hours or 168
	now_dt = foreverFairData_instance.the_time_at_the_tone_is()
	close_dt, default_first, default_last = foreverFairData_instance.get_auction_close_first_last_dates(now_dt, int(period_length_hours or 168), int(bidding_periods or 4))
	response_period_count = foreverFairData_instance.response_matrix_period_count()
	context: dict[str, Any] = {"auctions": foreverFairData_instance.list_auctions(), "period_length_hours": period_length_hours, "response_period_count": response_period_count, "bidding_periods": bidding_periods, "next_auction_id": next_auction_id, "next_real_bid_count": next_real_bid_count, "next_default_bid_count": next_default_bid_count,}
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
def trader_page(request: Request, auction_id: int | None = None):

	trader_cookie = request.cookies.get("trader_id")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	auction_id = foreverFairData_instance.get_next_auction_info()
	# this_trader_id = next((p for p in auction_case.traders if p.id == auction_case.current_trader_id), auction_case.traders[0])
	current_wells = foreverFairData_instance.get_trader_wells(trader_id)
	current_well = current_wells[0] # if current_wells else None
	bid_history = foreverFairData_instance.bid_history(auction_id, trader_id)
	quota_by_period = foreverFairData_instance.get_quota_auction_start(trader_id=this_trader_id.id, auction_id=auction_case.auction.id)
	period_rows: list[dict[str, Any]] = []
	for period in auction_case.auction.periods:
		period_key = int(period.id)
		period_rows.append({"period_id": period.id, "period_key": period_key, "period_label": period.label, "allocation": quota_by_period.get(period_key, 0.0),})
	context: dict[str, Any] = {"auction_case": auction_case, "current_trader": this_trader_id, "current_well": current_well, "bid_history": bid_history, "period_rows": period_rows, "auction_id": auction_case.auction.id,}
	notice = request.cookies.get("flash")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "Trader.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.post("/bids/new")
def create_bid(request: Request, auction_id: int = Form(...), well_id: int = Form(...), period_id: int = Form(...),  
	quantity: float = Form(...), price: float = Form(...),
	quantity2: str = Form(default=""), price2: str = Form(default=""),
	quantity3: str = Form(default=""), price3: str = Form(default=""),
	quantity4: str = Form(default=""), price4: str = Form(default=""), 
	quantity5: str = Form(default=""), price5: str = Form(default=""),
	is_automatic: bool = Form(default=False),):

	trader_cookie = request.cookies.get("trader_id")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	bid_steps: list[tuple[float, float]] = [(quantity, price)] + [(float(q), float(p)) for q, p in [(quantity2, price2), (quantity3, price3), (quantity4, price4), (quantity5, price5)] if str(q or "").strip() and str(p or "").strip()]
	try: BiddingController.submitBid(foreverFairData_instance, auction_id=auction_id, this_trader_id=trader_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_bid_default=is_automatic, bid_steps=bid_steps,)
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
def manager_run_auction(auction_id: int = Form(...)):
	# Guard: do not run an auction that has already closed by time.
	now_iso = foreverFairData_instance.the_time_at_the_tone_is().isoformat(timespec="minutes")
	auctions = foreverFairData_instance.list_auctions()
	target = next((a for a in auctions if int(a.get("auction_id") or 0) == int(auction_id)), None)
	if target is None: return _flash_redirect("/auctionmanager", "Error: Auction not found")
	bid_close = target.get("closed_date") or ""
	if target.get("status") == "CLOSED" or (bid_close and bid_close < now_iso): return _flash_redirect("/auctionmanager", "Error: Cannot run a closed auction")
	try:
		# Apply the latest default head-constraint bounds before running.
		# try: AuctionController.apply_default_bounds(foreverFairData_instance, auction_id)
		# except Exception: pass  # No default bounds yet; continue.
		AuctionController.runCurrentAuction(foreverFairData_instance, auction_id=auction_id)
	except Exception as e:
		if str(e) == "The auction cannot run because it has no bids.": return _flash_redirect("/auctionmanager", "The auction cannot run because it has no bids.")
		return _flash_redirect("/auctionmanager", f"Error: {e}")
	return _flash_redirect("/auctionmanager", "Auction run completed")

@app.post("/auctionmanager/delete-auction")
# def manager_delete_auction(auction_id: int = Form(...)):
# 	auctions = foreverFairData_instance.list_auctions()
# 	target = next((a for a in auctions if int(a.get("auction_id") or 0) == int(auction_id)), None)
# 	if target is None: return _flash_redirect("/auctionmanager", "Error: Auction not found")
# 	if target.get("status") == "CLOSED": return _flash_redirect("/auctionmanager", "Error: Cannot delete a closed auction")
# 	foreverFairData_instance.mark_auction_deleted(auction_id)
# 	return _flash_redirect("/auctionmanager", f"Auction {auction_id} deleted")

@app.get("/catchment", response_class=HTMLResponse)
def catchment_page(request: Request, auction_id: int | None = None):
	# auction_case = foreverFairData_instance.load(auction_id)
	well_price_rows, control_point_rows = foreverFairData_instance.catchment_price_rows(auction_case.auction.id)
	context: dict[str, Any] = {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}
	notice = request.cookies.get("flash")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "CatchmentPage.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/api/system-state")
def system_state_api(auction_id: int | None = None) -> dict[str, Any]:
	# auction_case = foreverFairData_instance.load(auction_id)
	latest_run = foreverFairData_instance.latest_run_summary(auction_case.auction.id)
	well_price_rows, control_point_rows = foreverFairData_instance.catchment_price_rows(auction_case.auction.id)
	return {"catchment_name": auction_case.catchment_name, "auction": auction_case.auction.model_dump(), "rights_conversion": auction_case.rights_conversion.model_dump(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

@app.get("/api/open-auctions")
def api_open_auctions():
	import sqlite3 as _sqlite3
	try:
		conn = _sqlite3.connect(DATA_DIR / "foreverfair.db")
		rows = conn.execute("SELECT auction_id, closed_date FROM auctions WHERE status='OPEN' ORDER BY created_date DESC").fetchall()
		conn.close()
		return JSONResponse([{"id": r[0], "closed_date": r[1]} for r in rows])
	except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/setup/db-status")
def setup_db_status(): return JSONResponse(SetupForeverFairDB.db_status(DATA_DIR / "foreverfair.db"), headers={"Cache-Control": "no-store"})

@app.post("/setup/create-db")
def setup_create_db():
	try:
		SetupForeverFairDB.create_empty_db(DATA_DIR / "foreverfair.db")
		return _flash_redirect("/programmer", "Empty database created")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error creating database: {e}")

@app.post("/setup/delete-db")
def setup_delete_db():
	db_path = DATA_DIR / "foreverfair.db"
	try:
		SetupForeverFairDB.delete_db(db_path)
		if db_path.exists(): return _flash_redirect("/programmer", "Error deleting database: database file still exists")
		return _flash_redirect("/programmer", "Database deleted")
	except Exception as e:
		import logging
		logging.getLogger("uvicorn.error").exception("Delete-db failed for %s", db_path)
		return _flash_redirect("/programmer", f"Error deleting database: {e}")

@app.post("/setup/import-decvar")
async def setup_import_decvar(file: UploadFile = File(...),):
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
		result = SetupForeverFairDB.import_mps(DATA_DIR / "foreverfair.db", text, period_length_hours=int(unit_hours))
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing MPS: {e}")
	notice = (f"MPS import complete: {result['response_matrix_inserted']} response factors,"
		      f" {result['control_point_rows_inserted']} control-point rows,"
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
		conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, meta_value) VALUES ('period_length_hours', ?)", (str(unit_hours),))
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "period_length_hours": unit_hours}

@app.get("/setup/current-bidding-periods")
def setup_current_bidding_periods():
	return {"num_bidding_periods": foreverFairData_instance.number_of_bidding_periods()}

@app.post("/setup/set-bidding-periods")
async def setup_set_bidding_periods(request: Request) -> dict[str, Any]:
	body = await request.json()
	try:
		value = int(body.get("num_bidding_periods", 4))
	except Exception:
		return {"ok": False, "error": "invalid number of bidding periods"}
	if value < 1 or value > 52: return {"ok": False, "error": "number of bidding periods must be 1..52"}
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(db_path)
	try:
		conn.execute("INSERT OR REPLACE INTO Catchment_info(meta_key, meta_value) VALUES ('num_bidding_periods', ?)", (str(value),))
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "num_bidding_periods": value}

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
