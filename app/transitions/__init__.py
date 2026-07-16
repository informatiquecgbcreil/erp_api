from flask import Blueprint

bp = Blueprint("transitions", __name__, url_prefix="/transitions")

from . import routes  # noqa: E402,F401
