"""
Client per l'area riservata FederGolf.

Porta in Python la logica che in server.js stava inline negli endpoint
/api/login, /api/profilo, /api/storico. Rispetto al vecchio codice:

  - Tutto ciò che era hard-coded (URL, regex, nomi cookie, selettori) ora
    arriva da config.yaml tramite `FigConfig`, passato al costruttore.
  - I cookie di sessione del tesserato sono tenuti in un oggetto `FigSession`
    che vive in RAM per la durata della richiesta Flask, non in un Map
    globale come `sessions` in server.js. La durata è breve: quando finisce
    la request Flask, i cookie vengono buttati. Se servono di nuovo, si
    ri-fa login usando le credenziali FIG salvate nel DB.
  - Le regex di parsing profilo sono caricate da config. Se FederGolf cambia
    struttura, si modifica una entry in YAML invece di redeployare Python.

Questo client NON salva né legge credenziali dal DB: è il livello
trasporto/scraping. Il blueprint `fig/routes.py` si occupa di prendere le
credenziali dall'utente loggato (via auth.get_fig_credentials_plain) e
passarle qui.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import FigSection

log = logging.getLogger(__name__)


# ─── Eccezioni ───────────────────────────────────────────────────────────────


class FigError(Exception):
    """Errore generico lato client FIG."""


class FigLoginFailed(FigError):
    """Credenziali errate o blocco server."""


class FigSessionExpired(FigError):
    """Cookie di sessione non più validi, serve ri-login."""


# ─── Sessione (contenitore cookie del tesserato) ────────────────────────────


@dataclass
class FigSession:
    """
    Cookie jar minimale per una sessione autenticata su FederGolf.
    Non è persistente: vive per la durata di una request Flask.
    """

    cookies: dict[str, str] = field(default_factory=dict)
    display_name: str = ""

    @property
    def is_authenticated(self) -> bool:
        return bool(self.cookies)

    def merge(self, resp: httpx.Response) -> None:
        """Accumula set-cookie della response nella jar."""
        for k, v in resp.cookies.items():
            self.cookies[k] = v

    def header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())


# ─── Client ──────────────────────────────────────────────────────────────────


class FigClient:
    """
    Client sincrono (httpx) per l'area riservata FederGolf.
    Un'istanza per request: non condividerla fra thread senza precauzioni.
    """

    def __init__(self, fig_cfg: FigSection):
        self.cfg = fig_cfg
        self._profile_patterns_compiled = {
            k: re.compile(pat, re.IGNORECASE)
            for k, pat in fig_cfg.profile_patterns.items()
        }
        self._uuid_rx = re.compile(fig_cfg.uuid_pattern, re.IGNORECASE)
        self._error_rxs = [
            re.compile(p, re.IGNORECASE) for p in fig_cfg.error_message_patterns
        ]

    # ── httpx helpers ────────────────────────────────────────────────────

    def _headers(self, session: FigSession | None = None) -> dict[str, str]:
        h = {
            "User-Agent": self.cfg.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self.cfg.accept_language,
            "Connection": "keep-alive",
        }
        if session and session.cookies:
            h["Cookie"] = session.header()
        return h

    def _url(self, endpoint_key: str, **fmt: Any) -> str:
        template = self.cfg.endpoints[endpoint_key]
        path = template.format(**fmt) if fmt else template
        return self.cfg.base_url + path

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.cfg.timeout_sec,
            follow_redirects=False,  # gestiamo noi i redirect per leggere i cookie
        )

    # ── Login ────────────────────────────────────────────────────────────

    def login(self, tessera: str, password: str) -> FigSession:
        """
        Esegue il login sull'area riservata FIG.
        Ritorna una FigSession con i cookie validi, o solleva FigLoginFailed.

        Porta 1:1 la logica di server.js /api/login (righe 294-389):
        GET pagina login → estrae token CSRF e tutti gli hidden input →
        POST con user/password → verifica cookie di sessione.
        """
        session = FigSession()
        with self._client() as client:
            # 1. GET pagina login → cookie iniziali + token CSRF
            r1 = client.get(
                self._url("login_page"),
                headers=self._headers(session),
            )
            session.merge(r1)
            html = r1.text

            hidden_fields, form_action = self._parse_login_form(html)
            csrf = hidden_fields.get(self.cfg.login.csrf_field)
            if not csrf:
                raise FigError("Token CSRF non trovato nella pagina di login")

            log.debug(
                "FIG login GET -> %s, csrf=OK, cookies=%s",
                r1.status_code,
                list(session.cookies.keys()),
            )

            # 2. POST credenziali
            body = {
                **hidden_fields,
                self.cfg.login.user_field: tessera,
                self.cfg.login.password_field: password,
            }
            post_url = (
                form_action
                if form_action.startswith("http")
                else self.cfg.base_url + form_action
            )

            r2 = client.post(
                post_url,
                data=body,
                headers={
                    **self._headers(session),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": self.cfg.base_url,
                    "Referer": self._url("login_page"),
                },
            )
            session.merge(r2)
            body_text = r2.text

            # 3. Verifica successo: cookie sessione + NOT login page
            has_session_cookie = self.cfg.login.session_cookie in session.cookies
            is_login_page = any(m in body_text for m in self.cfg.login_failed_markers)

            log.info(
                "FIG login POST -> %s | session_cookie=%s | is_login_page=%s",
                r2.status_code,
                has_session_cookie,
                is_login_page,
            )

            if not has_session_cookie or is_login_page:
                raise FigLoginFailed(self._extract_error_message(body_text))

            # 4. Display name (best-effort)
            m = re.search(
                r"Benvenuto[,\s]+([^<!\n]{2,40})",
                body_text,
                re.IGNORECASE,
            )
            if m:
                session.display_name = m.group(1).strip()

        return session

    def _parse_login_form(
        self, html: str
    ) -> tuple[dict[str, str], str]:
        """Estrae tutti gli input del form di login + il suo action."""
        hidden: dict[str, str] = {}
        for m in re.finditer(r"<input([^>]+)>", html, re.IGNORECASE):
            attrs = m.group(1)
            name_m = re.search(r'name="([^"]+)"', attrs, re.IGNORECASE)
            value_m = re.search(r'value="([^"]*)"', attrs, re.IGNORECASE)
            if name_m:
                hidden[name_m.group(1)] = value_m.group(1) if value_m else ""

        action_m = re.search(r'<form[^>]+action="([^"]+)"', html, re.IGNORECASE)
        action = action_m.group(1) if action_m else self.cfg.endpoints["login_fallback"]
        return hidden, action

    def _extract_error_message(self, html: str) -> str:
        for rx in self._error_rxs:
            m = rx.search(html)
            if m:
                return re.sub(r"\s+", " ", m.group(1)).strip()
        return "Credenziali non valide"

    # ── Profilo ──────────────────────────────────────────────────────────

    def fetch_profilo(self, session: FigSession) -> dict[str, Any]:
        """
        Ritorna un dict profilo come l'endpoint /api/profilo del vecchio server.

        Porta la logica di server.js righe 399-538: ShowGrid → estrae UUID →
        ViewDetail → converte HTML in plain text → applica regex configurate
        + `grab_after` per le label testuali.
        """
        with self._client() as client:
            grid_r = client.get(
                self._url("anagrafica_grid"),
                headers=self._headers(session),
            )
            if self._redirected_to_login(grid_r):
                raise FigSessionExpired("Sessione FIG scaduta (grid)")

            m = self._uuid_rx.search(grid_r.text)
            if not m:
                raise FigError("UUID tesserato non trovato nella ShowGrid")
            uuid = m.group(1)
            log.debug("FIG profilo: UUID=%s", uuid)

            detail_r = client.get(
                self._url("view_detail", uuid=uuid),
                headers=self._headers(session),
            )
            if self._redirected_to_login(detail_r):
                raise FigSessionExpired("Sessione FIG scaduta (detail)")
            html = detail_r.text

        return self._parse_profilo(html)

    def _parse_profilo(self, html: str) -> dict[str, Any]:
        """Converte l'HTML ViewDetail in plain text e applica i pattern."""
        plain = self._html_to_plain(html)

        profile: dict[str, Any] = {}

        # 1. Regex esplicite da config
        for key, rx in self._profile_patterns_compiled.items():
            m = rx.search(plain)
            if m:
                profile[key] = m.group(1).strip()

        # Post-processing: handicap_index / low_hcp_index → normalizza virgola
        for k in ("handicap_index", "low_hcp_index"):
            if k in profile:
                profile[k] = profile[k].replace(",", ".")

        # 2. Campi estratti via grab_after (label testuali)
        for label in self.cfg.profile_labels:
            display_label = label.replace("_", " ")
            value = self._grab_after(plain, display_label)
            if value:
                profile[label] = value

        # 3. Pulizia valori evidentemente sbagliati (>60 char, chr(0), ecc.)
        for k in list(profile.keys()):
            v = profile[k]
            if not isinstance(v, str):
                continue
            if len(v) > 60 or "\x00" in v:
                profile[k] = None

        return profile

    @staticmethod
    def _html_to_plain(html: str) -> str:
        plain = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
        plain = re.sub(r"<style[\s\S]*?</style>", " ", plain, flags=re.IGNORECASE)
        plain = re.sub(r"<[^>]+>", " ", plain)
        replacements = {
            "&#224;": "à", "&#232;": "è", "&#236;": "ì",
            "&#242;": "ò", "&#249;": "ù", "&#39;": "'",
            "&amp;": "&", "&nbsp;": " ",
        }
        for k, v in replacements.items():
            plain = plain.replace(k, v)
        plain = re.sub(r"\s+", " ", plain)
        return plain

    @staticmethod
    def _grab_after(plain: str, label: str) -> str | None:
        """
        Replica Python di `grabAfter()` di server.js righe 423-431.
        Cerca: label (opzionale *) + spazio + valore fino alla prossima parola
        con iniziale maiuscola (nuova label) o a '---' o fine stringa.

        Importante: la label è abbinata case-insensitive (così "Circolo",
        "circolo", "CIRCOLO" vanno tutti bene), ma la lookahead che delimita
        la fine del valore deve restare case-sensitive — altrimenti un valore
        come "GOLF CLUB BERGAMO" viene troncato a "GOLF" perché il secondo
        "C" di "CLUB" matcha [A-Z] ignorecase e la [a-z] successiva matcha
        qualsiasi lettera. Usiamo il flag inline (?i:...) sulla sola label
        per segregare l'IGNORECASE.
        """
        esc = re.escape(label.strip())
        pattern = (
            r"(?i:" + esc + r")"
            r"\s*\*?\s+([\u00c0-\u00ffA-Za-z0-9@./,()'\- ]{1,60}?)"
            r"(?=\s+[A-Z][a-z\u00c0-\u00ff]|\s+---\s|\s*$)"
        )
        m = re.search(pattern, plain)  # niente flag globale: lookahead case-sensitive
        if not m:
            return None
        value = re.sub(r"\s+", " ", m.group(1).strip())
        value = re.sub(r"\s*\*\s*$", "", value)
        if value in ("", "---") or len(value) < 1:
            return None
        return value

    # ── Storico risultati ────────────────────────────────────────────────

    def fetch_storico(self, session: FigSession) -> dict[str, Any]:
        """
        Ritorna {"results": [...], "hcp_history": [...]}.

        Porta la logica di server.js righe 541-722. Provo vari pattern di
        paginazione perché il sito FIG ASP.NET MVC non usa un parametro
        standard: il primo che produce una pagina diversa dalla pagina 1
        è quello buono.
        """
        base_url = self._url("risultati_grid")
        headers = self._headers(session)
        all_rows: list[dict[str, Any]] = []

        with self._client() as client:
            r1 = self._fetch_with_retry(client, base_url, headers)
            if self._redirected_to_login(r1):
                raise FigSessionExpired("Sessione FIG scaduta")
            html1 = r1.text
            page1_rows = self._parse_result_rows(html1)
            all_rows.extend(page1_rows)
            log.debug("FIG storico pag.1 -> %d righe", len(page1_rows))

            if len(page1_rows) >= self.cfg.storico.rows_per_page:
                self._fetch_remaining_pages(client, base_url, headers, page1_rows, all_rows)

        results, hcp_history = self._rows_to_results(all_rows)
        log.info("FIG storico: %d risultati finali", len(results))
        return {"results": results, "hcp_history": hcp_history}

    def _fetch_with_retry(
        self, client: httpx.Client, url: str, headers: dict[str, str]
    ) -> httpx.Response:
        r = client.get(url, headers=headers)
        if r.status_code == 500 and self.cfg.storico.retry_on_500:
            time.sleep(self.cfg.storico.retry_delay_sec)
            r = client.get(url, headers=headers)
        return r

    def _fetch_remaining_pages(
        self,
        client: httpx.Client,
        base_url: str,
        headers: dict[str, str],
        page1_rows: list[dict[str, Any]],
        all_rows: list[dict[str, Any]],
    ) -> None:
        """
        Trova il param di paginazione corretto provando quelli in config,
        poi scarica pagina 3, 4, ... fino a max_pages o pagina vuota/duplicata.
        """
        first_marker = page1_rows[0].get("_raw_first", "")

        pagination_param: str | None = None
        for param in self.cfg.storico.pagination_params:
            probe_url = f"{base_url}?{param}=2"
            pr = self._fetch_with_retry(client, probe_url, headers)
            if pr.status_code != 200:
                continue
            probe_rows = self._parse_result_rows(pr.text)
            if probe_rows and probe_rows[0].get("_raw_first", "") != first_marker:
                log.debug("FIG storico: param paginazione = %s", param)
                all_rows.extend(probe_rows)
                pagination_param = param
                # Ci segniamo la seconda pagina per confronto successivo
                prev_marker = probe_rows[0].get("_raw_first", "")
                break
        else:
            return  # Nessun param ha funzionato

        # Scarica pagine 3..max_pages
        for p in range(3, self.cfg.storico.max_pages + 1):
            pr = self._fetch_with_retry(
                client, f"{base_url}?{pagination_param}={p}", headers
            )
            if pr.status_code != 200:
                break
            rows = self._parse_result_rows(pr.text)
            if not rows:
                break
            if rows[0].get("_raw_first", "") == prev_marker:
                break  # pagina duplicata = fine storico
            all_rows.extend(rows)
            prev_marker = rows[0].get("_raw_first", "")
            if len(rows) < self.cfg.storico.rows_per_page:
                break  # ultima pagina

    @staticmethod
    def _parse_result_rows(html: str) -> list[dict[str, Any]]:
        """
        Estrae righe della tabella risultati. Ogni riga è un dict con le
        celle testuali + eventuali `_garaId` e `_circoloId` da link GesGolf.
        """
        tbody_m = re.search(r"<tbody[^>]*>([\s\S]*?)</tbody>", html, re.IGNORECASE)
        tbody = tbody_m.group(1) if tbody_m else html

        rows: list[dict[str, Any]] = []
        for row_m in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", tbody, re.IGNORECASE):
            row_html = row_m.group(1)
            cells = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c.group(1))).strip()
                for c in re.finditer(
                    r"<td[^>]*>([\s\S]*?)</td>", row_html, re.IGNORECASE
                )
            ]
            if len(cells) < 10:
                continue
            gara_id_m = re.search(r"GaraId=(\d+)", row_html, re.IGNORECASE)
            circolo_m = re.search(r"circolo_id=(\d+)", row_html, re.IGNORECASE)
            rows.append(
                {
                    "cells": cells,
                    "_raw_first": cells[0] if cells else "",
                    "_garaId": gara_id_m.group(1) if gara_id_m else "",
                    "_circoloId": circolo_m.group(1) if circolo_m else "",
                }
            )
        return rows

    @staticmethod
    def _rows_to_results(
        all_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Converte le righe grezze in oggetti risultato strutturati.
        Stessa mappatura posizionale di server.js righe 674-709.
        """
        def parse_date(s: str):
            from datetime import datetime
            parts = (s or "").split("/")
            if len(parts) != 3:
                return datetime(1970, 1, 1)
            try:
                return datetime(int(parts[2]), int(parts[1]), int(parts[0]))
            except ValueError:
                return datetime(1970, 1, 1)

        results: list[dict[str, Any]] = []
        hcp_history: list[dict[str, Any]] = []

        for i, row in enumerate(all_rows):
            c = row["cells"]

            def col(idx: int) -> str:
                return c[idx] if idx < len(c) else ""

            res = {
                "id": i,
                "garaId": row.get("_garaId", ""),
                "circoloIdGes": row.get("_circoloId", ""),
                "data": col(0),
                "tessera": col(1),
                "tesserato": col(1),
                "gara": col(2),
                "tipoRisultato": col(3),
                "esecutore": col(4),
                "giro": col(5),
                "formula": col(6),
                "buche": col(7),
                "valida": col(8),
                "playingHcp": col(9),
                "par": col(10),
                "cr": col(11),
                "sr": col(12),
                "stbl": col(13),
                "ags": col(14),
                "pcc": col(15),
                "sd": col(16),
                "corrSd": col(17),
                "corr": col(18),
                "indexVecchio": col(19),
                "indexNuovo": col(20),
                "variazione": col(21),
                "motivazione": col(6),
            }
            results.append(res)
            if res["indexNuovo"] and res["data"]:
                try:
                    val = float(res["indexNuovo"].replace(",", "."))
                    hcp_history.append({"date": res["data"], "value": val})
                except ValueError:
                    pass

        results.sort(key=lambda r: parse_date(r["data"]), reverse=True)
        hcp_history.sort(key=lambda r: parse_date(r["date"]))
        return results, hcp_history

    # ── Utils ────────────────────────────────────────────────────────────

    def _redirected_to_login(self, resp: httpx.Response) -> bool:
        loc = str(resp.url)
        return self.cfg.endpoints["login_page"] in loc
