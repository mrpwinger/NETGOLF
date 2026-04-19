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


def _records(cfg: AppConfig) -> list[dict]:
    """Restituisce la lista piatta di record dal nuovo campi_slope_cr.json."""
    data = cfg.campi_slope_cr
    # Supporta sia il nuovo formato (lista) che il vecchio (dict con 'circoli')
    if isinstance(data, list):
        return data
    return data.get("circoli", [])


def _group_by_circolo(records: list[dict]) -> list[dict]:
    """
    Raggruppa i record piatti per circolo, producendo la struttura:
    [{nome, percorsi: [{id, nome, par, tees:[{tee_nome, cr, slope}]}]}]
    """
    from collections import OrderedDict
    groups: OrderedDict[str, list] = OrderedDict()
    for i, r in enumerate(records):
        nome = r.get("circolo", "")
        if not nome:
            continue
        if nome not in groups:
            groups[nome] = []
        # Converti tees da dict {COLORE: {cr, slope}} a lista [{tee_nome, cr, slope, par}]
        tees_dict = r.get("tees", {})
        tees_list = [
            {"tee_nome": colore, "cr": v.get("cr"), "slope": v.get("slope")}
            for colore, v in tees_dict.items()
            if v.get("cr") or v.get("slope")
        ]
        groups[nome].append({
            "id": i,
            "nome": r.get("percorso", ""),
            "par": r.get("par"),
            "tees": tees_list,
        })
    return [{"nome": nome, "percorsi": percorsi} for nome, percorsi in groups.items()]


@bp.get("")
@login_required
def index():
    """Tutto il DB campi raggruppato per circolo."""
    cfg: AppConfig = current_app.config["NETGOLF"]
    records = _records(cfg)
    circoli = _group_by_circolo(records)
    return jsonify(
        totale=len(circoli),
        aggiornato="2026",
        circoli=circoli,
    )


@bp.get("/<string:nome>")
@login_required
def by_nome(nome: str):
    """Ricerca fuzzy sul nome del circolo."""
    cfg: AppConfig = current_app.config["NETGOLF"]
    q = nome.upper().strip()
    circoli = _group_by_circolo(_records(cfg))
    matches = [c for c in circoli if q in c.get("nome", "").upper()]
    return jsonify(query=q, risultati=matches)


@bp.get("/<string:nome>/percorsi")
@login_required
def percorsi_di(nome: str):
    """Percorsi di uno specifico circolo."""
    cfg: AppConfig = current_app.config["NETGOLF"]
    q = nome.upper().strip()
    circoli = _group_by_circolo(_records(cfg))
    for c in circoli:
        if c.get("nome", "").upper() == q:
            return jsonify(
                circolo=c["nome"],
                percorsi=[
                    {
                        "id": p.get("id"),
                        "nome": p.get("nome"),
                        "par": p.get("par"),
                        "tees": p.get("tees", []),
                    }
                    for p in c.get("percorsi", [])
                ],
            )
    return jsonify(percorsi=[])
