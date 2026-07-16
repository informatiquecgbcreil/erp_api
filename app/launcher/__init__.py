from flask import Blueprint

bp = Blueprint("launcher", __name__, url_prefix="/launcher")

from . import routes  # noqa: F401
