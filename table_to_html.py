# table_to_html.py | Created 2026-03-23
# Runs SQLite queries, writes HTML tables, exports CSVs, and supports quick diagnostic inspection of Busch datasets and transformed outputs files.
import csv
from ctypes import *
from pathlib import Path
import shutil
import sqlite3
import sys
import time
# import pandas, pandas.io.sql # Only for dump to CSV.

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "ForeverFair2026\data\Tianqiao"
if str(OUTPUT_DIR) not in sys.path: sys.path.insert(0, str(OUTPUT_DIR))
import LocalHTML

DEFAULT_DB_FILE = OUTPUT_DIR / "foreverfair.db"
DEFAULT_HTML_FILE = OUTPUT_DIR / "mytable.html"
DEFAULT_QUERY_CSV = OUTPUT_DIR / "myQueryOutput.csv"
DEFAULT_AGGREGATE_CSV = OUTPUT_DIR / "myAggregateOutput.csv"
DEFAULT_EXPORT_CSV = OUTPUT_DIR / "yourquery.csv"

def getTableNameFromQueryString(query): # Tries to get the table name from the query string.
	start = query.find(' from ')
	substring = query[start+6:]
	end = min(substring.find('\t'), substring.find(' '))
	return query[start+6:start+6+end]

def makeHTML(rows, fieldIndices = None):
	outputfilename = DEFAULT_HTML_FILE
	if not rows:
		print ("No rows")
		return

	if fieldIndices:
		fieldnames = ['']*len(rows[0])
		for field in fieldIndices: fieldnames[fieldIndices[field]] = field
		with open(outputfilename, 'w') as f: f.write('<html>' + LocalHTML.table(rows, header_row = fieldnames) + '</html>')
	else:
		with open(outputfilename, 'w') as f: f.write('<html>' + LocalHTML.table(rows) + '</html>')
	print ("Wrote %i rows to %s." % (len(rows), outputfilename))
	return

# def dumpQueryToCSV(query, databaseFileName = str(DEFAULT_DB_FILE)):
# 	con = sqlite3.connect(databaseFileName)
# 	table = pandas.io.sql.read_sql(query, con)
# 	table.to_csv(DEFAULT_EXPORT_CSV, index=False)
# 	con.close()
#dumpQueryToCSV("select * from mymaster limit 10", "eda.db")

def saveQueryToCSV (query, optionalFileName = str(DEFAULT_QUERY_CSV)):
	con = sqlite3.connect(str(DEFAULT_DB_FILE))
	cursor = con.cursor()
	cursor.execute(query)

	with open(optionalFileName, "w", newline='') as csv_file:
		csv_writer = csv.writer(csv_file, quoting=csv.QUOTE_NONNUMERIC)
		csv_writer.writerow([i[0] for i in cursor.description]) # write headers
		csv_writer.writerows(cursor)
	con.commit()
	con.close()

# saveQueryToCSV ("select sum(area) as t_area, crop_npv_oppcost from maps_atg_schema group by crop_npv_oppcost")
# saveQueryToCSV ("select cast(crop_npv_oppcost as integer) as crop_npv_oppcost_int, sum(area) as t_area from maps_atg_schema where crop_npv_oppcost < 2000 group by cast(crop_npv_oppcost as integer)")
# saveQueryToCSV ("select cast(crop_npv_oppcost as integer) as crop_npv_oppcost_int, sum(area) as t_area from maps_atg_schema group by cast(crop_npv_oppcost as integer)")
# saveQueryToCSV ("select sum(area)	 as t_area from maps_atg_schema", 'totalarea.txt')

def show(tablename): tableToHtml("select * from %s limit 500" % tablename)

def tableToHtml(query, optionalFileName = str(DEFAULT_HTML_FILE), talk=True, databaseFileName = str(DEFAULT_DB_FILE), transpose = False):
	''' This function does the query, and writes the result to optionalFileName.
		It returns a dictionary of the field names and their indices in the rows,
		and the rows themselves.
		Optionally, it can look up some field name descriptions and field values.
	'''
	# 1. Run the requested query.
	if not Path(databaseFileName).exists():
		print("Sorry, database file '%s' does not exist." % databaseFileName) # Don't create a zero size database if it doesn't exit.
		return
	if "maps_atg_schema" in query: query = query.replace("maps_atg_schema", PRIMARY_TABLE_NAME)
	# uri=True enables SQLite URI syntax; mode=ro opens read-only (ro), alternatives: rw (read-write, no create), rwc (read-write-create, default), memory (in-memory).
	con = sqlite3.connect(f"file:{databaseFileName}?mode=ro", uri=True) # Don't allow writing to the database.
	mycursor = con.cursor()
	execute_start = time.perf_counter()
	try: rowset = mycursor.execute(query)
	except Exception as e:
		print("Sorry, query '%s' failed, error %s." % (query, str(e)))
		con.close()
		return
	execute_elapsed = time.perf_counter() - execute_start
	if talk: print ("Finished execute in %.2f s, now fetching rows." % execute_elapsed)

	if rowset:
		fetch_start = time.perf_counter()
		fieldnames = [description[0] for description in rowset.description]
		fieldNameDict = {description[0]: desc_index for (desc_index, description) in enumerate(rowset.description)}
		rowList = list(rowset)
		counter = len(rowList)
		fetch_elapsed = time.perf_counter() - fetch_start
		if talk: print ("Finished fetch in %.2f s, now making HTML." % fetch_elapsed)
	con.close()

	# 4. Write the HTML file, transposing if requested.
	tablename = getTableNameFromQueryString(query)
#	print("tablename = ", tablename)
	write_start = time.perf_counter()
	_sorttable_src = Path(__file__).resolve().parent / 'sorttable.js'
	_sorttable_dst = Path(optionalFileName).resolve().parent / 'sorttable.js'
	if _sorttable_src.exists() and not _sorttable_dst.exists(): shutil.copy2(_sorttable_src, _sorttable_dst)
	_script_tag = '<script src="sorttable.js" type="text/javascript"></script>'
	with open(optionalFileName, 'w', encoding='utf-8') as htmlfile:
		if transpose:
			rowList.insert(0, fieldnames) # Put header at top.
			rowList = list(map(list, zip(*rowList))) # Transpose.
			rowList = sorted(rowList, key=lambda x:x[0].upper()) # sort by field name.
			htmlfile.write('<html>' + _script_tag + '<p>'+query+', ' + str(counter) + ' rows. </p>' + LocalHTML.table(rowList) + "</html>")
		else: htmlfile.write('<html>' + _script_tag + '<title>' + tablename + '</title><body><p>'+query+', ' + str(counter) + ' rows. </p>' + str(LocalHTML.Table(rowList, header_row = fieldnames)) + "</body></html>")
	write_elapsed = time.perf_counter() - write_start
	if talk: print ("Finished HTML write in %.2f s." % write_elapsed)
	print ("Found " + str(counter) + " rows to %s." % optionalFileName)
	return fieldNameDict, rowList

def printQueryRows(query, databaseFileName = str(DEFAULT_DB_FILE)): print((result[1] if (result := tableToHtml(query, optionalFileName='NUL', talk=False, databaseFileName=databaseFileName)) else []))

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