from flask import Blueprint

bp = Blueprint("activite", __name__, url_prefix="/activite")

from . import routes  # noqa: E402,F401
