from pathlib import Path
import sqlite3
import importlib.util

workspace = Path(r"C:\Users\johnr\Documents\Work documents\2 Research\Water\Programming\ForeverFair2026")
files_dir = Path(r"C:\Users\johnr\Documents\Work documents\2 Research\Water\Programming\ForeverFair2\Test4, pass Tianqiao\files")
db_path = workspace / "data" / "case_data" / "tmp_import_check.db"

spec = importlib.util.spec_from_file_location("setup_mod", workspace / "src" / "setup.py")
setup_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_mod)

delete_db = setup_mod.delete_db
create_empty_db = setup_mod.create_empty_db
import_decvar = setup_mod.import_decvar
import_hedcon = setup_mod.import_hedcon
import_mps = setup_mod.import_mps

auction_id = "auction-001"

delete_db(db_path)
create_empty_db(db_path)

res_decvar = import_decvar(db_path, (files_dir / "tianqiao.decvar").read_text(encoding="utf-8", errors="ignore"))
print("import_decvar:", res_decvar)

res_hedcon = import_hedcon(db_path, (files_dir / "tianqiao.hedcon").read_text(encoding="utf-8", errors="ignore"), auction_id=auction_id)
print("import_hedcon:", res_hedcon)

res_mps = import_mps(db_path, (files_dir / "tianqiao.mps").read_text(encoding="utf-8", errors="ignore"), auction_id=auction_id)
print("import_mps:", res_mps)

keys = ["gwm_num_wells", "gwm_num_pump_periods", "gwm_num_control_points", "gwm_num_control_periods"]
with sqlite3.connect(db_path) as conn:
    for k in keys:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (k,)).fetchone()
        print(f"metadata[{k}] =", row[0] if row else None)
