from pathlib import Path
import duckdb

con = duckdb.connect()
con.execute("LOAD spatial")

path = input(
    "Enter the full path to your boundary GeoParquet or GeoPackage: "
).strip()

file_path = Path(path).expanduser()

if not file_path.exists():
    raise FileNotFoundError(f"File not found: {file_path}")

suffix = file_path.suffix.lower()

if suffix in {".parquet", ".geoparquet"}:
    escaped_path = str(file_path).replace("'", "''")

    print("\n--- GeoParquet schema ---")
    schema = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{escaped_path}')"
    ).fetchall()

    for column in schema:
        print(f"Column: {column[0]} | Type: {column[1]}")

    print("\n--- Sample records ---")
    print(
        con.execute(
            f"SELECT * FROM read_parquet('{escaped_path}') LIMIT 3"
        ).df().drop(
            columns=["geometry", "geom", "wkb_geometry"],
            errors="ignore",
        ).to_string(index=False)
    )

elif suffix == ".gpkg":
    escaped_path = str(file_path).replace("'", "''")

    print("\n--- GeoPackage metadata ---")
    print(
        con.execute(
            f"SELECT * FROM ST_Read_Meta('{escaped_path}')"
        ).df().to_string(index=False)
    )

else:
    raise ValueError(
        "Please provide a .parquet, .geoparquet, or .gpkg file."
    )

con.close()
