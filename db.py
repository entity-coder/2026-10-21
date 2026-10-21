"""PharmaBill — SQLite data layer.

One row in ``medicines`` is one medicine *batch*: the same medicine name can
appear under several batch numbers with different expiry dates, MRPs and
stock levels. That is how real pharmacy stock works — expiry tracking only
makes sense per batch, never per medicine name.

Money is stored as REAL rupees rounded to 2 decimals at write time.
GST is treated as *inclusive* in the selling price (Indian MRP goods are
priced MRP-inclusive), so a line's GST is backed out of its amount instead
of added on top. The split is CGST = SGST = GST / 2 (intra-state sale).

No third-party dependency: only the standard library. The UI layer
(``ui.py``) and the PDF layer (``printing.py``) both talk to this module,
never to sqlite3 directly.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "pharmabill.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    expiry TEXT NOT NULL,          -- ISO date YYYY-MM-DD
    mrp REAL NOT NULL CHECK (mrp >= 0),
    gst_pct REAL NOT NULL DEFAULT 12 CHECK (gst_pct >= 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    low_stock_at INTEGER NOT NULL DEFAULT 5,
    UNIQUE (name, batch_no)
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_no TEXT UNIQUE NOT NULL,  -- PB-0001, PB-0002, ...
    bill_date TEXT NOT NULL,       -- ISO date YYYY-MM-DD
    customer TEXT NOT NULL DEFAULT 'Walk-in',
    phone TEXT NOT NULL DEFAULT '',
    payment TEXT NOT NULL DEFAULT 'Cash',  -- Cash / UPI / Card
    gross REAL NOT NULL,           -- sum of line amounts (GST-inclusive)
    gst_total REAL NOT NULL,       -- GST backed out of gross
    total REAL NOT NULL            -- == gross (kept explicit for receipts)
);

CREATE TABLE IF NOT EXISTS bill_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    name TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    expiry TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    rate REAL NOT NULL,
    gst_pct REAL NOT NULL,
    amount REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    changed_on TEXT NOT NULL,      -- ISO date
    delta INTEGER NOT NULL,        -- +in / -out (sale lines use -qty)
    reason TEXT NOT NULL DEFAULT ''
);
"""


def connect(path=DB_PATH):
    """Open a connection with foreign keys on and Row access."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=DB_PATH):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- medicines

def add_medicine(conn, name, batch_no, expiry, mrp, gst_pct=12,
                 stock=0, low_stock_at=5):
    """Insert a batch. Raises sqlite3.IntegrityError on duplicate name+batch."""
    cur = conn.execute(
        "INSERT INTO medicines (name, batch_no, expiry, mrp, gst_pct, stock,"
        " low_stock_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), batch_no.strip(), expiry, round(float(mrp), 2),
         float(gst_pct), int(stock), int(low_stock_at)))
    conn.commit()
    return cur.lastrowid


def update_medicine(conn, med_id, **fields):
    """Update only the whitelisted columns in ``fields``."""
    allowed = {"name", "batch_no", "expiry", "mrp", "gst_pct", "stock",
               "low_stock_at"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return
    values.append(med_id)
    conn.execute(f"UPDATE medicines SET {', '.join(sets)} WHERE id = ?",
                 values)
    conn.commit()


def delete_medicine(conn, med_id):
    """Refused if the batch appears on any bill (history must stay intact).

    The batch's stock-log rows are removed with it — movement history for
    a deleted batch is meaningless, and the FK would otherwise block the
    delete even for batches that were never billed.
    """
    used = conn.execute("SELECT 1 FROM bill_items WHERE medicine_id = ?"
                        " LIMIT 1", (med_id,)).fetchone()
    if used:
        raise ValueError("Batch is on a past bill and cannot be deleted.")
    conn.execute("DELETE FROM stock_log WHERE medicine_id = ?", (med_id,))
    conn.execute("DELETE FROM medicines WHERE id = ?", (med_id,))
    conn.commit()


def search_medicines(conn, query=""):
    """Name/batch search, earliest-expiry first (sell oldest stock first)."""
    like = f"%{query.strip()}%"
    return conn.execute(
        "SELECT * FROM medicines WHERE name LIKE ? OR batch_no LIKE ?"
        " ORDER BY expiry ASC, name COLLATE NOCASE",
        (like, like)).fetchall()


def get_medicine(conn, med_id):
    return conn.execute("SELECT * FROM medicines WHERE id = ?",
                        (med_id,)).fetchone()


def adjust_stock(conn, med_id, delta, reason, on_date=None):
    """Change stock by ``delta`` (negative = out). Never lets stock go below 0."""
    row = get_medicine(conn, med_id)
    if row is None:
        raise ValueError("Medicine not found.")
    new_stock = row["stock"] + int(delta)
    if new_stock < 0:
        raise ValueError(f"Not enough stock for {row['name']} "
                         f"({row['batch_no']}): have {row['stock']}.")
    conn.execute("UPDATE medicines SET stock = ? WHERE id = ?",
                 (new_stock, med_id))
    conn.execute("INSERT INTO stock_log (medicine_id, changed_on, delta,"
                 " reason) VALUES (?, ?, ?, ?)",
                 (med_id, on_date or date.today().isoformat(), int(delta),
                  reason))
    conn.commit()
    return new_stock


# ------------------------------------------------------------------- expiry

def expiry_status(expiry_iso, today=None):
    """'expired' / 'urgent' (<=30 days) / 'soon' (<=90 days) / 'ok'."""
    today = today or date.today()
    days = (date.fromisoformat(expiry_iso) - today).days
    if days < 0:
        return "expired"
    if days <= 30:
        return "urgent"
    if days <= 90:
        return "soon"
    return "ok"


def expiry_alerts(conn, today=None):
    """Batches expiring within 90 days (or already expired), soonest first."""
    today = today or date.today()
    horizon = (today + timedelta(days=90)).isoformat()
    return conn.execute(
        "SELECT * FROM medicines WHERE expiry <= ? ORDER BY expiry ASC",
        (horizon,)).fetchall()


def low_stock_items(conn):
    return conn.execute(
        "SELECT * FROM medicines WHERE stock <= low_stock_at"
        " ORDER BY stock ASC, name COLLATE NOCASE").fetchall()


# -------------------------------------------------------------------- bills

def gst_split(amount, gst_pct):
    """Back GST out of a GST-inclusive amount; returns (taxable, gst, cgst,
    sgst), all rounded to 2 decimals. CGST/SGST split the GST evenly."""
    taxable = round(amount * 100 / (100 + gst_pct), 2)
    gst = round(amount - taxable, 2)
    half = round(gst / 2, 2)
    return taxable, gst, half, gst - half  # last term keeps cgst+sgst == gst


def next_bill_no(conn):
    row = conn.execute("SELECT bill_no FROM bills ORDER BY id DESC"
                       " LIMIT 1").fetchone()
    n = int(row["bill_no"].split("-")[1]) + 1 if row else 1
    return f"PB-{n:04d}"


def create_bill(conn, customer, phone, payment, lines, bill_date=None):
    """Save a bill and decrement stock atomically.

    ``lines``: list of dicts with medicine_id, qty, rate (selling price,
    GST-inclusive). Quantities are capped by available stock — a sale can
    never push stock negative; the UI enforces the same cap before this.
    Returns (bill_no, gross, gst_total).
    """
    bill_date = bill_date or date.today().isoformat()
    gross = gst_total = 0.0
    items = []
    for line in lines:
        med = get_medicine(conn, line["medicine_id"])
        if med is None:
            raise ValueError("Medicine not found.")
        qty = int(line["qty"])
        if qty <= 0:
            raise ValueError("Quantity must be positive.")
        if qty > med["stock"]:
            raise ValueError(f"Not enough stock for {med['name']} "
                             f"({med['batch_no']}): have {med['stock']}.")
        amount = round(qty * float(line["rate"]), 2)
        _, gst, _, _ = gst_split(amount, med["gst_pct"])
        gross += amount
        gst_total += gst
        items.append((med, qty, float(line["rate"]), amount))
    gross, gst_total = round(gross, 2), round(gst_total, 2)
    bill_no = next_bill_no(conn)
    cur = conn.execute(
        "INSERT INTO bills (bill_no, bill_date, customer, phone, payment,"
        " gross, gst_total, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (bill_no, bill_date, customer.strip() or "Walk-in", phone.strip(),
         payment, gross, gst_total, gross))
    bill_id = cur.lastrowid
    for med, qty, rate, amount in items:
        conn.execute(
            "INSERT INTO bill_items (bill_id, medicine_id, name, batch_no,"
            " expiry, qty, rate, gst_pct, amount) VALUES (?, ?, ?, ?, ?,"
            " ?, ?, ?, ?)",
            (bill_id, med["id"], med["name"], med["batch_no"],
             med["expiry"], qty, rate, med["gst_pct"], amount))
        adjust_stock(conn, med["id"], -qty, f"Sale {bill_no}", bill_date)
    conn.commit()
    return bill_no, gross, gst_total


def get_bill(conn, bill_no):
    bill = conn.execute("SELECT * FROM bills WHERE bill_no = ?",
                        (bill_no,)).fetchone()
    if bill is None:
        return None, []
    items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?",
                         (bill["id"],)).fetchall()
    return bill, items


def day_report(conn, day):
    """Bills for one ISO date + totals. ``day`` is 'YYYY-MM-DD'."""
    bills = conn.execute(
        "SELECT * FROM bills WHERE bill_date = ? ORDER BY id ASC",
        (day,)).fetchall()
    gross = round(sum(b["gross"] for b in bills), 2)
    gst = round(sum(b["gst_total"] for b in bills), 2)
    return bills, {"bills": len(bills), "gross": gross, "gst": gst}


def dashboard_stats(conn, today=None):
    """One dict for the dashboard strip: today's sales + alert counts."""
    today = today or date.today()
    bills, totals = day_report(conn, today.isoformat())
    alerts = expiry_alerts(conn, today)
    counts = {"expired": 0, "urgent": 0, "soon": 0}
    for row in alerts:
        counts[expiry_status(row["expiry"], today)] += 1
    return {
        "today_bills": totals["bills"],
        "today_sales": totals["gross"],
        "today_gst": totals["gst"],
        "expired": counts["expired"],
        "expiring_30": counts["urgent"],
        "expiring_90": counts["soon"],
        "low_stock": len(low_stock_items(conn)),
    }
