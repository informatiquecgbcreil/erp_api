from flask import Blueprint

bp = Blueprint("inscriptions_annuelles", __name__, url_prefix="/inscriptions-annuelles")

from . import routes  # noqa: E402,F401
