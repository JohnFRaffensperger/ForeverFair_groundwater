import sqlite3
db = r"Catchment_data\Tianqiao\foreverfair.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

print("=== well_quota rows per auction ===")
for r in con.execute("SELECT auction_id, COUNT(*) as n, COUNT(DISTINCT well_id) as wells, COUNT(DISTINCT take_date) as periods FROM well_quota GROUP BY auction_id ORDER BY auction_id"):
    print(dict(r))

print("\n=== well_bids rows per auction (not deleted) ===")
for r in con.execute("SELECT auction_id, COUNT(*) as n, COUNT(DISTINCT well_id) as wells, COUNT(DISTINCT pumping_date) as periods FROM well_bids WHERE deleted=0 GROUP BY auction_id ORDER BY auction_id"):
    print(dict(r))

print("\n=== Auction 1 take_dates (distinct) ===")
for r in con.execute("SELECT DISTINCT take_date FROM well_quota WHERE auction_id=1 ORDER BY take_date"):
    print(r[0])

print("\n=== Auction 2 take_dates (distinct) ===")
for r in con.execute("SELECT DISTINCT take_date FROM well_quota WHERE auction_id=2 ORDER BY take_date"):
    print(r[0])

print("\n=== Auction 1 pumping_dates in bids (distinct) ===")
for r in con.execute("SELECT DISTINCT pumping_date FROM well_bids WHERE auction_id=1 AND deleted=0 ORDER BY pumping_date"):
    print(r[0])

print("\n=== Auction 2 pumping_dates in bids (distinct) ===")
for r in con.execute("SELECT DISTINCT pumping_date FROM well_bids WHERE auction_id=2 AND deleted=0 ORDER BY pumping_date"):
    print(r[0])

print("\n=== well_license bid_period range ===")
for r in con.execute("SELECT well_id, COUNT(*) as n, MIN(bid_period) as min_p, MAX(bid_period) as max_p FROM well_license GROUP BY well_id ORDER BY well_id LIMIT 10"):
    print(dict(r))

print("\n=== Auction info ===")
for r in con.execute("SELECT auction_id, firstWaterTakeDate, lastWaterTakeDate, period_length_hours FROM auctions ORDER BY auction_id"):
    print(dict(r))
con.close()
