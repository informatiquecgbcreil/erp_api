from flask import Blueprint

bp = Blueprint("salles", __name__, url_prefix="/salles")

from . import routes  # noqa: E402,F401
from . import locations  # noqa: E402,F401
