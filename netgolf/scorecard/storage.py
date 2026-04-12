"""
Persistenza scorecard.

Funzione principale:

    save_scorecard(user, parsed_data) -> Scorecard

Flusso:
    1. Calcola gli Stableford lordi/netti per ogni buca usando stableford.py.
    2. Cerca nello storico FIG dell'utente (live, via FigService) una gara
       che combaci per data + circolo (fuzzy substring case-insensitive sul
       nome circolo). Se la trova, crea o riusa una riga FigResult.
    3. Salva la Scorecard con FK opzionale a FigResult, e le 18 ScorecardHole.
    4. Commit e ritorna l'oggetto Scorecard.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..db import db
from ..models import FigResult, Scorecard, ScorecardHole, User
from .stableford import colpi_ricevuti, stableford_lordo, stableford_netto

logger = logging.getLogger(__name__)


def save_scorecard(user: User, parsed: dict[str, Any]) -> Scorecard:
    """
    Salva una scorecard estratta+confermata nel DB. Ritorna la Scorecard
    appena creata (con id assegnato e relazioni caricate).
    """
    # ── 1) Estrai i dati di header ──────────────────────────────────────
    torneo = parsed.get("torneo") or {}
    giocatore = parsed.get("giocatore") or {}
    campo = parsed.get("campo") or {}
    handicap = parsed.get("handicap") or {}
    buche_in = parsed.get("buche") or []

    hcp_gioco = _safe_int(handicap.get("hcp_gioco"))

    # ── 2) Cerca il match con lo storico FIG ────────────────────────────
    data_gara = (torneo.get("data_gara") or "").strip() or None
    circolo = (campo.get("circolo") or "").strip() or None
    fig_result = _find_or_create_fig_result(user, data_gara, circolo, torneo.get("nome"))

    # ── 3) Crea l'oggetto Scorecard (header) ────────────────────────────
    sc = Scorecard(
        user_id=user.id,
        fig_result_id=fig_result.id if fig_result else None,
        torneo_nome=torneo.get("nome"),
        data_gara=data_gara,
        circolo=circolo,
        percorso=campo.get("percorso"),
        tee_colore=campo.get("tee_colore"),
        par_totale=_safe_int(campo.get("par_totale")),
        cr=_safe_float(campo.get("cr_uomini")),
        sr=_safe_int(campo.get("sr_uomini")),
        giocatore_nome=giocatore.get("nome_completo"),
        giocatore_tessera=giocatore.get("tessera"),
        hcp_index=_safe_float(handicap.get("hcp_index")),
        hcp_gioco=hcp_gioco,
    )
    db.session.add(sc)
    db.session.flush()  # serve per avere sc.id prima di creare le buche

    # ── 4) Crea le 18 ScorecardHole con calcoli Stableford ──────────────
    tot_lordo = 0
    tot_netto = 0
    tot_score = 0

    for b in buche_in:
        if not isinstance(b, dict):
            continue
        buca_n = _safe_int(b.get("buca"))
        if buca_n is None or not (1 <= buca_n <= 18):
            continue

        par = _safe_int(b.get("par"))
        ord_c = _safe_int(b.get("ordine_colpi"))
        score = b.get("score")  # può essere int o "X"

        cr = colpi_ricevuti(hcp_gioco, ord_c)
        sl = stableford_lordo(par, score)
        sn = stableford_netto(par, score, cr)

        # Score raw per memorizzare anche le X
        if score is None:
            score_raw = None
        elif isinstance(score, str):
            score_raw = score
        else:
            try:
                score_raw = str(int(score))
            except (ValueError, TypeError):
                score_raw = str(score)

        h = ScorecardHole(
            scorecard_id=sc.id,
            buca=buca_n,
            par=par,
            metri_uomini=_safe_int(b.get("metri_uomini")),
            ordine_colpi=ord_c,
            score_raw=score_raw,
            colpi_ricevuti=cr,
            stbl_lordo=sl,
            stbl_netto=sn,
        )
        db.session.add(h)

        tot_lordo += sl
        tot_netto += sn
        if isinstance(score, int) or (isinstance(score, str) and score.isdigit()):
            tot_score += int(score)

    sc.stbl_lordo_totale = tot_lordo
    sc.stbl_netto_totale = tot_netto
    sc.score_lordo_totale = tot_score

    db.session.commit()
    logger.info(
        "Scorecard salvata: id=%d user=%s data=%s circolo=%s fig_result_id=%s "
        "stbl_lordo=%d stbl_netto=%d",
        sc.id, user.email, data_gara, circolo, sc.fig_result_id, tot_lordo, tot_netto,
    )
    return sc


def list_scorecards_for_user(user: User) -> list[Scorecard]:
    """Tutte le scorecard di un utente, più recenti prima."""
    return (
        db.session.query(Scorecard)
        .filter(Scorecard.user_id == user.id)
        .order_by(Scorecard.created_at.desc())
        .all()
    )


def get_scorecard(user: User, scorecard_id: int) -> Scorecard | None:
    """Una scorecard specifica, solo se appartiene all'utente."""
    return (
        db.session.query(Scorecard)
        .filter(Scorecard.id == scorecard_id, Scorecard.user_id == user.id)
        .first()
    )


def find_scorecard_for_gara(user: User, data_gara: str, circolo: str) -> Scorecard | None:
    """
    Cerca una scorecard salvata che corrisponda a una gara dello storico FIG
    (data esatta + match fuzzy sostringa sul circolo). Usata dal dashboard
    per decidere se mostrare l'icona "scorecard disponibile" su una riga.
    """
    if not data_gara or not circolo:
        return None
    cards = (
        db.session.query(Scorecard)
        .filter(Scorecard.user_id == user.id, Scorecard.data_gara == data_gara)
        .all()
    )
    for sc in cards:
        if _circolo_match(sc.circolo, circolo):
            return sc
    return None


# ─── Helper privati ───────────────────────────────────────────────────────


def _find_or_create_fig_result(
    user: User,
    data_gara: str | None,
    circolo: str | None,
    nome_torneo: str | None,
) -> FigResult | None:
    """
    Cerca nello storico FIG live una gara con stessa data e circolo (fuzzy).
    Se la trova, crea/riusa la riga FigResult corrispondente.
    Se non la trova (o se data/circolo mancano), ritorna None.
    """
    if not data_gara or not circolo:
        return None

    # Prima controlla se esiste già una FigResult locale (gara già caricata
    # in passato per un'altra scorecard dello stesso evento)
    existing = (
        db.session.query(FigResult)
        .filter(
            FigResult.user_id == user.id,
            FigResult.data_gara == data_gara,
        )
        .all()
    )
    for fr in existing:
        if _circolo_match(fr.circolo, circolo):
            return fr

    # Non esiste: chiamiamo lo storico FIG live per validare che la gara
    # esista davvero, prima di creare la FigResult
    try:
        from ..fig.service import FigService
        service = FigService.from_app()
        storico = service.fetch_storico(user)
    except Exception as e:
        logger.warning("Impossibile fetch storico FIG per match scorecard: %s", e)
        return None

    # storico è tipicamente {"results": [...]} o simile, normalizziamo
    results = []
    if isinstance(storico, dict):
        results = storico.get("results") or storico.get("hcpHistory") or []
    elif isinstance(storico, list):
        results = storico

    matched = None
    for r in results:
        if not isinstance(r, dict):
            continue
        r_data = _normalize_date(r.get("data") or r.get("date") or r.get("data_gara"))
        if r_data != data_gara:
            continue
        r_circolo = (r.get("circolo") or r.get("club") or r.get("campo") or "")
        if _circolo_match(r_circolo, circolo):
            matched = r
            break

    if not matched:
        return None

    # Match trovato: crea la FigResult
    fr = FigResult(
        user_id=user.id,
        data_gara=data_gara,
        circolo=circolo,
        nome_torneo=nome_torneo or matched.get("gara") or matched.get("torneo"),
        fig_data_raw=json.dumps(matched, ensure_ascii=False, default=str),
    )
    db.session.add(fr)
    db.session.flush()
    return fr


def _circolo_match(a: str | None, b: str | None) -> bool:
    """Match fuzzy sostringa case-insensitive: la più corta deve essere
    contenuta nella più lunga (dopo trim e uppercase)."""
    if not a or not b:
        return False
    aa = a.strip().upper()
    bb = b.strip().upper()
    if not aa or not bb:
        return False
    if len(aa) <= len(bb):
        return aa in bb
    return bb in aa


def _normalize_date(s) -> str | None:
    """
    Normalizza una data in 'YYYY-MM-DD'. Accetta:
      - già in ISO ('2026-04-11')
      - 'DD/MM/YYYY'
      - 'DD-MM-YYYY'
    """
    if not s:
        return None
    s = str(s).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) == 10 and s[2] in "/-" and s[5] in "/-":
        return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    return None


def _safe_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(str(v).replace(",", ".")))
        except Exception:
            return None


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return None
