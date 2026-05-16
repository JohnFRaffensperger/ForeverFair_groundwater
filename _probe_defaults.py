from pathlib import Path
import tempfile
import sys
sys.path.insert(0, 'src')
from services.ForeverFairData import ForeverFairData
from AuctionController import call_for_bids

tmpdir = Path(tempfile.mkdtemp())
repo = ForeverFairData(db_path=tmpdir / 'probe.db', debug_db_path=Path('Catchment_data/Tianqiao/foreverfair.db'))
next_auction = repo.get_next_auction_info()
print('next_auction:', next_auction)
if next_auction is None:
    raise SystemExit(0)
auction_id = int(next_auction['auction_id'])
auction = repo.get_auction_info(auction_id)
print('auction_period_count:', len(auction["periods"]))
with repo.connect_to_db() as conn:
    q = conn.execute('SELECT COUNT(*) FROM well_quota WHERE auction_id=?', (auction_id,)).fetchone()[0]
    qd = conn.execute('SELECT COUNT(DISTINCT take_date) FROM well_quota WHERE auction_id=?', (auction_id,)).fetchone()[0]
    b = conn.execute('SELECT COUNT(*) FROM well_bids WHERE auction_id=? AND deleted=0', (auction_id,)).fetchone()[0]
    print('before quota_rows:', q, 'quota_dates:', qd, 'bids:', b)
call_for_bids(repo, auction_id)
with repo.connect_to_db() as conn:
    b = conn.execute('SELECT COUNT(*) FROM well_bids WHERE auction_id=? AND deleted=0', (auction_id,)).fetchone()[0]
    bd = conn.execute('SELECT COUNT(*) FROM well_bids WHERE auction_id=? AND deleted=0 AND is_bid_default=1', (auction_id,)).fetchone()[0]
    print('after bids:', b, 'default_bids:', bd)
