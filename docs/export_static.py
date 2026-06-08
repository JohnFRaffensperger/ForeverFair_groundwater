from __future__ import annotations
import os
import re
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "Mockup_html_git_ignore"
STATIC_SRC = SRC / "web" / "static"
OUT.mkdir(parents=True, exist_ok=True)

os.environ["FOREVER_FAIR_CATCHMENT"] = "Tianqiao"
if str(SRC) not in sys.path:
	sys.path.insert(0, str(SRC))
if str(SRC / "web") not in sys.path:
	sys.path.insert(0, str(SRC / "web"))

from fastapi.testclient import TestClient
import ForeverFairPages

app = ForeverFairPages.app
ffdata = ForeverFairPages.ffdata
client = TestClient(app)

client.get("/auctionmanager")
traders = ffdata.list_of_traders()
env_trader = next((t for t in traders if str(t.get("trader_type") or "well") == "environmental"), None)
well_trader = next((t for t in traders if str(t.get("name") or "").strip().lower() == "baicai"), None)
if well_trader is None:
	well_trader = next((t for t in traders if str(t.get("trader_type") or "well") != "environmental"), None)
if well_trader is None and traders:
	well_trader = traders[0]
if env_trader is None and traders:
	env_trader = traders[0]

well_trader_id = int(well_trader["id"]) if well_trader else 0
env_trader_id = int(env_trader["id"]) if env_trader else well_trader_id

def _remove_scripts(html: str) -> str:
	return re.sub(r"<script\b.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)

def _inline_catchment_map(html: str) -> str:
	pattern = re.compile(r'<img([^>]*?)src="((?:https?://[^/]+)?/catchment-map-svg[^\"]*)"([^>]*)>', flags=re.IGNORECASE)
	def repl(match: re.Match[str]) -> str:
		map_url = match.group(2)
		svg_resp = client.get(map_url)
		if svg_resp.status_code != 200:
			return match.group(0)
		return f'<div class="static-catchment-map">{svg_resp.text}</div>'
	return pattern.sub(repl, html)

def _rewrite_for_local_static(html: str) -> str:
	route_to_file = {
		"/": "Researcher.html",
		"/researcher": "Researcher.html",
		"/hydrologist": "Hydrologist.html",
		"/programmer": "Programmer.html",
		"/auctionmanager": "AuctionManager.html",
		"/login": "Trader.html",
		"/trader": "Trader.html",
		"/environmental-buyer": "EnvironmentalBuyer.html",
		"/database-documentation": "Database_documentation.html",
		"/catchment": "CatchmentView.html",
		"/api/system-state": "Programmer.html",
		"/docs": "Researcher.html",
		"/redoc": "Researcher.html",
	}

	def route_replacer(match: re.Match[str]) -> str:
		attr = match.group(1)
		value = match.group(2)
		normalized = re.sub(r"^https?://[^/]+", "", value)
		normalized = normalized.split("?", 1)[0].split("#", 1)[0]
		target = route_to_file.get(normalized)
		if target is None and normalized.startswith("/static/"):
			target = normalized.rsplit("/", 1)[-1]
		if target is None:
			return f'{attr}="{value}"'
		return f'{attr}="{target}"'

	html = re.sub(r'(href|action)="((?:https?://[^/]+)?/[^"]*)"', route_replacer, html)

	html = re.sub(r'https?://[^/]+/static/foreverfair\.css', 'foreverfair.css', html)
	html = re.sub(r'https?://[^/]+/static/foreverfair\.png', 'foreverfair.png', html)
	html = re.sub(r'https?://[^/]+/static/sorttable\.js', 'sorttable.js', html)
	html = html.replace('/static/foreverfair.css', 'foreverfair.css')
	html = html.replace('/static/foreverfair.png', 'foreverfair.png')
	html = html.replace('/static/sorttable.js', 'sorttable.js')
	html = html.replace('http://testserverforeverfair.css', 'foreverfair.css')
	html = html.replace('http://testserverforeverfair.png', 'foreverfair.png')
	html = html.replace('http://testserversorttable.js', 'sorttable.js')

	def placeholder_link_replacer(match: re.Match[str]) -> str:
		inner_html = match.group(2)
		text_only = re.sub(r"<[^>]+>", "", inner_html).strip().lower()

		def choose_target(label_text: str) -> str:
			if "hydrologist" in label_text:
				return "Hydrologist.html"
			if "programmer" in label_text or "api" in label_text or "security" in label_text:
				return "Programmer.html"
			if "auction" in label_text:
				return "AuctionManager.html"
			if "database" in label_text:
				return "Database_documentation.html"
			if "catchment" in label_text:
				return "CatchmentView.html"
			if "environmental" in label_text:
				return "EnvironmentalBuyer.html"
			if "trader" in label_text or "login" in label_text:
				return "Trader.html"
			if "researcher" in label_text:
				return "Researcher.html"
			return "Researcher.html"

		target = choose_target(text_only)
		return f'{match.group(1)}href="{target}"{inner_html}</a>'

	def external_link_replacer(match: re.Match[str]) -> str:
		text_only = re.sub(r"<[^>]+>", "", match.group(3)).strip().lower()
		target = "Researcher.html"
		if "gwm" in text_only or "modflow" in text_only:
			target = "Hydrologist.html"
		elif "manual" in text_only or "bibliography" in text_only or "matching users" in text_only:
			target = "Researcher.html"
		elif "lp solve" in text_only:
			target = "Programmer.html"
		return f'{match.group(1)}href="{target}"{match.group(2)}{match.group(3)}</a>'

	html = re.sub(r'(<a[^>]*?)href="#"([^>]*>)(.*?)(</a>)', placeholder_link_replacer, html, flags=re.IGNORECASE | re.DOTALL)
	html = re.sub(r'(<a[^>]*?)href="https?://[^"]+"([^>]*>)(.*?)(</a>)', external_link_replacer, html, flags=re.IGNORECASE | re.DOTALL)
	return html

def _postprocess(html: str) -> str:
	html = _inline_catchment_map(html)
	html = _remove_scripts(html)
	html = _rewrite_for_local_static(html)
	return html

routes = [
	("LoginPage.html", "/login", {}),
	("Researcher.html", "/researcher", {}),
	("Database_documentation.html", "/database-documentation", {}),
	("Hydrologist.html", "/hydrologist", {}),
	("Programmer.html", "/programmer", {}),
	("AuctionManager.html", "/auctionmanager", {}),
	("Trader.html", "/trader", {"trader_id": str(well_trader_id)}),
	("EnvironmentalBuyer.html", "/environmental-buyer", {"trader_id": str(env_trader_id)}),
	("CatchmentView.html", "/catchment", {}),
]

for filename, route, cookies in routes:
	response = client.get(route, cookies=cookies, follow_redirects=True)
	if response.status_code != 200:
		raise RuntimeError(f"Failed to render {route}: HTTP {response.status_code}")
	html = _postprocess(response.text)
	(OUT / filename).write_text(html, encoding="utf-8")
	print(f"Wrote {filename} from {route}")

for static_name in ("foreverfair.css", "foreverfair.png"):
	shutil.copy2(STATIC_SRC / static_name, OUT / static_name)
print(f"Copied static assets into {OUT}")
