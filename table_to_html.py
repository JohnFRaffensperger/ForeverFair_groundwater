from __future__ import annotations

import csv
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, cast

OUTPUT_DIR = Path(__file__).resolve().parent / "Catchment_data" / "Tianqiao"
if str(OUTPUT_DIR) not in sys.path:
	sys.path.insert(0, str(OUTPUT_DIR))
import LocalHTML

DEFAULT_DB_FILE = OUTPUT_DIR / "foreverfair.db"
DEFAULT_HTML_FILE = OUTPUT_DIR / "mytable.html"
DEFAULT_QUERY_CSV = OUTPUT_DIR / "myQueryOutput.csv"
DEFAULT_AGGREGATE_CSV = OUTPUT_DIR / "myAggregateOutput.csv"
DEFAULT_EXPORT_CSV = OUTPUT_DIR / "yourquery.csv"
PRIMARY_TABLE_NAME = "maps_atg_schema"
HTMLTableRenderer = cast(Callable[..., str], getattr(LocalHTML, "table"))

def _render_html_table(rows: list[list[Any]], header_row: list[str] | None = None) -> str:
	if header_row is None:
		return HTMLTableRenderer(rows)
	return HTMLTableRenderer(rows, header_row=header_row)

def _transpose_rows(rows: list[list[Any]], header_row: list[str]) -> list[list[Any]]:
	if not rows:
		return [[field_name] for field_name in header_row]
	source_rows: list[list[Any]] = [list(header_row)] + [list(row) for row in rows]
	column_count = len(source_rows[0])
	return [[source_rows[row_index][column_index] for row_index in range(len(source_rows))] for column_index in range(column_count)]

def getTableNameFromQueryString(query: str) -> str:
	"""Return the first token after FROM, or 'query' if it cannot be inferred."""
	query_lower = query.lower()
	start = query_lower.find(" from ")
	if start < 0:
		return "query"
	tail = query[start + 6:].strip()
	if not tail:
		return "query"
	return tail.split()[0].strip() or "query"

def makeHTML(rows: list[list[Any]], fieldIndices: dict[str, int] | None = None) -> None:
	outputfilename = DEFAULT_HTML_FILE
	if not rows:
		print("No rows")
		return
	if fieldIndices:
		fieldnames = [""] * len(rows[0])
		for field_name, column_index in fieldIndices.items():
			fieldnames[column_index] = field_name
		html = _render_html_table(rows, header_row=fieldnames)
	else:
		html = _render_html_table(rows)
	with open(outputfilename, "w", encoding="utf-8") as handle:
		handle.write("<html>" + html + "</html>")
	print(f"Wrote {len(rows)} rows to {outputfilename}.")

def saveQueryToCSV(query: str, optionalFileName: str | Path = DEFAULT_QUERY_CSV) -> None:
	with sqlite3.connect(str(DEFAULT_DB_FILE)) as conn:
		cursor = conn.cursor()
		cursor.execute(query)
		with open(optionalFileName, "w", newline="", encoding="utf-8") as csv_file:
			csv_writer = csv.writer(csv_file, quoting=csv.QUOTE_NONNUMERIC)
			csv_writer.writerow([description[0] for description in cursor.description])
			csv_writer.writerows(cursor)

def show(tablename: str) -> tuple[dict[str, int], list[list[Any]]] | None:
	return tableToHtml(f"select * from {tablename} limit 500")

def tableToHtml(query: str, optionalFileName: str | Path = DEFAULT_HTML_FILE, talk: bool = True, databaseFileName: str | Path = DEFAULT_DB_FILE, transpose: bool = False) -> tuple[dict[str, int], list[list[Any]]] | None:
	"""Run a read-only query and optionally write its result as HTML."""
	database_path = Path(databaseFileName)
	if not database_path.exists():
		print(f"Sorry, database file '{database_path}' does not exist.")
		return None
	if "maps_atg_schema" in query:
		query = query.replace("maps_atg_schema", PRIMARY_TABLE_NAME)
	read_only_uri = f"file:{database_path}?mode=ro"
	with sqlite3.connect(read_only_uri, uri=True) as conn:
		cursor = conn.cursor()
		execute_start = time.perf_counter()
		try:
			rowset = cursor.execute(query)
		except sqlite3.Error as exc:
			print(f"Sorry, query '{query}' failed, error {exc}.")
			return None
		execute_elapsed = time.perf_counter() - execute_start
		if talk:
			print(f"Finished execute in {execute_elapsed:.2f} s, now fetching rows.")
		fetch_start = time.perf_counter()
		fieldnames = [description[0] for description in rowset.description or []]
		fieldNameDict = {field_name: index for index, field_name in enumerate(fieldnames)}
		rowList = [list(row) for row in rowset.fetchall()]
		counter = len(rowList)
		fetch_elapsed = time.perf_counter() - fetch_start
		if talk:
			print(f"Finished fetch in {fetch_elapsed:.2f} s, now making HTML.")

	tablename = getTableNameFromQueryString(query)
	output_path = Path(optionalFileName)
	write_start = time.perf_counter()
	if output_path.name.upper() != "NUL":
		sorttable_src = Path(__file__).resolve().parent / "sorttable.js"
		sorttable_dst = output_path.resolve().parent / "sorttable.js"
		if sorttable_src.exists() and not sorttable_dst.exists():
			shutil.copy2(sorttable_src, sorttable_dst)
	script_tag = '<script src="sorttable.js" type="text/javascript"></script>'
	output_rows = [row[:] for row in rowList]
	with open(output_path, "w", encoding="utf-8") as htmlfile:
		if transpose:
			transposed_rows = _transpose_rows(output_rows, fieldnames)
			transposed_rows.sort(key=lambda row: str(row[0]).upper())
			htmlfile.write(f"<html>{script_tag}<p>{query}, {counter} rows. </p>{_render_html_table(transposed_rows)}</html>")
			output_rows = transposed_rows
		else:
			htmlfile.write(f"<html>{script_tag}<title>{tablename}</title><body><p>{query}, {counter} rows. </p>{_render_html_table(output_rows, header_row=fieldnames)}</body></html>")
	write_elapsed = time.perf_counter() - write_start
	if talk:
		print(f"Finished HTML write in {write_elapsed:.2f} s.")
	print(f"Found {counter} rows to {output_path}.")
	return fieldNameDict, output_rows

def printQueryRows(query: str, databaseFileName: str | Path = DEFAULT_DB_FILE) -> None:
	result = tableToHtml(query, optionalFileName="NUL", talk=False, databaseFileName=databaseFileName)
	print(result[1] if result else [])

# For well_id=1, control_point_id=1, creates a table well_id, take_period, effect_period, quota_auction_start, factor_value, constraint_quota.
# tableToHtml("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=0 AND take_date IS NOT NULL)),
#   effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period FROM (SELECT DISTINCT effect_date FROM control_point_event WHERE auction_id=0 AND effect_date IS NOT NULL)),
#   q AS (SELECT wq.well_id, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=0),
#   cpe AS (SELECT cp.control_point_id, edi.effect_period, cp.allowable_head_change FROM control_point_event cp JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date WHERE cp.auction_id=0),
#   denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.factor_value) AS total_load FROM response_matrix rm
#     JOIN q ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period WHERE rm.pumping_period <= rm.effect_period GROUP BY rm.control_point_id, rm.effect_period)
# SELECT rm.well_id, rm.pumping_period AS take_period, rm.effect_period, q.quota_auction_start, rm.factor_value, q.quota_auction_start * rm.factor_value * cpe.allowable_head_change / denom.total_load AS constraint_quota
# FROM response_matrix rm
# JOIN q     ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period
# JOIN cpe   ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
# JOIN denom ON denom.control_point_id=rm.control_point_id AND denom.effect_period=rm.effect_period
# WHERE rm.well_id=1 AND rm.control_point_id=1
# """)

# Query intent:
# 1) Map take_date -> pumping_period and effect_date -> effect_period (1-based).
# 2) Build q(w,u)=quota_auction_start and cpe(k,t)=allowable_head_change.
# 3) Build denom(k,t)=sum_{all wells v, all pumping periods r<=t} (q(v,r) * F(v,r,t,k)).
# 4) Return rows for control_point_id=1, pumping_period=1, effect_period=1, for all wells.
#
# constraint_quota formula used in SELECT:
# constraint_quota(w,u,t,k) = q(w,u) * F(w,u,t,k) * allowable_head_change(t,k) / denom(k,t)
# where q(w,u)=quota_auction_start, F(w,u,t,k)=response_matrix.factor_value,
# and denom(k,t)=SUM(q(v,r)*F(v,r,t,k)) over all v and r<=t.
# tableToHtml("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=0 AND take_date IS NOT NULL)),
#   effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period FROM (SELECT DISTINCT effect_date FROM control_point_event WHERE auction_id=0 AND effect_date IS NOT NULL)),
#   q AS (SELECT wq.well_id, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=0),
# 	cpe AS (SELECT cp.control_point_id, edi.effect_period, cp.allowable_head_change, cp.alpha AS alpha_db FROM control_point_event cp JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date WHERE cp.auction_id=0),
#   denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.factor_value) AS total_load FROM response_matrix rm
#     JOIN q ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period WHERE rm.pumping_period <= rm.effect_period GROUP BY rm.control_point_id, rm.effect_period)
# SELECT rm.well_id, rm.pumping_period AS take_period, rm.effect_period, q.quota_auction_start, rm.factor_value, denom.total_load AS denominator, cpe.allowable_head_change / denom.total_load AS alpha_computed, cpe.alpha_db AS alpha_db, q.quota_auction_start * rm.factor_value * cpe.allowable_head_change / denom.total_load AS constraint_quota
# FROM response_matrix rm
# JOIN q     ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period
# JOIN cpe   ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
# JOIN denom ON denom.control_point_id=rm.control_point_id AND denom.effect_period=rm.effect_period
# WHERE rm.control_point_id=1 AND rm.pumping_period=1 AND rm.effect_period=1
# """)

if __name__ == "__main__":
	tableToHtml("""WITH take_date_idx AS (SELECT take_date, ROW_NUMBER() OVER (ORDER BY take_date) AS pumping_period FROM (SELECT DISTINCT take_date FROM well_quota WHERE auction_id=0 AND take_date IS NOT NULL)),
	  effect_date_idx AS (SELECT effect_date, ROW_NUMBER() OVER (ORDER BY effect_date) AS effect_period FROM (SELECT DISTINCT effect_date FROM control_point_event WHERE auction_id=0 AND effect_date IS NOT NULL)),
	  q AS (SELECT wq.well_id, tdi.pumping_period, wq.quota_auction_start FROM well_quota wq JOIN take_date_idx tdi ON tdi.take_date = wq.take_date WHERE wq.auction_id=0),
		cpe AS (SELECT cp.control_point_id, edi.effect_period, cp.allowable_head_change, cp.alpha AS alpha_db FROM control_point_event cp JOIN effect_date_idx edi ON edi.effect_date = cp.effect_date WHERE cp.auction_id=0),
	  denom AS (SELECT rm.control_point_id, rm.effect_period, SUM(q.quota_auction_start * rm.factor_value) AS total_load FROM response_matrix rm
	    JOIN q ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period WHERE rm.pumping_period <= rm.effect_period GROUP BY rm.control_point_id, rm.effect_period)
	SELECT cpe.control_point_id, rm.well_id, rm.pumping_period AS take_period, rm.effect_period, q.quota_auction_start, rm.factor_value, denom.total_load AS denominator, cpe.allowable_head_change / denom.total_load AS alpha_computed, cpe.alpha_db AS alpha_db, q.quota_auction_start * rm.factor_value * cpe.allowable_head_change / denom.total_load AS constraint_quota
	FROM response_matrix rm
	JOIN q     ON q.well_id=rm.well_id AND q.pumping_period=rm.pumping_period
	JOIN cpe   ON cpe.control_point_id=rm.control_point_id AND cpe.effect_period=rm.effect_period
	JOIN denom ON denom.control_point_id=rm.control_point_id AND denom.effect_period=rm.effect_period
	WHERE rm.well_id=22 AND rm.pumping_period=1 AND rm.effect_period=1
	""")