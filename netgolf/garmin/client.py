from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


class GarminError(Exception):
    pass


class GarminLoginFailed(GarminError):
    pass


class GarminRateLimited(GarminError):
    pass


def _parse_hole_pars(hole_pars_str: str) -> list[int]:
    """Converte '434434545354444535' in [4,3,4,4,3,4,5,4,5,3,5,4,4,4,4,5,3,5]"""
    return [int(c) for c in (hole_pars_str or "") if c.isdigit()]


def _parse_date(iso_str: str) -> str | None:
    """Converte '2026-04-18T08:56:47.000Z' in '2026-04-18'"""
    if not iso_str:
        return None
    return iso_str[:10]


class GarminClient:
    """
    Client Garmin Connect per scaricare le scorecard golf.
    Usa python-garminconnect internamente.
    """

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._api = None

    def _get_api(self):
        if self._api is not None:
            return self._api
        try:
            from garminconnect import Garmin, GarminConnectAuthenticationError
        except ImportError as e:
            raise GarminError(
                "Pacchetto 'garminconnect' non installato. "
                "Aggiungi 'garminconnect' al requirements.txt."
            ) from e

        try:
            api = Garmin(self.email, self.password)
            api.login()
            self._api = api
            return api
        except Exception as e:
            err = str(e)
            if "429" in err:
                raise GarminRateLimited(
                    "Garmin ha bloccato temporaneamente i login da questo IP (429). "
                    "Riprova tra 1-2 ore."
                ) from e
            raise GarminLoginFailed(f"Login Garmin fallito: {e}") from e

    def fetch_scorecards(self, per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        """
        Scarica le scorecard golf da Garmin Connect.
        Ritorna lista di dict normalizzati compatibili con il formato NETGOLF.
        """
        api = self._get_api()
        try:
            result = api.connectapi(
                "/gcs-golfcommunity/api/v2/scorecard/summary",
                params={"per-page": per_page, "page": page},
            )
        except Exception as e:
            raise GarminError(f"Errore fetch scorecard Garmin: {e}") from e

        summaries = result.get("scorecardSummaries", [])
        log.info("Garmin: %d scorecard scaricate (pagina %d)", len(summaries), page)
        return [self._normalize(s) for s in summaries]

    def fetch_all_scorecards(self, max_pages: int = 20) -> list[dict[str, Any]]:
        """Scarica tutte le scorecard paginando automaticamente."""
        api = self._get_api()
        all_sc = []
        page = 1
        per_page = 100

        while page <= max_pages:
            try:
                result = api.connectapi(
                    "/gcs-golfcommunity/api/v2/scorecard/summary",
                    params={"per-page": per_page, "page": page},
                )
            except Exception as e:
                raise GarminError(f"Errore fetch pagina {page}: {e}") from e

            summaries = result.get("scorecardSummaries", [])
            total = result.get("totalRows", 0)
            all_sc.extend(summaries)

            log.info(
                "Garmin: pagina %d — %d scorecard (totale %d/%d)",
                page, len(summaries), len(all_sc), total,
            )

            if len(all_sc) >= total or not summaries:
                break
            page += 1

        return [self._normalize(s) for s in all_sc]

    def _normalize(self, s: dict) -> dict:
        """
        Normalizza una scorecard Garmin nel formato usato da NETGOLF:
        {garmin_id, data_gara, circolo, holes:[{buca, par, score_raw}], ...}
        """
        hole_pars = _parse_hole_pars(s.get("holePars", ""))
        holes_raw = s.get("holes", [])

        holes = []
        for h in holes_raw:
            n = h.get("number", 0)
            par = hole_pars[n - 1] if 0 < n <= len(hole_pars) else None
            strokes = h.get("strokes")
            holes.append({
                "buca": n,
                "par": par,
                "score_raw": str(strokes) if strokes is not None else "X",
                "ordine_colpi": None,
                "metri_uomini": None,
            })

        return {
            "garmin_id": s.get("id"),
            "data_gara": _parse_date(s.get("startTime", "")),
            "circolo": s.get("courseName", ""),
            "torneo_nome": s.get("courseName", ""),
            "score_type": s.get("scoreType", ""),
            "strokes_totali": s.get("strokes"),
            "handicapped_strokes": s.get("handicappedStrokes"),
            "stbl_senza_hcp": s.get("scoreWithoutHandicap"),
            "stbl_con_hcp": s.get("scoreWithHandicap"),
            "buche_completate": s.get("holesCompleted", 18),
            "holes": holes,
        }
