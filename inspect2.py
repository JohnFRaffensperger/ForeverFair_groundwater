import sqlite3
db = r"Catchment_data\Tianqiao\foreverfair.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

print("=== auctions table ===")
for r in con.execute("SELECT auction_id, firstWaterTakeDate, lastWaterTakeDate, period_length_hours, status FROM auctions ORDER BY auction_id"):
    print(dict(r))

print("\n=== Catchment_info ===")
for r in con.execute("SELECT meta_key, meta_value FROM Catchment_info ORDER BY meta_key"):
    print(dict(r))

print("\n=== well_quota auction 2 distinct periods (first 5 wells) ===")
for r in con.execute("SELECT well_id, COUNT(*) as n, MIN(take_date) as first_d, MAX(take_date) as last_d FROM well_quota WHERE auction_id=2 GROUP BY well_id ORDER BY well_id LIMIT 5"):
    print(dict(r))

print("\n=== well_bids auction 2 per well (first 5 wells) ===")
for r in con.execute("SELECT well_id, COUNT(*) as n, MIN(pumping_date) as first_d, MAX(pumping_date) as last_d FROM well_bids WHERE auction_id=2 AND deleted=0 GROUP BY well_id ORDER BY well_id LIMIT 5"):
    print(dict(r))
con.close()
