"""Blueprint gesgolf: client GesGolf + endpoint scorecard."""

from flask import Blueprint

bp = Blueprint("gesgolf", __name__, url_prefix="/api/gesgolf")

from . import routes  # noqa: E402,F401
