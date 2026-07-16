from flask import Blueprint

bp = Blueprint("questionnaires", __name__, url_prefix="/questionnaires")

from . import routes  # noqa: E402,F401
