"""Blueprint fig: client FederGolf e endpoint che espongono profilo/storico."""

from flask import Blueprint

bp = Blueprint("fig", __name__, url_prefix="/api/fig")

from . import routes  # noqa: E402,F401
