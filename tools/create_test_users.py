import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

"""Crée deux comptes de test (admin + direction) de manière idempotente."""

from app import create_app
from app.extensions import db
from app.rbac import bootstrap_rbac
from bootstrap_user import ensure_db_is_sane, ensure_user, _safe_role_codes

TEST_USERS = [
    {
        "email": "admin.test@mars.local",
        "password": "AdminMars123!",
        "role": "admin_tech",
        "nom": "Admin Test",
        "secteur": None,
    },
    {
        "email": "directeur.test@mars.local",
        "password": "DirecteurMars123!",
        "role": "direction",
        "nom": "Directeur Test",
        "secteur": None,
    },
]


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        bootstrap_rbac()
        ensure_db_is_sane()

        print("=== CREATE TEST USERS ===")
        for payload in TEST_USERS:
            user, created = ensure_user(
                email=payload["email"],
                password=payload["password"],
                role_code=payload["role"],
                nom=payload["nom"],
                secteur=payload["secteur"],
            )
            print(
                f"- {user.email:<30} created={str(created):<5} role={_safe_role_codes(user)}"
            )


if __name__ == "__main__":
    main()
