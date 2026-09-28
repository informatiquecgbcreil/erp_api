"""Revue des droits route par route (demande de consolidation, P3).

Chaque route GET sans paramètre est visitée par chaque rôle par défaut :
- aucune ne doit planter (500) ;
- les pages d'administration technique restent fermées aux rôles métier ;
- aucune route de l'application n'est ouverte sans connexion en dehors de
  la liste publique connue (kiosque, connexion, fichiers statiques…).

Les routes à identifiant (fiches, séances…) sont couvertes par les tests de
périmètre dédiés (test_kiosque_droits, test_consolidation_access, …).
"""
import uuid

import pytest

MDP = "motdepasse-tests"

#: Routes volontairement publiques. Toute nouvelle route sans connexion doit
#: être ajoutée ici en connaissance de cause.
PUBLIQUES = {
    "static", "media_file", "healthz", "source_archive",
    "auth.login", "auth.password_reset_request", "auth.password_reset_token",
    "main.calendrier_ics", "launcher.index", "launcher.launcher_qr",
    # Application installable : manifeste, service worker, page hors connexion
    # et mode d'emploi, avant toute connexion (aucune donnée).
    "pwa.manifeste", "pwa.service_worker", "pwa.hors_ligne", "pwa.installer",
}
ROLES = ["animateur", "accueil", "responsable_secteur", "finance", "directrice", "admin_tech"]
#: Pages d'administration technique : jamais pour un rôle métier limité.
#: (Les imports Excel et historique, eux, sont des outils métier bornés aux
#: secteurs de la personne : admin/routes.import_excel, historical_routes.)
ADMIN_TECH = ("admin.users", "admin.droits", "admin.debug_rbac", "admin.instance_settings",
              "admin.modules", "admin.sauvegardes", "admin.journal_audit", "admin.matrice_droits",
              "admin.sante_systeme")


def _routes_get_sans_parametre(app):
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments:
            continue
        if rule.endpoint.startswith(("static", "setup.")) or rule.endpoint in {"auth.logout"}:
            continue
        yield rule


def _compte(app, role):
    from app.extensions import db
    from app.models import Role, User
    email = f"crawl-{role}-{uuid.uuid4().hex[:6]}@test.fr"
    with app.app_context():
        u = User(email=email, nom=f"Crawl {role}", secteur_assigne="Numérique")
        u.set_password(MDP)
        u.roles.append(Role.query.filter_by(code=role).one())
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    assert c.post("/", data={"email": email, "password": MDP}).status_code == 302
    return c


def test_aucune_route_ouverte_sans_connexion_hors_liste_publique(app):
    ouvertes = []
    client = app.test_client()
    for rule in _routes_get_sans_parametre(app):
        if rule.endpoint.startswith("kiosk.") or rule.endpoint in PUBLIQUES:
            continue
        r = client.get(rule.rule)
        if r.status_code == 200:
            ouvertes.append(rule.rule)
    assert ouvertes == []


@pytest.mark.parametrize("role", ROLES)
def test_chaque_role_parcourt_les_pages_sans_erreur(app, role):
    client = _compte(app, role)
    erreurs, admin_ouvertes = [], []
    for rule in _routes_get_sans_parametre(app):
        if rule.endpoint.startswith("kiosk."):
            continue
        r = client.get(rule.rule)
        if r.status_code >= 500:
            erreurs.append((rule.rule, r.status_code))
        if role not in {"admin_tech", "directrice"} and rule.endpoint.startswith(ADMIN_TECH) and r.status_code == 200:
            admin_ouvertes.append(rule.rule)
    assert erreurs == []
    assert admin_ouvertes == []
