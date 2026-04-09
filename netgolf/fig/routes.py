"""
Endpoint JSON del blueprint fig.

Mapping con il vecchio server.js:
  /api/login        → soppresso (il login ora è quello NETGOLF, auth.login)
  /api/profilo      → /api/fig/profilo
  /api/storico      → /api/fig/storico
  /api/logout       → soppresso (vedi auth.logout)
"""

from __future__ import annotations

from flask import jsonify
from flask_babel import gettext as _
from flask_login import current_user, login_required

from .client import FigError, FigLoginFailed, FigSessionExpired
from .service import FigCredentialsMissing, FigService
from . import bp


def _handle_fig_errors(fn):
    """Decoratore che mappa le eccezioni FIG in risposte JSON sensate."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FigCredentialsMissing:
            return jsonify(
                error=_("Credenziali FIG non configurate."),
                code="fig_credentials_missing",
            ), 412  # 412 Precondition Failed
        except FigLoginFailed as e:
            return jsonify(
                error=_("Login FIG fallito: %(msg)s", msg=str(e)),
                code="fig_login_failed",
            ), 401
        except FigSessionExpired:
            return jsonify(
                error=_("Sessione FIG scaduta, riprova."),
                code="fig_session_expired",
            ), 401
        except FigError as e:
            return jsonify(
                error=_("Errore comunicazione FIG: %(msg)s", msg=str(e)),
                code="fig_error",
            ), 502

    return wrapper


@bp.get("/profilo")
@login_required
@_handle_fig_errors
def profilo():
    service = FigService.from_app()
    data = service.fetch_profilo(current_user)
    return jsonify(profile=data)


@bp.get("/storico")
@login_required
@_handle_fig_errors
def storico():
    service = FigService.from_app()
    data = service.fetch_storico(current_user)
    return jsonify(**data)


@bp.get("/all")
@login_required
@_handle_fig_errors
def profilo_e_storico():
    """Endpoint combinato: un solo login FIG per profilo+storico."""
    service = FigService.from_app()
    data = service.fetch_profilo_e_storico(current_user)
    return jsonify(**data)
