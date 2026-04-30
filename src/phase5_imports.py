# phase5_imports.py - Phase 5 import utilities for seed JSON elimination
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Provide JSON-based import functions to initialize database without seed.json

import json
import sqlite3
from pathlib import Path


def import_periods_json(db_path: Path, json_text: str) -> dict:
    """Import periods from JSON.
    
    Expected JSON structure:
    {
        "periods": [
            {"id": "W1", "label": "Week 1"},
            {"id": "W2", "label": "Week 2"}
        ]
    }
    
    Inserts into periods table (period_id as integer from id suffix, display_label from label).
    """
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"periods_inserted": 0, "errors": [f"JSON parse error: {e}"]}
    
    periods = payload.get("periods", [])
    if not periods:
        return {"periods_inserted": 0, "errors": ["No 'periods' array found in JSON"]}
    
    conn = sqlite3.connect(db_path)
    inserted = 0
    errors = []
    
    for period in periods:
        period_id_str = period.get("id", "").strip()
        label = period.get("label", "").strip()
        
        if not period_id_str or not label:
            errors.append(f"Skipped period with missing id or label: {period}")
            continue
        
        # Extract numeric ID from string (e.g., "W1" -> 1, "Period_3" -> 3)
        period_id = None
        for char in period_id_str:
            if char.isdigit():
                period_id = int("".join(c for c in period_id_str if c.isdigit()))
                break
        
        if period_id is None:
            errors.append(f"Could not extract numeric ID from '{period_id_str}'")
            continue
        
        try:
            conn.execute(
                "INSERT OR REPLACE INTO periods(period_id, period_date, display_label) VALUES (?, NULL, ?)",
                (period_id, label)
            )
            inserted += 1
        except Exception as exc:
            errors.append(f"Period {period_id_str}: {exc}")
    
    conn.commit()
    conn.close()
    return {"periods_inserted": inserted, "errors": errors[:20]}


def import_traders_and_allocations_json(db_path: Path, json_text: str) -> dict:
    """Import traders and their period allocations from JSON.
    
    Expected JSON structure:
    {
        "traders": [
            {
                "id": "trader-chen",
                "name": "Farmer Chen",
                "allocation_by_period": {"W1": 90.0, "W2": 88.0, ...}
            }
        ],
        "auction_id": "auction-001"  // Required for allocation inserts
    }
    """
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"traders_inserted": 0, "allocations_inserted": 0, "errors": [f"JSON parse error: {e}"]}
    
    traders = payload.get("traders", [])
    auction_id = payload.get("auction_id")
    
    if not traders:
        return {"traders_inserted": 0, "allocations_inserted": 0, "errors": ["No 'traders' array found"]}
    
    conn = sqlite3.connect(db_path)
    traders_inserted = 0
    allocations_inserted = 0
    errors = []
    
    for trader in traders:
        trader_id = trader.get("id", "").strip()
        name = trader.get("name", "").strip()
        allocations = trader.get("allocation_by_period", {})
        
        if not trader_id or not name:
            errors.append(f"Skipped trader with missing id or name: {trader}")
            continue
        
        # Insert trader
        try:
            conn.execute(
                "INSERT OR REPLACE INTO traders(trader_id, name_tag) VALUES (?, ?)",
                (trader_id, name)
            )
            traders_inserted += 1
        except Exception as exc:
            errors.append(f"Trader {trader_id}: {exc}")
            continue
        
        # Insert allocations if auction_id provided
        if auction_id and allocations:
            for period_str, allocation_val in allocations.items():
                # Extract numeric period ID
                period_id = int("".join(c for c in period_str if c.isdigit())) if any(c.isdigit() for c in period_str) else None
                
                if period_id is None:
                    errors.append(f"Could not extract period ID from '{period_str}' for trader {trader_id}")
                    continue
                
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO trader_allocations(auction_id, trader_id, period_id, allocation) "
                        "VALUES (?, ?, ?, ?)",
                        (auction_id, trader_id, period_id, float(allocation_val))
                    )
                    allocations_inserted += 1
                except Exception as exc:
                    errors.append(f"Allocation {trader_id} period {period_id}: {exc}")
    
    conn.commit()
    conn.close()
    return {
        "traders_inserted": traders_inserted,
        "allocations_inserted": allocations_inserted,
        "errors": errors[:20]
    }


def import_control_points_and_bounds_json(db_path: Path, json_text: str) -> dict:
    """Import control points and their period bounds from JSON.
    
    Expected JSON structure:
    {
        "control_points": [
            {
                "id": "cp-01",
                "name": "North head constraint",
                "bound_by_period": {"W1": 46.0, "W2": 44.0, ...}
            }
        ],
        "use_as_defaults": true  // If true, insert to default_control_point_bounds; else requires auction_id
    }
    """
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"control_points_inserted": 0, "bounds_inserted": 0, "errors": [f"JSON parse error: {e}"]}
    
    control_points = payload.get("control_points", [])
    use_as_defaults = payload.get("use_as_defaults", True)
    auction_id = payload.get("auction_id")
    
    if not control_points:
        return {"control_points_inserted": 0, "bounds_inserted": 0, "errors": ["No 'control_points' array found"]}
    
    conn = sqlite3.connect(db_path)
    cp_inserted = 0
    bounds_inserted = 0
    errors = []
    
    for cp in control_points:
        cp_id = cp.get("id", "").strip()
        name = cp.get("name", "").strip()
        bounds = cp.get("bound_by_period", {})
        
        if not cp_id or not name:
            errors.append(f"Skipped control point with missing id or name: {cp}")
            continue
        
        # Insert control point
        try:
            conn.execute(
                "INSERT OR REPLACE INTO control_points(control_point_id, name) VALUES (?, ?)",
                (cp_id, name)
            )
            cp_inserted += 1
        except Exception as exc:
            errors.append(f"Control point {cp_id}: {exc}")
            continue
        
        # Insert bounds
        if bounds:
            for period_str, bound_val in bounds.items():
                period_id = int("".join(c for c in period_str if c.isdigit())) if any(c.isdigit() for c in period_str) else None
                
                if period_id is None:
                    errors.append(f"Could not extract period ID from '{period_str}' for CP {cp_id}")
                    continue
                
                try:
                    if use_as_defaults:
                        # Insert to default_control_point_bounds (no auction)
                        conn.execute(
                            "INSERT OR REPLACE INTO default_control_point_bounds(control_point_id, period_id, bound) "
                            "VALUES (?, ?, ?)",
                            (cp_id, period_id, float(bound_val))
                        )
                    elif auction_id:
                        # Insert to control_point_bounds (specific auction)
                        conn.execute(
                            "INSERT OR REPLACE INTO control_point_bounds(auction_id, control_point_id, period_id, bound) "
                            "VALUES (?, ?, ?, ?)",
                            (auction_id, cp_id, period_id, float(bound_val))
                        )
                    bounds_inserted += 1
                except Exception as exc:
                    errors.append(f"Bound {cp_id} period {period_id}: {exc}")
    
    conn.commit()
    conn.close()
    return {
        "control_points_inserted": cp_inserted,
        "bounds_inserted": bounds_inserted,
        "errors": errors[:20]
    }


def import_auction_metadata_json(db_path: Path, json_text: str) -> dict:
    """Import auction metadata (catchment_name, source_note, current_trader_id).
    
    Expected JSON structure:
    {
        "catchment_name": "Primary Catchment",
        "source_note": "Source description",
        "current_trader_id": "trader-chen"
    }
    """
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"metadata_inserted": 0, "errors": [f"JSON parse error: {e}"]}
    
    conn = sqlite3.connect(db_path)
    inserted = 0
    errors = []
    
    for key in ["catchment_name", "source_note", "current_trader_id"]:
        value = payload.get(key)
        if value is not None:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO metadata(meta_key, meta_value) VALUES (?, ?)",
                    (key, str(value))
                )
                inserted += 1
            except Exception as exc:
                errors.append(f"Metadata {key}: {exc}")
    
    conn.commit()
    conn.close()
    return {"metadata_inserted": inserted, "errors": errors[:20]}
