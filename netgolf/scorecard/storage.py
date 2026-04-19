"""
Calcoli Stableford WHS (World Handicap System) + Adjusted Gross Score.

Funzioni pubbliche:

    colpi_ricevuti(hcp_gioco, ordine_colpi) -> int
    net_double_bogey(par, colpi_ricevuti) -> int
    adjusted_gross_score(par, score_raw, colpi_ricevuti) -> int
    stableford_lordo(par, score) -> int
    stableford_netto(par, score, colpi_ricevuti) -> int

Regola WHS chiave:
- Lo score di una buca, ai fini del calcolo handicap, non può eccedere il
  net double bogey = par + 2 + colpi_ricevuti. Score "X" / no return / pickup
  vengono trattati allo stesso modo: sostituiti col net double bogey.
- L'AGS (Adjusted Gross Score) di una buca è lo score capato a quel valore.
- Stableford lordo e netto si calcolano sull'AGS, non sullo score grezzo.
"""

from __future__ import annotations

from typing import Optional
from netgolf.db import db
from netgolf.models import Scorecard, ScorecardHole    

def colpi_ricevuti(hcp_gioco: int | None, ordine_colpi: int | None) -> int:
    """Quanti colpi tecnici riceve il giocatore su una buca, per WHS."""
    if hcp_gioco is None or ordine_colpi is None:
        return 0
    if hcp_gioco <= 0 or ordine_colpi < 1 or ordine_colpi > 18:
        return 0
    n = 0
    threshold = ordine_colpi
    while hcp_gioco >= threshold:
        n += 1
        threshold += 18
    return n


def net_double_bogey(par: int | None, colpi_ricevuti_buca: int) -> int | None:
    """
    Score massimo accettabile per la buca = par + 2 + colpi_ricevuti.
    Ritorna None se par è None (non possiamo calcolare).
    """
    if par is None:
        return None
    return par + 2 + (colpi_ricevuti_buca or 0)


def adjusted_gross_score(par: int | None, score_raw, colpi_ricevuti_buca: int) -> int | None:
    """
    AGS hole-by-hole secondo WHS.

    Regole:
    - score_raw è "X", None, "" o non numerico → ritorna net_double_bogey
    - score_raw > net_double_bogey → ritorna net_double_bogey (capping)
    - score_raw <= net_double_bogey → ritorna score_raw

    Ritorna None se non si può calcolare il net double bogey (par mancante).
    """
    ndb = net_double_bogey(par, colpi_ricevuti_buca)
    if ndb is None:
        return None

    # X / None / vuoto / stringa non numerica → no return → uso ndb
    if score_raw is None:
        return ndb
    if isinstance(score_raw, str):
        s = score_raw.strip().upper()
        if not s or s == "X" or s == "NR":
            return ndb
        try:
            score_int = int(s)
        except ValueError:
            return ndb
    else:
        try:
            score_int = int(score_raw)
        except (ValueError, TypeError):
            return ndb

    return min(score_int, ndb)


def stableford_lordo(par: int | None, score) -> int:
    """
    Punti Stableford lordi. Da chiamare CON L'AGS (lo score già capato),
    non con lo score grezzo del giocatore.
    """
    if par is None or score is None:
        return 0
    if isinstance(score, str):
        try:
            score_int = int(score)
        except ValueError:
            return 0
    else:
        try:
            score_int = int(score)
        except (ValueError, TypeError):
            return 0
    return max(0, 2 + (par - score_int))


def stableford_netto(par: int | None, score, colpi_ricevuti_buca: int) -> int:
    """
    Punti Stableford netti = stableford_lordo applicato a (score - colpi).
    Da chiamare CON L'AGS, non con lo score grezzo.
    """
    if par is None or score is None:
        return 0
    if isinstance(score, str):
        try:
            score_int = int(score)
        except ValueError:
            return 0
    else:
        try:
            score_int = int(score)
        except (ValueError, TypeError):
            return 0
    score_netto = score_int - (colpi_ricevuti_buca or 0)
    return max(0, 2 + (par - score_netto))

def save_scorecard(user_id: int, header: dict, holes: list[dict]) -> Scorecard:
    """
    Crea (o aggiorna) una Scorecard con le relative ScorecardHole.
    - header: dizionario con i campi della Scorecard (senza id, user_id, holes)
    - holes: lista di dict con i campi di ScorecardHole (senza scorecard_id)
    Ritorna l'oggetto Scorecard salvato (con id popolato).
    """
    sc = Scorecard(user_id=user_id, **{
        k: header.get(k) for k in (
            "torneo_nome", "data_gara", "circolo", "percorso", "tee_colore",
            "par_totale", "cr", "sr",
            "giocatore_nome", "giocatore_tessera", "hcp_index", "hcp_gioco",
            "stbl_lordo_totale", "stbl_netto_totale",
            "score_lordo_totale", "ags_totale",
            "fig_result_id",
        ) if k in header
    })
    db.session.add(sc)
    db.session.flush()  # popola sc.id prima di creare i figli

    for h in holes:
        hole = ScorecardHole(scorecard_id=sc.id, **{
            k: h.get(k) for k in (
                "buca", "par", "metri_uomini", "ordine_colpi",
                "score_raw", "score_ags",
                "colpi_ricevuti", "stbl_lordo", "stbl_netto",
            ) if k in h
        })
        db.session.add(hole)

    db.session.commit()
    return sc


def list_scorecards_for_user(user_id: int) -> list[Scorecard]:
    """
    Ritorna tutte le scorecard dell'utente, ordinate per data decrescente.
    """
    return (
        db.session.execute(
            db.select(Scorecard)
            .where(Scorecard.user_id == user_id)
            .order_by(Scorecard.data_gara.desc(), Scorecard.created_at.desc())
        )
        .scalars()
        .all()
    )


def get_scorecard(scorecard_id: int, user_id: int) -> Optional[Scorecard]:
    """
    Ritorna la scorecard con quell'id, solo se appartiene a user_id.
    Ritorna None se non esiste o se appartiene a un altro utente.
    """
    return db.session.execute(
        db.select(Scorecard).where(
            Scorecard.id == scorecard_id,
            Scorecard.user_id == user_id,
        )
    ).scalar_one_or_none()


def find_scorecard_for_gara(user_id: int, fig_result_id: int) -> Optional[Scorecard]:
    """
    Cerca una scorecard già caricata per questa gara FIG.
    Utile per evitare duplicati o per linkare OCR → gara storico.
    """
    return db.session.execute(
        db.select(Scorecard).where(
            Scorecard.user_id == user_id,
            Scorecard.fig_result_id == fig_result_id,
        )
    ).scalar_one_or_none()

def _date_fig_to_iso(data_fig: str) -> str | None:
    """Converte 'DD/MM/YYYY' → 'YYYY-MM-DD'. Ritorna None se malformata."""
    parts = (data_fig or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
    except ValueError:
        return None


def _circolo_match(a: str, b: str) -> bool:
    """Match fuzzy case-insensitive: uno contiene l'altro."""
    a = (a or "").strip().upper()
    b = (b or "").strip().upper()
    return bool(a and b and (a in b or b in a))


def find_or_create_fig_result(
    user_id: int,
    data_gara_iso: str,
    circolo: str,
    nome_torneo: str | None = None,
    fig_data_raw: str | None = None,
) -> "FigResult":
    """
    Cerca una FigResult per (user_id, data_gara, circolo).
    Se non esiste la crea. Ritorna sempre un oggetto persistito.
    """
    from netgolf.db import db
    from netgolf.models import FigResult

    existing = db.session.execute(
        db.select(FigResult).where(
            FigResult.user_id == user_id,
            FigResult.data_gara == data_gara_iso,
        )
    ).scalars().all()

    for fig in existing:
        if _circolo_match(fig.circolo, circolo):
            return fig

    # Non trovata: crea
    fig = FigResult(
        user_id=user_id,
        data_gara=data_gara_iso,
        circolo=circolo,
        nome_torneo=nome_torneo,
        fig_data_raw=fig_data_raw,
    )
    db.session.add(fig)
    db.session.commit()
    return fig


def match_scorecard_to_storico(
    user_id: int,
    scorecard_data_gara: str | None,
    scorecard_circolo: str | None,
    storico_results: list[dict],
) -> "FigResult | None":
    """
    Cerca nei risultati live dello storico FIG una gara che matcha
    data + circolo della scorecard. Se trovata, crea/recupera la FigResult
    e la ritorna. Ritorna None se nessun match.

    storico_results: lista di dict dal client FIG (campo 'data' DD/MM/YYYY,
    campo 'esecutore' per il circolo, campo 'gara' per il nome torneo).
    """
    if not scorecard_data_gara or not scorecard_circolo:
        return None

    for r in storico_results:
        data_iso = _date_fig_to_iso(r.get("data", ""))
        if not data_iso:
            continue
        if data_iso != scorecard_data_gara:
            continue
        circolo_fig = r.get("esecutore", "") or r.get("gara", "")
        if not _circolo_match(circolo_fig, scorecard_circolo):
            continue

        # Match trovato
        import json
        return find_or_create_fig_result(
            user_id=user_id,
            data_gara_iso=data_iso,
            circolo=circolo_fig,
            nome_torneo=r.get("gara"),
            fig_data_raw=json.dumps(r, ensure_ascii=False),
        )

    return None


def link_scorecard_to_fig(scorecard_id: int, user_id: int, fig_result_id: int) -> bool:
    """
    Collega una scorecard a una FigResult. Ritorna True se ok.
    """
    from netgolf.db import db
    from netgolf.models import Scorecard, FigResult

    sc = db.session.execute(
        db.select(Scorecard).where(
            Scorecard.id == scorecard_id,
            Scorecard.user_id == user_id,
        )
    ).scalar_one_or_none()

    fig = db.session.execute(
        db.select(FigResult).where(
            FigResult.id == fig_result_id,
            FigResult.user_id == user_id,
        )
    ).scalar_one_or_none()

    if not sc or not fig:
        return False

    sc.fig_result_id = fig_result_id
    db.session.commit()
    return True


def unlink_scorecard_from_fig(scorecard_id: int, user_id: int) -> bool:
    """
    Scollega una scorecard dalla FigResult. La scorecard resta intatta.
    Ritorna True se ok.
    """
    from netgolf.db import db
    from netgolf.models import Scorecard

    sc = db.session.execute(
        db.select(Scorecard).where(
            Scorecard.id == scorecard_id,
            Scorecard.user_id == user_id,
        )
    ).scalar_one_or_none()

    if not sc:
        return False

    sc.fig_result_id = None
    db.session.commit()
    return True

def delete_scorecard(scorecard_id: int, user_id: int) -> bool:
    """
    Cancella una scorecard e le relative buche (cascade).
    Ritorna True se cancellata, False se non trovata o non appartiene all'utente.
    """
    from netgolf.db import db
    from netgolf.models import Scorecard

    sc = db.session.execute(
        db.select(Scorecard).where(
            Scorecard.id == scorecard_id,
            Scorecard.user_id == user_id,
        )
    ).scalar_one_or_none()

    if not sc:
        return False

    db.session.delete(sc)
    db.session.commit()
    return True
