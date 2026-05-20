import sys, os, sqlite3, pathlib
sys.path.append(os.path.join(os.getcwd(), 'src'))
from ForeverFairData import ForeverFairData
from AuctionController import AuctionController

db_path = pathlib.Path('Catchment_data/Tianqiao/temp_auction_validation.db')
if db_path.exists():
    db_path.unlink()
repo = ForeverFairData(db_path)

repo.create_empty_db()
repo.save_meta_data('period_length_hours', 168)
repo.import_decvar(pathlib.Path('Catchment_data/Tianqiao/DecVar_12Dec2024.csv'))
repo.import_hedcon(pathlib.Path('Catchment_data/Tianqiao/HedCon_12Dec2024.csv'))
repo.import_trader_names(pathlib.Path('Catchment_data/Tianqiao/TraderNames.csv'))
repo.import_trader_wells(pathlib.Path('Catchment_data/Tianqiao/TraderWells.csv'))
repo.import_well_locations(pathlib.Path('Catchment_data/Tianqiao/WellLocations.csv'))
repo.import_control_point_locations(pathlib.Path('Catchment_data/Tianqiao/ControlPointLocations.csv'))
repo.import_mps(pathlib.Path('Catchment_data/Tianqiao/TQ_ResponseMatrix_12Dec2024.mps'))

ac = AuctionController()
auction_id = ac.set_up_auction_system(db_path)

conn = sqlite3.connect(db_path)
count_quota = conn.execute('SELECT COUNT(*) FROM well_quota WHERE auction_id = ?', (auction_id,)).fetchone()[0]
min_max_start = conn.execute('SELECT MIN(quota_auction_start), MAX(quota_auction_start) FROM well_quota WHERE auction_id = ?', (auction_id,)).fetchone()
diff_count = conn.execute('''
    SELECT COUNT(*) 
    FROM well_quota q
    JOIN well_license l ON q.well_id = l.well_id AND q.take_date = l.effect_date
    WHERE q.auction_id = ? AND q.quota_auction_start != l.license_quantity
''', (auction_id,)).fetchone()[0]

sample_rows = conn.execute('''
    SELECT q.well_id, q.take_date, l.license_quantity, q.quota_auction_start
    FROM well_quota q
    JOIN well_license l ON q.well_id = l.well_id AND q.take_date = l.effect_date
    WHERE q.auction_id = ?
    LIMIT 2
''', (auction_id,)).fetchall()

print(f'Auction ID: {auction_id}')
print(f'Well Quota Row Count: {count_quota}')
print(f'Min/Max Quota Auction Start: {min_max_start[0]} / {min_max_start[1]}')
print(f'Count of mismatches vs license_quantity: {diff_count}')
print('Sample Rows (well_id, take_date, license_quantity, quota_auction_start):')
for r in sample_rows:
    print(r)
conn.close()
