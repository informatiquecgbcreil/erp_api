from flask import Blueprint

bp = Blueprint("quartiers", __name__, url_prefix="/quartiers")

from . import routes  # noqa: E402,F401
