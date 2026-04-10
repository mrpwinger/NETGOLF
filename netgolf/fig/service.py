"""
Service layer FIG: tiene insieme le credenziali salvate dell'utente e il
client di scraping.

Flusso tipico:
    service = FigService.from_app()
    data = service.fetch_profilo(user)

Al suo interno:
  1. Legge le credenziali FIG cifrate dal DB (via auth.get_fig_credentials_plain).
  2. Se mancano, ritorna None / solleva FigCredentialsMissing.
  3. Se ci sono, decifra con la master key del server.
  4. Usa FigClient per fare login + fetch.
  5. Riporta il risultato.

Nota: non facciamo caching aggressivo dei cookie di sessione FIG. Ogni
chiamata del service ri-autentica da capo. In futuro si può ottimizzare con
un cookie jar condiviso con TTL breve, ma finché NETGOLF è "a uso interattivo"
questo costa pochi secondi in più e toglie una classe di bug.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import current_app

from ..config import AppConfig
from ..models import User
from .client import FigClient, FigError, FigLoginFailed, FigSession

log = logging.getLogger(__name__)


class FigCredentialsMissing(FigError):
    """L'utente non ha ancora configurato tessera + password FIG."""


class FigService:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client = FigClient(cfg.raw.fig)

    @classmethod
    def from_app(cls) -> "FigService":
        return cls(current_app.config["NETGOLF"])

    # ── Core ─────────────────────────────────────────────────────────────

    def _login(self, user: User) -> FigSession:
        """Prende le credenziali cifrate dal DB, decifra, fa login."""
        from ..auth.routes import get_fig_credentials_plain

        creds = get_fig_credentials_plain(user)
        if creds is None:
            raise FigCredentialsMissing(
                "Credenziali FIG non configurate. "
                "Impostale dal profilo NETGOLF."
            )
        tessera, password = creds
        log.info("FIG login per utente %s (tessera %s)", user.email, tessera)
        return self.client.login(tessera, password)

    def fetch_profilo(self, user: User) -> dict[str, Any]:
        session = self._login(user)
        profilo = self.client.fetch_profilo(session)
        self._cache_profilo(profilo)
        return profilo

    def fetch_storico(self, user: User) -> dict[str, Any]:
        session = self._login(user)
        return self.client.fetch_storico(session)

    def fetch_profilo_e_storico(self, user: User) -> dict[str, Any]:
        """
        Un singolo login per prendere sia profilo che storico (più efficiente
        di due chiamate consecutive, evita il doppio login FIG).
        """
        session = self._login(user)
        profilo = self.client.fetch_profilo(session)
        self._cache_profilo(profilo)
        storico = self.client.fetch_storico(session)
        return {"profilo": profilo, **storico}

    @staticmethod
    def _cache_profilo(profilo: dict[str, Any]) -> None:
        """
        Memorizza nella sessione Flask i campi del profilo che altri
        blueprint (es. gesgolf) possono dover leggere senza rifare login FIG.
        Chiamare SOLO da contesto di request, altrimenti session è undefined.
        """
        from flask import session as flask_session

        flask_session["fig_profilo"] = {
            "nome": profilo.get("nome"),
            "cognome": profilo.get("cognome"),
            "tessera": profilo.get("tessera"),
            "circolo": profilo.get("circolo"),
        }

    @staticmethod
    def get_cached_profilo() -> dict[str, Any] | None:
        """Ritorna il profilo cachato in sessione, o None se mai fetchato."""
        from flask import session as flask_session

        return flask_session.get("fig_profilo")
