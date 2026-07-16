from flask import Blueprint

bp = Blueprint("partenaires", __name__, url_prefix="/partenaires")

from . import routes  # noqa: E402,F401
