from flask import Blueprint

bp = Blueprint("campi", __name__, url_prefix="/api/campi")

from . import routes  # noqa: E402,F401
