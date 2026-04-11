"""Blueprint scorecard: upload immagine + OCR via Anthropic API + review."""

from flask import Blueprint

bp = Blueprint("scorecard", __name__, url_prefix="/scorecard")

from . import routes  # noqa: E402,F401
