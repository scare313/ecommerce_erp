"""Import landed cost and packaging cost into the catalog.

Reads a cost file with the columns:

    sku, landed_cost, packaging, gst_rate

and writes them into ``product_master.total_unit_cogs`` /
``product_master.gst_rate`` and ``pack_master.packaging_cogs``.

SKUs are matched on wording rather than exact string, so the marketplace
prefixes that the same product picks up on different channels — ``CAP-``,
``OCAP-``, ``FCAP-``, ``SCAP-``, ``L-CAP-`` — all resolve to one catalog
entry. Only genuinely new products need a new row in the cost file.

Usage
-----
    python scripts/import_cogs.py cost_master.csv            # preview only
    python scripts/import_cogs.py cost_master.csv --commit   # write to the DB

Nothing is written without ``--commit``.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.infrastructure.database import get_engine  # noqa: E402

# Words that mean the same product on different channels, and words that are
# channel noise rather than product identity.
SYNONYMS = {
    "NAVY": "NAVYBLUE",
    "CAPS": "CAP",
    "PARA": "PARACHUTE",
    "FULLNET": "NETFULL",
    "GREEN": "OLIVEGREEN",
    "OLIVE": "OLIVEGREEN",
}
NOISE = {"L", "M", "S", "A", "O", "F", "PLAIN"}
FILLER = {"CAP", "BASEBALL"}


def tokens(sku: str, drop_filler: bool = False) -> frozenset:
    """Reduce a SKU to the set of words that identify the product."""
    cleaned = re.sub(r"[^A-Z0-9]+", "-", str(sku).strip().upper())
    out = []
    for tok in cleaned.split("-"):
        if not tok:
            continue
        tok = re.sub(r"(\D)\d+$", r"\1", tok)   # NAVYBLUE1 -> NAVYBLUE
        tok = SYNONYMS.get(tok, tok)
        if tok in NOISE or (drop_filler and tok in FILLER):
            continue
        out.append(tok)
    return frozenset(out)


def build_index(costs: pd.DataFrame):
    """Return exact and loose token indexes over the cost file."""
    exact, loose = {}, defaultdict(list)
    for row in costs.itertuples(index=False):
        entry = (float(row.landed_cost), float(row.packaging), float(row.gst_rate))
        exact.setdefault(tokens(row.sku), entry)
        loose[tokens(row.sku, True)].append(entry)
    return exact, loose


def lookup(sku: str, exact: dict, loose: dict):
    """Find the cost for one catalog SKU, or None if it is genuinely new."""
    hit = exact.get(tokens(sku))
    if hit:
        return hit, "exact"
    key = tokens(sku, True)
    candidates = loose.get(key, [])
    if len(candidates) == 1 and len(key) >= 2:
        return candidates[0], "wording"
    return None, "unmatched"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cost_file", help="CSV or XLSX with sku, landed_cost, packaging, gst_rate")
    ap.add_argument("--commit", action="store_true", help="write changes (default: preview)")
    args = ap.parse_args()

    if args.cost_file.lower().endswith((".xlsx", ".xls")):
        costs = pd.read_excel(args.cost_file)
    else:
        costs = pd.read_csv(args.cost_file)

    required = {"sku", "landed_cost", "packaging", "gst_rate"}
    missing = required - set(costs.columns)
    if missing:
        print(f"ERROR: cost file is missing columns: {sorted(missing)}")
        return 1
    costs = costs.dropna(subset=["sku", "landed_cost"])

    exact, loose = build_index(costs)
    engine = get_engine()

    with engine.connect() as conn:
        products = pd.read_sql(
            "SELECT sku, name, total_unit_cogs, gst_rate FROM product_master", conn
        )
        packs = pd.read_sql("SELECT pack_sku, master_sku, quantity FROM pack_master", conn)

    updates, unmatched = [], []
    for row in products.itertuples(index=False):
        found, how = lookup(row.sku, exact, loose)
        if found is None:
            unmatched.append(row.sku)
            continue
        landed, packaging, gst = found
        updates.append(
            {
                "sku": row.sku,
                "old_cogs": row.total_unit_cogs,
                "cogs": landed,
                "packaging": packaging,
                # Cost files carry the rate as a fraction (0.05); the catalog
                # stores it as a percentage (5.0).
                "gst": gst * 100 if gst <= 1 else gst,
                "how": how,
            }
        )

    print(f"\nCatalog products : {len(products)}")
    print(f"Matched to a cost: {len(updates)}")
    print(f"Unmatched        : {len(unmatched)}")
    if unmatched:
        print("\nNo cost found for these — add them to the cost file:")
        for sku in unmatched:
            print(f"  - {sku}")

    if updates:
        preview = pd.DataFrame(updates)[["sku", "old_cogs", "cogs", "packaging", "how"]]
        print("\nChanges to apply:")
        print(preview.to_string(index=False))

    if not args.commit:
        print("\nPreview only. Re-run with --commit to write these to the database.")
        return 0

    pack_by_master = defaultdict(list)
    for p in packs.itertuples(index=False):
        pack_by_master[p.master_sku].append((p.pack_sku, p.quantity or 1))

    with engine.connect() as conn:
        for upd in updates:
            conn.execute(
                text(
                    "UPDATE product_master SET total_unit_cogs = :c, gst_rate = :g "
                    "WHERE sku = :s"
                ),
                {"c": upd["cogs"], "g": upd["gst"], "s": upd["sku"]},
            )
            # Packaging is consumed once per pack shipped, not once per piece,
            # so it belongs on pack_master and is not multiplied by quantity.
            for pack_sku, _qty in pack_by_master.get(upd["sku"], []):
                conn.execute(
                    text("UPDATE pack_master SET packaging_cogs = :p WHERE pack_sku = :k"),
                    {"p": upd["packaging"], "k": pack_sku},
                )
        conn.commit()

    print(f"\nCommitted {len(updates)} product costs.")
    if unmatched:
        print(f"{len(unmatched)} products still have no cost and will read as free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
