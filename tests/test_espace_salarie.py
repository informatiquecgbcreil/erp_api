"""Espace salarié : intégration de l'application « Récup » dans l'ERP.

Parcours réels par le client HTTP, avec les rôles réels :
- liens automatiques compte ↔ fiche salarié ;
- circuit signé des récupérations (salarié → assistant·e → direction → notification) ;
- frais kilométriques au barème (tranche par cumul annuel), passage en dépense ;
- confidentialité des salaires, y compris face à l'administrateur technique ;
- coffre-fort de documents (liste d'accès stricte) ;
- accueil à deux onglets et page RH en direct.
"""
import datetime as dt
import io
import uuid

import pytest

from tests.conftest import signature_tracee


def _suffixe():
    return uuid.uuid4().hex[:6]


def _compte(app, nom, role_code, secteur=None):
    """Crée un compte (rôle unique, comme l'ERP l'impose) et renvoie (email, id)."""
    email = f"{uuid.uuid4().hex[:8]}@example.org"
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User(email=email, nom=nom, secteur_assigne=secteur)
        u.set_password("pw-test-123")
        u.roles.append(Role.query.filter_by(code=role_code).first())
        db.session.add(u)
        db.session.commit()
        return email, u.id


def _client(app, email):
    c = app.test_client()
    r = c.post("/", data={"email": email, "password": "pw-test-123"})
    assert r.status_code == 302, "connexion impossible"
    return c


def _fiche(app, nom, prenom, secteur=None, user_id=None, etp=1.0):
    """Fiche bornée dans le temps : la base de test est partagée, et d'autres
    tests comptent les salariés actifs d'années lointaines (2048, 2054…)."""
    with app.app_context():
        from app.extensions import db
        from app.models import Salarie
        aujourd_hui = dt.date.today()
        s = Salarie(nom=nom, prenom=prenom, secteur=secteur, user_id=user_id, etp=etp,
                    date_entree=dt.date(aujourd_hui.year - 1, 1, 1),
                    date_sortie=dt.date(aujourd_hui.year + 1, 12, 31))
        db.session.add(s)
        db.session.commit()
        return s.id


def _salarie_connecte(app, role="animateur", secteur=None):
    """Un salarié avec compte relié à sa fiche ; renvoie (client, salarie_id, user_id)."""
    suf = _suffixe()
    email, uid = _compte(app, f"Sal{suf} Test{suf}", role, secteur=secteur)
    sid = _fiche(app, f"Test{suf}", f"Sal{suf}", secteur=secteur, user_id=uid)
    return _client(app, email), sid, uid


# ---------------------------------------------------------------------------
# Rôles et droits
# ---------------------------------------------------------------------------

def test_roles_recoivent_les_bons_droits(app):
    with app.app_context():
        from app.models import Role

        def perms(code):
            return {p.code for p in Role.query.filter_by(code=code).first().permissions}

        assistant = perms("assistant_direction")
        assert {"salarie:espace", "recup:transmettre"} <= assistant
        assert "recup:decider" not in assistant and "salaires:gerer" not in assistant
        tech = perms("admin_tech")
        assert {"salarie:espace", "frais_km:baremes", "coffre:types"} <= tech
        assert "salaires:gerer" not in tech and "recup:decider" not in tech
        finance = perms("finance")
        assert {"frais_km:suivi", "frais_km:baremes", "salarie:espace"} <= finance
        assert "salaires:gerer" not in finance and "recup:decider" not in finance
        assert "salaires:gerer" in perms("direction")
        assert "salarie:espace" in perms("animateur")


# ---------------------------------------------------------------------------
# Liens automatiques compte ↔ fiche
# ---------------------------------------------------------------------------

def test_lien_automatique_quand_le_nom_est_sans_ambiguite(app):
    suf = _suffixe()
    email, uid = _compte(app, f"Élodie Lienauto{suf}", "animateur")
    sid = _fiche(app, f"LIENAUTO{suf}", "Elodie")  # accents et majuscules ignorés
    c = _client(app, email)
    r = c.get("/salarie/recuperations")
    assert r.status_code == 200
    assert "Mes heures sup et récupérations" in r.get_data(as_text=True)
    with app.app_context():
        from app.extensions import db
        from app.models import Salarie
        assert db.session.get(Salarie, sid).user_id == uid


def test_pas_de_lien_quand_deux_fiches_portent_le_meme_nom(app):
    suf = _suffixe()
    email, uid = _compte(app, f"Sam Homonyme{suf}", "animateur")
    _fiche(app, f"Homonyme{suf}", "Sam")
    _fiche(app, f"Homonyme{suf}", "Sam")
    c = _client(app, email)
    r = c.get("/salarie/recuperations")
    assert "pas encore reliée" in r.get_data(as_text=True)
    with app.app_context():
        from app.models import Salarie
        assert Salarie.query.filter_by(user_id=uid).first() is None


def test_page_rh_relie_un_compte_a_la_main(app, admin_client):
    suf = _suffixe()
    _, uid = _compte(app, f"Autre Nom{suf}", "accueil")
    sid = _fiche(app, f"Different{suf}", "Fiche")
    r = admin_client.post(f"/rh/salaries/{sid}/compte", data={"user_id": uid})
    assert r.status_code == 302
    with app.app_context():
        from app.extensions import db
        from app.models import Salarie
        assert db.session.get(Salarie, sid).user_id == uid
    page = admin_client.get("/rh").get_data(as_text=True)
    assert "Heures / frais km" in page and "Compte" in page


# ---------------------------------------------------------------------------
# Circuit des récupérations
# ---------------------------------------------------------------------------

def test_circuit_complet_signe_et_solde(app, admin_client):
    secteur = f"Sect{_suffixe()}"
    client, sid, uid = _salarie_connecte(app, secteur=secteur)
    aujourd_hui = dt.date.today()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite
        at = AtelierActivite(nom="Atelier soirée", secteur=secteur, type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        seance = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                                 date_session=aujourd_hui, heure_debut="18:00", heure_fin="21:00")
        db.session.add(seance)
        db.session.commit()
        seance_id = seance.id

    # L'agenda du jour propose la séance, et la déclaration y est reliée.
    suggestions = client.get(f"/salarie/agenda-du-jour?jour={aujourd_hui.isoformat()}").get_json()
    assert any(s["cle"] == f"seance:{seance_id}" for s in suggestions)
    r = client.post("/salarie/recuperations/heures", data={
        "date_travail": aujourd_hui.isoformat(), "duree": "5h", "lien": f"seance:{seance_id}", "motif": ""})
    assert r.status_code == 302

    # Demande signée d'emblée → soumise.
    client.post("/salarie/recuperations/demandes", data={
        "date_recuperation": (aujourd_hui + dt.timedelta(days=7)).isoformat(), "duree": "3:00",
        "motif": "Pont", "signature_data": signature_tracee()})
    with app.app_context():
        from app.models import DemandeRecuperation, HeureSupplementaire
        h = HeureSupplementaire.query.filter_by(salarie_id=sid).one()
        assert h.minutes == 300 and h.session_id == seance_id
        demande = DemandeRecuperation.query.filter_by(salarie_id=sid).one()
        assert demande.statut == "soumise" and demande.signature_salarie_id
        did = demande.id

    # L'assistant·e transmet mais ne peut pas décider.
    email_a, _ = _compte(app, f"Assist {_suffixe()}", "assistant_direction")
    assistant = _client(app, email_a)
    assert assistant.post(f"/salarie/equipe/recuperations/{did}/decider",
                          data={"decision": "accepter", "signature_data": signature_tracee()}).status_code == 403
    sans_signature = assistant.post(f"/salarie/equipe/recuperations/{did}/transmettre", data={})
    assert sans_signature.status_code == 302
    assistant.post(f"/salarie/equipe/recuperations/{did}/transmettre", data={"signature_data": signature_tracee()})

    # Un refus sans motif est refusé ; l'acceptation passe.
    admin_client.post(f"/salarie/equipe/recuperations/{did}/decider",
                      data={"decision": "refuser", "signature_data": signature_tracee()})
    with app.app_context():
        from app.extensions import db
        from app.models import DemandeRecuperation
        assert db.session.get(DemandeRecuperation, did).statut == "transmise"
    admin_client.post(f"/salarie/equipe/recuperations/{did}/decider",
                      data={"decision": "accepter", "signature_data": signature_tracee()})
    assistant.post(f"/salarie/equipe/recuperations/{did}/notifier", data={"signature_data": signature_tracee()})

    with app.app_context():
        from app.extensions import db
        from app.models import DemandeRecuperation
        from app.services.espace_salarie import solde_minutes
        d = db.session.get(DemandeRecuperation, did)
        assert d.statut == "acceptee" and d.notifiee_le is not None
        assert all([d.signature_transmission_id, d.signature_decision_id, d.signature_notification_id])
        assert solde_minutes(sid) == 120  # 5 h créditées − 3 h récupérées
        sig_id = d.signature_salarie_id

    # Le détail et les signatures : la personne et l'équipe, pas un collègue.
    assert client.get(f"/salarie/recuperations/demandes/{did}").status_code == 200
    assert client.get(f"/salarie/signatures/{sig_id}.png").status_code == 200
    collegue, _, _ = _salarie_connecte(app)
    assert collegue.get(f"/salarie/recuperations/demandes/{did}").status_code == 403
    assert collegue.get(f"/salarie/signatures/{sig_id}.png").status_code == 403
    page = client.get("/salarie/recuperations").get_data(as_text=True)
    assert "Atelier soirée" in page and "2h00" in page


def test_direction_retire_des_heures_avec_justification(app, admin_client):
    client, sid, _ = _salarie_connecte(app)
    client.post("/salarie/recuperations/heures", data={
        "date_travail": dt.date.today().isoformat(), "duree": "2,5", "motif": "Réunion"})
    with app.app_context():
        from app.models import HeureSupplementaire
        hid = HeureSupplementaire.query.filter_by(salarie_id=sid).one().id
    admin_client.post(f"/salarie/equipe/heures/{hid}/retirer", data={"commentaire": ""})
    admin_client.post(f"/salarie/equipe/heures/{hid}/retirer", data={"commentaire": "Déjà payées"})
    admin_client.post(f"/salarie/equipe/heures/{hid}/retirer", data={"commentaire": "Encore"})
    with app.app_context():
        from app.models import HeureSupplementaire
        from app.services.espace_salarie import solde_minutes
        assert HeureSupplementaire.query.filter_by(origine_id=hid).count() == 1
        assert solde_minutes(sid) == 0
    assert "Déjà payées" in client.get("/salarie/recuperations").get_data(as_text=True)


def test_une_demande_decidee_ne_s_annule_plus(app, admin_client):
    client, sid, _ = _salarie_connecte(app)
    client.post("/salarie/recuperations/demandes", data={
        "date_recuperation": dt.date.today().isoformat(), "duree": "1h", "signature_data": signature_tracee()})
    with app.app_context():
        from app.models import DemandeRecuperation
        did = DemandeRecuperation.query.filter_by(salarie_id=sid).one().id
    # La direction décide sans transmission (assistant·e absent·e).
    admin_client.post(f"/salarie/equipe/recuperations/{did}/decider",
                      data={"decision": "refuser", "commentaire": "Période chargée",
                            "signature_data": signature_tracee()})
    client.post(f"/salarie/recuperations/demandes/{did}/annuler")
    with app.app_context():
        from app.extensions import db
        from app.models import DemandeRecuperation
        assert db.session.get(DemandeRecuperation, did).statut == "refusee"


# ---------------------------------------------------------------------------
# Frais kilométriques
# ---------------------------------------------------------------------------

def _bareme(app, annee, cv):
    """Barème « voiture » réaliste : 0,636 €/km jusqu'à 5 000 km, puis 0,357 × d + 1 395 €."""
    with app.app_context():
        from app.extensions import db
        from app.models import BaremeKilometrique
        db.session.add_all([
            BaremeKilometrique(annee=annee, type_vehicule="voiture", puissance_fiscale=cv, km_de=0, km_a=5000,
                               taux_millieme=636, forfait_centimes=0, bonus_electrique_pct=20),
            BaremeKilometrique(annee=annee, type_vehicule="voiture", puissance_fiscale=cv, km_de=5001, km_a=20000,
                               taux_millieme=357, forfait_centimes=139500, bonus_electrique_pct=20),
        ])
        db.session.commit()


def test_calcul_km_par_cumul_annuel(app):
    cv = 40 + int(uuid.uuid4().int % 50)
    _bareme(app, 2026, cv)
    _, sid, _ = _salarie_connecte(app)
    with app.app_context():
        from app.extensions import db
        from app.models import FraisKilometrique
        from app.services import frais_km as fk
        premier = fk.calculer(sid, 2026, "voiture", cv, 30, False)
        assert premier.montant_centimes == 1908 and not premier.bareme_anterieur
        assert fk.calculer(sid, 2026, "voiture", cv, 30, True).montant_centimes == 2290  # +20 %
        # 4 990 km déjà parcourus : le trajet de 20 km franchit la tranche.
        db.session.add(FraisKilometrique(
            salarie_id=sid, date_trajet=dt.date(2026, 3, 1), annee=2026, annee_bareme=2026,
            type_vehicule="voiture", puissance_fiscale=cv, distance_km=4990, motif="cumul",
            montant_centimes=317364, statut="signee"))
        db.session.commit()
        franchit = fk.calculer(sid, 2026, "voiture", cv, 20, False)
        # F(5010) − F(4990) = (5010 × 0,357 + 1 395) − 4 990 × 0,636 = 3 183,57 − 3 173,64
        assert franchit.cumul_avant == 4990
        assert franchit.montant_centimes == 993
        # Barème de l'année non saisi : le précédent s'applique, signalé.
        anterieur = fk.calculer(sid, 2027, "voiture", cv, 10, False)
        assert anterieur.annee_bareme == 2026 and anterieur.bareme_anterieur
        with pytest.raises(fk.BaremeIntrouvable):
            fk.calculer(sid, 2025, "voiture", cv, 10, False)


def test_note_km_signee_justificatif_et_passage_en_depense(app, admin_client):
    cv = 100 + int(uuid.uuid4().int % 50)
    annee = dt.date.today().year
    _bareme(app, annee, cv)
    client, sid, _ = _salarie_connecte(app, secteur="Numérique")
    jour = dt.date.today().isoformat()
    champs = {"date_trajet": jour, "type_vehicule": "voiture", "puissance_fiscale": cv,
              "distance_km": "42", "motif": "Atelier hors les murs"}
    apercu = client.post("/salarie/frais-km", data=champs).get_data(as_text=True)
    assert "26,71" in apercu  # 42 × 0,636 = 26,712 €
    sans_signature = client.post("/salarie/frais-km/enregistrer", data=champs)
    assert sans_signature.status_code == 302
    donnees = dict(champs, signature_data=signature_tracee(),
                   justificatif=(io.BytesIO(b"%PDF-1.4 test"), "ticket.pdf"))
    client.post("/salarie/frais-km/enregistrer", data=donnees, content_type="multipart/form-data")
    with app.app_context():
        from app.models import FraisKilometrique
        note = FraisKilometrique.query.filter_by(salarie_id=sid).one()
        assert note.montant_centimes == 2671 and note.signature_id and note.justificatif_chemin
        nid = note.id

    assert client.get(f"/salarie/frais-km/{nid}/justificatif").status_code == 200
    collegue, _, _ = _salarie_connecte(app)
    assert collegue.get(f"/salarie/frais-km/{nid}/justificatif").status_code == 403
    assert collegue.get("/salarie/equipe/frais-km").status_code == 403

    with app.app_context():
        from app.extensions import db
        from app.models import LigneBudget, Subvention
        sub = Subvention(nom=f"Numérique {annee} {_suffixe()}", secteur="Numérique", annee_exercice=annee,
                         montant_attribue=1000.0)
        db.session.add(sub)
        db.session.flush()
        ligne = LigneBudget(subvention_id=sub.id, nature="charge", compte="6251", libelle="Déplacements",
                            montant_base=1000.0, montant_reel=1000.0)
        db.session.add(ligne)
        db.session.commit()
        lid = ligne.id
    suivi = admin_client.get(f"/salarie/equipe/frais-km?annee={annee}").get_data(as_text=True)
    assert "Atelier hors les murs" in suivi
    assert f'value="{lid}" selected' in suivi  # ligne 625 du secteur proposée d'office
    admin_client.post(f"/salarie/equipe/frais-km/{nid}/depense", data={"ligne_budget_id": lid})
    admin_client.post(f"/salarie/equipe/frais-km/{nid}/depense", data={"ligne_budget_id": lid})
    with app.app_context():
        from app.extensions import db
        from app.models import Depense, FraisKilometrique
        note = db.session.get(FraisKilometrique, nid)
        assert note.depense_id
        depense = db.session.get(Depense, note.depense_id)
        assert depense.montant == pytest.approx(26.71) and depense.reference_piece == f"FK-{nid}"
        assert Depense.query.filter_by(reference_piece=f"FK-{nid}").count() == 1


def test_baremes_ajout_duplication_et_droits(app, admin_client):
    cv = 200 + int(uuid.uuid4().int % 50)
    admin_client.post("/salarie/frais-km/baremes", data={
        "action": "ajouter", "annee": "2031", "type_vehicule": "voiture", "puissance_fiscale": cv,
        "tranche": "0_5000", "taux": "0,529", "forfait": "", "bonus_electrique_pct": "20"})
    admin_client.post("/salarie/frais-km/baremes", data={
        "action": "dupliquer", "annee_source": "2031", "annee_cible": "2032", "type_vehicule": "voiture"})
    with app.app_context():
        from app.models import BaremeKilometrique
        lignes = BaremeKilometrique.query.filter_by(puissance_fiscale=cv).all()
        assert sorted(l.annee for l in lignes) == [2031, 2032]
        assert all(l.taux_millieme == 529 for l in lignes)
    animateur, _, _ = _salarie_connecte(app)
    assert animateur.get("/salarie/frais-km/baremes").status_code == 403


# ---------------------------------------------------------------------------
# Salaires : confidentialité
# ---------------------------------------------------------------------------

def test_profil_salarial_visible_par_le_salarie_et_la_direction_seulement(app, admin_client):
    client, sid, _ = _salarie_connecte(app)
    r = admin_client.post(f"/salarie/equipe/salaires/{sid}", data={
        "action": "enregistrer", "taux_brut": "14,50", "taux_charge": "21,80", "semaines_travaillees": "46",
        "charge_libelle": ["Mutuelle", ""], "charge_valeur": ["0,45", ""], "charge_note": ["", ""],
        "reporter_masse": "1"})
    assert r.status_code == 302
    with app.app_context():
        from app.extensions import db
        from app.models import ProfilSalarial, Salarie
        profil = ProfilSalarial.query.filter_by(salarie_id=sid).one()
        assert profil.taux_horaire_charge_centimes == 2180 and len(profil.charges) == 1
        # 21,80 € × 35 h × 1 ETP × 46 semaines
        assert db.session.get(Salarie, sid).salaire_brut_charge == pytest.approx(35098.0)

    mien = client.post("/salarie/salaire", data={"action": "calculer", "mode": "heures_vers_cout", "heures": "10"})
    assert "218,00" in mien.get_data(as_text=True)
    assert client.get("/salarie/equipe/salaires").status_code == 403

    email_t, _ = _compte(app, f"Tech {_suffixe()}", "admin_tech")
    tech = _client(app, email_t)
    assert tech.get("/salarie/equipe/salaires").status_code == 403
    assert tech.get(f"/salarie/equipe/salaires/{sid}").status_code == 403


def test_admin_technique_ne_peut_pas_s_ouvrir_les_salaires(app):
    """admin:rbac ne suffit pas : l'accès aux salaires ne se donne que par qui l'a déjà."""
    email_t, uid_t = _compte(app, f"Tech {_suffixe()}", "admin_tech")
    tech = _client(app, email_t)
    with app.app_context():
        from app.models import Role
        perms_tech = sorted({p.code for p in Role.query.filter_by(code="admin_tech").first().permissions})
    tech.post("/admin/save_role_perms", data={"role_code": "admin_tech",
                                              "perm_codes": perms_tech + ["salaires:gerer"]})
    tech.post("/admin/set_user_roles", data={"user_id": uid_t, "role_code": "direction"})
    tech.post("/admin/droits", data={"action": "set_role_perms", "role_code": "admin_tech",
                                     "perm_codes": perms_tech + ["salaires:gerer"]})
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        assert "salaires:gerer" not in {p.code for p in Role.query.filter_by(code="admin_tech").first().permissions}
        assert [r.code for r in db.session.get(User, uid_t).roles] == ["admin_tech"]


# ---------------------------------------------------------------------------
# Coffre-fort
# ---------------------------------------------------------------------------

def test_coffre_fort_liste_d_acces_stricte(app, admin_client):
    destinataire, sid, uid = _salarie_connecte(app)
    with app.app_context():
        from app.models import TypeDocumentRh
        type_id = TypeDocumentRh.query.filter_by(code="fiche_paie").one().id
    refus = admin_client.post("/salarie/documents/deposer", content_type="multipart/form-data", data={
        "type_id": type_id, "fichier": (io.BytesIO(b"x"), "paie.docx")})
    assert refus.status_code == 302
    admin_client.post("/salarie/documents/deposer", content_type="multipart/form-data", data={
        "type_id": type_id, "salarie_id": sid, "note": "Septembre",
        "fichier": (io.BytesIO(b"%PDF-1.4 paie"), "paie-septembre.pdf")})
    with app.app_context():
        from app.models import DocumentRh
        doc = DocumentRh.query.filter_by(salarie_id=sid).one()
        assert [a.user_id for a in doc.acces] == [uid]
        did = doc.id
    assert "paie-septembre.pdf" in destinataire.get("/salarie/documents").get_data(as_text=True)
    assert destinataire.get(f"/salarie/documents/{did}/telecharger").status_code == 200
    autre, _, _ = _salarie_connecte(app)
    assert autre.get(f"/salarie/documents/{did}/telecharger").status_code == 403
    assert destinataire.post(f"/salarie/documents/{did}/supprimer").status_code == 403
    admin_client.post(f"/salarie/documents/{did}/supprimer")
    with app.app_context():
        from app.extensions import db
        from app.models import DocumentRh
        assert db.session.get(DocumentRh, did) is None


# ---------------------------------------------------------------------------
# Accueil et page RH
# ---------------------------------------------------------------------------

def test_accueil_a_deux_onglets(app, admin_client):
    client, sid, _ = _salarie_connecte(app)
    client.post("/salarie/recuperations/heures", data={
        "date_travail": dt.date.today().isoformat(), "duree": "1h15", "motif": "Permanence"})
    salarie = client.get("/dashboard?espace=salarie").get_data(as_text=True)
    assert "Espace salarié" in salarie and "1h15" in salarie and "Solde à récupérer" in salarie
    # Le choix est retenu, et l'onglet Activités reste à un clic.
    assert "Solde à récupérer" in client.get("/dashboard").get_data(as_text=True)
    activites = client.get("/dashboard?espace=activites").get_data(as_text=True)
    assert "Activités du centre" in activites and "Solde à récupérer" not in activites

    direction = admin_client.get("/dashboard?espace=salarie").get_data(as_text=True)
    assert "L'équipe : à traiter" in direction
    admin_client.get("/dashboard?espace=activites")


def test_fiche_avec_historique_ne_se_supprime_pas(app, admin_client):
    client, sid, _ = _salarie_connecte(app)
    client.post("/salarie/recuperations/heures", data={
        "date_travail": dt.date.today().isoformat(), "duree": "1h", "motif": "x"})
    admin_client.post(f"/rh/salaries/{sid}/supprimer")
    with app.app_context():
        from app.extensions import db
        from app.models import Salarie
        assert db.session.get(Salarie, sid) is not None


def test_toutes_les_pages_s_affichent(app, admin_client):
    """Chaque écran s'ouvre sans erreur, pour chaque profil (sinon 403 attendu)."""
    client, sid, _ = _salarie_connecte(app)
    client.post("/salarie/recuperations/demandes", data={
        "date_recuperation": dt.date.today().isoformat(), "duree": "1h"})
    with app.app_context():
        from app.models import DemandeRecuperation
        did = DemandeRecuperation.query.filter_by(salarie_id=sid).one().id
    email_a, _ = _compte(app, f"Assist {_suffixe()}", "assistant_direction")
    assistant = _client(app, email_a)
    sans_fiche = _client(app, _compte(app, f"Sansfiche {_suffixe()}", "accueil")[0])

    pages_salarie = ["/salarie/recuperations", "/salarie/frais-km", "/salarie/salaire", "/salarie/documents",
                     "/salarie/documents/deposer", f"/salarie/recuperations/demandes/{did}",
                     "/dashboard?espace=salarie", "/dashboard?espace=activites"]
    pages_equipe = ["/salarie/equipe/recuperations", "/salarie/equipe/recuperations?statut=a_traiter&periode=30j",
                    "/salarie/equipe/frais-km?depense=non", "/salarie/frais-km/baremes", "/salarie/equipe/salaires",
                    f"/salarie/equipe/salaires/{sid}", "/salarie/documents/types", "/rh"]
    for url in pages_salarie:
        assert client.get(url).status_code == 200, url
    for url in pages_salarie + pages_equipe:
        assert admin_client.get(url).status_code == 200, url
    for url in pages_equipe:
        assert client.get(url).status_code == 403, url
    assert assistant.get("/salarie/equipe/recuperations").status_code == 200
    assert assistant.get("/salarie/equipe/salaires").status_code == 403
    assert assistant.get("/salarie/equipe/frais-km").status_code == 403
    assert assistant.get("/dashboard?espace=salarie").status_code == 200
    for url in ("/salarie/recuperations", "/salarie/frais-km", "/salarie/salaire"):
        page = sans_fiche.get(url)
        assert page.status_code == 200 and "pas encore reliée" in page.get_data(as_text=True), url
    assert sans_fiche.get("/salarie/documents").status_code == 200
    assert client.get("/salarie/").status_code == 302
    admin_client.get("/dashboard?espace=activites")


def test_supprimer_un_compte_garde_la_fiche_et_son_historique(app, admin_client):
    client, sid, uid = _salarie_connecte(app)
    client.post("/salarie/recuperations/heures", data={
        "date_travail": dt.date.today().isoformat(), "duree": "1h", "motif": "x"})
    admin_client.post(f"/admin/delete/{uid}")
    with app.app_context():
        from app.extensions import db
        from app.models import HeureSupplementaire, Salarie, User
        assert db.session.get(User, uid) is None
        fiche = db.session.get(Salarie, sid)
        assert fiche is not None and fiche.user_id is None
        assert HeureSupplementaire.query.filter_by(salarie_id=sid).count() == 1
