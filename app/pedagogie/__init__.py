from flask import Blueprint

bp = Blueprint("pedagogie", __name__, url_prefix="/pedagogie")

from . import routes  # noqa: E402,F401
