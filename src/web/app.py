# web/app.py. Claude guided by JFR, 2026 04 21.
# Purpose: Define FastAPI routes and wire web dependencies.

from __future__ import annotations
import sys
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "Tianqiao"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

repository = AuctionRepository(seed_path=DATA_DIR / "forever_fair_seed.json", db_path=DATA_DIR / "foreverfair.db",)

app = FastAPI(title="Forever Fair 3.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
	participants = repository.list_participants()
	return templates.TemplateResponse(request, "LoginPage.html", {"participants": participants})

@app.post("/login")
def do_login(participant_id: str = Form(...)):
	response = RedirectResponse(url="/trader", status_code=303)
	response.set_cookie("participant_id", participant_id, max_age=86400, httponly=True)
	return response

@app.get("/researcher", response_class=HTMLResponse)
def researcher_page(request: Request):
	participants = repository.list_participants()
	return templates.TemplateResponse(request, "Researcher.html", {"participants": participants})

@app.get("/docs/hydrologist", response_class=HTMLResponse)
def doc_hydrologist(request: Request):
	import sqlite3 as _sqlite3
	try:
		conn = _sqlite3.connect(DATA_DIR / "foreverfair.db")
		open_auctions = [
			{"id": row[0], "label": row[1]}
			for row in conn.execute(
				"SELECT auction_id, label FROM auctions WHERE status='OPEN' ORDER BY created_at DESC"
			).fetchall()
		]
		conn.close()
	except Exception:
		open_auctions = []
	return templates.TemplateResponse(request, "Hydrologist.html", {
		"open_auctions": open_auctions,
		"notice": request.query_params.get("notice"),
	})

@app.get("/docs/programmer", response_class=HTMLResponse)
def doc_programmer(request: Request):
	report = db_setup.missing_import_data_report(DATA_DIR / "foreverfair.db")
	return templates.TemplateResponse(request, "Programmer.html", {
		"notice": request.query_params.get("notice"),
		"missing_report": report,
	})

@app.get("/docs/trader", response_class=HTMLResponse)
def doc_trader(request: Request): return templates.TemplateResponse(request, "Trader.html", {})

@app.get("/docs/auctionmanager", response_class=HTMLResponse)
def doc_auctionmanager(request: Request): return templates.TemplateResponse(request, "AuctionManager.html", {})

@app.get("/", response_class=HTMLResponse)
def home(): return RedirectResponse(url="/researcher", status_code=303)

@app.get("/trader", response_class=HTMLResponse)
def trader_page(request: Request, auction_id: str | None = None):
	participant_id = request.cookies.get("participant_id")
	if not participant_id:
		return RedirectResponse(url="/login", status_code=303)
	context = AuctionController.getTraderPageState(repository, auction_id, participant_id=participant_id)
	context["notice"] = request.query_params.get("notice")
	return templates.TemplateResponse(request, "TraderPage.html", context)

@app.post("/bids/new")
def create_bid( request: Request, auction_id: str = Form(...), well_id: str = Form(...), period_id: str = Form(...), quantity: float = Form(...), price: float = Form(...), ):
	participant_id = request.cookies.get("participant_id")
	if not participant_id:
		return RedirectResponse(url="/login", status_code=303)
	BiddingController.submitBid(repository, auction_id=auction_id, participant_id=participant_id, well_id=well_id, period_id=period_id, quantity=quantity, price=price,)
	return RedirectResponse(url=f"/trader?auction_id={auction_id}&notice=Bid+saved", status_code=303)

@app.post("/bids/{bid_id}/delete")
def delete_bid(request: Request, bid_id: str):
	participant_id = request.cookies.get("participant_id") or ""
	deleted = BiddingController.deleteBid(repository, bid_id, participant_id)
	return RedirectResponse(url="/trader?notice=Bid+deleted" if deleted else "/trader?notice=Bid+not+found", status_code=303)

@app.get("/manager", response_class=HTMLResponse)
def manager_page(request: Request):
	context = AuctionController.getManagerPageState(repository)
	context["notice"] = request.query_params.get("notice")
	return templates.TemplateResponse(request, "ManagerPage.html", context)

@app.post("/manager/setup-auction")
def manager_setup_auction( auction_id: str = Form(...), label: str = Form(...), bid_close_label: str = Form(...), period_labels: str = Form(...), clear_existing_bids: bool = Form(default=True), ):
	AuctionController.SetUpAuction(repository, auction_id=auction_id.strip(), label=label.strip(), bid_close_label=bid_close_label.strip(), period_labels=[item.strip() for item in period_labels.split(",")], clear_existing_bids=clear_existing_bids,)
	return RedirectResponse(url="/manager?notice=Auction+created", status_code=303)

@app.post("/manager/run-auction")
def manager_run_auction(auction_id: str = Form(...)):
	AuctionController.runCurrentAuction(repository, auction_id=auction_id)
	return RedirectResponse(url="/manager?notice=Auction+run+completed", status_code=303)

@app.post("/manager/reset-data")
def manager_reset_data():
	AuctionController.ResetAuctionData(repository)
	return RedirectResponse(url="/manager?notice=Database+reset+from+seed", status_code=303)

@app.get("/catchment", response_class=HTMLResponse)
def catchment_page(request: Request, auction_id: str | None = None):
	context = AuctionController.getCatchmentPageState(repository, auction_id)
	context["notice"] = request.query_params.get("notice")
	return templates.TemplateResponse(request, "CatchmentPage.html", context)

@app.get("/api/system-state")
def system_state_api(auction_id: str | None = None): return AuctionController.getSystemState(repository, auction_id)

@app.get("/api/open-auctions")
def api_open_auctions():
	import sqlite3 as _sqlite3
	try:
		conn = _sqlite3.connect(DATA_DIR / "foreverfair.db")
		rows = conn.execute(
			"SELECT auction_id, label FROM auctions WHERE status='OPEN' ORDER BY created_at DESC"
		).fetchall()
		conn.close()
		return JSONResponse([{"id": r[0], "label": r[1]} for r in rows])
	except Exception as e:
		return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/hydrologist/apply-default-bounds")
def hydrologist_apply_default_bounds(auction_id: str = Form(...)):
	try:
		result = db_setup.apply_default_bounds(DATA_DIR / "foreverfair.db", auction_id)
		notice = f"Applied+{result['bounds_applied']}+default+bounds+to+auction+{auction_id}"
	except Exception as e:
		notice = "Error:+" + str(e).replace(" ", "+")
	return RedirectResponse(url=f"/docs/hydrologist?notice={notice}", status_code=303)

@app.get("/setup/db-status")
def setup_db_status():
	return JSONResponse(db_setup.db_status(DATA_DIR / "foreverfair.db"), headers={"Cache-Control": "no-store"})

@app.post("/setup/create-db")
def setup_create_db():
	try:
		db_setup.create_empty_db(DATA_DIR / "foreverfair.db")
		return RedirectResponse(url="/docs/programmer?notice=Empty+database+created", status_code=303)
	except Exception as e:
		error_msg = str(e).replace(" ", "+")
		return RedirectResponse(url=f"/docs/programmer?notice=Error+creating+database:+{error_msg}", status_code=303)

@app.post("/setup/delete-db")
def setup_delete_db():
	try:
		db_setup.delete_db(DATA_DIR / "foreverfair.db")
		return RedirectResponse(url="/docs/programmer?notice=Database+deleted", status_code=303)
	except Exception as e:
		error_msg = str(e).replace(" ", "+")
		return RedirectResponse(url=f"/docs/programmer?notice=Error+deleting+database:+{error_msg}", status_code=303)

@app.post("/setup/import-decvar")
async def setup_import_decvar(
	file: UploadFile = File(...),
):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_decvar(DATA_DIR / "foreverfair.db", text)
	notice = (f"DECVAR+import+complete:+{result['wells_inserted']}+wells+inserted"
	          f"+(inferred:+{result['num_wells']}+wells,+{result['num_pump_periods']}+pump+periods)")
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)

@app.post("/setup/import-hedcon")
async def setup_import_hedcon(
	file: UploadFile = File(...),
):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		result = db_setup.import_hedcon(DATA_DIR / "foreverfair.db", text)
	except Exception as e:
		error_msg = str(e).replace(" ", "+")
		return RedirectResponse(url=f"/docs/programmer?notice=Error+importing+HEDCON:+{error_msg}", status_code=303)
	notice = (f"HEDCON+import+complete:+{result['control_points_inserted']}+control+points,"
	          f"+{result['control_point_bounds_inserted']}+bounds+inserted"
	          f"+(inferred:+{result['num_control_points']}+control+points,+{result['num_control_periods']}+control+periods)")
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)

@app.post("/setup/import-mps")
async def setup_import_mps(
	file: UploadFile = File(...),
):
	try:
		text = (await file.read()).decode("utf-8", errors="replace")
		result = db_setup.import_mps(DATA_DIR / "foreverfair.db", text)
	except Exception as e:
		error_msg = str(e).replace(" ", "+")
		return RedirectResponse(url=f"/docs/programmer?notice=Error+importing+MPS:+{error_msg}", status_code=303)
	notice = (f"MPS+import+complete:+{result['response_factors_inserted']}+response+factors,"
		      f"+{result['control_point_bounds_inserted']}+bounds,"
		      f"+{result['well_rights_inserted']}+well+rights"
		      f"+(using:+{result['num_wells']}+wells,+{result['num_pump_periods']}+pump+periods,"
		      f"+{result['num_control_points']}+control+points,+{result['num_control_periods']}+control+periods)")
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)

@app.post("/setup/import-trader-names")
async def setup_import_trader_names(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_trader_names(DATA_DIR / "foreverfair.db", text)
	notice = (f"Trader+names+import:+{result['traders_inserted']}+inserted,"
	          f"+{result['traders_skipped']}+skipped")
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)

@app.post("/setup/import-trader-wells")
async def setup_import_trader_wells(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_trader_wells(DATA_DIR / "foreverfair.db", text)
	notice = f"Trader-well+assignments:+{result['wells_assigned']}+wells+assigned"
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)


@app.post("/setup/import-well-lat-lon")
async def setup_import_well_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_well_lat_lon(DATA_DIR / "foreverfair.db", text)
	notice = (
		f"Well+lat-lon+import:+{result['wells_updated']}+updated,"
		f"+{result['rows_skipped']}+skipped"
	)
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)


@app.post("/setup/import-control-point-lat-lon")
async def setup_import_control_point_lat_lon(file: UploadFile = File(...)):
	text = (await file.read()).decode("utf-8", errors="replace")
	result = db_setup.import_control_point_lat_lon(DATA_DIR / "foreverfair.db", text)
	notice = (
		f"Control-point+lat-lon+import:+{result['control_points_updated']}+updated,"
		f"+{result['rows_skipped']}+skipped"
	)
	if result["errors"]: notice += f"+({len(result['errors'])}+errors)"
	return RedirectResponse(url=f"/docs/programmer?notice={notice}", status_code=303)
