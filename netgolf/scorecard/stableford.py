"""
Calcoli Stableford WHS (World Handicap System).

Tre funzioni pubbliche, tutte pure (no I/O, no DB):

    colpi_ricevuti(hcp_gioco, ordine_colpi) -> int
    stableford_lordo(par, score) -> int
    stableford_netto(par, score, colpi_ricevuti) -> int

Note di dominio:
- L'ordine_colpi (handicap stroke index) di una buca va da 1 a 18: 1 = buca più
  difficile, 18 = più facile. Il giocatore riceve un colpo "tecnico" sulle
  buche più difficili in funzione del proprio hcp_gioco.
- La regola WHS: se hcp_gioco >= ordine_colpi → 1 colpo; se hcp_gioco >=
  ordine_colpi + 18 → 2 colpi; ecc. Per hcp_gioco negativi (giocatori sotto
  par) si invertono i segni — non lo gestiamo qui perché NETGOLF target
  utenti hcp positivi.
- Stableford lordo: 2 punti per il par, +1 ogni colpo sotto, -1 ogni colpo
  sopra, minimo 0. Es. par 4 / score 4 = 2 pt, score 3 = 3 pt, score 5 =
  1 pt, score 6+ = 0 pt.
- Stableford netto: applica la stessa formula allo score netto = score lordo
  meno colpi_ricevuti.
- Score "X" / no return → 0 punti sia lordo che netto.
"""

from __future__ import annotations


def colpi_ricevuti(hcp_gioco: int | None, ordine_colpi: int | None) -> int:
    """
    Quanti colpi tecnici riceve il giocatore su questa buca, secondo WHS.

    Esempio: hcp_gioco=19, ordine_colpi=1 → 2 colpi (perché 19 >= 1 e 19 >= 1+18=19)
             hcp_gioco=19, ordine_colpi=2 → 1 colpo (19 >= 2 ma non >= 20)
             hcp_gioco=19, ordine_colpi=18 → 1 colpo
             hcp_gioco=8,  ordine_colpi=10 → 0 colpi (8 < 10)
    """
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

def net_double_bogey(par, colpi_ricevuti) -> int:
    """Score massimo accettabile per la buca: par + 2 + colpi_ricevuti."""

def adjusted_gross_score(par, score_raw, colpi_ricevuti) -> int:
    """
    AGS hole-by-hole secondo WHS:
    - Se score è X/None → ritorna net_double_bogey
    - Se score > net_double_bogey → ritorna net_double_bogey
    - Altrimenti → ritorna score
    """


def stableford_lordo(par: int | None, score) -> int:
    """
    Punti Stableford lordi per la buca. Score può essere int o "X" (no return).
    """
    if par is None or score is None:
        return 0
    if isinstance(score, str):
        # "X", "NR", o qualsiasi stringa → no return → 0 punti
        return 0
    try:
        score_int = int(score)
    except (ValueError, TypeError):
        return 0

    return max(0, 2 + (par - score_int))


def stableford_netto(par: int | None, score, colpi_ricevuti_buca: int) -> int:
    """
    Punti Stableford netti: applica la formula Stableford allo score
    dopo aver tolto i colpi tecnici ricevuti.
    """
    if par is None or score is None:
        return 0
    if isinstance(score, str):
        return 0
    try:
        score_int = int(score)
    except (ValueError, TypeError):
        return 0

    score_netto = score_int - (colpi_ricevuti_buca or 0)
    return max(0, 2 + (par - score_netto))
