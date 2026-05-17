from pathlib import Path
import tempfile
import sys
sys.path.insert(0, 'src')
from ForeverFairData import ForeverFairData
from AuctionController import runCurrentAuction

tmpdir = Path(tempfile.mkdtemp())
repo = ForeverFairData(db_path=tmpdir / 'probe.db', debug_db_path=Path('Catchment_data/Tianqiao/foreverfair.db'))
auction_id = int(repo.get_next_auction_info()['auction_id'])
runCurrentAuction(repo, auction_id)
print('run_summary:', repo.get_run_summary(auction_id))
print('has_default_bids:', repo.has_default_bids(auction_id))
