"""
Endpoint di consultazione del DB campi (data/campi_slope_cr.json).

Equivalente degli endpoint /api/campi* del vecchio server.js, ma senza la
parte di refresh/scraping: quella è ora nel comando CLI `flask campi-refresh`
(vedi netgolf/campi/cli.py). Gli endpoint qui fanno solo lettura.
"""

from __future__ import annotations

from flask import current_app, jsonify
from flask_login import login_required

from ..config import AppConfig
from . import bp


@bp.get("")
@login_required
def index():
    """Tutto il DB campi (come /api/campi del vecchio server.js)."""
    gs_cfg: AppConfig = current_app.config["NETGOLF"]
    return jsonify(gs_cfg.campi_slope_cr)


@bp.get("/<string:nome>")
@login_required
def by_nome(nome: str):
    """Ricerca fuzzy sul nome del circolo (come /api/campi/:nome)."""
    gs_cfg: AppConfig = current_app.config["NETGOLF"]
    q = nome.upper().strip()
    circoli = gs_cfg.campi_slope_cr.get("circoli", [])
    matches = [
        c for c in circoli
        if q in c.get("nome", "").upper()
        or (c.get("id") and q in str(c["id"]))
    ]
    return jsonify(query=q, risultati=matches)


@bp.get("/<string:nome>/percorsi")
@login_required
def percorsi_di(nome: str):
    """Percorsi di uno specifico circolo (come /api/campi/:nome/percorsi)."""
    gs_cfg: AppConfig = current_app.config["NETGOLF"]
    q = nome.upper().strip()
    for c in gs_cfg.campi_slope_cr.get("circoli", []):
        if c.get("nome", "").upper() == q or str(c.get("id", "")) == q:
            return jsonify(
                circolo=c["nome"],
                percorsi=[
                    {
                        "id": p.get("percorso_id"),
                        "nome": p.get("nome_percorso"),
                        "tees": p.get("tees", []),
                    }
                    for p in c.get("percorsi", [])
                ],
            )
    return jsonify(percorsi=[])
