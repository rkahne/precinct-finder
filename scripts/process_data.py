#!/usr/bin/env python3
"""
Pre-processes the precinct leaders Excel file and Louisville precincts shapefile
into a single GeoJSON file used by the webapp.

Run once locally (requires geopandas, pandas, openpyxl):
    pip install geopandas pandas openpyxl
    python scripts/process_data.py
"""
import os
import sys
import json
import pandas as pd
import geopandas as gpd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_leader_counts():
    excel_path = os.path.join(
        BASE_DIR, "other-data", "Precinct Leaders and Volunteers-2026.xlsx"
    )
    df = pd.read_excel(excel_path, header=0)

    # Column 5 (index 4) has no header — it's email
    df.columns = [
        "firstname", "lastname", "legislative_district", "precinct",
        "email", "role_type", "precinct_role",
        "waiver", "url_params", "submission_timestamp",
    ]

    df["firstname"] = df["firstname"].str.strip().str.lower()
    df["lastname"] = df["lastname"].str.strip().str.lower()
    df["precinct"] = df["precinct"].str.strip().str.upper()

    df_clean = df[["firstname", "lastname", "precinct"]].dropna(
        subset=["firstname", "lastname", "precinct"]
    )

    # Unique individuals = unique (firstname, lastname) per precinct
    unique_counts = (
        df_clean.drop_duplicates(subset=["firstname", "lastname", "precinct"])
        .groupby("precinct")
        .size()
    )

    leader_counts = unique_counts.to_dict()

    print(f"Loaded {len(df_clean)} records across {len(leader_counts)} precincts")
    print(f"  3+ leaders: {sum(1 for v in leader_counts.values() if v >= 3)}")
    print(f"  1-2 leaders: {sum(1 for v in leader_counts.values() if 0 < v < 3)}")
    return leader_counts


def load_shapefile(leader_counts):
    shp_path = os.path.join(
        BASE_DIR,
        "shapefiles",
        "louisville_precincts_2023",
        "Jefferson_County_KY_Voting_Precincts.shp",
    )
    gdf = gpd.read_file(shp_path)
    gdf = gdf.to_crs("EPSG:4326")

    # Simplify to reduce GeoJSON file size (~10m precision)
    gdf["geometry"] = gdf["geometry"].simplify(tolerance=0.0001, preserve_topology=True)

    gdf["unique_leaders"] = gdf["PRECINCT"].map(leader_counts).fillna(0).astype(int)
    gdf["has_enough_leaders"] = gdf["unique_leaders"] >= 3

    out = gdf[["PRECINCT", "LEGISDIST", "unique_leaders", "has_enough_leaders", "geometry"]].copy()
    out.columns = ["precinct", "leg_dist", "unique_leaders", "has_enough_leaders", "geometry"]
    return out


def main():
    print("Processing leader data...")
    leader_counts = load_leader_counts()

    print("\nProcessing shapefile...")
    gdf = load_shapefile(leader_counts)

    out_path = os.path.join(BASE_DIR, "precinct-finder", "static", "data", "precincts.geojson")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    gdf.to_file(out_path, driver="GeoJSON")

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\nSaved precincts.geojson ({size_kb:.0f} KB)")
    print(f"  Total precincts: {len(gdf)}")
    print(f"  With 3+ leaders: {gdf['has_enough_leaders'].sum()}")
    print(f"  Needing leaders: {(~gdf['has_enough_leaders']).sum()}")

    # Also save a summary JSON for quick stats
    summary = {
        "total_precincts": len(gdf),
        "precincts_with_enough": int(gdf["has_enough_leaders"].sum()),
        "precincts_needing_leaders": int((~gdf["has_enough_leaders"]).sum()),
        "leader_threshold": 3,
    }
    summary_path = os.path.join(BASE_DIR, "precinct-finder", "static", "data", "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved summary.json")


if __name__ == "__main__":
    main()
