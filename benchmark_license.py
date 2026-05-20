import time
import sqlite3
import sys
import os

# Add src to path if needed
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from ForeverFairData import ForeverFairData
except ImportError:
    # If not in src, try root
    from ForeverFairData import ForeverFairData

db_path = "Catchment_data/Tianqiao/tmp_license_benchmark.db"

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
    for row in conn.execute(f"PRAGMA index_list({table})"):
        idx_name = row[1]
        cols = [c[2] for c in conn.execute(f"PRAGMA index_info({idx_name})")]
        print(f"{idx_name}: {cols}")
conn.close()
