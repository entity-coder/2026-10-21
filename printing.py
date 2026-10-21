"""PharmaBill — printable PDFs with reportlab.

Two formats, both generated from the same bill data:
* A4 — a full invoice with a GST breakup table (taxable / CGST / SGST).
* 80mm thermal — a narrow receipt for counter printers.

Both are saved next to the database (``invoices/`` folder) and opened with
the OS default viewer; the shop can print from there. Nothing here needs
the internet or a printer driver — the PDF is the printable artifact.
"""

from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from db import gst_split

OUT_DIR = Path(__file__).resolve().parent / "invoices"
SHOP_NAME = "PharmaBill Medical Store"  # overridden from Settings at runtime


def _fmt_inr(value):
    return f"Rs {value:,.2f}"


def _fmt_date(iso):
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def _draw_header(c, width, shop_name):
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, _y(c) - 0, shop_name)
    c.setFont("Helvetica", 9)
    c.drawCentredString(width / 2, _y(c) - 14, "GST Invoice  |  Thank you, get well soon!")
    c.line(10, _y(c) - 22, width - 10, _y(c) - 22)


def _y(c):
    """Current y tracked on the canvas object itself (simple page cursor)."""
    return c._pb_y


def _set_y(c, value):
    c._pb_y = value


def _line(c, text, x, font="Helvetica", size=9):
    c.setFont(font, size)
    c.drawString(x, _y(c), text)
    _set_y(c, _y(c) - 13)


def invoice_pdf(bill, items, shop_name=SHOP_NAME, thermal=False):
    """Write the invoice PDF; returns the file path as a string."""
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{bill['bill_no']}.pdf"
    width = 80 * mm if thermal else A4[0]
    height = 297 * mm if thermal else A4[1]
    c = canvas.Canvas(str(path), pagesize=(width, height))
    _set_y(c, height - 20)
    margin = 8 if thermal else 36

    _draw_header(c, width, shop_name)
    _set_y(c, _y(c) - 30)
    _line(c, f"Bill No: {bill['bill_no']}", margin, "Helvetica-Bold", 10)
    _line(c, f"Date: {_fmt_date(bill['bill_date'])}", margin)
    _line(c, f"Customer: {bill['customer']}"
             + (f"  |  {bill['phone']}" if bill["phone"] else ""), margin)
    _line(c, f"Payment: {bill['payment']}", margin)
    _set_y(c, _y(c) - 4)
    c.line(margin, _y(c), width - margin, _y(c))
    _set_y(c, _y(c) - 14)

    # Item table: name/batch, qty, rate, amount (+ expiry on A4).
    if thermal:
        _line(c, "ITEM            QTY  AMOUNT", margin, "Helvetica-Bold", 8)
        for it in items:
            _line(c, f"{it['name'][:14]:14} {it['qty']:>3} "
                     f"{it['amount']:>7.2f}", margin, "Helvetica", 8)
    else:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin, _y(c), "Medicine (Batch, Exp)")
        c.drawRightString(width - margin - 150, _y(c), "Qty")
        c.drawRightString(width - margin - 100, _y(c), "Rate")
        c.drawRightString(width - margin - 40, _y(c), "Amount")
        _set_y(c, _y(c) - 14)
        for it in items:
            label = (f"{it['name']} ({it['batch_no']}, "
                     f"exp {_fmt_date(it['expiry'])})")
            c.setFont("Helvetica", 9)
            c.drawString(margin, _y(c), label[:52])
            c.drawRightString(width - margin - 150, _y(c), str(it["qty"]))
            c.drawRightString(width - margin - 100, _y(c),
                              f"{it['rate']:.2f}")
            c.drawRightString(width - margin - 40, _y(c),
                              f"{it['amount']:.2f}")
            _set_y(c, _y(c) - 14)

    _set_y(c, _y(c) - 4)
    c.line(margin, _y(c), width - margin, _y(c))
    _set_y(c, _y(c) - 14)
    taxable = round(sum(gst_split(it["amount"], it["gst_pct"])[0]
                        for it in items), 2)
    cgst = round(sum(gst_split(it["amount"], it["gst_pct"])[2]
                     for it in items), 2)
    sgst = round(sum(gst_split(it["amount"], it["gst_pct"])[3]
                     for it in items), 2)
    for label, value in (("Taxable value", taxable), ("CGST", cgst),
                         ("SGST", sgst),
                         ("TOTAL (GST-inclusive)", bill["total"])):
        bold = label.startswith("TOTAL")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        c.drawString(margin, _y(c), label)
        c.drawRightString(width - margin, _y(c), _fmt_inr(value))
        _set_y(c, _y(c) - 14)
    _set_y(c, _y(c) - 6)
    _line(c, "Prices are MRP-inclusive of GST. No returns on refrigerated"
              " items.", margin, "Helvetica-Oblique", 8)
    c.showPage()
    c.save()
    return str(path)


def day_report_pdf(day, bills, totals, shop_name=SHOP_NAME):
    """One-page A4 summary of a day's bills; returns the file path."""
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"day-report-{day}.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    width = A4[0]
    _set_y(c, A4[1] - 20)
    _draw_header(c, width, shop_name)
    _set_y(c, _y(c) - 30)
    _line(c, f"Day report — {_fmt_date(day)}", 36, "Helvetica-Bold", 12)
    _set_y(c, _y(c) - 4)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(36, _y(c), "Bill")
    c.drawString(120, _y(c), "Customer")
    c.drawRightString(width - 150, _y(c), "Payment")
    c.drawRightString(width - 36, _y(c), "Amount")
    _set_y(c, _y(c) - 14)
    for b in bills:
        c.setFont("Helvetica", 9)
        c.drawString(36, _y(c), b["bill_no"])
        c.drawString(120, _y(c), b["customer"][:28])
        c.drawRightString(width - 150, _y(c), b["payment"])
        c.drawRightString(width - 36, _y(c), f"{b['total']:.2f}")
        _set_y(c, _y(c) - 14)
    _set_y(c, _y(c) - 4)
    c.line(36, _y(c), width - 36, _y(c))
    _set_y(c, _y(c) - 14)
    for label, value in (("Bills", totals["bills"]),
                         ("Total sales", _fmt_inr(totals["gross"])),
                         ("GST collected", _fmt_inr(totals["gst"]))):
        _line(c, f"{label}: {value}", 36, "Helvetica-Bold", 10)
    c.showPage()
    c.save()
    return str(path)


if __name__ == "__main__":
    print("Run the app (main.py) to generate real invoices.")
