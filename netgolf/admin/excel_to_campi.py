"""
Converte il foglio Excel dei campi FIG in campi_slope_cr.json.
Usato dalla route admin /admin/campi/update.
"""
from __future__ import annotations

import io
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd


TEE_COLS: dict[str, tuple[int, int]] = {
    "NERO":    (3,  4),
    "BIANCO":  (5,  6),
    "GIALLO":  (7,  8),
    "VERDE":   (9,  10),
    "BLU":     (11, 12),
    "ROSSO":   (13, 14),
    "ARANCIO": (15, 16),
}


def excel_to_campi_json(excel_bytes: bytes) -> list[dict]:
    """Converte bytes di un .xlsx nel formato campi_slope_cr.json."""
    df = pd.read_excel(io.BytesIO(excel_bytes), header=None)
    data = df.iloc[2:].reset_index(drop=True)

    records = []
    for _, row in data.iterrows():
        circolo  = str(row[0]).strip() if pd.notna(row[0]) else ""
        percorso = str(row[1]).strip() if pd.notna(row[1]) else ""
        par_raw  = row[2]

        if not circolo or circolo == "nan":
            continue

        try:
            par = int(par_raw) if pd.notna(par_raw) else None
        except (ValueError, TypeError):
            par = None

        tees: dict[str, dict] = {}
        for color, (cr_col, slope_col) in TEE_COLS.items():
            cr_val    = row[cr_col]    if cr_col    < len(row) and pd.notna(row[cr_col])    else None
            slope_val = row[slope_col] if slope_col < len(row) and pd.notna(row[slope_col]) else None
            if cr_val is not None or slope_val is not None:
                tees[color] = {
                    "cr":    float(cr_val)    if cr_val    is not None else None,
                    "slope": int(slope_val)   if slope_val is not None else None,
                }

        if tees:
            records.append({
                "circolo":  circolo,
                "percorso": percorso,
                "par":      par,
                "tees":     tees,
            })

    return records


def update_campi_json_file(excel_bytes: bytes, json_path: str | Path) -> tuple[int, str]:
    """
    Aggiorna campi_slope_cr.json dai bytes dell'Excel.
    Crea un backup del file esistente prima di sovrascrivere.

    Returns:
        (n_record, backup_path)
    """
    json_path = Path(json_path)

    backup_path = ""
    if json_path.exists():
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = str(json_path.with_suffix(f".bak_{ts}.json"))
        shutil.copy2(json_path, backup_path)

    records = excel_to_campi_json(excel_bytes)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return len(records), backup_path
