from flask import Blueprint
bp = Blueprint("garmin", __name__, url_prefix="/garmin")
from . import routes  # noqa: E402,F401
