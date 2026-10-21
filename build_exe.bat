@echo off
REM PharmaBill — one-click Windows .exe build (PyInstaller).
REM Run this once on the shop PC. The .exe lands in dist\PharmaBill\.
REM Your data (pharmabill.db, invoices\, backups\) lives next to the .exe
REM and is never touched by a rebuild.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --windowed --name PharmaBill ^
  --add-data "settings.json;." ^
  main.py
echo.
echo Done. Run dist\PharmaBill\PharmaBill.exe
pause
