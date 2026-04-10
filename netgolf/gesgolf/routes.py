"""
Endpoint scorecard GesGolf.
Equivalente a /api/gesgolf-score di server.js.
"""

from __future__ import annotations

from flask import current_app, jsonify, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from ..config import AppConfig
from ..fig.service import FigCredentialsMissing
from .client import GesgolfClient, GesgolfError
from . import bp


@bp.get("/score")
@login_required
def score():
    """
    Query params (stessi del vecchio /api/gesgolf-score):
      circolo   nome del circolo come appare su FIG
      gara      nome gara (opzionale, usato per match)
      data      GG/MM/AAAA
      valida    S/V per indicare che la gara è valida per HCP
      garaId    opzionale, se il client ha già l'id da FederGolf
      circoloId opzionale, idem
    """
    gs_cfg: AppConfig = current_app.config["NETGOLF"]

    circolo = (request.args.get("circolo") or "").strip()
    gara_nome = (request.args.get("gara") or "").strip()
    data_fig = (request.args.get("data") or "").strip()
    valida = (request.args.get("valida") or "").strip()
    gara_id_direct = (request.args.get("garaId") or "").strip()
    circolo_id_direct = (request.args.get("circoloId") or "").strip()

    if valida not in ("S", "V"):
        return jsonify(
            error=_("Scorecard GesGolf disponibile solo per gare valide per HCP."),
            notValid=True,
        )

    # 1. Credenziali FIG opzionali ma utili: servono tessera+nome per l'URL
    from ..auth.routes import get_fig_credentials_plain
    creds = get_fig_credentials_plain(current_user)
    if creds is None:
        return jsonify(
            error=_("Credenziali FIG necessarie per leggere la scorecard. Configurale dal profilo."),
            code="fig_credentials_missing",
        ), 412
    tessera, _pwd = creds

    # 2. Risolvi circolo_id
    circolo_id = circolo_id_direct or gs_cfg.resolve_circolo_id(circolo)
    if not circolo_id:
        return jsonify(
            error=_("Circolo non trovato su GesGolf."),
            notOnGesgolf=True,
        )

    # 3. Risolvi gara_id
    client = GesgolfClient(gs_cfg.raw.gesgolf)
    try:
        gara_id = gara_id_direct or client.resolve_gara_id(
            circolo_id, data_fig, gara_nome
        )
    except GesgolfError as e:
        return jsonify(error=_("Errore GesGolf: %(msg)s", msg=str(e))), 502

    if not gara_id:
        return jsonify(
            error=_("Gara del %(data)s non trovata su GesGolf per %(circolo)s.",
                    data=data_fig, circolo=circolo)
        )

    # 4. Scarica scorecard
    # Il cognome serve per matchare la posizione in classifica. Lo leggiamo
    # dal profilo FIG cachato in sessione (popolato da FigService.fetch_profilo).
    # Se la cache è vuota (primo accesso, o /api/gesgolf/score chiamato
    # prima di /api/fig/profilo), facciamo un fetch al volo.
    from ..fig.service import FigService

    cached = FigService.get_cached_profilo()
    if not cached:
        try:
            FigService.from_app().fetch_profilo(current_user)
            cached = FigService.get_cached_profilo() or {}
        except Exception:
            cached = {}

    cognome = (cached.get("cognome") or "").upper()
    nome_giocatore = " ".join(
        filter(None, [cached.get("nome"), cached.get("cognome")])
    ) or tessera
    nome_giocatore = nome_giocatore.upper()

    try:
        scorecard = client.fetch_scorecard(
            circolo_id=circolo_id,
            gara_id=gara_id,
            tessera=tessera,
            nome_giocatore=nome_giocatore,
            data_fig=data_fig,
            cognome_upper=cognome,
        )
    except GesgolfError as e:
        return jsonify(error=_("Errore GesGolf: %(msg)s", msg=str(e))), 502

    return jsonify(
        scorecard={"holes": scorecard.holes},
        playerName=scorecard.player_name,
        hcpCat=scorecard.hcp_cat,
        posizione=scorecard.posizione,
    )
