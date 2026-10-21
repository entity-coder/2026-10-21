"""PharmaBill — backup & restore.

A backup is a timestamped .zip of the SQLite database plus the invoices
folder. Restore first makes a safety copy of the current database
(``pharmabill.db.bak-<timestamp>``) so a bad restore can never destroy the
live data — the same pattern RepairDesk/DairyManager use.
"""

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_FILE = BASE / "pharmabill.db"
INVOICES_DIR = BASE / "invoices"
BACKUP_DIR = BASE / "backups"


def _stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_backup():
    """Zip db + invoices. Returns the backup file path as a string."""
    BACKUP_DIR.mkdir(exist_ok=True)
    path = BACKUP_DIR / f"pharmabill-backup-{_stamp()}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_FILE.exists():
            zf.write(DB_FILE, DB_FILE.name)
        if INVOICES_DIR.exists():
            for pdf in INVOICES_DIR.glob("*.pdf"):
                zf.write(pdf, f"invoices/{pdf.name}")
    return str(path)


def restore_backup(zip_path):
    """Restore a backup made by create_backup. Returns the safety-copy path.

    Raises ValueError if the zip does not contain pharmabill.db.
    """
    with zipfile.ZipFile(zip_path) as zf:
        if "pharmabill.db" not in zf.namelist():
            raise ValueError("Not a PharmaBill backup (no pharmabill.db).")
        safety = BASE / f"pharmabill.db.bak-{_stamp()}"
        if DB_FILE.exists():
            shutil.copy2(DB_FILE, safety)
        else:
            safety = None
        zf.extract("pharmabill.db", BASE)
        for name in zf.namelist():
            if name.startswith("invoices/") and name.endswith(".pdf"):
                INVOICES_DIR.mkdir(exist_ok=True)
                zf.extract(name, BASE)
    return str(safety) if safety else None
