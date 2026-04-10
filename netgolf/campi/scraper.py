"""
Scraper del DB campi (Slope + Course Rating) dal sito FederGolf.

Porta in Python la logica di scripts/scrape_campi.js del vecchio progetto.
La pagina https://www.federgolf.it/settore-tecnico/calcolo-hcp/ è WordPress:
espone un endpoint admin-ajax con action/nonce che, dato circolo+circolo_id,
risponde con il JSON dei percorsi (ciascuno con i propri tees e rating).

Strategia:
  1. GET della pagina HTML, estrae:
     - lista circoli dai <option value="..."> del select
     - URL admin-ajax, action, nonce dai globals JS inline
  2. POST su admin-ajax per ogni circolo, con delay tra richieste
  3. Scrive il JSON risultante nello stesso formato atteso da config.yaml
     (data_files.campi_slope_cr).

Uso:
    flask --app netgolf:create_app campi-refresh
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


BASE = "https://www.federgolf.it"
CALCOLO_HCP_PATH = "/settore-tecnico/calcolo-hcp/"
DEFAULT_AJAX = BASE + "/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,*/*",
    "Accept-Language": "it-IT,it;q=0.9",
}


class CampiScraperError(Exception):
    pass


def scrape_campi(
    output_path: str | Path,
    delay_sec: float = 0.2,
    timeout_sec: float = 12.0,
    progress_callback=None,
) -> dict[str, Any]:
    """
    Esegue lo scraping completo e salva il risultato.

    output_path  Dove scrivere il JSON (sovrascrive se esiste).
    delay_sec    Pausa tra POST consecutivi (rate limiting cortese).
    progress_callback(i, n, nome) opzionale per CLI/UI che vuole mostrare
                 l'avanzamento.

    Ritorna il dict finale (utile per test).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers=HEADERS, timeout=timeout_sec) as client:
        # 1. GET pagina HCP
        log.info("Scraping campi: fetch pagina calcolo-hcp")
        r = client.get(BASE + CALCOLO_HCP_PATH)
        r.raise_for_status()
        html = r.text

        circoli = _extract_circoli(html)
        if not circoli:
            raise CampiScraperError("Nessun circolo estratto dal select HTML")
        log.info("Circoli trovati: %d", len(circoli))

        ajaxurl = _first_match(
            html, r"""ajaxurl["'\s]*[=:]\s*['"]([^'"]+)['"]"""
        ) or DEFAULT_AJAX
        nonce = _first_match(
            html, r"""nonce["'\s]*[=:]\s*['"]([a-f0-9]{6,20})['"]"""
        ) or ""
        action = (
            _first_match(
                html,
                r"""action["'\s]*[=:]\s*['"]([a-z_]*(?:percors|circol|tee|slope)[a-z_]*)['"]""",
                flags=re.IGNORECASE,
            )
            or "fig_get_percorsi"
        )
        log.info("ajaxurl=%s | action=%s | nonce=%s", ajaxurl, action, nonce or "(vuoto)")

        # 2. Loop POST
        result: list[dict[str, Any]] = []
        ajax_headers = {
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + CALCOLO_HCP_PATH,
        }

        for i, circ in enumerate(circoli):
            if progress_callback:
                progress_callback(i + 1, len(circoli), circ["nome"])
            if delay_sec > 0:
                time.sleep(delay_sec)

            body = {
                "action": action,
                "circolo": circ["nome"],
                "circolo_id": circ["id"],
                "nonce": nonce,
            }
            percorsi: list[Any] = []
            try:
                rr = client.post(ajaxurl, data=body, headers=ajax_headers)
                txt = rr.text.strip()
                # Il backend risponde "0" se la action non è registrata;
                # ignoriamo anche risposte HTML (redirect a homepage WP)
                if txt and txt != "0" and "<!DOCTYPE" not in txt:
                    try:
                        percorsi = json.loads(txt)
                    except json.JSONDecodeError:
                        log.debug(
                            "Risposta non-JSON per %s: %r", circ["nome"], txt[:100]
                        )
            except httpx.HTTPError as e:
                log.warning("Fetch %s fallito: %s", circ["nome"], e)

            result.append({**circ, "percorsi": percorsi or []})

        # 3. Salva
        db = {
            "aggiornato": datetime.now(timezone.utc).isoformat(),
            "fonte": BASE,
            "totale": len(result),
            "completato": len(result),
            "circoli": result,
        }
        output_path.write_text(json.dumps(db, indent=2, ensure_ascii=False))
        log.info("DB campi salvato in %s (%d circoli)", output_path, len(result))
        return db


def _extract_circoli(html: str) -> list[dict[str, str]]:
    """
    Estrae dal <select> HTML la lista di circoli con id + nome uppercase.
    Equivalente Python del loop `while ((m=rx.exec(html))!==null)` JS.
    """
    circoli: list[dict[str, str]] = []
    seen: set[str] = set()
    rx = re.compile(
        r'<option[^>]*value="([^"]+)"[^>]*>([^<]+)</option>', re.IGNORECASE
    )
    for m in rx.finditer(html):
        cid = m.group(1).strip()
        nome = m.group(2).strip()
        if not cid or not nome:
            continue
        if "seleziona" in nome.lower():
            continue
        if cid in seen:
            continue
        seen.add(cid)
        circoli.append({"id": cid, "nome": nome.upper()})
    return circoli


def _first_match(text: str, pattern: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags=flags)
    return m.group(1) if m else None
