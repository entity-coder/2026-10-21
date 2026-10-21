# PharmaBill — Medical Store Billing

GST billing for pharmacies and medical stores, with the feature that
actually matters at a medicine counter: **batch and expiry-date tracking
with alerts**. Offline-first desktop app — no internet, no subscription,
your data stays on the shop PC.

## What it does

- **Medicine catalog, per batch** — name, batch no., expiry date, MRP,
  GST %, stock, and a low-stock threshold. The same medicine under two
  batch numbers is two rows, because expiry only makes sense per batch.
- **Fast billing** — type-to-search picker (earliest-expiry first, so old
  stock sells first), quantity capped at available stock, GST breakup
  (taxable value / CGST / SGST) on every bill, numbered invoices
  (PB-0001, PB-0002, …).
- **Printable invoices** — A4 invoice or 80mm thermal receipt, saved as
  PDF in `invoices/`.
- **Expiry alerts on the dashboard** — expired batches in red, expiring
  within 30 days in orange, within 90 days in amber, every morning at a
  glance.
- **Stock management** — stock in/out with a reason and a movement log;
  low-stock items flagged on the dashboard.
- **Day-wise sales report** — pick a date, see every bill with totals and
  GST collected, print a one-page PDF summary.
- **Backup & restore** — one-click timestamped zip of the database plus
  all invoice PDFs; restore always keeps a safety copy of the current
  database first.

## How to run

```bash
pip install -r requirements.txt
python main.py
```

On first run a local `pharmabill.db` SQLite database is created next to
the app. To test the data layer without the GUI:

```bash
python test_pharmabill.py
```

## Ship it as a Windows .exe

On the shop PC, run `build_exe.bat` once — it installs dependencies and
builds a one-click `dist\PharmaBill\PharmaBill.exe` with PyInstaller.
Your database, invoices, and backups live next to the .exe and are never
touched by a rebuild.

## Project structure

| File | What it is |
| --- | --- |
| `main.py` | Entry point |
| `db.py` | SQLite schema + all queries (catalog, billing, stock, reports) |
| `ui.py` | CustomTkinter screens: Dashboard, Medicines, Billing, Stock, Reports, Settings |
| `printing.py` | ReportLab invoice (A4 + 80mm thermal) and day-report PDFs |
| `backup.py` | Timestamped zip backup / restore with safety copy |
| `test_pharmabill.py` | Headless checks for the data + PDF layers |

## Deliberately no AI angle

This is money and medicine math: billing totals, GST splits, stock levels,
and expiry dates must be deterministic and auditable. An LLM in the
billing path could invent a price or a date, so there is none — the same
call made for the earlier shop apps (RepairDesk, DairyManager).

## Interview notes

**What is this project?**
A desktop billing app for a medical store, built with Python,
CustomTkinter, and SQLite. The core problem it solves is not billing —
any POS does that — it is *batch-level expiry tracking*: a pharmacy
loses money on expired stock, and expiry is a property of a batch, not
of a medicine name, so the data model is built around that.

**Why one row per batch instead of one row per medicine?**
Because two batches of Paracetamol 500 can have different expiry dates,
MRPs, and stock levels. Collapsing them into one row would make expiry
alerts meaningless — you could never say *which* stock expires when.
The trade-off is a slightly busier catalog, handled by search and by
sorting the billing picker earliest-expiry-first so older stock is sold
before newer stock (FIFO by expiry, which is also what a pharmacist
wants).

**Why is GST treated as inclusive in the price?**
Indian medicines are sold at MRP, and MRP is GST-inclusive by law. So
the bill shows the customer the MRP total, and the GST breakup is backed
out of it (taxable = amount × 100 / (100 + GST%)) rather than added on
top. CGST and SGST split the GST evenly, which is correct for an
intra-state sale; an inter-state sale would need IGST instead, and that
is the honest limitation — the app assumes one state.

**Why can stock never go negative?**
Two independent guards: the billing screen caps quantity at available
stock, and `db.create_bill` re-checks inside the same transaction before
writing. A bill that fails halfway (e.g. one line over-sells) writes
nothing and consumes no bill number — atomicity matters because a
half-written bill would corrupt both the ledger and the stock count.
The same "never negative" rule applies to manual stock-outs.

**Why can't a billed batch be deleted?**
Deleting it would orphan bill history — old invoices would reference a
batch that no longer exists. So delete is refused for billed batches,
while unbilled batches delete cleanly (their stock-log rows go with
them). This is a small example of referential integrity as a product
decision, not just a database feature.

**Why SQLite and a local .exe instead of a web app?**
A medical store counter needs to work during an internet outage, and the
shop owner should not pay a monthly fee for their own sales data.
SQLite is a single file, so backup is literally zipping one file plus
the invoice PDFs — which is why backup/restore is a three-line story
instead of a sync service. The trade-off is one machine, one user; there
is no multi-counter sync, and that is stated as a limitation rather than
hidden.

**How would I extend it?**
Barcode scanning (batch lookup by scanned code), supplier/purchase
entries with GST input credit, IGST support for inter-state sales,
near-expiry auto-discount suggestions, and a daily expiry digest. The
batch-row model already supports all of these without a schema redesign.
