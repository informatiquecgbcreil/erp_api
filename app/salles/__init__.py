from flask import Blueprint

bp = Blueprint("salles", __name__, url_prefix="/salles")

from . import routes  # noqa: E402,F401
