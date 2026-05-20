import time
import sqlite3
import sys
import os
from pathlib import Path

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from ForeverFairData import ForeverFairData

db_path = Path("Catchment_data/Tianqiao/tmp_license_benchmark_v3.db")

# 3) Instantiate ForeverFairData (runs migrations/indexes)
ffd = ForeverFairData(db_path)

# 4) Time set_license_demand once
start_time = time.time()
changes = ffd.set_license_demand_on_aquifer()
end_time = time.time()

# 5) Report
print(f"Elapsed: {end_time - start_time:.4f} seconds")
print(f"Changes: {changes}")

# 6) Print indexes
conn = sqlite3.connect(db_path)
tables = ["aquifer_head_limits", "response_matrix", "well_license"]
print("\nIndexes:")
for table in tables:
    print(f"--- {table} ---")
    # Get index list
    cursor = conn.execute(f"PRAGMA index_list({table})")
    for row in cursor.fetchall():
        idx_name = row[1]
        # Get column names for each index
        col_cursor = conn.execute(f"PRAGMA index_info({idx_name})")
        cols = [c[2] for c in col_cursor.fetchall()]
        print(f"{idx_name}: {cols}")
conn.close()
