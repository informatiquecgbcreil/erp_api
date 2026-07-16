import argparse
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import User, Role
from app.rbac import bootstrap_rbac


REQUIRED_TABLES = {
    "user",
    "role",
    "permission",
    "user_roles",
    "role_permissions",
}

def _safe_role_codes(u):
    rc = getattr(u, "role_codes", None)
    if callable(rc):
        return rc()
    if rc is None:
        return "n/a"
    return rc


def ensure_db_is_sane():
    """Vérifie que les tables/colonnes minimales existent. Crash propre si incohérent."""
    insp = inspect(db.engine)

    # tables présentes ?
    existing = set(insp.get_table_names())
    missing = sorted(REQUIRED_TABLES - existing)
    if missing:
        raise RuntimeError(
            "DB incomplète (tables manquantes): " + ", ".join(missing) +
            " | Lance d'abord l'init DB (create_all + bootstrap_rbac)."
        )

    # colonnes minimales côté user
    user_cols = {c["name"] for c in insp.get_columns("user")}
    for col in ("id", "email", "password_hash", "nom"):
        if col not in user_cols:
            raise RuntimeError(f"DB incompatible: colonne user.{col} manquante")


def ensure_user(email: str, password: str, role_code: str, nom: str, secteur: str | None):
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("Email vide")

    u = User.query.filter_by(email=email).first()
    created = False

    if not u:
        u = User(email=email, nom=nom or "Utilisateur")
        created = True

    if secteur is not None:
        u.secteur_assigne = secteur

    u.set_password(password)

    db.session.add(u)
    db.session.commit()

    role = Role.query.filter_by(code=role_code).first()
    if not role:
        role = Role(code=role_code, label=role_code)
        db.session.add(role)
        db.session.commit()

    if hasattr(u, "roles") and role not in u.roles:
        u.roles.append(role)
        db.session.commit()

    return u, created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="admin@asso.com")
    parser.add_argument(
        "--password",
        required=True,
        help="Mot de passe du compte (obligatoire, 12 caractères minimum)",
    )
    parser.add_argument(
        "--role",
        default="admin_tech",
        choices=["direction", "directrice", "directeur", "finance", "responsable_secteur", "admin_tech"],
    )
    parser.add_argument("--nom", default="Admin Test")
    parser.add_argument("--secteur", default=None)
    args = parser.parse_args()

    if len(args.password) < 12:
        parser.error("Le mot de passe doit contenir au moins 12 caractères.")

    app = create_app()

    with app.app_context():
        print("DB URI     =", app.config.get("SQLALCHEMY_DATABASE_URI"))
        print("DB DIALECT =", db.engine.dialect.name)

        # 1) Crée tout ce que SQLAlchemy connaît
        db.create_all()

        # 2) Bootstrap RBAC (idempotent)
        bootstrap_rbac()

        # 3) Vérifie que la DB est cohérente (sinon message clair)
        ensure_db_is_sane()

        # 4) Crée/répare le user
        role_aliases = {
            "directrice": "direction",
            "directeur": "direction",
        }
        role_code = role_aliases.get(args.role, args.role)

        u, created = ensure_user(
            email=args.email,
            password=args.password,
            role_code=role_code,
            nom=args.nom,
            secteur=args.secteur,
        )

        print("=== BOOTSTRAP OK ===")
        print("created =", created)
        print("email   =", u.email)
        print("rbac    =", _safe_role_codes(u))
        print("password reset done.")


if __name__ == "__main__":
    main()
