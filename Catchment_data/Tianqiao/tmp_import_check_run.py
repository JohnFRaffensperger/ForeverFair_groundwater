from pathlib import Path
import sqlite3
import importlib.util

files_dir = Path(__file__).resolve().parent
workspace = files_dir.parents[1]
db_path = files_dir / "tmp_import_check.db"
period_length_hours = 168

spec = importlib.util.spec_from_file_location("setup_mod", workspace / "src" / "SetupForeverFairDB.py")
setup_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_mod)

create_empty_db = setup_mod.create_empty_db
delete_db = setup_mod.delete_db
import_decvar = setup_mod.import_decvar
import_hedcon = setup_mod.import_hedcon
import_mps = setup_mod.import_mps

delete_db(db_path)
create_empty_db(db_path)

res_decvar = import_decvar(
    db_path,
    (files_dir / "tianqiao.decvar").read_text(encoding="utf-8", errors="ignore"),
)
print("import_decvar:", res_decvar)

res_hedcon = import_hedcon(
    db_path,
    (files_dir / "tianqiao.hedcon").read_text(encoding="utf-8", errors="ignore"),
)
print("import_hedcon:", res_hedcon)

with sqlite3.connect(db_path) as conn:
    hedcon_bounds_count = conn.execute("SELECT COUNT(*) FROM control_point_event WHERE auction_id=0 AND effect_date LIKE 'stg:%'").fetchone()[0]
print("staged_control_point_event_rows_after_hedcon:", hedcon_bounds_count)

res_mps = import_mps(
    db_path,
    (files_dir / "tianqiao.mps").read_text(encoding="utf-8", errors="ignore"),
    period_length_hours=period_length_hours,
)
print("import_mps:", res_mps)

with sqlite3.connect(db_path) as conn:
    cpb_count = conn.execute("SELECT COUNT(*) FROM control_point_event WHERE auction_id=0 AND effect_date NOT LIKE 'stg:%'").fetchone()[0]
print("final_control_point_event_rows:", cpb_count)
