"""
Endpoint frase obiettivo mensile.

Porting di /api/frase GET/POST del vecchio server.js (righe 1344-1382).
Differenze rispetto al vecchio:
  - Le frasi disponibili sono lette da data/frasi_obiettivo.csv, non da
    frasi_obiettivo.json.
  - Le assegnazioni sono salvate nel DB (tabella frasi_assegnate) invece
    che in un file JSON + persistenza GitHub.
  - La selezione casuale avviene SERVER-SIDE: il vecchio server delegava
    al client ("la selezione casuale avviene lato client per non dover
    caricare il JSON sul server"). Qui il CSV è già caricato in memoria
    al boot (via AppConfig.frasi), quindi tanto vale fare tutto dal server
    e avere un endpoint più semplice.
"""

from __future__ import annotations

import random
from datetime import datetime

from flask import current_app, jsonify, request
from flask_babel import gettext as _
from flask_login import current_user, login_required
from sqlalchemy import select

from ..config import AppConfig
from ..db import db
from ..models import FraseAssegnata
from . import bp


def _fascia_per_hcp(hcp: float, cfg: AppConfig) -> str | None:
    band = cfg.band_for_hcp(hcp)
    return band.label_it if band else None  # fascia = sempre label IT (chiave CSV)


@bp.get("")
@login_required
def get_frase():
    """
    Se l'utente ha già una frase per (anno, mese) corrente la restituisce.
    Altrimenti ne sceglie una casuale in base alla fascia HCP passata come
    query ?hcp=12.3 e la salva.
    """
    gs_cfg: AppConfig = current_app.config["NETGOLF"]
    now = datetime.utcnow()

    # 1. Già assegnata per questo mese?
    existing = db.session.scalar(
        select(FraseAssegnata).where(
            FraseAssegnata.user_id == current_user.id,
            FraseAssegnata.anno == now.year,
            FraseAssegnata.mese == now.month,
        )
    )
    if existing:
        return jsonify(
            fraseId=existing.frase_id,
            frase=existing.frase_testo,
            fascia=existing.fascia,
            anno=existing.anno,
            mese=existing.mese,
            cached=True,
        )

    # 2. Nuova assegnazione
    try:
        hcp = float(request.args.get("hcp", "").replace(",", "."))
    except ValueError:
        return jsonify(error=_("Parametro hcp mancante o non valido.")), 400

    fascia = _fascia_per_hcp(hcp, gs_cfg)
    if not fascia:
        return jsonify(error=_("Nessuna fascia HCP per %(hcp)s", hcp=hcp)), 400

    lang = current_user.locale or gs_cfg.raw.i18n.default_locale
    candidates = gs_cfg.frasi_per_fascia(fascia, lang=lang)
    # Fallback: se non abbiamo traduzioni nella lingua utente, usa l'italiano
    if not candidates and lang != "it":
        candidates = gs_cfg.frasi_per_fascia(fascia, lang="it")
    if not candidates:
        return jsonify(error=_("Nessuna frase disponibile per la fascia %(f)s", f=fascia)), 404

    choice = random.choice(candidates)
    record = FraseAssegnata(
        user_id=current_user.id,
        anno=now.year,
        mese=now.month,
        frase_id=choice.id,
        frase_testo=choice.testo,
        fascia=fascia,
    )
    db.session.add(record)
    db.session.commit()

    return jsonify(
        fraseId=choice.id,
        frase=choice.testo,
        fascia=fascia,
        anno=now.year,
        mese=now.month,
        cached=False,
    )
