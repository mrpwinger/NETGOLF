#!/usr/bin/env bash
# Build script per Railway / Render.
# Installa dipendenze e compila le traduzioni Babel in .mo.
set -e

echo "[build] pip install"
pip install -r requirements.txt

echo "[build] compile traduzioni"
pybabel compile -d translations -f 2>/dev/null || echo "[build] nessun .po da compilare (saltato)"

echo "[build] done"
