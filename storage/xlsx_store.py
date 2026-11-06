"""
The "true log" -- an .xlsx workbook that mirrors snipes.json in a format
that's actually pleasant to open and skim (this replaced an older Google
Sheets integration; see scripts/resync.py). Every classified photo gets one
row, upserted by its entry id so re-classifying a photo updates its row in
place instead of duplicating it.

Columns (in order): Date, Time, Sniper, Sniped, Group Snipe, Notable, Kind,
Photo Path, Permalink, ID. The ID column is hidden from the live-log view
in the webapp but is what upsert/delete match rows on.

Writes go to a temp path and are swapped in with os.replace so a reader
never sees a half-written workbook.
"""
import os
import threading
import uuid

from openpyxl import Workbook, load_workbook

import config

HEADER = [
    "Date", "Time", "Sniper", "Sniped", "Group Snipe", "Notable",
    "Kind", "Photo Path", "Permalink", "ID",
]
ID_COL = len(HEADER)  # 1-indexed column number of the ID column

_lock = threading.Lock()


def _open_or_create() -> Workbook:
    if os.path.exists(config.XLSX_PATH):
        return load_workbook(config.XLSX_PATH)
    wb = Workbook()
    ws = wb.active
    ws.title = "Snipes"
    ws.append(HEADER)
    return wb


def _save(wb: Workbook):
    os.makedirs(os.path.dirname(config.XLSX_PATH) or ".", exist_ok=True)
    tmp_path = os.path.join(
        os.path.dirname(config.XLSX_PATH) or ".",
        f".{os.path.basename(config.XLSX_PATH)}.{uuid.uuid4().hex}.tmp",
    )
    wb.save(tmp_path)
    os.replace(tmp_path, config.XLSX_PATH)


def upsert_true_log_row(entry_id: str, row: list):
    """Writes `row` (must match HEADER's column order, including the ID as
    the last element) to entry_id's row, creating it if it doesn't exist."""
    with _lock:
        wb = _open_or_create()
        ws = wb.active
        for r in range(2, ws.max_row + 1):
            if ws.cell(row=r, column=ID_COL).value == entry_id:
                for c, value in enumerate(row, start=1):
                    ws.cell(row=r, column=c, value=value)
                _save(wb)
                return
        ws.append(row)
        _save(wb)


def delete_true_log_row(entry_id: str):
    with _lock:
        wb = _open_or_create()
        ws = wb.active
        for r in range(2, ws.max_row + 1):
            if ws.cell(row=r, column=ID_COL).value == entry_id:
                ws.delete_rows(r, 1)
                _save(wb)
                return


def read_true_log_rows() -> list:
    """Every data row (header excluded) as a list of cell values, in the
    same column order as HEADER."""
    with _lock:
        wb = _open_or_create()
        ws = wb.active
        rows = []
        for r in range(2, ws.max_row + 1):
            row = [ws.cell(row=r, column=c).value for c in range(1, len(HEADER) + 1)]
            if any(v is not None for v in row):
                rows.append(row)
        return rows
