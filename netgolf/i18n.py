"""
Internazionalizzazione.

Regole di selezione del locale, in ordine di priorità:
  1. querystring ?lang=xx  (se xx è supportato)
  2. cookie netgolf_lang    (se valore è supportato)
  3. preferenza salvata sull'User (se loggato)
  4. header Accept-Language del browser
  5. default_locale da config

Quando arriva ?lang=xx, la vista auth/i18n salva anche il cookie, così la
scelta persiste tra richieste successive.
"""

from __future__ import annotations

from flask import current_app, request
from flask_babel import Babel
from flask_login import current_user

babel = Babel()


def select_locale() -> str:
    cfg = current_app.config["NETGOLF"].raw.i18n
    supported = set(cfg.supported_locales)

    # 1. querystring
    q = request.args.get(cfg.query_param)
    if q and q in supported:
        return q

    # 2. cookie
    c = request.cookies.get(cfg.cookie_name)
    if c and c in supported:
        return c

    # 3. preferenza utente loggato
    try:
        if current_user.is_authenticated and current_user.locale in supported:
            return current_user.locale
    except Exception:
        pass

    # 4. Accept-Language
    best = request.accept_languages.best_match(list(supported))
    if best:
        return best

    # 5. default
    return cfg.default_locale


def init_babel(app) -> None:
    babel.init_app(app, locale_selector=select_locale)
