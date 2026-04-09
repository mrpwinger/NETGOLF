"""
Client GesGolf.

Porta la logica di server.js righe 864-1218 (resolveGaraId, extractGare,
matchGara, parseScorecard, /api/gesgolf-score) in una classe configurabile.

La mappa nome → circolo_id non sta più qui: sta in data/circoli_gesgolf.csv,
caricata da AppConfig.resolve_circolo_id().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from ..config import GesgolfSection

log = logging.getLogger(__name__)


class GesgolfError(Exception):
    pass


class GesgolfGaraNotFound(GesgolfError):
    """Nessuna gara GesGolf corrispondente al match dato."""


@dataclass
class Gara:
    id: str
    data: str  # formato dd-mm-yyyy
    block: str = ""


@dataclass
class Scorecard:
    holes: list[dict[str, int]]
    player_name: str = ""
    hcp_cat: str = ""
    posizione: str | None = None


class GesgolfClient:
    def __init__(self, cfg: GesgolfSection):
        self.cfg = cfg

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.cfg.timeout_sec,
            headers={
                "User-Agent": self.cfg.user_agent,
                "Accept": "text/html,*/*",
            },
        )

    def _url(self, key: str) -> str:
        return self.cfg.base_url + self.cfg.endpoints[key]

    # ── Resolve GaraId ───────────────────────────────────────────────────

    def resolve_gara_id(
        self,
        circolo_id: str,
        data_fig: str,
        gara_nome: str = "",
    ) -> str | None:
        """
        Cerca GaraId su GesGolf per una data in formato GG/MM/AAAA.
        Porta la logica di resolveGaraId() di server.js.

        GesGolf mostra di default solo il mese corrente: per date storiche
        (anno diverso da quello corrente) il lookup non è disponibile e
        ritorniamo None.
        """
        parts = (data_fig or "").split("/")
        if len(parts) != 3:
            return None
        gg, mm, aaaa = parts
        date_str = f"{gg.zfill(2)}-{mm.zfill(2)}-{aaaa}"

        now = datetime.now()
        if int(aaaa) != now.year:
            log.info("GesGolf: anno %s ≠ corrente, storico non disponibile", aaaa)
            return None

        base_url = f"{self._url('gare')}?circolo_id={circolo_id}"

        # Strategia 1: POST con __EVENTTARGET (dropdown ASP.NET WebForms)
        with self._client() as client:
            try:
                gara_id = self._strategy_eventtarget(
                    client, base_url, aaaa, mm, date_str, gara_nome
                )
                if gara_id:
                    return gara_id
            except Exception as e:
                log.warning("GesGolf strategia EVENTTARGET fallita: %s", e)

            # Strategia 2: GET diretto con parametri Anno/Mese
            try:
                url2 = f"{base_url}&Anno={aaaa}&Mese={int(mm)}"
                r = client.get(url2)
                gare = self._extract_gare(r.text)
                if gare:
                    found = self._match_gara(gare, date_str, gara_nome)
                    if found:
                        return found
            except Exception as e:
                log.warning("GesGolf strategia GET fallita: %s", e)

        return None

    def _strategy_eventtarget(
        self,
        client: httpx.Client,
        url: str,
        aaaa: str,
        mm: str,
        date_str: str,
        gara_nome: str,
    ) -> str | None:
        """Replica della strategia 1 di server.js: POST con __EVENTTARGET."""
        # GET iniziale per ViewState
        r1 = client.get(url)
        fields = self._extract_hidden_inputs(r1.text)

        # Primo POST: cambio anno
        body1 = {
            **fields,
            "__EVENTTARGET": self.cfg.form_targets["anno"],
            "__EVENTARGUMENT": "",
            self.cfg.form_targets["anno"]: aaaa,
            self.cfg.form_targets["mese"]: mm,
        }
        r2 = client.post(
            url,
            data=body1,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": url,
            },
        )

        # Secondo POST: cambio mese (aggiorna ViewState dopo il primo POST)
        fields2 = self._extract_hidden_inputs(r2.text)
        body2 = {
            **fields2,
            "__EVENTTARGET": self.cfg.form_targets["mese"],
            "__EVENTARGUMENT": "",
            self.cfg.form_targets["anno"]: aaaa,
            self.cfg.form_targets["mese"]: mm,
        }
        r3 = client.post(
            url,
            data=body2,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": url,
            },
        )

        # Usa la risposta che contiene GaraId
        for candidate in (r3.text, r2.text, r1.text):
            if "GaraId" in candidate:
                gare = self._extract_gare(candidate)
                if gare:
                    found = self._match_gara(gare, date_str, gara_nome)
                    if found:
                        return found
                break
        return None

    @staticmethod
    def _extract_hidden_inputs(html: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for m in re.finditer(
            r'<input[^>]+name="([^"]+)"[^>]*value="([^"]*)"', html, re.IGNORECASE
        ):
            fields[m.group(1)] = m.group(2)
        return fields

    @staticmethod
    def _extract_gare(html: str) -> list[Gara]:
        """3 pattern in fallback come in server.js extractGare()."""
        gare: list[Gara] = []

        # Pattern A: <li> con data e GaraId
        for m in re.finditer(r"<li[^>]*>([\s\S]*?)</li>", html, re.IGNORECASE):
            block = m.group(1)
            date_m = re.search(r"(\d{2}-\d{2}-\d{4})", block)
            id_m = re.search(r"GaraId=(\d+)", block)
            if date_m and id_m:
                gare.append(Gara(id=id_m.group(1), data=date_m.group(1), block=block))
        if gare:
            return gare

        # Pattern B: <strong>data</strong> vicino a GaraId
        for m in re.finditer(
            r"<strong>(\d{2}-\d{2}-\d{4})</strong>([\s\S]{0,600}?)GaraId=(\d+)",
            html,
        ):
            gare.append(Gara(id=m.group(3), data=m.group(1), block=m.group(0)))
        if gare:
            return gare

        # Pattern C: data vicino a GaraId in testo
        for m in re.finditer(
            r"(\d{2}-\d{2}-\d{4})[\s\S]{0,400}?GaraId=(\d+)", html
        ):
            gare.append(Gara(id=m.group(2), data=m.group(1)))
        return gare

    @staticmethod
    def _match_gara(gare: list[Gara], date_str: str, gara_nome: str) -> str | None:
        # 1. Match data esatta
        for g in gare:
            if g.data == date_str:
                return g.id

        # 2. Match parola-chiave del nome gara
        if gara_nome:
            kws = [w for w in gara_nome.upper().split() if len(w) > 3]
            for kw in kws:
                for g in gare:
                    if kw in g.block.upper():
                        return g.id

        # 3. Unica gara del periodo
        if len(gare) == 1:
            return gare[0].id
        return None

    # ── Fetch scorecard ──────────────────────────────────────────────────

    def fetch_scorecard(
        self,
        circolo_id: str,
        gara_id: str,
        tessera: str,
        nome_giocatore: str,
        data_fig: str,
        cognome_upper: str = "",
    ) -> Scorecard:
        """
        Scarica la scorecard buca-per-buca + la posizione in classifica netta.
        Porta la logica di /api/gesgolf-score (server.js righe 1115-1178).
        """
        with self._client() as client:
            # 1. Scorecard
            score_url = (
                self._url("score_persona")
                + f"?circolo_id={circolo_id}&GaraId={gara_id}"
                f"&Tessera={tessera}&Giri=1&Nome={nome_giocatore}"
            )
            r_score = client.get(score_url)
            if not r_score.is_success:
                raise GesgolfError(f"GesGolf non risponde ({r_score.status_code})")
            score_html = r_score.text
            holes = self._parse_scorecard(score_html)

            player_m = re.search(r"<h[123][^>]*>([^<]{3,50})</h[123]>", score_html, re.IGNORECASE)
            player_name = player_m.group(1) if player_m else nome_giocatore

            cat_m = re.search(r"Cat(?:egoria)?[\s.:]*([^\s<]{1,20})", score_html, re.IGNORECASE)
            hcp_cat = cat_m.group(1) if cat_m else ""

            # 2. Posizione in classifica netta
            posizione: str | None = None
            try:
                _, mm, anno = data_fig.split("/")
                class_url = (
                    self._url("classifiche")
                    + f"?circolo_id={circolo_id}&GaraId={gara_id}"
                    f"&Anno={anno}&Mese={int(mm)}"
                )
                r_class = client.get(class_url)
                class_html = r_class.text
                if cognome_upper:
                    rx1 = re.compile(
                        re.escape(cognome_upper) + r"[\s\S]{0,200}?(?:Pos\.?|°|#)\s*(\d+)",
                        re.IGNORECASE,
                    )
                    rx2 = re.compile(
                        r"(\d+)\s*[°]?\s*[\s\S]{0,100}?" + re.escape(cognome_upper),
                        re.IGNORECASE,
                    )
                    m = rx1.search(class_html) or rx2.search(class_html)
                    if m:
                        posizione = m.group(1)
            except Exception as e:
                log.debug("GesGolf classifica non estraibile: %s", e)

        return Scorecard(
            holes=holes,
            player_name=player_name,
            hcp_cat=hcp_cat,
            posizione=posizione,
        )

    @staticmethod
    def _parse_scorecard(html: str) -> list[dict[str, int]]:
        """Replica di parseScorecard() di server.js righe 1181-1218."""
        holes: list[dict[str, int]] = []

        for t_m in re.finditer(r"<table[^>]*>([\s\S]*?)</table>", html, re.IGNORECASE):
            t_html = t_m.group(1)
            rows = [
                rm.group(1)
                for rm in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", t_html, re.IGNORECASE)
            ]
            if len(rows) < 3:
                continue

            header_cells = [
                re.sub(r"<[^>]+>", "", hm.group(0)).strip()
                for hm in re.finditer(
                    r"<t[dh][^>]*>[^<]*</t[dh]>", rows[0], re.IGNORECASE
                )
            ]
            has_buca_header = any(re.match(r"^(buca|hole|nr\.?|n\.?|#)$", h, re.IGNORECASE) for h in header_cells)
            has_numbers = any(re.match(r"^[1-9]$", h) for h in header_cells)
            if not has_buca_header and not has_numbers:
                continue

            for row in rows[1:]:
                cells = [
                    re.sub(r"<[^>]+>", "", c.group(1)).strip()
                    for c in re.finditer(
                        r"<td[^>]*>([^<]*)</td>", row, re.IGNORECASE
                    )
                ]
                if len(cells) < 4:
                    continue
                try:
                    buca = int(cells[0])
                except ValueError:
                    continue
                if not 1 <= buca <= 18:
                    continue
                try:
                    par = int(cells[1])
                except ValueError:
                    par = 0
                tirati = 0
                try:
                    tirati = int(cells[-2])
                except ValueError:
                    try:
                        tirati = int(cells[2])
                    except ValueError:
                        pass
                if tirati > 0:
                    holes.append({"buca": buca, "par": par, "tirati": tirati})

            if len(holes) >= 9:
                break

        # Fallback regex
        if not holes:
            for m in re.finditer(
                r"(?:buca|hole)\s*(\d+)[^<]*par[^<]*(\d+)[^<]*(\d+)",
                html,
                re.IGNORECASE,
            ):
                holes.append(
                    {
                        "buca": int(m.group(1)),
                        "par": int(m.group(2)),
                        "tirati": int(m.group(3)),
                    }
                )
        return holes
