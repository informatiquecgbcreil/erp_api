from flask import Blueprint

bp = Blueprint("kiosk", __name__, url_prefix="/kiosk")

from . import routes  # noqa: E402,F401
