from pathlib import Path
import sqlite3
import importlib.util

workspace = Path(r"C:\Users\johnr\Documents\Work documents\2 Research\Water\Programming\ForeverFair2026")
files_dir = Path(r"C:\Users\johnr\Documents\Work documents\2 Research\Water\Programming\ForeverFair2\Test4, pass Tianqiao\files")
db_path = workspace / "data" / "case_data" / "tmp_import_check.db"

spec = importlib.util.spec_from_file_location("setup_mod", workspace / "src" / "setup.py")
setup_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_mod)

create_empty_db = setup_mod.create_empty_db
delete_db = setup_mod.delete_db
import_decvar = setup_mod.import_decvar
import_hedcon = setup_mod.import_hedcon
import_mps = setup_mod.import_mps

auction_id = "auction-001"
wells = 29
pump_periods = 52
control_points = 10
control_periods = 78

delete_db(db_path)
create_empty_db(db_path)

res_decvar = import_decvar(
    db_path,
    (files_dir / "tianqiao.decvar").read_text(encoding="utf-8", errors="ignore"),
    wells,
    pump_periods,
)
print("import_decvar:", res_decvar)

res_hedcon = import_hedcon(
    db_path,
    (files_dir / "tianqiao.hedcon").read_text(encoding="utf-8", errors="ignore"),
    control_points,
    control_periods,
    auction_id=auction_id,
)
print("import_hedcon:", res_hedcon)

with sqlite3.connect(db_path) as conn:
    hedcon_bounds_count = conn.execute("SELECT COUNT(*) FROM control_point_bounds").fetchone()[0]
print("control_point_bounds_count_after_hedcon:", hedcon_bounds_count)

res_mps = import_mps(
    db_path,
    (files_dir / "tianqiao.mps").read_text(encoding="utf-8", errors="ignore"),
    auction_id,
    wells,
    pump_periods,
    control_points,
    control_periods,
)
print("import_mps:", res_mps)

with sqlite3.connect(db_path) as conn:
    cpb_count = conn.execute("SELECT COUNT(*) FROM control_point_bounds").fetchone()[0]
print("final_control_point_bounds_count:", cpb_count)
