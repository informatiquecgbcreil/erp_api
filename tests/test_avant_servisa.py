"""Correctifs de la relecture de main (605cadc) avant mise à jour d'un serveur.

- façade « hors les murs » : les salariés du réseau local ne sont pas pris
  pour des visiteurs d'Internet ;
- rôle Accueil : il reçoit tout le monde, il doit pouvoir modifier toutes les
  fiches (permission dédiée, sans la portée structure qui ouvrirait le
  pilotage financier et le journal), y compris sur une base existante ;
- synthèse d'un participant : plus d'erreur 500 hors rôle global ;
- fusion et nettoyage des fausses fiches bornés au secteur pour les rôles
  qui ne lisent l'annuaire qu'en lecture ;
- écran des doublons accessible à l'animateur ;
- un chemin de document hors des dossiers métier ne bloque plus
  l'anonymisation ;
- kiosque : tout l'annuaire retrouvable (accents, majuscules, fautes de
  frappe), pour ne plus créer « Celine michu » à côté de « Céline Michut » ;
  compteurs adaptés à un tunnel et à l'arrivée d'un groupe ;
- reprise Windows : pas de mot de passe en ligne de commande, PostgreSQL 18.
"""
import importlib.util
from pathlib import Path
import uuid

import pytest
from sqlalchemy.engine import make_url


def _login(app, email, password="motdepasse-tests"):
    client = app.test_client()
    r = client.post("/", data={"email": email, "password": password})
    assert r.status_code == 302, r.status_code
    return client


def _user(app, code, secteur, key):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        user = User(email=f"{code}{key}@test.fr", nom=code, secteur_assigne=secteur)
        user.set_password("motdepasse-tests")
        user.roles.append(Role.query.filter_by(code=code).one())
        db.session.add(user)
        db.session.commit()
        return user.email


@pytest.fixture
def monde(app):
    from app.extensions import db
    from app.models import Participant
    key = uuid.uuid4().hex[:10]
    with app.app_context():
        a = Participant(nom="Maison" + key, prenom="Anne", created_secteur="Numérique")
        b = Participant(nom="Ailleurs" + key, prenom="Bruno", created_secteur="EPE")
        db.session.add_all([a, b])
        db.session.commit()
        ids = a.id, b.id
    return key, ids


# ---------------------------------------------------------------- façade LAN

@pytest.mark.parametrize("host", ["192.168.1.10:8000", "10.0.0.5", "srv-cgb.local:8000", "[fe80::1]:8000",
                                  "100.101.102.103:8000"])
def test_reseau_local_reste_interne_avec_facade(app, monkeypatch, host):
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "servisa.tailea5a8f.ts.net")
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "http://192.168.1.10:8000")
    assert app.test_client().get("/", headers={"Host": host}).status_code == 200, host


def test_nom_de_ce_serveur_reste_interne(app, monkeypatch):
    import socket
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "servisa.tailea5a8f.ts.net")
    nom = socket.gethostname().split(".")[0].lower()
    client = app.test_client()
    assert client.get("/", headers={"Host": nom + ":8000"}).status_code == 200
    # Un autre nom court n'est pas deviné : à déclarer dans ERP_LAN_HOSTS.
    assert client.get("/", headers={"Host": "autre-poste-" + nom}).status_code == 403
    monkeypatch.setitem(app.config, "LAN_HOSTS", ["autre-poste-" + nom])
    assert client.get("/", headers={"Host": "autre-poste-" + nom}).status_code == 200


@pytest.mark.parametrize("host", ["servisa.tailea5a8f.ts.net", "servisa.tailea5a8f.ts.net.", "site-inconnu.fr", "a", ""])
def test_hote_public_ou_inconnu_reste_ferme(app, monkeypatch, host):
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "servisa.tailea5a8f.ts.net")
    client = app.test_client()
    if host:
        r = client.get("/", headers={"Host": host})
    else:
        r = client.get("/", environ_overrides={"HTTP_HOST": ""})
    assert r.status_code == 403


def test_entete_de_tunnel(app, monkeypatch):
    client = app.test_client()
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "")
    # Funnel : toujours public.
    assert client.get("/", headers={"Host": "192.168.1.10:8000", "Tailscale-Funnel-Request": "?1"}).status_code == 403
    # Cloudflare sans façade configurée : site servi par Cloudflare, pas de kiosque public.
    assert client.get("/", headers={"Host": "192.168.1.10:8000", "Cf-Connecting-Ip": "1.2.3.4"}).status_code == 200
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "kiosque.exemple.fr")
    assert client.get("/", headers={"Host": "192.168.1.10:8000", "Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403


def test_nom_de_domaine_lan_declare(app, monkeypatch):
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "kiosque.exemple.fr")
    client = app.test_client()
    assert client.get("/", headers={"Host": "erp.centre-social.fr"}).status_code == 403
    monkeypatch.setitem(app.config, "LAN_HOSTS", ["erp.centre-social.fr"])
    assert client.get("/", headers={"Host": "erp.centre-social.fr"}).status_code == 200


# ---------------------------------------------------------------- accueil

def test_accueil_agit_sur_toutes_les_fiches(app, monde):
    from app.extensions import db
    from app.models import Participant
    key, (a, b) = monde
    client = _login(app, _user(app, "accueil", "Numérique", key))
    r = client.post(f"/participants/{b}/edit", data={"nom": "Guichet" + key, "prenom": "Bruno"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, b).nom == "Guichet" + key


def test_accueil_sans_secteur_travaille(app, monde):
    key, (a, b) = monde
    client = _login(app, _user(app, "accueil", None, key))
    assert client.get(f"/participants/{a}/edit").status_code == 200
    assert client.get(f"/participants/{b}/edit").status_code == 200


def test_accueil_ne_recoit_pas_la_portee_structure(app, monde):
    key, _ = monde
    client = _login(app, _user(app, "accueil", "Numérique", key))
    for url in ("/direction/pilotage", "/journal-metier", "/participants/import"):
        assert client.get(url).status_code in (302, 403, 404), url


def test_accueil_existant_recoit_la_permission_dediee(app):
    """Base créée avant la permission : attribution automatique au démarrage."""
    from app.extensions import db
    from app.models import Permission, Role
    from app.rbac import PERMS_AUTO_GRANT
    assert PERMS_AUTO_GRANT["participants:edit_all"] == ("accueil",)
    with app.app_context():
        role = Role.query.filter_by(code="accueil").one()
        assert "participants:edit_all" in {p.code for p in role.permissions}
        assert "scope:all_secteurs" not in {p.code for p in role.permissions}
        assert Permission.query.filter_by(code="participants:edit_all").one()


def test_synthese_pour_l_animateur(app, monde):
    from app.extensions import db
    from app.models import AtelierActivite, PresenceActivite, SessionActivite
    key, (a, b) = monde
    with app.app_context():
        atelier = AtelierActivite(nom="Synthese" + key, secteur="Numérique")
        db.session.add(atelier); db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Numérique")
        db.session.add(s); db.session.flush()
        db.session.add(PresenceActivite(session_id=s.id, participant_id=a))
        db.session.commit()
    client = _login(app, _user(app, "animateur", "Numérique", key))
    assert client.get(f"/participants/{a}/synthese").status_code == 200


# ---------------------------------------------------------------- fusion / nettoyage

def test_responsable_ne_fusionne_pas_hors_secteur(app, monde):
    from app.extensions import db
    from app.models import Participant, Role
    key, (a, b) = monde
    email = _user(app, "responsable_secteur", "Numérique", key)
    with app.app_context():
        from app.models import User, Permission
        role = Role.query.filter_by(code="responsable_secteur").one()
        assert "participants:view_all" in {p.code for p in role.permissions}
        perm = Permission.query.filter_by(code="participants:delete").first()
        if perm and perm not in role.permissions:
            role.permissions.append(perm)
            db.session.commit()
    client = _login(app, email)
    r = client.post("/participants/merge", data={"keep_id": a, "merge_ids": [b]})
    assert r.status_code == 403
    with app.app_context():
        assert db.session.get(Participant, b) is not None


def test_nettoyage_des_faux_borne_au_secteur(app):
    from app.extensions import db
    from app.models import Participant, Permission, Role
    key = uuid.uuid4().hex[:8]
    with app.app_context():
        faux = Participant(nom="TOTAL ?" + key, prenom="", created_secteur="EPE")
        db.session.add(faux)
        role = Role.query.filter_by(code="responsable_secteur").one()
        perm = Permission.query.filter_by(code="participants:delete").first()
        if perm and perm not in role.permissions:
            role.permissions.append(perm)
        db.session.commit()
        faux_id = faux.id
    client = _login(app, _user(app, "responsable_secteur", "Numérique", key))
    client.post("/participants/cleanup-fakes", data={"preview": "0"})
    with app.app_context():
        assert db.session.get(Participant, faux_id) is not None


def test_doublons_accessibles_a_l_animateur(app):
    key = uuid.uuid4().hex[:8]
    client = _login(app, _user(app, "animateur", "Numérique", key))
    assert client.get("/participants/duplicates").status_code == 200


# ---------------------------------------------------------------- anonymisation

def test_anonymisation_avec_document_hors_dossiers(app, monde, tmp_path):
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    key, (a, b) = monde
    ailleurs = tmp_path / "ancien-serveur" / "signature.png"
    ailleurs.parent.mkdir()
    ailleurs.write_bytes(b"x")
    with app.app_context():
        atelier = AtelierActivite(nom="Atelier" + key, secteur="Numérique")
        db.session.add(atelier); db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Numérique")
        db.session.add(s); db.session.flush()
        db.session.add(PresenceActivite(session_id=s.id, participant_id=a, signature_path=str(ailleurs)))
        db.session.commit()
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
    client = _login(app, ADMIN_EMAIL, ADMIN_PASSWORD)
    r = client.post(f"/participants/{a}/anonymize")
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, a).nom.startswith("ANONYME")
    assert ailleurs.exists()  # jamais effacé hors des dossiers métier
    from app.models import PendingFileDeletion
    with app.app_context():
        assert PendingFileDeletion.query.filter(PendingFileDeletion.file_path.like("%ancien-serveur%")).first()


# ---------------------------------------------------------------- kiosque

@pytest.fixture
def seance(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    from app.utils.dates import utcnow
    token = uuid.uuid4().hex
    with app.app_context():
        atelier = AtelierActivite(nom="Kiosque " + token[:6], secteur="Numérique")
        db.session.add(atelier); db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Numérique", session_type="COLLECTIF",
                            kiosk_open=True, kiosk_token=token, kiosk_opened_at=utcnow())
        db.session.add(s); db.session.commit()
        return s.id, token


def test_kiosque_accueille_un_groupe(app, seance):
    from app.kiosk import routes as kiosque
    sid, token = seance
    kiosque._CREATIONS.reinitialiser()
    client = app.test_client()
    codes = [client.post(f"/kiosk/session/{token}", data={"action": "add_participant", "nom": f"Groupe{i}x{sid}",
                                                          "prenom": "Membre", "force_creation": "1"}).status_code
             for i in range(20)]
    assert 429 not in codes


def test_cle_client_funnel(app):
    from app.kiosk.routes import _adresse_client, _compteur_pin, _ECHECS_PIN_PARTAGE
    with app.test_request_context("/kiosk/", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                  headers={"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.9"}):
        assert _adresse_client() == "funnel:203.0.113.9"
    for entetes in ({"Tailscale-Funnel-Request": "?1"},
                    {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "127.0.0.1"}):  # via Caddy
        with app.test_request_context("/kiosk/", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers=entetes):
            assert _compteur_pin(_adresse_client()) is _ECHECS_PIN_PARTAGE
    # Depuis le réseau local, l'en-tête ne permet pas de choisir son compteur.
    with app.test_request_context("/kiosk/", environ_base={"REMOTE_ADDR": "192.168.1.30"},
                                  headers={"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "1.2.3.4"}):
        assert _adresse_client() == "192.168.1.30"


# ---------------------------------------------------------------- reprise Windows

@pytest.mark.parametrize("password", ["postgres", "erp", "5432", "S3cr\u00e8t p@ss"])
def test_mots_de_passe_courants_acceptes_et_absents(password):
    from urllib.parse import quote
    from desktop.migration import tool_arguments
    url = make_url(f"postgresql+psycopg://erp:{quote(password, safe='')}@127.0.0.1:5432/erp")
    command = tool_arguments(r"C:\Program Files\Mon Centre Social\postgresql\bin\pg_dump.exe", url)
    assert ":" + quote(password, safe="") + "@" not in " ".join(command) and "erp@127.0.0.1" in command[-1]


def test_outil_postgresql_sans_mot_de_passe_en_argument():
    from desktop.migration import tool_arguments
    url = make_url("postgresql+psycopg://ancien:S3cr%C3%A8t%20p%40ss@127.0.0.1:5432/erp?sslmode=disable")
    command = tool_arguments("pg_dump", url, "--format=custom")
    joined = " ".join(command)
    assert "S3cr" not in joined and "p%40ss" not in joined
    assert "ancien@127.0.0.1:5432/erp" in joined and "sslmode=disable" in joined


@pytest.mark.parametrize("source,target,ok", [
    (100023, 180006, True), (170005, 180006, True), (180001, 180006, True),
    (180001, 170011, False), (190000, 180006, False), (90624, 180006, False)])
def test_versions_source_acceptees(source, target, ok):
    from desktop.migration import MigrationError, validate_postgres_versions
    if ok:
        validate_postgres_versions(source, target)
    else:
        with pytest.raises(MigrationError):
            validate_postgres_versions(source, target)


def test_copies_de_reprise_supprimees(tmp_path):
    from desktop.migration import clean_work
    (tmp_path / "files" / "instance").mkdir(parents=True)
    (tmp_path / "files" / "instance" / "a.txt").write_text("x")
    (tmp_path / "source.dump").write_bytes(b"dump")
    (tmp_path / "fichiers.zip").write_bytes(b"zip")
    (tmp_path / "complete.json").write_text("{}")
    clean_work(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["complete.json"]


def test_attribution_automatique_sur_base_existante(app):
    """Simule une base d'avant cette version : permission absente, puis démarrage."""
    from app.extensions import db
    from app.models import Permission, Role
    from app.rbac import bootstrap_rbac
    with app.app_context():
        perm = Permission.query.filter_by(code="participants:edit_all").one()
        for role in Role.query.all():
            if perm in role.permissions:
                role.permissions.remove(perm)
        db.session.delete(perm)
        db.session.commit()
        bootstrap_rbac()
        db.session.expire_all()
        codes = {p.code for p in Role.query.filter_by(code="accueil").one().permissions}
        assert "participants:edit_all" in codes
        autres = [r.code for r in Role.query.all() if r.code != "accueil"
                  and "participants:edit_all" in {p.code for p in r.permissions}]
        assert autres == []


def test_kiosque_retrouve_toutes_les_variantes(app, seance):
    from app.extensions import db
    from app.models import Participant
    sid, token = seance
    key = uuid.uuid4().hex[:6]
    with app.app_context():
        p = Participant(nom="Michut" + key, prenom="Céline", created_secteur="EPE",
                        email="celine@secret.test", telephone="0611223344", ville="Nogent")
        db.session.add(p); db.session.commit(); pid = p.id
    client = app.test_client()
    url = f"/kiosk/session/{token}"
    for saisie in ("celine michut" + key, "MICHUT" + key.upper(), "Céline", "michut" + key + " cel"):
        ids = [r["id"] for r in client.get(url + "/search", query_string={"q": saisie}).get_json()["results"]]
        assert pid in ids, saisie
    label = client.get(url + "/search", query_string={"q": "michut" + key}).get_json()["results"][0]["label"]
    assert label == f"Michut{key} Céline"  # rien d'autre de la fiche
    # Création : la fiche existante (autre secteur, faute de frappe) est proposée.
    page = client.post(url, data={"action": "add_participant", "nom": "michu" + key, "prenom": "celine"})
    assert page.status_code == 200 and f"Michut{key}".encode() in page.data
    assert b"0611223344" not in page.data and b"celine@secret.test" not in page.data
    with app.app_context():
        assert Participant.query.filter(Participant.nom.ilike("michu%" + key + "%")).count() == 1


def test_une_faute_de_frappe_ne_rapproche_pas_les_noms_courts():
    from app.services.doublons import _proches
    assert _proches("michut", "michot") and _proches("dupont", "dupond")
    assert not _proches("lea", "leo") and not _proches("paul", "saul")
