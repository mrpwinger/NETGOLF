from flask import Blueprint

bp = Blueprint("frasi", __name__, url_prefix="/api/frase")

from . import routes  # noqa: E402,F401
