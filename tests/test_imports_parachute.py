"""Le parachute des imports Excel : sauvegarde avant, annulation du dernier.

Ce que ces tests garantissent, pour l'import des présences (Administration)
comme pour celui de l'annuaire (Participants > Importer) :

- pas d'import réel sans sauvegarde juste avant ; si elle échoue, rien
  n'est importé ;
- l'annulation retire exactement ce que l'import a créé et remet ce qu'il a
  modifié sur des fiches existantes ;
- ce qui a servi depuis (une fiche qui a reçu d'autres présences, une
  valeur retouchée à la main) est conservé, et compté ;
- seul le dernier import s'annule, une seule fois ;
- le journal d'audit de l'annulation ne contient aucun nom.
"""
import datetime as dt
import io
import json
import re
import uuid

from openpyxl import Workbook

JOUR = dt.datetime(2018, 5, 14)


def _suffixe() -> str:
    return uuid.uuid4().hex[:6].upper()


def _classeur_presences(lignes, onglet) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = onglet
    ws.append(["NOMS", "PRENOMS", "ANNEE", "", "SEXE", "QUARTIER", "VILLE", JOUR])
    for ligne in lignes:
        ws.append(ligne)
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def _secteur(app):
    from app.secteurs import get_secteur_labels

    with app.app_context():
        return get_secteur_labels(active_only=True)[0]


def _importer_presences(admin_client, app, contenu, dry_run="0"):
    return admin_client.post("/admin/import-excel", data={
        "secteur": _secteur(app), "dry_run": dry_run,
        "xlsx_file": (io.BytesIO(contenu), "presences.xlsx"),
    }, content_type="multipart/form-data")


def _dernier_lot(app, nature):
    from app.services.imports_lots import dernier_lot

    with app.app_context():
        lot = dernier_lot(nature)
        return lot.id if lot else None


# ---------------------------------------------------------------------------
# Import des présences
# ---------------------------------------------------------------------------

def test_import_reel_sauvegarde_avant_puis_s_annule_proprement(app, admin_client, regler_territoire):
    from app.extensions import db
    from app.models import AtelierActivite, ImportLot, Participant, PresenceActivite, Quartier, SessionActivite
    from app.services.sauvegarde import dossier_sauvegardes

    regler_territoire()
    suf = _suffixe()
    with app.app_context():
        # Une fiche existante, sans ville : l'import la complète.
        ancienne = Participant(nom=f"ANCIEN{suf}", prenom="Rose", date_naissance=dt.date(1970, 1, 1))
        db.session.add(ancienne)
        db.session.commit()
        ancienne_id = ancienne.id

    r = _importer_presences(admin_client, app, _classeur_presences([
        [f"NOUVEAU{suf}", "Léo", 1990, "", "M", f"Quartier neuf {suf}", "Beauvais", "x"],
        [f"ANCIEN{suf}", "Rose", 1970, "", "F", None, "Allonne", "x"],
    ], onglet=f"Atelier {suf}"))
    assert r.status_code == 200
    lot_id = _dernier_lot(app, "presences_excel")
    # Le bouton (son adresse, pas la phrase, qui figure aussi dans l'aide).
    assert f"/admin/import-excel/annuler/{lot_id}" in r.get_data(as_text=True)

    with app.app_context():
        lot = db.session.get(ImportLot, lot_id)
        assert lot.fichier == "presences.xlsx"
        # La sauvegarde d'avant import existe bel et bien sur le disque.
        assert lot.sauvegarde and any(f.name.startswith(lot.sauvegarde) for f in dossier_sauvegardes().iterdir())
        nouveau = Participant.query.filter_by(nom=f"NOUVEAU{suf}").one()
        atelier = AtelierActivite.query.filter_by(nom=f"Atelier {suf}").one()
        assert (db.session.get(Participant, ancienne_id).ville,
                db.session.get(Participant, ancienne_id).genre) == ("Allonne", "F")
        ids = {"nouveau": nouveau.id, "atelier": atelier.id, "quartier": nouveau.quartier_id,
               "lot": lot.id}

    r = admin_client.post(f"/admin/import-excel/annuler/{ids['lot']}", follow_redirects=True)
    assert "Import annulé" in r.get_data(as_text=True)

    with app.app_context():
        assert db.session.get(Participant, ids["nouveau"]) is None
        assert db.session.get(Quartier, ids["quartier"]) is None
        assert PresenceActivite.query.join(SessionActivite).filter(
            SessionActivite.atelier_id == ids["atelier"]).count() == 0
        assert db.session.get(AtelierActivite, ids["atelier"]).is_deleted is True
        assert SessionActivite.query.filter_by(atelier_id=ids["atelier"], is_deleted=False).count() == 0
        # La fiche existante retrouve sa ville et son genre d'avant (aucun).
        ancienne = db.session.get(Participant, ancienne_id)
        assert (ancienne.ville, ancienne.genre) == (None, None)
        lot = db.session.get(ImportLot, ids["lot"])
        assert lot.annule_le is not None
        rapport = json.loads(lot.annulation_json)
        assert rapport["retires"]["participants"] == 1 and rapport["retires"]["modifications"] == 2
        assert rapport["conserves"] == {}

    # Une seule fois.
    r = admin_client.post(f"/admin/import-excel/annuler/{ids['lot']}", follow_redirects=True)
    assert "une seule fois" in r.get_data(as_text=True)


def test_ce_qui_a_servi_depuis_est_conserve(app, admin_client, regler_territoire):
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite

    regler_territoire()
    suf = _suffixe()
    _importer_presences(admin_client, app, _classeur_presences([
        [f"UTILE{suf}", "Nina", 1985, "", "F", None, "Creil", "x"],
        [f"INUTILE{suf}", "Omar", 1986, "", "M", None, "Creil", "x"],
    ], onglet=f"Atelier {suf}"))

    with app.app_context():
        utile = Participant.query.filter_by(nom=f"UTILE{suf}").one()
        # Depuis l'import, Nina est venue à une autre activité, saisie à la main,
        # et sa ville a été corrigée sur sa fiche.
        autre = AtelierActivite(nom=f"Autre {suf}", secteur=_secteur(app))
        db.session.add(autre)
        db.session.flush()
        seance = SessionActivite(atelier_id=autre.id, secteur=autre.secteur, date_session=dt.date(2018, 6, 1))
        db.session.add(seance)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=seance.id, participant_id=utile.id))
        db.session.commit()
        utile_id = utile.id
        inutile_id = Participant.query.filter_by(nom=f"INUTILE{suf}").one().id

    admin_client.post(f"/admin/import-excel/annuler/{_dernier_lot(app, 'presences_excel')}")

    with app.app_context():
        from app.models import ImportLot

        assert db.session.get(Participant, utile_id) is not None
        assert PresenceActivite.query.filter_by(participant_id=utile_id).count() == 1  # la présence saisie à la main
        assert db.session.get(Participant, inutile_id) is None
        rapport = json.loads(db.session.get(ImportLot, _dernier_lot(app, "presences_excel")).annulation_json)
        assert rapport["conserves"] == {"fiches utilisées depuis l'import (présences, inscriptions…)": 1}


def test_seul_le_dernier_import_s_annule(app, admin_client, regler_territoire):
    from app.extensions import db
    from app.models import ImportLot

    regler_territoire()
    suf = _suffixe()
    _importer_presences(admin_client, app, _classeur_presences(
        [[f"PREMIER{suf}", "Ali", 1990, "", "M", None, "Creil", "x"]], onglet=f"A1 {suf}"))
    premier = _dernier_lot(app, "presences_excel")
    _importer_presences(admin_client, app, _classeur_presences(
        [[f"SECOND{suf}", "Bea", 1991, "", "F", None, "Creil", "x"]], onglet=f"A2 {suf}"))

    r = admin_client.post(f"/admin/import-excel/annuler/{premier}", follow_redirects=True)
    assert "Seul le dernier import" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(ImportLot, premier).annule_le is None


def test_sans_sauvegarde_pas_d_import(app, admin_client, regler_territoire, monkeypatch):
    from app.models import ImportLot, Participant
    from app.services import sauvegarde

    def en_panne():
        raise RuntimeError("disque plein")

    monkeypatch.setattr(sauvegarde, "creer_sauvegarde", en_panne)
    regler_territoire()
    suf = _suffixe()
    with app.app_context():
        lots_avant = ImportLot.query.count()

    r = _importer_presences(admin_client, app, _classeur_presences(
        [[f"BLOQUE{suf}", "Zoé", 1990, "", "F", None, "Creil", "x"]], onglet=f"Atelier {suf}"))
    page = r.get_data(as_text=True)
    assert "Import non lancé" in page and "disque plein" in page
    with app.app_context():
        assert Participant.query.filter_by(nom=f"BLOQUE{suf}").count() == 0
        assert ImportLot.query.count() == lots_avant


def test_un_test_d_import_ne_sauvegarde_ni_ne_journalise(app, admin_client, regler_territoire, monkeypatch):
    from app.models import ImportLot
    from app.services import sauvegarde

    appels = []
    monkeypatch.setattr(sauvegarde, "creer_sauvegarde", lambda: appels.append(1))
    regler_territoire()
    suf = _suffixe()
    with app.app_context():
        lots_avant = ImportLot.query.count()
    _importer_presences(admin_client, app, _classeur_presences(
        [[f"ESSAI{suf}", "Lou", 1990, "", "F", None, "Creil", "x"]], onglet=f"Atelier {suf}"), dry_run="1")
    assert appels == []
    with app.app_context():
        assert ImportLot.query.count() == lots_avant


def test_le_journal_d_audit_de_l_annulation_ne_contient_aucun_nom(app, admin_client, regler_territoire):
    from app.models import AuditLog

    regler_territoire()
    suf = _suffixe()
    _importer_presences(admin_client, app, _classeur_presences(
        [[f"SECRET{suf}", "Camille", 1990, "", "F", None, "Creil", "x"]], onglet=f"Atelier {suf}"))
    lot_id = _dernier_lot(app, "presences_excel")
    admin_client.post(f"/admin/import-excel/annuler/{lot_id}")
    with app.app_context():
        entree = AuditLog.query.filter_by(action="import.annule", cible=f"import_lot:{lot_id}").one()
        assert f"SECRET{suf}" not in (entree.details or "") and "Camille" not in (entree.details or "")


# ---------------------------------------------------------------------------
# Import de l'annuaire
# ---------------------------------------------------------------------------

def _classeur_annuaire(lignes) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Habitants"
    ws.append(["NOMS", "PRENOMS", "ANNEE NAISSANCE", "SEXE", "VILLE", "ADRESSE", "QUARTIER"])
    for ligne in lignes:
        ws.append(ligne)
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def _importer_annuaire(admin_client, contenu):
    r = admin_client.post("/participants/import",
                          data={"fichier": (io.BytesIO(contenu), "annuaire.xlsx")},
                          content_type="multipart/form-data")
    page = r.get_data(as_text=True)
    token = re.search(r'name="token" value="([0-9a-f]{32})"', page).group(1)
    nom = re.search(r'name="fichier_nom" value="([^"]*)"', page).group(1)
    return admin_client.post("/participants/import/confirmer", data={"token": token, "fichier_nom": nom})


def test_annuaire_sauvegarde_puis_annulation(app, admin_client, regler_territoire):
    from app.extensions import db
    from app.models import ImportLot, Participant, PresenceActivite, Quartier, SessionActivite, AtelierActivite

    regler_territoire()
    suf = _suffixe()
    _importer_annuaire(admin_client, _classeur_annuaire([
        [f"ANNU{suf}", "Inès", 1992, "F", "Nogent-sur-Oise", "1 rue A", f"Les Granges {suf}"],
        [f"GARDE{suf}", "Hugo", 1993, "M", "Creil", "2 rue B", ""],
    ]))
    with app.app_context():
        lot = db.session.get(ImportLot, _dernier_lot(app, "annuaire"))
        assert lot.fichier == "annuaire.xlsx" and lot.sauvegarde
        annu = Participant.query.filter_by(nom=f"ANNU{suf}").one()
        garde = Participant.query.filter_by(nom=f"GARDE{suf}").one()
        quartier_id = annu.quartier_id
        # Hugo est venu à une séance depuis : sa fiche doit rester.
        atelier = AtelierActivite(nom=f"Atelier {suf}", secteur=_secteur(app))
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(atelier_id=atelier.id, secteur=atelier.secteur, date_session=dt.date(2018, 6, 2))
        db.session.add(seance)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=seance.id, participant_id=garde.id))
        db.session.commit()
        ids = (annu.id, garde.id, lot.id)

    page = admin_client.get("/participants/import").get_data(as_text=True)
    assert f"/participants/import/annuler/{ids[2]}" in page
    r = admin_client.post(f"/participants/import/annuler/{ids[2]}", follow_redirects=True)
    assert "Import annulé" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Participant, ids[0]) is None
        assert db.session.get(Quartier, quartier_id) is None
        assert db.session.get(Participant, ids[1]) is not None


def test_annuler_demande_les_droits_de_l_import(app, client):
    r = client.post("/participants/import/annuler/1")
    assert r.status_code in (302, 401, 403)
    r = client.post("/admin/import-excel/annuler/1")
    assert r.status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# Garde-fous ajoutés à la relecture
# ---------------------------------------------------------------------------

def test_rejouer_un_fichier_deja_importe_ne_masque_pas_l_import_precedent(app, admin_client,
                                                                          regler_territoire):
    """Un import qui ne crée ni ne modifie rien ne devient pas « le dernier »."""
    from app.services.imports_lots import annulable
    from app.extensions import db
    from app.models import ImportLot

    regler_territoire()
    suf = _suffixe()
    contenu = _classeur_presences([[f"REJOUE{suf}", "Tom", 1990, "", "M", None, "Creil", "x"]],
                                  onglet=f"Atelier {suf}")
    _importer_presences(admin_client, app, contenu)
    premier = _dernier_lot(app, "presences_excel")
    _importer_presences(admin_client, app, contenu)  # même fichier : tout existe déjà
    assert _dernier_lot(app, "presences_excel") == premier
    with app.app_context():
        assert annulable(db.session.get(ImportLot, premier))


def test_un_responsable_de_secteur_n_annule_pas_l_import_d_un_autre(app, admin_client, regler_territoire):
    import uuid as _uuid

    from app.extensions import db
    from app.models import ImportLot, Role, User

    regler_territoire()
    suf = _suffixe()
    _importer_presences(admin_client, app, _classeur_presences(
        [[f"AUTRUI{suf}", "Ana", 1990, "", "F", None, "Creil", "x"]], onglet=f"Atelier {suf}"))
    lot_id = _dernier_lot(app, "presences_excel")

    with app.app_context():
        responsable = User(email=f"resp{_uuid.uuid4().hex[:8]}@test.fr", nom="Resp",
                           secteur_assigne=_secteur(app))
        responsable.set_password("motdepasse-tests")
        responsable.roles.append(Role.query.filter_by(code="responsable_secteur").one())
        db.session.add(responsable)
        db.session.commit()
        email = responsable.email
    autre = app.test_client()
    assert autre.post("/", data={"email": email, "password": "motdepasse-tests"}).status_code == 302

    assert f"/admin/import-excel/annuler/{lot_id}" not in autre.get("/admin/import-excel").get_data(as_text=True)
    assert autre.post(f"/admin/import-excel/annuler/{lot_id}").status_code == 403
    with app.app_context():
        assert db.session.get(ImportLot, lot_id).annule_le is None
