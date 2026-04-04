#!/usr/bin/env python3
"""
Pre-processes the precinct leaders Excel file and Louisville precincts shapefile
into a GeoJSON file AND updates the PostgreSQL precincts table.

Run whenever the Excel spreadsheet changes:
    DATABASE_URL=postgresql://user:pass@localhost/precinctdb python scripts/process_data.py

Requires (data processing):
    pip install geopandas pandas openpyxl

Requires (DB update):
    pip install psycopg2-binary
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

    df.columns = [
        "firstname", "lastname", "legislative_district", "precinct",
        "email", "role_type", "precinct_role",
        "waiver", "url_params", "submission_timestamp",
    ]

    df["firstname"] = df["firstname"].str.strip().str.lower()
    df["lastname"]  = df["lastname"].str.strip().str.lower()
    df["precinct"]  = df["precinct"].str.strip().str.upper()

    df_clean = df[["firstname", "lastname", "precinct"]].dropna(
        subset=["firstname", "lastname", "precinct"]
    )

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
    gdf["geometry"] = gdf["geometry"].simplify(tolerance=0.0001, preserve_topology=True)

    gdf["unique_leaders"]     = gdf["PRECINCT"].map(leader_counts).fillna(0).astype(int)
    gdf["has_enough_leaders"] = gdf["unique_leaders"] >= 3

    out = gdf[["PRECINCT", "LEGISDIST", "unique_leaders", "has_enough_leaders", "geometry"]].copy()
    out.columns = ["precinct", "leg_dist", "unique_leaders", "has_enough_leaders", "geometry"]
    return out


def write_geojson(gdf):
    out_path = os.path.join(BASE_DIR, "precinct-finder", "static", "data", "precincts.geojson")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    gdf.to_file(out_path, driver="GeoJSON")

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\nSaved precincts.geojson ({size_kb:.0f} KB)")
    print(f"  Total precincts:   {len(gdf)}")
    print(f"  With 3+ leaders:   {gdf['has_enough_leaders'].sum()}")
    print(f"  Needing leaders:   {(~gdf['has_enough_leaders']).sum()}")

    summary = {
        "total_precincts":          len(gdf),
        "precincts_with_enough":    int(gdf["has_enough_leaders"].sum()),
        "precincts_needing_leaders": int((~gdf["has_enough_leaders"]).sum()),
        "leader_threshold":         3,
    }
    summary_path = os.path.join(BASE_DIR, "precinct-finder", "static", "data", "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print("Saved summary.json")


def update_database(gdf):
    """Upsert precinct leader counts into PostgreSQL."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("\nNo DATABASE_URL set — skipping database update.")
        return

    try:
        import psycopg2
    except ImportError:
        print("\npsycopg2 not installed — skipping database update.")
        return

    print("\nUpdating database...")
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            rows_upserted = 0
            for _, row in gdf.iterrows():
                cur.execute(
                    """INSERT INTO precincts (precinct_code, leg_dist, unique_leaders, has_enough_leaders, updated_at)
                       VALUES (%s, %s, %s, %s, NOW())
                       ON CONFLICT (precinct_code) DO UPDATE SET
                           leg_dist           = EXCLUDED.leg_dist,
                           unique_leaders     = EXCLUDED.unique_leaders,
                           has_enough_leaders = EXCLUDED.has_enough_leaders,
                           updated_at         = NOW()""",
                    (
                        str(row["precinct"]),
                        str(row["leg_dist"]),
                        int(row["unique_leaders"]),
                        bool(row["has_enough_leaders"]),
                    ),
                )
                rows_upserted += 1
        conn.commit()
        print(f"  Upserted {rows_upserted} precinct rows.")
    except Exception as exc:
        conn.rollback()
        print(f"  Database update failed: {exc}", file=sys.stderr)
    finally:
        conn.close()


def main():
    print("Processing leader data...")
    leader_counts = load_leader_counts()

    print("\nProcessing shapefile...")
    gdf = load_shapefile(leader_counts)

    write_geojson(gdf)
    update_database(gdf)


if __name__ == "__main__":
    main()
