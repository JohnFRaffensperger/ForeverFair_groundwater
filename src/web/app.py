# web/app.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Define FastAPI routes and wire web dependencies.

from __future__ import annotations
import sys
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import AuctionController
import BiddingController
from services.repository import AuctionRepository
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup as db_setup
import phase5_imports

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "Tianqiao"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

repository = AuctionRepository(seed_path=DATA_DIR / "forever_fair_seed.json", db_path=DATA_DIR / "foreverfair.db",)

app = FastAPI(title="Forever Fair 3.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

def _flash_redirect(url: str, msg: str, status_code: int = 303) -> RedirectResponse:
	r = RedirectResponse(url=url, status_code=status_code)
	r.set_cookie("flash", msg, max_age=60, httponly=True, samesite="lax")
	return r

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
	participants = repository.list_participants()
	now_iso = datetime.now().isoformat()
	upcoming = repository.list_auctions()
	next_final = next((a for a in reversed(upcoming) if a.get("status") == "OPEN" and a.get("auction_type") != "tentative" and (a.get("closed_date") or "") > now_iso), None)
	next_tentative = next((a for a in reversed(upcoming) if a.get("status") == "OPEN" and a.get("auction_type") == "tentative" and (a.get("closed_date") or "") > now_iso), None)
	return templates.TemplateResponse(request, "LoginPage.html", {"participants": participants, "next_final": next_final, "next_tentative": next_tentative, })

@app.post("/login")
def do_login(participant_id: str = Form(...)):
	response = RedirectResponse(url="/trader", status_code=303)
	response.set_cookie("participant_id", participant_id, max_age=86400, httponly=True)
	return response

@app.get("/researcher", response_class=HTMLResponse)
def researcher_page(request: Request):
	participants = repository.list_participants()
	return templates.TemplateResponse(request, "Researcher.html", {"participants": participants})

@app.get("/database-documentation", response_class=HTMLResponse)
def database_documentation_page(request: Request):
	return templates.TemplateResponse(request, "Database_documentation.html", {})

@app.get("/hydrologist", response_class=HTMLResponse)
def doc_hydrologist(request: Request):
	import sqlite3 as _sqlite3
	bounds_imported_at = None
	try:
		conn = _sqlite3.connect(DATA_DIR / "foreverfair.db")
		row = conn.execute("SELECT MAX(imported_at) FROM default_control_point_bounds").fetchone() 
		bounds_imported_at = row[0] if row and row[0] else None
		conn.close()
	except Exception: pass
	notice = request.cookies.get("flash")
	resp = templates.TemplateResponse(request, "Hydrologist.html", {"bounds_imported_at": bounds_imported_at, "notice": notice, })
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/programmer", response_class=HTMLResponse)
def doc_programmer(request: Request):
	report = db_setup.missing_import_data_report(DATA_DIR / "foreverfair.db")
	notice = request.cookies.get("flash")
	resp = templates.TemplateResponse(request, "Programmer.html", {"notice": notice, "missing_report": report, })
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/auctionmanager", response_class=HTMLResponse)
def doc_auctionmanager(request: Request):
	context = AuctionController.getManagerPageState(repository)
	notice = request.cookies.get("flash")
	context["notice"] = notice
	context["now"] = datetime.now().isoformat(timespec="minutes")
	base = datetime.now()
	days_ahead = (7 - base.weekday()) % 7
	if days_ahead == 0: days_ahead = 7
	default_first = (base + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
	default_last = default_first + timedelta(days=28)
	context["default_first_water_take"] = default_first.isoformat(timespec="minutes")
	context["default_last_water_take"] = default_last.isoformat(timespec="minutes")
	period_len = context.get("period_length_hours")
	resp_n = context.get("response_period_count")
	if period_len and resp_n:
		context["default_last_constrained"] = (default_first + timedelta(hours=int(period_len) * int(resp_n))).isoformat(timespec="minutes")
	else:
		context["default_last_constrained"] = ""
	resp = templates.TemplateResponse(request, "AuctionManager.html", context)
	resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
	resp.headers["Pragma"] = "no-cache"
	resp.headers["Expires"] = "0"
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/", response_class=HTMLResponse)
def home(): return RedirectResponse(url="/researcher", status_code=303)

@app.get("/trader", response_class=HTMLResponse)
def trader_page(request: Request, auction_id: str | None = None):
	participant_id = request.cookies.get("participant_id")
	if not participant_id: return RedirectResponse(url="/login", status_code=303)
	context = AuctionController.getTraderPageState(repository, auction_id, participant_id=participant_id)
	notice = request.cookies.get("flash")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "Trader.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.post("/bids/new")
def create_bid(request: Request, auction_id: str = Form(...), well_id: str = Form(...), period_id: str = Form(...),  
	quantity: float = Form(...), price: float = Form(...),
	quantity2: str = Form(default=""), price2: str = Form(default=""),
	quantity3: str = Form(default=""), price3: str = Form(default=""),
	quantity4: str = Form(default=""), price4: str = Form(default=""), 
	quantity5: str = Form(default=""), price5: str = Form(default=""),
	is_automatic: bool = Form(default=False),):

	participant_id = request.cookies.get("participant_id")
	if not participant_id: return RedirectResponse(url="/login", status_code=303)

	def _parse_optional_step(q_raw: str, p_raw: str, step_num: int) -> tuple[float, float] | None:
		q_text = str(q_raw or "").strip()
		p_text = str(p_raw or "").strip()
		if not q_text and not p_text: return None
		if not q_text or not p_text: raise ValueError(f"Step {step_num}: quantity and price must both be provided.")
		try: return float(q_text), float(p_text)
		except Exception: raise ValueError(f"Step {step_num}: invalid number format.")

	bid_steps: list[tuple[float, float]] = [(quantity, price)]
	for step_num, (q_raw, p_raw) in enumerate([(quantity2, price2), (quantity3, price3), (quantity4, price4), (quantity5, price5)], start=2,):
		parsed = _parse_optional_step(q_raw, p_raw, step_num)
		if parsed is not None: bid_steps.append(parsed)
	try: BiddingController.submitBid(repository, auction_id=auction_id, participant_id=participant_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_automatic=is_automatic, bid_steps=bid_steps,)
	except ValueError as e:
		return _flash_redirect(f"/trader?auction_id={auction_id}", f"Error: {e}")
	return _flash_redirect(f"/trader?auction_id={auction_id}", "Bid saved")

@app.post("/bids/{bid_id}/delete")
def delete_bid(request: Request, bid_id: str):
	participant_id = request.cookies.get("participant_id") or ""
	deleted = BiddingController.deleteBid(repository, bid_id, participant_id)
	return _flash_redirect("/trader", "Bid deleted" if deleted else "Bid not found")

@app.get("/manager", response_class=HTMLResponse)
def manager_page():
	return RedirectResponse(url="/auctionmanager", status_code=303)

@app.post("/manager/setup-auction")
def manager_setup_auction (bid_close_time: str = Form(...), tentative: bool = Form(default=False), first_water_take_time: str = Form(...), last_water_take_time: str = Form(...), ):
	try: close_dt = datetime.fromisoformat(bid_close_time)
	except Exception: return _flash_redirect("/auctionmanager", "Error: invalid closing date/time")
	try:
		first_take_dt = datetime.fromisoformat(first_water_take_time)
		last_take_dt = datetime.fromisoformat(last_water_take_time)
	except Exception:
		return _flash_redirect("/auctionmanager", "Error: invalid water-take date/time")
	if close_dt < datetime.now(): return _flash_redirect("/auctionmanager", "Error: closing date/time cannot be in the past")
	if last_take_dt < first_take_dt: return _flash_redirect("/auctionmanager", "Error: lastWaterTakeDate must be on or after firstWaterTakeDate")

	auction_type = "tentative" if tentative else "final"
	period_length_hours = repository.latest_period_length_hours()
	if period_length_hours is None:
		return _flash_redirect("/auctionmanager", "Error: import an .mps file and choose period length first")

	try:
		AuctionController.SetUpAuction(repository, closed_date=close_dt.isoformat(timespec="minutes"),
			first_water_take_date=first_take_dt.isoformat(timespec="minutes"), last_water_take_date=last_take_dt.isoformat(timespec="minutes"), period_length_hours=int(period_length_hours), auction_type=auction_type,)
	except ValueError as e:
		return _flash_redirect("/auctionmanager", f"Error: {e}")
	return _flash_redirect("/auctionmanager", "Auction created")

@app.post("/manager/run-auction")
def manager_run_auction(auction_id: str = Form(...)):
	# Guard: do not run an auction that has already closed by time.
	now_iso = datetime.now().isoformat(timespec="minutes")
	auctions = repository.list_auctions()
	target = next((a for a in auctions if str(a.get("auction_id")) == str(auction_id)), None)
	if target is None: return _flash_redirect("/auctionmanager", "Error: Auction not found")
	bid_close = target.get("closed_date") or ""
	if target.get("status") == "CLOSED" or (bid_close and bid_close < now_iso): return _flash_redirect("/auctionmanager", "Error: Cannot run a closed auction")
	try:
		# Apply the latest default head-constraint bounds before running.
		try: db_setup.apply_default_bounds(DATA_DIR / "foreverfair.db", auction_id)
		except Exception: pass  # No default bounds yet; continue.
		AuctionController.runCurrentAuction(repository, auction_id=auction_id)
	except Exception as e:
		if str(e) == "The auction cannot run because it has no bids.": return _flash_redirect("/auctionmanager", "The auction cannot run because it has no bids.")
		return _flash_redirect("/auctionmanager", f"Error: {e}")
	return _flash_redirect("/auctionmanager", "Auction run completed")

@app.post("/manager/delete-auction")
def manager_delete_auction(auction_id: str = Form(...)):
	auctions = repository.list_auctions()
	target = next((a for a in auctions if str(a.get("auction_id")) == str(auction_id)), None)
	if target is None: return _flash_redirect("/auctionmanager", "Error: Auction not found")
	if target.get("status") == "CLOSED": return _flash_redirect("/auctionmanager", "Error: Cannot delete a closed auction")
	repository.mark_auction_deleted(auction_id)
	return _flash_redirect("/auctionmanager", f"Auction {auction_id} deleted")

@app.get("/catchment", response_class=HTMLResponse)
def catchment_page(request: Request, auction_id: str | None = None):
	context = AuctionController.getCatchmentPageState(repository, auction_id)
	notice = request.cookies.get("flash")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "CatchmentPage.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/api/system-state")
def system_state_api(auction_id: str | None = None): return AuctionController.getSystemState(repository, auction_id)

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
def setup_db_status(): return JSONResponse(db_setup.db_status(DATA_DIR / "foreverfair.db"), headers={"Cache-Control": "no-store"})

@app.post("/setup/create-db")
def setup_create_db():
	try:
		db_setup.create_empty_db(DATA_DIR / "foreverfair.db")
		return _flash_redirect("/programmer", "Empty database created")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error creating database: {e}")

@app.post("/setup/delete-db")
def setup_delete_db():
	db_path = DATA_DIR / "foreverfair.db"
	try:
		db_setup.delete_db(db_path)
		if db_path.exists(): return _flash_redirect("/programmer", "Error deleting database: database file still exists")
		return _flash_redirect("/programmer", "Database deleted")
	except Exception as e:
		import logging
		logging.getLogger("uvicorn.error").exception("Delete-db failed for %s", db_path)
		return _flash_redirect("/programmer", f"Error deleting database: {e}")

@app.post("/setup/import-decvar")
async def setup_import_decvar(file: UploadFile = File(...),):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_decvar(DATA_DIR / "foreverfair.db", text)
	notice = (f"DECVAR import complete: {result['wells_inserted']} wells inserted"
	          f" (inferred: {result['num_wells']} wells, {result['num_pump_periods']} pump periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-hedcon")
async def setup_import_hedcon(file: UploadFile = File(...),):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		result = db_setup.import_hedcon(DATA_DIR / "foreverfair.db", text)
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing HEDCON: {e}")
	notice = (f"HEDCON import complete: {result['control_points_inserted']} control points,"
	          f" {result['control_point_bounds_inserted']} bounds inserted"
	          f" (inferred: {result['num_control_points']} control points, {result['num_control_periods']} control periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-mps")
async def setup_import_mps(file: UploadFile = File(...), period_unit: str = Form(...),):
	try:
		unit_hours = {"hour": 1, "day": 24, "week": 168}.get(str(period_unit).strip().lower())
		if unit_hours is None: return _flash_redirect("/programmer", "Error importing MPS: invalid period unit")
		text = (await file.read()).decode("utf-8", errors="replace")
		result = db_setup.import_mps(DATA_DIR / "foreverfair.db", text, period_length_hours=int(unit_hours))
	except Exception as e:
		return _flash_redirect("/programmer", f"Error importing MPS: {e}")
	notice = (f"MPS import complete: {result['response_matrix_inserted']} response factors,"
		      f" {result['control_point_bounds_inserted']} bounds,"
		      f" {result['license_rows_inserted']} trader-license rows"
		      f" ({result['wells_ensured']} wells ensured)"
		      f" (using: {result['num_wells']} wells, {result['num_pump_periods']} pump periods,"
		      f" {result['num_control_points']} control points, {result['num_control_periods']} control periods)")
	notice += f"; period length set to {result['period_length_hours']} hours"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.get("/setup/current-period-unit")
def setup_current_period_unit():
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists(): return {"period_unit": None, "period_length_hours": None}
	hours = repository.latest_period_length_hours()
	if hours is None: return {"period_unit": None, "period_length_hours": None}
	if hours == 1: unit = "hour"
	elif hours == 168: unit = "week"
	else: unit = "day"
	return {"period_unit": unit, "period_length_hours": hours}

@app.post("/setup/set-period-unit")
async def setup_set_period_unit(request: Request):
	body = await request.json()
	unit = str(body.get("period_unit", "")).strip().lower()
	unit_hours = {"hour": 1, "day": 24, "week": 168}.get(unit)
	if unit_hours is None: return {"ok": False, "error": "invalid period unit"}
	db_path = DATA_DIR / "foreverfair.db"
	if not db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(db_path)
	try:
		row = conn.execute("SELECT rmi_id FROM response_matrix_info ORDER BY rmi_id DESC LIMIT 1").fetchone()
		if row is None:
			return {"ok": False, "error": "no response_matrix_info row; import MPS first"}
		conn.execute("UPDATE response_matrix_info SET period_length_hours=? WHERE rmi_id=?", (float(unit_hours), row[0]))
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "period_length_hours": unit_hours}

@app.post("/setup/import-trader-names")
async def setup_import_trader_names(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_trader_names(DATA_DIR / "foreverfair.db", text)
	notice = (f"Trader names import: {result['traders_inserted']} inserted,"
	          f" {result['traders_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-trader-wells")
async def setup_import_trader_wells(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_trader_wells(DATA_DIR / "foreverfair.db", text)
	notice = f"Trader-well assignments: {result['wells_assigned']} wells assigned"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-well-lat-lon")
async def setup_import_well_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_well_lat_lon(DATA_DIR / "foreverfair.db", text)
	notice = (f"Well lat-lon import: {result['wells_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

# Phase 5 JSON import routes (eliminating seed.json dependency)
@app.post("/setup/import-periods-json")
async def setup_import_periods_json(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = phase5_imports.import_periods_json(DATA_DIR / "foreverfair.db", text)
	notice = f"Periods import: {result['periods_inserted']} inserted"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-traders-and-allocations-json")
async def setup_import_traders_and_allocations_json(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = phase5_imports.import_traders_and_allocations_json(DATA_DIR / "foreverfair.db", text)
	notice = (f"Traders & allocations import: {result['traders_inserted']} traders,"
	          f" {result['allocations_inserted']} allocations")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-control-points-and-bounds-json")
async def setup_import_control_points_and_bounds_json(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = phase5_imports.import_control_points_and_bounds_json(DATA_DIR / "foreverfair.db", text)
	notice = (f"Control points & bounds import: {result['control_points_inserted']} CPs,"
	          f" {result['bounds_inserted']} bounds")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-auction-metadata-json")
async def setup_import_auction_metadata_json(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = phase5_imports.import_auction_metadata_json(DATA_DIR / "foreverfair.db", text)
	notice = f"Metadata import: {result['metadata_inserted']} entries"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-control-point-lat-lon")
async def setup_import_control_point_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_control_point_lat_lon(DATA_DIR / "foreverfair.db", text)
	notice = (f"Control-point lat-lon import: {result['control_points_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)
