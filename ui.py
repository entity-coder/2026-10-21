"""PharmaBill — CustomTkinter desktop UI.

Screens (left sidebar navigation):
* Dashboard — today's sales strip + expiry alerts (expired / <=30d / <=90d)
  + low-stock list. This is the screen the shop opens every morning.
* Medicines — batch catalog: add / edit / delete, live search.
* Billing — type-to-search picker (earliest expiry first, so old stock
  sells first), quantity capped at available stock, GST breakup, printable
  invoice (A4 or 80mm thermal PDF).
* Stock — stock in/out with a reason, plus the movement log.
* Reports — day-wise sales: pick a date, see bills + totals, print a PDF.
* Settings — shop name (printed on invoices) + backup / restore.

Everything runs offline against the local SQLite database (db.py).
"""

import json
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import backup as backup_mod
import db
import printing

BASE = Path(__file__).resolve().parent
SETTINGS_FILE = BASE / "settings.json"

STATUS_COLORS = {"expired": "#c0392b", "urgent": "#e67e22",
                 "soon": "#b7950b", "ok": "#27ae60"}


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def shop_name():
    return load_settings().get("shop_name", "PharmaBill Medical Store")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PharmaBill — Medical Store Billing")
        self.geometry("1100x680")
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("green")

        self.conn = db.connect()
        db.init_db()

        self.sidebar = ctk.CTkFrame(self, width=170, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        ctk.CTkLabel(self.sidebar, text="PharmaBill",
                     font=("Arial", 20, "bold")).pack(pady=(20, 4))
        ctk.CTkLabel(self.sidebar, text="Medical store billing",
                     font=("Arial", 11)).pack(pady=(0, 20))

        self.screens = {}
        for name in ("Dashboard", "Medicines", "Billing", "Stock",
                     "Reports", "Settings"):
            btn = ctk.CTkButton(self.sidebar, text=name,
                                command=lambda n=name: self.show(n))
            btn.pack(padx=12, pady=4, fill="x")
        self.content = ctk.CTkFrame(self, corner_radius=0)
        self.content.pack(side="left", fill="both", expand=True)
        self.current = None
        self.show("Dashboard")

    # ------------------------------------------------------------ navigation
    def show(self, name):
        if self.current is not None:
            self.current.pack_forget()
        if name not in self.screens:
            self.screens[name] = {
                "Dashboard": DashboardScreen,
                "Medicines": MedicinesScreen,
                "Billing": BillingScreen,
                "Stock": StockScreen,
                "Reports": ReportsScreen,
                "Settings": SettingsScreen,
            }[name](self.content, self)
        self.current = self.screens[name]
        self.current.refresh()
        self.current.pack(fill="both", expand=True)

    def destroy(self):
        try:
            self.conn.close()
        finally:
            super().destroy()


class Screen(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.conn = app.conn

    def refresh(self):
        pass

    def title(self, text):
        ctk.CTkLabel(self, text=text, font=("Arial", 18, "bold")).pack(
            anchor="w", padx=16, pady=(14, 8))


class DashboardScreen(Screen):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.title("Dashboard — today at a glance")
        self.stats = ctk.CTkFrame(self)
        self.stats.pack(fill="x", padx=16)
        self.alerts = ctk.CTkScrollableFrame(self, height=420)
        self.alerts.pack(fill="both", expand=True, padx=16, pady=12)

    def refresh(self):
        for w in self.stats.winfo_children():
            w.destroy()
        for w in self.alerts.winfo_children():
            w.destroy()
        s = db.dashboard_stats(self.conn)
        cards = [("Bills today", s["today_bills"]),
                 ("Sales today", f"Rs {s['today_sales']:,.2f}"),
                 ("GST today", f"Rs {s['today_gst']:,.2f}"),
                 ("Expired batches", s["expired"]),
                 ("Expiring ≤30 days", s["expiring_30"]),
                 ("Expiring ≤90 days", s["expiring_90"]),
                 ("Low-stock items", s["low_stock"])]
        for i, (label, value) in enumerate(cards):
            frame = ctk.CTkFrame(self.stats)
            frame.grid(row=0, column=i, padx=6, pady=10, sticky="nsew")
            ctk.CTkLabel(frame, text=str(value),
                         font=("Arial", 16, "bold")).pack(padx=10, pady=(8, 0))
            ctk.CTkLabel(frame, text=label, font=("Arial", 11)).pack(
                padx=10, pady=(0, 8))
        ctk.CTkLabel(self.alerts, text="Expiry alerts (within 90 days)",
                     font=("Arial", 14, "bold")).pack(anchor="w", pady=4)
        rows = db.expiry_alerts(self.conn)
        if not rows:
            ctk.CTkLabel(self.alerts, text="No batches expiring in the next"
                         " 90 days.").pack(anchor="w")
        for r in rows:
            status = db.expiry_status(r["expiry"])
            ctk.CTkLabel(
                self.alerts,
                text=f"{r['name']}  |  batch {r['batch_no']}  |  "
                     f"exp {r['expiry']}  |  stock {r['stock']}  |  "
                     f"{status.upper()}",
                text_color=STATUS_COLORS[status]).pack(anchor="w")
        ctk.CTkLabel(self.alerts, text="Low stock",
                     font=("Arial", 14, "bold")).pack(anchor="w", pady=(14, 4))
        for r in db.low_stock_items(self.conn):
            ctk.CTkLabel(
                self.alerts,
                text=f"{r['name']}  |  batch {r['batch_no']}  |  "
                     f"stock {r['stock']} (alert at {r['low_stock_at']})"
            ).pack(anchor="w")


class MedicineDialog(ctk.CTkToplevel):
    """Add / edit one batch. Validates dates and numbers before saving."""

    def __init__(self, master, conn, row=None):
        super().__init__(master)
        self.conn, self.row = conn, row
        self.title("Edit batch" if row else "Add medicine batch")
        self.geometry("420x430")
        self.fields = {}
        specs = [("name", "Medicine name", row["name"] if row else ""),
                 ("batch_no", "Batch no.", row["batch_no"] if row else ""),
                 ("expiry", "Expiry (YYYY-MM-DD)",
                  row["expiry"] if row else ""),
                 ("mrp", "MRP (Rs)", str(row["mrp"]) if row else ""),
                 ("gst_pct", "GST %", str(row["gst_pct"]) if row else "12"),
                 ("stock", "Opening stock",
                  str(row["stock"]) if row else "0"),
                 ("low_stock_at", "Low-stock alert at",
                  str(row["low_stock_at"]) if row else "5")]
        for key, label, value in specs:
            ctk.CTkLabel(self, text=label).pack(anchor="w", padx=16,
                                               pady=(8, 0))
            entry = ctk.CTkEntry(self)
            entry.insert(0, value)
            entry.pack(fill="x", padx=16)
            self.fields[key] = entry
        ctk.CTkButton(self, text="Save", command=self.save).pack(pady=16)

    def save(self):
        try:
            values = {k: e.get().strip() for k, e in self.fields.items()}
            date.fromisoformat(values["expiry"])  # raises on bad format
            mrp, gst, stock, low = (float(values["mrp"]),
                                    float(values["gst_pct"]),
                                    int(values["stock"]),
                                    int(values["low_stock_at"]))
            if mrp < 0 or gst < 0 or stock < 0 or low < 0:
                raise ValueError("Negative numbers are not allowed.")
            if not values["name"] or not values["batch_no"]:
                raise ValueError("Name and batch no. are required.")
            if self.row:
                db.update_medicine(self.conn, self.row["id"],
                                   name=values["name"],
                                   batch_no=values["batch_no"],
                                   expiry=values["expiry"], mrp=mrp,
                                   gst_pct=gst, stock=stock, low_stock_at=low)
            else:
                db.add_medicine(self.conn, values["name"],
                                values["batch_no"], values["expiry"], mrp,
                                gst, stock, low)
        except (ValueError, OverflowError) as exc:
            messagebox.showerror("Cannot save", str(exc), parent=self)
            return
        except Exception as exc:  # e.g. duplicate name+batch
            messagebox.showerror("Cannot save", str(exc), parent=self)
            return
        self.destroy()


class MedicinesScreen(Screen):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.title("Medicines — batch catalog")
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=16)
        self.search = ctk.CTkEntry(top, placeholder_text="Search name/batch…")
        self.search.pack(side="left", fill="x", expand=True, padx=6, pady=8)
        self.search.bind("<KeyRelease>", lambda _e: self.refresh())
        ctk.CTkButton(top, text="+ Add batch",
                      command=lambda: self.open_dialog(None)).pack(
                          side="right", padx=6)
        self.list = ctk.CTkScrollableFrame(self)
        self.list.pack(fill="both", expand=True, padx=16, pady=12)
        self.selected = None

    def open_dialog(self, row):
        dlg = MedicineDialog(self, self.conn, row)
        dlg.wait_window()
        self.refresh()

    def refresh(self):
        for w in self.list.winfo_children():
            w.destroy()
        rows = db.search_medicines(self.conn, self.search.get())
        if not rows:
            ctk.CTkLabel(self.list, text="No batches yet — add the first"
                         " one above.").pack(anchor="w")
            return
        for r in rows:
            status = db.expiry_status(r["expiry"])
            frame = ctk.CTkFrame(self.list)
            frame.pack(fill="x", pady=3)
            ctk.CTkLabel(
                frame,
                text=f"{r['name']}  |  batch {r['batch_no']}  |  "
                     f"exp {r['expiry']} ({status})  |  "
                     f"MRP Rs {r['mrp']:.2f}  |  GST {r['gst_pct']}%  |  "
                     f"stock {r['stock']}",
                text_color=STATUS_COLORS[status]).pack(side="left", padx=8)
            ctk.CTkButton(frame, text="Edit", width=60,
                          command=lambda row=r: self.open_dialog(row)).pack(
                              side="right", padx=4)
            ctk.CTkButton(frame, text="Delete", width=60, fg_color="#c0392b",
                          command=lambda row=r: self.delete(row)).pack(
                              side="right", padx=4)

    def delete(self, row):
        if not messagebox.askyesno("Delete batch",
                                   f"Delete {row['name']} ({row['batch_no']})?",
                                   parent=self):
            return
        try:
            db.delete_medicine(self.conn, row["id"])
        except ValueError as exc:
            messagebox.showerror("Cannot delete", str(exc), parent=self)
            return
        self.refresh()


class BillingScreen(Screen):
    """Fast billing: search picker -> qty -> line list -> save & print.

    The picker lists batches earliest-expiry first so older stock is sold
    before newer stock. Quantities are capped at available stock both here
    and again in db.create_bill, so stock can never go negative.
    """

    def __init__(self, master, app):
        super().__init__(master, app)
        self.title("Billing")
        self.lines = []  # dicts: medicine_id, name, batch, expiry, qty, rate, gst

        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=16)
        self.customer = ctk.CTkEntry(form, placeholder_text="Customer name")
        self.customer.pack(side="left", padx=6, pady=8, fill="x", expand=True)
        self.phone = ctk.CTkEntry(form, placeholder_text="Phone (optional)")
        self.phone.pack(side="left", padx=6, pady=8)
        self.payment = ctk.CTkOptionMenu(form, values=["Cash", "UPI", "Card"])
        self.payment.pack(side="left", padx=6, pady=8)

        picker = ctk.CTkFrame(self)
        picker.pack(fill="x", padx=16, pady=(8, 0))
        self.search = ctk.CTkEntry(picker,
                                  placeholder_text="Search medicine / batch…")
        self.search.pack(side="left", fill="x", expand=True, padx=6, pady=8)
        self.search.bind("<KeyRelease>", lambda _e: self.refresh_picker())
        self.qty = ctk.CTkEntry(picker, width=80, placeholder_text="Qty")
        self.qty.pack(side="left", padx=6, pady=8)
        ctk.CTkButton(picker, text="Add line", width=90,
                      command=self.add_line).pack(side="left", padx=6)
        self.picker = ctk.CTkScrollableFrame(self, height=110)
        self.picker.pack(fill="x", padx=16, pady=4)
        self.picked = None

        self.line_list = ctk.CTkScrollableFrame(self)
        self.line_list.pack(fill="both", expand=True, padx=16, pady=8)
        bottom = ctk.CTkFrame(self)
        bottom.pack(fill="x", padx=16, pady=(0, 12))
        self.totals = ctk.CTkLabel(bottom, text="", font=("Arial", 13, "bold"))
        self.totals.pack(side="left", padx=8, pady=8)
        self.paper = ctk.CTkOptionMenu(bottom, values=["A4", "80mm thermal"])
        self.paper.pack(side="right", padx=6, pady=8)
        ctk.CTkButton(bottom, text="Save bill & print",
                      command=self.save_bill).pack(side="right", padx=6,
                                                   pady=8)
        ctk.CTkButton(bottom, text="Clear", fg_color="#7f8c8d",
                      command=self.clear).pack(side="right", padx=6, pady=8)

    def refresh_picker(self):
        for w in self.picker.winfo_children():
            w.destroy()
        query = self.search.get().strip()
        if not query:
            return
        for r in db.search_medicines(self.conn, query)[:8]:
            status = db.expiry_status(r["expiry"])
            btn = ctk.CTkButton(
                self.picker, fg_color="transparent",
                text_color=STATUS_COLORS[status],
                text=f"{r['name']}  |  batch {r['batch_no']}  |  "
                     f"exp {r['expiry']}  |  MRP Rs {r['mrp']:.2f}  |  "
                     f"stock {r['stock']}",
                anchor="w",
                command=lambda row=r: self.pick(row))
            btn.pack(fill="x", pady=1)

    def pick(self, row):
        self.picked = row
        self.search.delete(0, "end")
        self.search.insert(0, f"{row['name']} ({row['batch_no']})")
        for w in self.picker.winfo_children():
            w.destroy()
        self.qty.focus_set()

    def add_line(self):
        if self.picked is None:
            messagebox.showwarning("Pick a batch",
                                   "Search and pick a batch first.",
                                   parent=self)
            return
        try:
            qty = int(self.qty.get())
        except ValueError:
            messagebox.showerror("Bad quantity", "Enter a whole number.",
                                 parent=self)
            return
        if qty <= 0:
            messagebox.showerror("Bad quantity", "Quantity must be positive.",
                                 parent=self)
            return
        already = sum(l["qty"] for l in self.lines
                      if l["medicine_id"] == self.picked["id"])
        if qty + already > self.picked["stock"]:
            messagebox.showerror(
                "Not enough stock",
                f"Only {self.picked['stock'] - already} left of "
                f"{self.picked['name']} ({self.picked['batch_no']}).",
                parent=self)
            return
        self.lines.append({"medicine_id": self.picked["id"],
                           "name": self.picked["name"],
                           "batch_no": self.picked["batch_no"],
                           "expiry": self.picked["expiry"],
                           "qty": qty, "rate": self.picked["mrp"],
                           "gst_pct": self.picked["gst_pct"]})
        self.picked = None
        self.search.delete(0, "end")
        self.qty.delete(0, "end")
        self.refresh_lines()

    def refresh_lines(self):
        for w in self.line_list.winfo_children():
            w.destroy()
        gross = gst_total = 0.0
        for i, line in enumerate(self.lines):
            amount = round(line["qty"] * line["rate"], 2)
            _, gst, _, _ = db.gst_split(amount, line["gst_pct"])
            gross += amount
            gst_total += gst
            frame = ctk.CTkFrame(self.line_list)
            frame.pack(fill="x", pady=2)
            ctk.CTkLabel(
                frame,
                text=f"{line['name']} ({line['batch_no']})  x {line['qty']}  "
                     f"@ Rs {line['rate']:.2f}  =  Rs {amount:.2f}").pack(
                         side="left", padx=8)
            ctk.CTkButton(frame, text="Remove", width=70, fg_color="#c0392b",
                          command=lambda idx=i: self.remove_line(idx)).pack(
                              side="right", padx=4)
        self.totals.configure(
            text=f"Gross: Rs {gross:.2f}   |   GST (incl.): "
                 f"Rs {gst_total:.2f}   |   Payable: Rs {gross:.2f}")

    def remove_line(self, idx):
        self.lines.pop(idx)
        self.refresh_lines()

    def clear(self):
        self.lines = []
        self.picked = None
        self.search.delete(0, "end")
        self.qty.delete(0, "end")
        self.customer.delete(0, "end")
        self.phone.delete(0, "end")
        self.refresh_lines()

    def save_bill(self):
        if not self.lines:
            messagebox.showwarning("Empty bill", "Add at least one line.",
                                   parent=self)
            return
        try:
            bill_no, gross, gst = db.create_bill(
                self.conn, self.customer.get(), self.phone.get(),
                self.payment.get(),
                [{"medicine_id": l["medicine_id"], "qty": l["qty"],
                  "rate": l["rate"]} for l in self.lines])
        except ValueError as exc:
            messagebox.showerror("Cannot save bill", str(exc), parent=self)
            return
        bill, items = db.get_bill(self.conn, bill_no)
        path = printing.invoice_pdf(
            bill, items, shop_name(),
            thermal=(self.paper.get() == "80mm thermal"))
        messagebox.showinfo("Bill saved",
                            f"{bill_no} saved (Rs {gross:.2f}, GST "
                            f"Rs {gst:.2f}).\nInvoice PDF: {path}",
                            parent=self)
        self.clear()

    def refresh(self):
        self.refresh_lines()


class StockScreen(Screen):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.title("Stock in / out")
        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=16)
        self.search = ctk.CTkEntry(form,
                                   placeholder_text="Search medicine / batch…")
        self.search.pack(side="left", fill="x", expand=True, padx=6, pady=8)
        self.search.bind("<KeyRelease>", lambda _e: self.refresh_picker())
        self.qty = ctk.CTkEntry(form, width=80, placeholder_text="+/- qty")
        self.qty.pack(side="left", padx=6, pady=8)
        self.reason = ctk.CTkEntry(form, placeholder_text="Reason (e.g. purchase, damage)")
        self.reason.pack(side="left", fill="x", expand=True, padx=6, pady=8)
        ctk.CTkButton(form, text="Apply", command=self.apply).pack(
            side="left", padx=6)
        self.picker = ctk.CTkScrollableFrame(self, height=110)
        self.picker.pack(fill="x", padx=16, pady=4)
        self.picked = None
        self.picked_label = ctk.CTkLabel(self, text="No batch selected.")
        self.picked_label.pack(anchor="w", padx=16)
        ctk.CTkLabel(self, text="Recent movements",
                     font=("Arial", 14, "bold")).pack(anchor="w", padx=16,
                                                      pady=(12, 4))
        self.log = ctk.CTkScrollableFrame(self)
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    def refresh_picker(self):
        for w in self.picker.winfo_children():
            w.destroy()
        query = self.search.get().strip()
        if not query:
            return
        for r in db.search_medicines(self.conn, query)[:8]:
            ctk.CTkButton(
                self.picker, fg_color="transparent", anchor="w",
                text=f"{r['name']}  |  batch {r['batch_no']}  |  "
                     f"exp {r['expiry']}  |  stock {r['stock']}",
                command=lambda row=r: self.pick(row)).pack(fill="x", pady=1)

    def pick(self, row):
        self.picked = row
        self.picked_label.configure(
            text=f"Selected: {row['name']} ({row['batch_no']}) — "
                 f"stock {row['stock']}")
        for w in self.picker.winfo_children():
            w.destroy()

    def apply(self):
        if self.picked is None:
            messagebox.showwarning("Pick a batch",
                                   "Search and pick a batch first.",
                                   parent=self)
            return
        try:
            delta = int(self.qty.get())
        except ValueError:
            messagebox.showerror("Bad quantity", "Enter a whole number"
                                 " (negative for stock-out).", parent=self)
            return
        try:
            new_stock = db.adjust_stock(self.conn, self.picked["id"], delta,
                                        self.reason.get() or "Manual adjust")
        except ValueError as exc:
            messagebox.showerror("Cannot adjust", str(exc), parent=self)
            return
        messagebox.showinfo("Stock updated",
                            f"New stock: {new_stock}", parent=self)
        self.qty.delete(0, "end")
        self.refresh()

    def refresh(self):
        for w in self.log.winfo_children():
            w.destroy()
        rows = self.conn.execute(
            "SELECT s.changed_on, s.delta, s.reason, m.name, m.batch_no"
            " FROM stock_log s JOIN medicines m ON m.id = s.medicine_id"
            " ORDER BY s.id DESC LIMIT 100").fetchall()
        for r in rows:
            sign = "+" if r["delta"] > 0 else ""
            ctk.CTkLabel(
                self.log,
                text=f"{r['changed_on']}  |  {r['name']} ({r['batch_no']})"
                     f"  |  {sign}{r['delta']}  |  {r['reason']}").pack(
                         anchor="w")


class ReportsScreen(Screen):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.title("Day-wise sales report")
        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=16)
        ctk.CTkLabel(form, text="Date (YYYY-MM-DD):").pack(side="left",
                                                           padx=6, pady=8)
        self.day = ctk.CTkEntry(form, width=130)
        self.day.insert(0, date.today().isoformat())
        self.day.pack(side="left", padx=6, pady=8)
        ctk.CTkButton(form, text="Show", command=self.refresh).pack(
            side="left", padx=6)
        ctk.CTkButton(form, text="Print PDF",
                      command=self.print_pdf).pack(side="left", padx=6)
        self.summary = ctk.CTkLabel(self, text="",
                                    font=("Arial", 13, "bold"))
        self.summary.pack(anchor="w", padx=16, pady=8)
        self.list = ctk.CTkScrollableFrame(self)
        self.list.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    def _day(self):
        try:
            return date.fromisoformat(self.day.get().strip()).isoformat()
        except ValueError:
            messagebox.showerror("Bad date", "Use YYYY-MM-DD.", parent=self)
            return None

    def refresh(self):
        day = self._day()
        if day is None:
            return
        bills, totals = db.day_report(self.conn, day)
        self.summary.configure(
            text=f"{day}: {totals['bills']} bills  |  sales "
                 f"Rs {totals['gross']:,.2f}  |  GST "
                 f"Rs {totals['gst']:,.2f}")
        for w in self.list.winfo_children():
            w.destroy()
        for b in bills:
            ctk.CTkLabel(
                self.list,
                text=f"{b['bill_no']}  |  {b['customer']}  |  "
                     f"{b['payment']}  |  Rs {b['total']:.2f}").pack(
                         anchor="w")

    def print_pdf(self):
        day = self._day()
        if day is None:
            return
        bills, totals = db.day_report(self.conn, day)
        path = printing.day_report_pdf(day, bills, totals, shop_name())
        messagebox.showinfo("Report printed", f"Saved: {path}", parent=self)


class SettingsScreen(Screen):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.title("Settings & backup")
        ctk.CTkLabel(self, text="Shop name (printed on invoices):").pack(
            anchor="w", padx=16)
        self.shop = ctk.CTkEntry(self, width=400)
        self.shop.insert(0, shop_name())
        self.shop.pack(anchor="w", padx=16, pady=6)
        ctk.CTkButton(self, text="Save shop name",
                      command=self.save_shop).pack(anchor="w", padx=16,
                                                   pady=6)
        ctk.CTkLabel(self, text="Backup & restore",
                     font=("Arial", 14, "bold")).pack(anchor="w", padx=16,
                                                      pady=(20, 4))
        ctk.CTkLabel(
            self,
            text="A backup zips the database and all invoice PDFs. Restore"
                 " always keeps a safety copy of the current database first."
        ).pack(anchor="w", padx=16)
        ctk.CTkButton(self, text="Create backup now",
                      command=self.do_backup).pack(anchor="w", padx=16,
                                                   pady=6)
        ctk.CTkButton(self, text="Restore from backup…",
                      command=self.do_restore).pack(anchor="w", padx=16,
                                                    pady=6)

    def save_shop(self):
        data = load_settings()
        data["shop_name"] = self.shop.get().strip() or shop_name()
        save_settings(data)
        messagebox.showinfo("Saved", "Shop name updated.", parent=self)

    def do_backup(self):
        path = backup_mod.create_backup()
        messagebox.showinfo("Backup created", path, parent=self)

    def do_restore(self):
        path = filedialog.askopenfilename(
            title="Choose a PharmaBill backup (.zip)",
            filetypes=[("Zip files", "*.zip")])
        if not path:
            return
        try:
            safety = backup_mod.restore_backup(path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Restore failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Restored",
            "Backup restored. Restart the app to reload.\n"
            + (f"Safety copy: {safety}" if safety else ""),
            parent=self)


def main():
    db.init_db()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
