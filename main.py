"""PharmaBill — medical store billing with batch & expiry tracking.

Run:
    pip install -r requirements.txt
    python main.py

On first run a local ``pharmabill.db`` SQLite database is created next to
this file. Everything works offline; invoices are saved as PDFs in
``invoices/``.
"""

import db
from ui import main

if __name__ == "__main__":
    db.init_db()
    main()
