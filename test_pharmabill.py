"""Headless checks for PharmaBill's data + PDF layers (stdlib + reportlab).

Run:  python test_pharmabill.py
Uses a throwaway database in the system temp dir — never touches the
real pharmabill.db.
"""

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import printing

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def main():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "test.db")
    db.init_db(path)
    conn = db.connect(path)
    today = date.today()

    # --- catalog: one medicine, two batches, expiry ordering
    m1 = db.add_medicine(conn, "Paracetamol 500", "B101",
                         (today + timedelta(days=20)).isoformat(), 12.50, 12,
                         100, 10)
    m2 = db.add_medicine(conn, "Paracetamol 500", "B102",
                         (today + timedelta(days=200)).isoformat(), 13.00, 12,
                         50, 10)
    rows = db.search_medicines(conn, "paracetamol")
    check("search finds both batches, earliest expiry first",
          len(rows) == 2 and rows[0]["batch_no"] == "B101")

    # --- expiry statuses incl. boundaries
    check("status expired/urgent/soon/ok",
          db.expiry_status((today - timedelta(days=1)).isoformat(), today)
          == "expired"
          and db.expiry_status((today + timedelta(days=30)).isoformat(),
                               today) == "urgent"
          and db.expiry_status((today + timedelta(days=90)).isoformat(),
                               today) == "soon"
          and db.expiry_status((today + timedelta(days=91)).isoformat(),
                               today) == "ok")
    alerts = db.expiry_alerts(conn, today)
    check("90-day alert window catches only the near batch",
          len(alerts) == 1 and alerts[0]["batch_no"] == "B101")

    # --- GST-inclusive split: 12% on Rs 125 -> taxable 111.61, gst 13.39
    taxable, gst, cgst, sgst = db.gst_split(125.00, 12)
    check("gst split sums back to amount",
          abs(taxable + gst - 125.00) < 0.01 and abs(cgst + sgst - gst) < 0.01
          and taxable == 111.61)

    # --- billing: stock decrement, bill numbering, over-sell refused
    bill_no, gross, gst_total = db.create_bill(
        conn, "Walk-in", "", "Cash",
        [{"medicine_id": m1, "qty": 10, "rate": 12.50}])
    check("first bill is PB-0001, gross 125.00",
          bill_no == "PB-0001" and gross == 125.00)
    check("stock decremented 100 -> 90",
          db.get_medicine(conn, m1)["stock"] == 90)
    try:
        db.create_bill(conn, "X", "", "Cash",
                       [{"medicine_id": m1, "qty": 9999, "rate": 12.50}])
        check("over-sell refused", False)
    except ValueError:
        check("over-sell refused", True)
    check("failed bill did not consume a bill number",
          db.next_bill_no(conn) == "PB-0002")

    # --- stock adjust: negative blocked, log recorded
    db.adjust_stock(conn, m2, 25, "Purchase", today.isoformat())
    check("stock-in 50 -> 75", db.get_medicine(conn, m2)["stock"] == 75)
    try:
        db.adjust_stock(conn, m2, -1000, "Damage")
        check("stock-out below zero refused", False)
    except ValueError:
        check("stock-out below zero refused", True)

    # --- delete guard: billed batch cannot be deleted
    try:
        db.delete_medicine(conn, m1)
        check("billed batch delete refused", False)
    except ValueError:
        check("billed batch delete refused", True)
    db.delete_medicine(conn, m2)
    check("unbilled batch deletes cleanly",
          db.get_medicine(conn, m2) is None)

    # --- day report + dashboard
    bills, totals = db.day_report(conn, today.isoformat())
    check("day report: 1 bill, gross 125.00",
          len(bills) == 1 and totals["gross"] == 125.00)
    stats = db.dashboard_stats(conn, today)
    check("dashboard counts (1 expiring<=30, low-stock 0)",
          stats["today_bills"] == 1 and stats["expiring_30"] == 1
          and stats["low_stock"] == 0)

    # --- low-stock detection
    db.add_medicine(conn, "Amoxicillin 250", "A9",
                    (today + timedelta(days=300)).isoformat(), 45.00, 12,
                    3, 5)
    check("low-stock list catches stock<=threshold",
          len(db.low_stock_items(conn)) == 1)

    # --- PDFs: A4 invoice, thermal invoice, day report — all valid PDFs
    bill, items = db.get_bill(conn, "PB-0001")
    for thermal in (False, True):
        p = printing.invoice_pdf(bill, items, "Test Store", thermal=thermal)
        with open(p, "rb") as f:
            check(f"invoice pdf ({'thermal' if thermal else 'A4'}) valid",
                  f.read(5) == b"%PDF-")
    bills, totals = db.day_report(conn, today.isoformat())
    p = printing.day_report_pdf(today.isoformat(), bills, totals, "Test Store")
    with open(p, "rb") as f:
        check("day report pdf valid", f.read(5) == b"%PDF-")

    conn.close()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
