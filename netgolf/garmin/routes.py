from __future__ import annotations
import logging
from flask import jsonify, request
from flask_babel import gettext as _
from flask_login import current_user, login_required
from .client import GarminError, GarminLoginFailed, GarminRateLimited
from .service import GarminCredentialsMissing, GarminService
from . import bp

log = logging.getLogger(__name__)


def _handle_garmin_errors(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GarminCredentialsMissing:
            return jsonify(error=_("Credenziali Garmin non configurate."), code="garmin_credentials_missing"), 412
        except GarminLoginFailed as e:
            return jsonify(error=str(e), code="garmin_login_failed"), 401
        except GarminRateLimited as e:
            return jsonify(error=str(e), code="garmin_rate_limited"), 429
        except GarminError as e:
            return jsonify(error=str(e), code="garmin_error"), 502
    return wrapper


@bp.get("/scorecards")
@login_required
@_handle_garmin_errors
def scorecards():
    """Scarica tutte le scorecard Garmin dell'utente."""
    service = GarminService.from_app()
    data = service.fetch_scorecards(current_user)
    return jsonify(scorecards=data, totale=len(data))


@bp.post("/credentials")
@login_required
def save_credentials():
    """Salva le credenziali Garmin nel profilo."""
    from flask_babel import gettext as _
    data = request.get_json() or {}
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify(error=_("Email e password sono obbligatorie.")), 400

    service = GarminService.from_app()
    service.save_credentials(current_user, email, password)
    return jsonify(ok=True, message=_("Credenziali Garmin salvate."))


@bp.delete("/credentials")
@login_required
def delete_credentials():
    """Rimuove le credenziali Garmin."""
    from flask_babel import gettext as _
    service = GarminService.from_app()
    service.delete_credentials(current_user)
    return jsonify(ok=True, message=_("Credenziali Garmin rimosse."))
