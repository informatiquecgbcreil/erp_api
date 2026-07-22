from flask import Blueprint

bp = Blueprint("veille", __name__, url_prefix="/veille-financements")

from . import routes  # noqa: E402,F401
