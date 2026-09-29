"""Export « Participants » CSAT filtré : période, secteur, atelier(s), séance(s).

Format du modèle d'import CSAT : last_name;first_name;location;gender;birthdate
(genre M/F/N, date JJ/MM/AAAA). Seules les personnes VENUES comptent (absences
excusées sur demande) ; séances annulées et mises à la corbeille exclues ;
chaque personne une seule fois.
"""
import csv
import datetime as dt
import io
import uuid

import pytest

from tests.test_export_contacts_emailing import _login_role


def _lire(reponse):
    assert reponse.status_code == 200, reponse.status_code
    assert "text/csv" in reponse.headers["Content-Type"]
    lignes = list(csv.reader(io.StringIO(reponse.data.decode("utf-8-sig")), delimiter=";"))
    assert lignes[0] == ["last_name", "first_name", "location", "gender", "birthdate"]
    return lignes[1:]


@pytest.fixture()
def jeu(app):
    """Deux ateliers ; présences en mars et avril ; une absence excusée ; une
    séance annulée ; une séance à la corbeille ; une personne jamais venue."""
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    t = uuid.uuid4().hex[:6]
    secteur_a, secteur_b = f"NumCsat{t}", f"FamCsat{t}"
    with app.app_context():
        a = AtelierActivite(nom=f"Atelier A {t}", secteur=secteur_a)
        b = AtelierActivite(nom=f"Atelier B {t}", secteur=secteur_b)
        db.session.add_all([a, b])
        db.session.flush()

        def seance(atelier, jour, **extra):
            s = SessionActivite(atelier_id=atelier.id, secteur=atelier.secteur, session_type="COLLECTIF",
                                date_session=jour, heure_debut="14:00", heure_fin="16:00", **extra)
            db.session.add(s)
            db.session.flush()
            return s

        a1 = seance(a, dt.date(2026, 3, 10))
        a2 = seance(a, dt.date(2026, 4, 15))
        b1 = seance(b, dt.date(2026, 4, 20))
        annulee = seance(a, dt.date(2026, 4, 16), statut="annulee")
        corbeille = seance(a, dt.date(2026, 4, 17), is_deleted=True)

        def personne(nom, **extra):
            p = Participant(nom=f"{nom}{t}", prenom="Test", ville="Creil", genre="Femme",
                            date_naissance=dt.date(1990, 5, 4), created_secteur=secteur_a, **extra)
            db.session.add(p)
            db.session.flush()
            return p

        mars, deux, excuse, jamais, annul, jete = (personne(n) for n in
                                                   ("Mars", "Deux", "Excuse", "Jamais", "Annul", "Jete"))

        def presence(p, s, type_="present"):
            db.session.add(PresenceActivite(participant_id=p.id, session_id=s.id, presence_type=type_))

        presence(mars, a1)
        presence(deux, a2)
        presence(deux, b1)
        presence(excuse, a2, "excuse")
        presence(annul, annulee)
        presence(jete, corbeille)
        db.session.commit()
        return {"t": t, "secteur_a": secteur_a, "secteur_b": secteur_b, "a": a.id, "b": b.id,
                "a1": a1.id, "a2": a2.id, "b1": b1.id}


def _noms(lignes, t):
    return sorted(l[0][: -len(t)] for l in lignes if l[0].endswith(t))


def test_periode_seules_les_personnes_venues(admin_client, jeu):
    lignes = _lire(admin_client.get("/participants/export-csat.csv", query_string={"du": "2026-04-01", "au": "2026-04-30"}))
    assert _noms(lignes, jeu["t"]) == ["Deux"], "une seule ligne malgré deux séances ; ni excusé, ni annulée, ni corbeille"
    ligne = next(l for l in lignes if l[0] == f"Deux{jeu['t']}")
    assert ligne == [f"Deux{jeu['t']}", "Test", "Creil", "F", "04/05/1990"]
    avec = _lire(admin_client.get("/participants/export-csat.csv",
                                  query_string={"du": "2026-04-01", "au": "2026-04-30", "excuses": "1"}))
    assert _noms(avec, jeu["t"]) == ["Deux", "Excuse"]


def test_ateliers_et_seances(admin_client, jeu):
    atelier_a = _lire(admin_client.get("/participants/export-csat.csv", query_string={"atelier_id": jeu["a"]}))
    assert _noms(atelier_a, jeu["t"]) == ["Deux", "Mars"]
    deux_ateliers = _lire(admin_client.get(f"/participants/export-csat.csv?atelier_id={jeu['a']}&atelier_id={jeu['b']}"
                                           "&du=2026-04-01"))
    assert _noms(deux_ateliers, jeu["t"]) == ["Deux"]
    seance = _lire(admin_client.get("/participants/export-csat.csv", query_string={"session_id": jeu["a1"]}))
    assert _noms(seance, jeu["t"]) == ["Mars"]
    secteur_b = _lire(admin_client.get("/participants/export-csat.csv",
                                       query_string={"du": "2026-01-01", "secteur": jeu["secteur_b"]}))
    assert _noms(secteur_b, jeu["t"]) == ["Deux"]


def test_sans_filtre_tout_l_annuaire_comme_avant(admin_client, jeu):
    assert "Jamais" in _noms(_lire(admin_client.get("/participants/export-csat.csv")), jeu["t"])


def test_page_de_preparation(admin_client, jeu):
    page = admin_client.get("/participants/export-csat", query_string={"du": "2026-04-01", "au": "2026-04-30",
                                                                         "atelier_id": jeu["a"]}).get_data(as_text=True)
    assert "15/04/2026" in page and "16/04/2026" not in page and "17/04/2026" not in page
    assert "20/04/2026" not in page, "atelier B non choisi"
    assert f'name="session_id" value="{jeu["a2"]}" checked' in page
    assert "<strong data-csat-nombre>1</strong>" in page
    # Aucune séance cochée : message au lieu d'un fichier vide trompeur.
    r = admin_client.get("/participants/export-csat.csv", query_string={"du": "2026-04-01", "choix_seances": "1"})
    assert r.status_code == 302 and "/participants/export-csat" in r.headers["Location"]


def test_seances_cochees_dans_la_liste_d_un_atelier(admin_client, jeu):
    r = admin_client.post(f"/activite/atelier/{jeu['a']}/sessions/actions",
                          data={"action": "export_csat_participants", "sid": [jeu["a1"], jeu["a2"]]})
    assert _noms(_lire(r), jeu["t"]) == ["Deux", "Mars"]
    # Envoyées : un second export des mêmes séances n'a plus personne à proposer.
    r = admin_client.post(f"/activite/atelier/{jeu['a']}/sessions/actions",
                          data={"action": "export_csat_participants", "sid": [jeu["a1"], jeu["a2"]]},
                          follow_redirects=True)
    assert "déjà été envoyés à CSAT" in r.get_data(as_text=True)


def test_liens_dans_les_pages(admin_client, jeu):
    assert "/participants/export-csat?atelier_id=" in admin_client.get(f"/activite/atelier/{jeu['a']}/sessions").get_data(as_text=True)
    page = admin_client.get(f"/activite/session/{jeu['a1']}/emargement").get_data(as_text=True)
    assert 'action="/participants/export-csat.csv"' in page and f'name="session_id" value="{jeu["a1"]}"' in page
    assert "/participants/export-csat\"" in admin_client.get("/participants/").get_data(as_text=True)


def test_compte_borne_a_son_secteur(app, jeu):
    from app.extensions import db
    from app.models import Permission, Role
    with app.app_context():
        role = Role.query.filter_by(code="export_secteur_seul").first()
        if role is None:
            role = Role(code="export_secteur_seul", label="Export borné secteur")
            db.session.add(role)
            for code in ("dashboard:view", "participants:view"):
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    role.permissions.append(perm)
            db.session.commit()
    c = _login_role(app, f"csat-{jeu['t']}@example.org", "export_secteur_seul", secteur=jeu["secteur_b"])
    # Demander le secteur A ne sert à rien : seules les séances de SON secteur comptent.
    lignes = _lire(c.get("/participants/export-csat.csv", query_string={"du": "2026-01-01", "secteur": jeu["secteur_a"]}))
    assert _noms(lignes, jeu["t"]) == ["Deux"]
    page = c.get("/participants/export-csat", query_string={"du": "2026-01-01"}).get_data(as_text=True)
    assert f"Atelier A {jeu['t']}" not in page and f"Atelier B {jeu['t']}" in page


def test_telechargement_marque_et_ne_renvoie_pas(admin_client, jeu):
    donnees = {"du": "2026-03-01", "au": "2026-04-30", "atelier_id": jeu["a"], "nouveaux": "1"}
    assert _noms(_lire(admin_client.post("/participants/export-csat.csv", data=donnees)), jeu["t"]) == ["Deux", "Mars"]
    # Deuxième fois : personne (CSAT créerait des doublons), message clair.
    r = admin_client.post("/participants/export-csat.csv", data=donnees, follow_redirects=True)
    assert "déjà été envoyées à CSAT" in r.get_data(as_text=True)
    # Renvoi volontaire possible.
    tout = dict(donnees, nouveaux="0")
    assert _noms(_lire(admin_client.post("/participants/export-csat.csv", data=tout)), jeu["t"]) == ["Deux", "Mars"]
    # La page coche « jamais envoyées » par défaut ; décochée, les envoyées comptent.
    page = admin_client.get("/participants/export-csat", query_string={"du": "2026-03-01", "atelier_id": jeu["a"]}).get_data(as_text=True)
    assert 'name="nouveaux" value="1" checked' in page and "<strong data-csat-nombre>0</strong>" in page
    page = admin_client.get("/participants/export-csat", query_string={"filtre": "1", "du": "2026-03-01",
                                                                         "atelier_id": jeu["a"]}).get_data(as_text=True)
    assert "<strong data-csat-nombre>2</strong>" in page


def test_reprise_depuis_l_export_csv_de_csat(app, admin_client):
    """Les personnes déjà saisies dans CSAT (avant l'outil) sont retrouvées et
    marquées ; homonymes départagés par la date de naissance, sinon non marqués."""
    from app.extensions import db
    from app.models import Participant
    t = uuid.uuid4().hex[:6]
    with app.app_context():
        fiches = {
            "exacte": Participant(nom=f"Fakhreddine{t}", prenom="Malika", date_naissance=dt.date(1977, 5, 13)),
            "accents": Participant(nom=f"Hamadouche-Élan{t}", prenom="Soraya", date_naissance=None),
            "homonyme1": Participant(nom=f"Kartal{t}", prenom="Ulas", date_naissance=dt.date(2017, 5, 18)),
            "homonyme2": Participant(nom=f"Kartal{t}", prenom="Ulas", date_naissance=dt.date(1980, 1, 2)),
            "ambigu1": Participant(nom=f"Goren{t}", prenom="Ada"),
            "ambigu2": Participant(nom=f"Goren{t}", prenom="Ada"),
            "autre_date": Participant(nom=f"Martin{t}", prenom="Alex", date_naissance=dt.date(2000, 1, 1)),
            "absente": Participant(nom=f"Absente{t}", prenom="Zoé"),
        }
        db.session.add_all(fiches.values())
        db.session.commit()
        ids = {k: p.id for k, p in fiches.items()}
    # En-têtes tels qu'affichés par CSAT (français), séparateur « ; ».
    export_csat = (
        "Nom;Prénom;Genre;Date de naissance;Lieu de résidence\n"
        f"Fakhreddine{t};Malika;Féminin;13/05/1977;Creil\n"
        f"HAMADOUCHE ELAN{t};soraya;Féminin;21/05/1973;Cauffry\n"
        f"Kartal{t};Ulas;Masculin;18/05/2017;Creil\n"
        f"Goren{t};Ada;Féminin;;Creil\n"
        f"Martin{t};Alex;Masculin;15/08/1995;Creil\n"
        f"Inconnue{t};Personne;Féminin;01/01/1990;Creil\n"
    ).encode("utf-8-sig")
    r = admin_client.post("/participants/export-csat/deja-dans-csat",
                          data={"fichier": (io.BytesIO(export_csat), "participants.csv")},
                          content_type="multipart/form-data", follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "3 personne(s) marquée(s)" in page and "sur 6 ligne(s)" in page
    assert f"Goren{t} Ada" in page and f"Inconnue{t} Personne" in page and f"Martin{t} Alex" in page
    with app.app_context():
        marque = {k: db.session.get(Participant, i).exported_csat_at is not None for k, i in ids.items()}
    assert marque == {"exacte": True, "accents": True, "homonyme1": True, "homonyme2": False,
                      "ambigu1": False, "ambigu2": False, "autre_date": False, "absente": False}


def test_reprise_format_d_import_et_fichier_non_reconnu(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    t = uuid.uuid4().hex[:6]
    with app.app_context():
        p = Participant(nom=f"Dupont{t}", prenom="Benjamin", date_naissance=dt.date(1995, 8, 15))
        db.session.add(p)
        db.session.commit()
        pid = p.id
    contenu = f"last_name,first_name,location,gender,birthdate\nDupont{t},Benjamin,Creil,M,1995-08-15\n".encode()
    admin_client.post("/participants/export-csat/deja-dans-csat",
                      data={"fichier": (io.BytesIO(contenu), "x.csv")}, content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(Participant, pid).exported_csat_at is not None
    r = admin_client.post("/participants/export-csat/deja-dans-csat",
                          data={"fichier": (io.BytesIO(b"colonne;autre\n1;2\n"), "x.csv")},
                          content_type="multipart/form-data", follow_redirects=True)
    assert "Fichier non reconnu" in r.get_data(as_text=True)
