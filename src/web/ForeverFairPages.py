# web/ForeverFairPages.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Define FastAPI routes and wire web dependencies.

from __future__ import annotations
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
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

load_dotenv()
_configured = os.environ.get(ACTIVE_CATCHMENT_ENV_VAR, "").strip()
if not _configured:
	raise ValueError(f"{ACTIVE_CATCHMENT_ENV_VAR} is not set. Create a .env file in the project root with {ACTIVE_CATCHMENT_ENV_VAR}=<catchment name>.")
_available = _available_catchment_dirs()
if not any(path.name == _configured for path in _available):
	raise ValueError(f"Catchment {_configured!r} not found in {CATCHMENT_ROOT}. Available: {[p.name for p in _available]}")
_active_catchment_name = _configured
del _configured, _available
ffdata = ForeverFairData(db_path=CATCHMENT_ROOT / _active_catchment_name / "foreverfair.db", debug_db_path=DEBUG_DB_PATH)

def set_active_catchment(catchment_name: str) -> None:
	global ffdata, _active_catchment_name
	selected_name = catchment_name.strip()
	available_names = {path.name for path in _available_catchment_dirs()}
	if selected_name not in available_names: raise ValueError(f"Unknown catchment: {selected_name}")
	_active_catchment_name = selected_name
	ffdata = ForeverFairData(db_path=CATCHMENT_ROOT / selected_name / "foreverfair.db", debug_db_path=DEBUG_DB_PATH)

app = FastAPI(title="Forever Fair 2026")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

_auctionmanager_debug_log: list[str] = []
_auctionmanager_run_active = False

def add_auctionmanager_debug(message: str) -> None: _auctionmanager_debug_log.append(str(message))

def clear_auctionmanager_debug() -> None: _auctionmanager_debug_log.clear()

def get_auctionmanager_debug_text() -> str: return "\n".join(_auctionmanager_debug_log[-400:])

def set_auctionmanager_run_active(is_active: bool) -> None:
	global _auctionmanager_run_active
	_auctionmanager_run_active = is_active

def get_auctionmanager_run_active() -> bool: return _auctionmanager_run_active

def _flash_redirect(url: str, msg: str, status_code: int = 303) -> RedirectResponse:
	r = RedirectResponse(url=url, status_code=status_code)
	r.set_cookie("flash", msg, max_age=60, httponly=True, samesite="lax")
	return r

def _common_template_context() -> dict[str, Any]: return {"active_catchment_name": _active_catchment_name}

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
	traders = ffdata.list_of_traders()
	now_iso = ffdata.the_time_at_the_tone_is().isoformat(timespec="minutes")
	upcoming = ffdata.list_auctions()
	next_final = next((a for a in reversed(upcoming) if a["status"] == "OPEN" and a["auction_type"] != "tentative" and (a["closed_date"] or "") > now_iso), None)
	next_tentative = next((a for a in reversed(upcoming) if a["status"] == "OPEN" and a["auction_type"] == "tentative" and (a["closed_date"] or "") > now_iso), None)
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
	traders = ffdata.list_of_traders()
	context = _common_template_context()
	context.update({"traders": traders})
	return templates.TemplateResponse(request, "Researcher.html", context)

@app.get("/database-documentation", response_class=HTMLResponse)
def database_documentation_page(request: Request):
	return templates.TemplateResponse(request, "Database_documentation.html", _common_template_context())

@app.get("/hydrologist", response_class=HTMLResponse)
def doc_hydrologist(request: Request):
	notice = request.cookies.get("flash", "")
	context = _common_template_context()
	context.update({"bounds_imported_at": ffdata.bounds_imported_at(), "notice": notice, })
	resp = templates.TemplateResponse(request, "Hydrologist.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/programmer", response_class=HTMLResponse)
def doc_programmer(request: Request):
	report = SetupForeverFairDB.missing_import_data_report(ffdata.db_path)
	notice = request.cookies.get("flash", "")
	context = _common_template_context()
	period_length_hours = ffdata.latest_period_length_hours()
	bidding_periods = ffdata.get_number_of_bidding_periods()
	now_dt = ffdata.the_time_at_the_tone_is()
	context["today_display"] = now_dt.strftime("%d %b %Y")
	context["today_weekday"] = now_dt.strftime("%A")
	context["period_length_hours"] = period_length_hours
	context["bidding_periods"] = bidding_periods
	if period_length_hours is not None:
		close_dt, _, _ = ffdata.get_auction_close_first_last_dates(now_dt, period_length_hours, bidding_periods)
		period_td = timedelta(hours=period_length_hours)
		context["first_three_closes"] = [( close_dt + i * period_td).strftime("%d %b %Y %H:%M") for i in range(3)]
	else: context["first_three_closes"] = None
	context.update({"notice": notice, "missing_report": report, "available_catchments": [path.name for path in _available_catchment_dirs()], "max_bid_steps": ffdata.get_max_bid_steps(), })
	resp = templates.TemplateResponse(request, "Programmer.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/auctionmanager", response_class=HTMLResponse)
def doc_auctionmanager(request: Request):
	bidding_periods = ffdata.get_number_of_bidding_periods()
	next_auction = ffdata.get_next_auction_info()
	if next_auction is None:
		auctions = ffdata.list_auctions()
		next_auction_id = max((int(auction["auction_id"]) for auction in auctions), default=0) + 1
		if next_auction_id <= bidding_periods:
			AuctionController.create_auction(ffdata.db_path)
			next_auction = ffdata.get_next_auction_info()
	auctions = ffdata.list_auctions()
	remaining_auctions = ffdata.get_remaining_auctions_for_auction(int(next_auction["auction_id"])) if next_auction is not None else 0
	next_real_bid_count, next_default_bid_count = ffdata.get_bid_count(next_auction["auction_id"]) if next_auction is not None else (0, 0)
	period_length_hours = ffdata.latest_period_length_hours()
	now_dt = ffdata.the_time_at_the_tone_is()
	close_dt, default_first, default_last = ffdata.get_auction_close_first_last_dates(now_dt, period_length_hours or 168, bidding_periods)

	response_period_count = ffdata.response_matrix_period_count()
	context: dict[str, Any] = {"auction": next_auction, "auctions": auctions, "period_length_hours": period_length_hours, "response_period_count": response_period_count, "bidding_periods": bidding_periods, "remaining_auctions": remaining_auctions, "next_auction_id": next_auction["auction_id"] if next_auction is not None else "none", "next_real_bid_count": next_real_bid_count, "next_default_bid_count": next_default_bid_count,}
	notice = request.cookies.get("flash", "")
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
	context.update(_common_template_context())
	context["debug_text"] = get_auctionmanager_debug_text()
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


def _build_trader_context(request: Request) -> dict[str, Any] | RedirectResponse:
	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	next_auction = ffdata.get_next_auction_info()
	if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
	auction_id = next_auction["auction_id"]
	auction_info = ffdata.get_auction_info(auction_id)
	current_wells = ffdata.get_trader_wells(trader_id)
	current_well = current_wells[0] if current_wells else None
	bid_history = ffdata.get_bid_history(auction_id, trader_id)
	current_well_id = current_well["id"] if current_well else None
	quota_by_period = ffdata.get_well_start_quota(well_id=current_well_id, auction_id=auction_id) if isinstance(current_well_id, int) else {}
	clearing_price_by_period = ffdata.get_well_clearing_price_for_current_rows(well_id=current_well_id, auction_id=auction_id) if isinstance(current_well_id, int) else {}
	max_bid_steps = ffdata.get_max_bid_steps()
	latest_bids_by_period: dict[int, list[dict[str, Any]]] = {}
	for bid in bid_history:
		period_id = bid["period_id"]
		if period_id not in latest_bids_by_period: latest_bids_by_period[period_id] = []
		if latest_bids_by_period[period_id] and latest_bids_by_period[period_id][0]["bid_id"] != bid["bid_id"]: continue
		latest_bids_by_period[period_id].append(bid)
	period_rows: list[dict[str, Any]] = []
	for period in auction_info["periods"]: period_rows.append({"period_id": period["id"], "period_key": period["id"], "period_label": period["label"], "allocation": quota_by_period.get(period["id"], 0.0), "clearing_price": clearing_price_by_period.get(period["id"]), "latest_bids": latest_bids_by_period.get(period["id"], []),})
	period_label_by_id = {int(period["id"]): str(period["label"]).split("T")[0] for period in auction_info["periods"]}
	history_rows: list[dict[str, Any]] = []
	history_row_by_bid_id: dict[int, dict[str, Any]] = {}
	for bid in bid_history:
		bid_id = int(bid["bid_id"])
		if bid_id not in history_row_by_bid_id:
			period_id = int(bid["period_id"])
			history_row_by_bid_id[bid_id] = {
				"submitted_at": bid["submitted_at"],
				"period_label": period_label_by_id.get(period_id, str(period_id)),
				"bid_id": bid_id,
				"allocation": quota_by_period.get(period_id, 0.0),
				"clearing_price": clearing_price_by_period.get(period_id),
				"steps": [],
				"final_allocation": bid["final_allocation"],
				"traded_price": bid["traded_price"],
			}
			history_rows.append(history_row_by_bid_id[bid_id])
		history_row_by_bid_id[bid_id]["steps"].append({"quantity": bid["quantity"], "price": bid["price"]})
	manual_submitted_at = [str(bid["submitted_at"]) for bid in bid_history if not bool(bid.get("is_default", False)) and bid.get("submitted_at")]
	bid_entry_status_message = f"Last submitted on {max(manual_submitted_at)}." if manual_submitted_at else "Not yet submitted. An automatic bid is in place."
	
	matching_traders = [t for t in ffdata.list_of_traders() if t["id"] == trader_id]
	current_trader: dict[str, Any] = matching_traders[0] if matching_traders else {"id": trader_id, "name": ""}
	context: dict[str, Any] = {"current_trader": current_trader, "current_well": current_well, "bid_history": bid_history, "bid_history_rows": history_rows, "period_rows": period_rows, "auction_id": auction_id, "auction_case": {"auction": auction_info}, "max_bid_steps": max_bid_steps, "optional_bid_step_numbers": list(range(2, max_bid_steps + 1)), "bid_entry_status_message": bid_entry_status_message,}
	notice = request.cookies.get("flash", "")
	context["notice"] = notice
	context.update(_common_template_context())
	return context

@app.get("/trader", response_class=HTMLResponse)
def trader_page(request: Request):
	context = _build_trader_context(request)
	if isinstance(context, RedirectResponse): return context
	resp = templates.TemplateResponse(request, "Trader.html", context)
	if context["notice"]: resp.delete_cookie("flash")
	return resp

@app.post("/bids/new")
async def create_bid(request: Request):
	form = await request.form()
	return_to = str(form.get("return_to", "/trader")).strip()
	if return_to != "/trader": return_to = "/trader"
	period_text = ""
	try:
		auction_id = int(str(form["auction_id"]).strip())
		well_id = int(str(form["well_id"]).strip())
		period_id = int(str(form["period_id"]).strip())
		quantity = float(str(form["quantity"]).strip())
		price = float(str(form["price"]).strip())
		auction_info = ffdata.get_auction_info(auction_id)
		period_lookup = {int(p["id"]): str(p["label"]).split("T")[0] for p in auction_info["periods"]}
		period_text = period_lookup.get(period_id, str(period_id))
	except Exception:
		q_text = str(form.get("quantity", "")).strip()
		p_text = str(form.get("price", "")).strip()
		pid_text = str(form.get("period_id", "")).strip()
		return _flash_redirect(return_to, f"Error: invalid bid form values for pumping period {pid_text} (quantity='{q_text}', price='{p_text}')")
	is_default = str(form.get("is_default", "")).strip().lower() in {"true", "on", "1", "yes"}

	trader_cookie = request.cookies.get("trader_id", "")
	if not trader_cookie: return RedirectResponse(url="/login", status_code=303)
	try: trader_id = int(trader_cookie)
	except ValueError: return RedirectResponse(url="/login", status_code=303)

	bid_steps: list[tuple[float, float]] = [(quantity, price)]
	for step_num in range(2, ffdata.get_max_bid_steps() + 1):
		quantity_text = str(form[f"quantity{step_num}"] or "").strip()
		price_text = str(form[f"price{step_num}"] or "").strip()
		if not quantity_text and not price_text: continue
		if not quantity_text or not price_text:
			return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: pumping period {period_text}, step {step_num} requires both quantity and price (quantity='{quantity_text}', price='{price_text}')")
		try: bid_steps.append((float(quantity_text), float(price_text)))
		except ValueError:
			return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: pumping period {period_text}, step {step_num} has invalid quantity or price (quantity='{quantity_text}', price='{price_text}')")
	try: BiddingController.submitBid(ffdata, auction_id=auction_id, this_trader_id=trader_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price, is_bid_default=is_default, bid_steps=bid_steps,)
	except ValueError as e: return _flash_redirect(f"{return_to}?auction_id={auction_id}", f"Error: {e}")
	return _flash_redirect(f"{return_to}?auction_id={auction_id}", "Bid saved")

@app.post("/bids/{bid_id}/delete")
async def delete_bid(request: Request, bid_id: int):
	form = await request.form()
	return_to = str(form.get("return_to", "/trader")).strip()
	if return_to != "/trader": return_to = "/trader"
	trader_cookie = request.cookies.get("trader_id", "")
	trader_id = int(trader_cookie) if trader_cookie and trader_cookie.isdigit() else 0
	deleted = BiddingController.deleteBid(ffdata, bid_id, trader_id)
	return _flash_redirect(return_to, "Bid deleted" if deleted else "Bid not found")

@app.post("/auctionmanager/run-auction")
async def manager_run_auction(request: Request):
	try:
		form = await request.form()
		auction_id = int(str(form["auction_id"]).strip())
	except (ValueError, KeyError): return JSONResponse({"ok": False, "message": "Error: Missing or invalid auction_id"}, status_code=400)
	if ffdata.get_remaining_auctions_for_auction(auction_id) <= 0:
		if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": False, "message": "Error: No auctions remain in this schedule."}, status_code=400)
		return _flash_redirect("/auctionmanager", "Error: No auctions remain in this schedule.")
	
	# Guard: do not run an auction that has already closed by time.
	target = next((a for a in ffdata.list_auctions() if a["auction_id"] == auction_id), None)
	if target is None:
		if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": False, "message": "Error: Auction not found"}, status_code=404)
		return _flash_redirect("/auctionmanager", "Error: Auction not found")

	bid_close = target["closed_date"] or ""
	if target["status"] == "CLOSED" or (bid_close and bid_close < ffdata.the_time_at_the_tone_is().isoformat(timespec="minutes")):
		if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": False, "message": "Error: Cannot run a closed auction"}, status_code=400)
		return _flash_redirect("/auctionmanager", "Error: Cannot run a closed auction")
	try:
		clear_auctionmanager_debug()
		set_auctionmanager_run_active(True)
		add_auctionmanager_debug(f"Run requested for auction_id={auction_id}")

		# Run the auction,
		revenue = await run_in_threadpool(AuctionController.runCurrentAuction, ffdata, auction_id, add_auctionmanager_debug)
		add_auctionmanager_debug("Auction run completed")
	except Exception as e:
		add_auctionmanager_debug(f"Error: {e}")
		if request.headers["x-requested-with"] == "fetch":
			set_auctionmanager_run_active(False)
			if str(e) == "The auction cannot run because it has no bids.": return JSONResponse({"ok": False, "message": "The auction cannot run because it has no bids."}, status_code=400)
			return JSONResponse({"ok": False, "message": f"Error: {e}"}, status_code=500)
		set_auctionmanager_run_active(False)
		if str(e) == "The auction cannot run because it has no bids.": return _flash_redirect("/auctionmanager", "The auction cannot run because it has no bids.")
		return _flash_redirect("/auctionmanager", f"Error: {e}")
	set_auctionmanager_run_active(False)
	redirect_url = "/auctionmanager"
	if request.headers["x-requested-with"] == "fetch": return JSONResponse({"ok": True, "message": "Auction run completed", "redirect_url": redirect_url, "revenue": float(revenue or 0.0)})
	return RedirectResponse(url=redirect_url, status_code=303)

@app.get("/api/auctionmanager-debug")
def api_auctionmanager_debug() -> JSONResponse: return JSONResponse({"debug_text": get_auctionmanager_debug_text(), "run_active": get_auctionmanager_run_active()}, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})

@app.get("/catchment", response_class=HTMLResponse)
def catchment_page(request: Request, auction_id: int | None = None):
	if auction_id is None:
		next_auction = ffdata.get_next_auction_info()
		if next_auction is None: return _flash_redirect("/auctionmanager", "No open auction. Ask the auction manager to create one.")
		auction_id = next_auction["auction_id"]
	well_price_rows, control_point_rows = ffdata.catchment_price_rows(auction_id)
	context: dict[str, Any] = {"catchment_name": ffdata.get_catchment_name(), "auction": ffdata.get_auction_info(auction_id), "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}
	context.update(_common_template_context())
	notice = request.cookies.get("flash", "")
	context["notice"] = notice
	resp = templates.TemplateResponse(request, "CatchmentPage.html", context)
	if notice: resp.delete_cookie("flash")
	return resp

@app.get("/api/system-state")
def system_state_api(auction_id: int | None = None) -> dict[str, Any]:
	if auction_id is None:
		next_auction = ffdata.get_next_auction_info()
		if next_auction is None: return {"error": "No open auction. Ask the auction manager to create one."}
		auction_id = next_auction["auction_id"]
	latest_run = ffdata.get_run_summary(auction_id)
	well_price_rows, control_point_rows = ffdata.catchment_price_rows(auction_id)
	return {"catchment_name": ffdata.get_catchment_name(), "auction": ffdata.get_auction_info(auction_id), "rights_conversion": ffdata.get_rights_conversion_dict(), "latest_run": latest_run, "well_price_rows": well_price_rows, "control_point_rows": control_point_rows,}

@app.get("/api/open-auctions")
def api_open_auctions():
	try:
		return JSONResponse([{"id": a["auction_id"], "closed_date": a["closed_date"]} for a in ffdata.list_open_auctions()])
	except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/setup/db-status") # TODO: this file should never know the db status.
def setup_db_status(): return JSONResponse(SetupForeverFairDB.db_status(ffdata.db_path), headers={"Cache-Control": "no-store"})

@app.post("/setup/create-db")
def setup_create_db():
	try:
		SetupForeverFairDB.create_empty_db(ffdata.db_path)
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
	try:
		SetupForeverFairDB.delete_db(ffdata.db_path)
		if ffdata.db_path.exists(): return _flash_redirect("/programmer", "Error deleting database: database file still exists")
		SetupForeverFairDB.create_empty_db(ffdata.db_path)
		return _flash_redirect("/programmer", "Database deleted")
	except Exception as e:
		import logging
		logging.getLogger("uvicorn.error").exception("Delete-db failed for %s", db_path)
		return _flash_redirect("/programmer", f"Error deleting database: {e}")

@app.post("/setup/import-decvar")
async def setup_import_decvar(file: UploadFile = File(...),):
	status = SetupForeverFairDB.db_status(ffdata.db_path)
	tables = status["tables"]
	if not status["exists"] or "wells" not in tables:
		return _flash_redirect("/programmer", "Create new dataase first")
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_decvar(ffdata.db_path, text)
	notice = (f"DECVAR import complete: {result['wells_inserted']} wells inserted"
	          f" (inferred: {result['num_wells']} wells, {result['num_pump_periods']} pump periods)")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-hedcon")
async def setup_import_hedcon(file: UploadFile = File(...),):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		result = SetupForeverFairDB.import_hedcon(ffdata.db_path, text)
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
		unit_hours = {"hour": 1, "day": 24, "week": 168}[str(period_unit).strip().lower()]
		text = (await file.read()).decode("utf-8", errors="replace")
		result = SetupForeverFairDB.import_mps(ffdata.db_path, text, period_length_hours=unit_hours)
	except KeyError:
		return _flash_redirect("/programmer", "Error importing MPS: invalid period unit")
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
	if not ffdata.db_path.exists(): return {"period_unit": None, "period_length_hours": None}
	hours = ffdata.latest_period_length_hours()
	if hours is None: return {"period_unit": None, "period_length_hours": None}
	if hours == 1: unit = "hour"
	elif hours == 24: unit = "day"
	elif hours == 168: unit = "week"
	else: unit = "day"
	return {"period_unit": unit, "period_length_hours": hours}

@app.post("/setup/set-period-unit")
async def setup_set_period_unit(request: Request) -> dict[str, Any]:
	body = await request.json()
	unit = str(body["period_unit"]).strip().lower()
	try: unit_hours = {"hour": 1, "day": 24, "week": 168}[unit]
	except KeyError: return {"ok": False, "error": "invalid period unit"}
	if not ffdata.db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "period_length_hours", unit_hours)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "period_length_hours": unit_hours}

@app.get("/setup/current-bidding-periods")
def setup_current_bidding_periods():
	return {"num_bidding_periods": ffdata.get_number_of_bidding_periods()}

@app.get("/setup/current-max-bid-steps")
def setup_current_max_bid_steps() -> dict[str, Any]:
	return {"max_bid_steps": ffdata.get_max_bid_steps()}

@app.get("/setup/current-rights-policy")
def setup_current_rights_policy() -> dict[str, Any]:
	return {"rights_policy": ffdata.get_rights_policy()}

@app.post("/setup/set-rights-policy")
async def setup_set_rights_policy(request: Request) -> dict[str, Any]:
	VALID_POLICIES = {"Users_pay", "Auction_manager_pays", "Quota_scaled"}
	body = await request.json()
	value = body["rights_policy"]
	if value not in VALID_POLICIES:
		return {"ok": False, "error": f"invalid rights policy: {value}"}
	if not ffdata.db_path.exists():
		return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
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
		value = int(body["num_bidding_periods"])
	except Exception:
		return {"ok": False, "error": "invalid number of bidding periods"}
	if value < 1 or value > 52: return {"ok": False, "error": "number of bidding periods must be 1..52"}
	if not ffdata.db_path.exists(): return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
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
		value = int(body["max_bid_steps"])
	except Exception:
		return {"ok": False, "error": "invalid maximum bid steps"}
	if value < 1 or value > 5:
		return {"ok": False, "error": "maximum bid steps must be 1..5"}
	if not ffdata.db_path.exists():
		return {"ok": False, "error": "database does not exist"}
	import sqlite3
	conn = sqlite3.connect(ffdata.db_path)
	try:
		SetupForeverFairDB.save_catchment_info(conn, "MAX_BID_STEPS", value)
		conn.commit()
	finally:
		conn.close()
	return {"ok": True, "max_bid_steps": value}

@app.post("/setup/import-trader-names")
async def setup_import_trader_names(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_trader_names(ffdata.db_path, text)
	notice = (f"Trader names import: {result['traders_inserted']} inserted,"
	          f" {result['traders_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-trader-wells")
async def setup_import_trader_wells(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_trader_wells(ffdata.db_path, text)
	notice = f"Trader-well assignments: {result['wells_assigned']} wells assigned"
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-well-lat-lon")
async def setup_import_well_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_well_lat_lon(ffdata.db_path, text)
	notice = (f"Well lat-lon import: {result['wells_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/import-control-point-lat-lon")
async def setup_import_control_point_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = SetupForeverFairDB.import_control_point_lat_lon(ffdata.db_path, text)
	notice = (f"Control-point lat-lon import: {result['control_points_updated']} updated,"
		f" {result['rows_skipped']} skipped")
	if result["errors"]: notice += f" ({len(result['errors'])} errors)"
	return _flash_redirect("/programmer", notice)

@app.post("/setup/setup-first-auction")
def setup_first_auction():
	try:
		auction_id = AuctionController.set_up_auction_system(ffdata.db_path)
		return _flash_redirect("/programmer", f"Auction system set up: auction_id={auction_id}")
	except Exception as e:
		return _flash_redirect("/programmer", f"Error setting up auction system: {e}")
