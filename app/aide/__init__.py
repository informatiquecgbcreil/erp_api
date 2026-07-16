from flask import Blueprint

bp = Blueprint("aide", __name__, url_prefix="/aide")

from app.aide import routes  # noqa: E402,F401
