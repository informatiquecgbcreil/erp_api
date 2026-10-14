"""Rapport SOS : tout pour faire réparer une panne, sans donnée personnelle.

Le test principal met dans un faux journal d'erreurs le nom, l'e-mail, le
téléphone, l'adresse d'une vraie fiche — comme le ferait un message
d'erreur maladroit — et vérifie qu'aucun ne ressort dans le rapport.
"""
import json
import logging
import uuid

import pytest


def _rapport_affiche(reponse) -> str:
    """Le rapport tel qu'affiché (la zone à copier), pas le reste de la page."""
    import html
    import re

    trouve = re.search(r'<textarea id="rapport-sos"[^>]*>(.*?)</textarea>', reponse.get_data(as_text=True), re.S)
    assert trouve, "rapport absent de la page"
    return html.unescape(trouve.group(1))


def _ecrire_journal(dossier, lignes):
    (dossier / "erreurs.log").write_text("\n".join(lignes) + "\n", encoding="utf-8")


@pytest.fixture
def journaux(tmp_path, monkeypatch):
    """Un dossier de journaux à soi : celui de l'application n'est pas touché."""
    from app.services import rapport_sos

    monkeypatch.setattr(rapport_sos, "dossier_journaux", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# L'anonymiseur seul
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texte, attendu", [
    ("écrire à amina.dupont@mail.fr", "écrire à [EMAIL]"),
    ("appeler le 06 12 34 56 78 ou le 0612345678 ou +33 6 12 34 56 78",
     "appeler le [TÉLÉPHONE] ou le [TÉLÉPHONE] ou [TÉLÉPHONE]"),
    ("habite 12 rue Victor Hugo, 60100 Creil", "habite [ADRESSE], 60100 Creil"),
    ("IBAN FR76 3000 6000 0112 3456 7890 189", "IBAN [IBAN]"),
    ("n° sécu 1850560100123", "n° sécu [NUMÉRO]"),
    ("GET /kiosk/signer/Xk3_aZ9qLmN0pQrStUvWxYz12345", "GET /kiosk/signer/[JETON]"),
    ("/recherche?q=Dupont&secteur=Famille", "/recherche?q=…&secteur=…"),
    ('File "C:\\Users\\antoine\\app\\x.py"', 'File "C:\\Users\\[UTILISATEUR]\\app\\x.py"'),
    # Ce qui n'est pas une donnée personnelle reste lisible.
    ("KeyError: 'Rouher' le 2026-10-14 10:32:11, fiche 123, code 60100",
     "KeyError: 'Rouher' le 2026-10-14 10:32:11, fiche 123, code 60100"),
])
def test_les_formes_reconnaissables_sont_masquees(texte, attendu):
    from app.services.anonymisation_texte import anonymiser

    assert anonymiser(texte) == attendu


def test_les_identites_connues_sont_masquees_sans_accent_ni_casse():
    from app.services.anonymisation_texte import anonymiser

    assert anonymiser("Fiche de Héloïse NDIAYE, et Error", {"heloise", "ndiaye", "error"}) == \
        "Fiche de [NOM] [NOM], et [NOM]"


# ---------------------------------------------------------------------------
# Le rapport
# ---------------------------------------------------------------------------

def test_aucune_donnee_d_une_fiche_ne_sort_dans_le_rapport(app, admin_client, journaux):
    from app.extensions import db
    from app.models import Participant

    suf = uuid.uuid4().hex[:6]
    nom, prenom = f"Zoubirou{suf}", f"Maelys{suf}"
    email, telephone, adresse = f"maelys.{suf}@exemple.fr", "07 61 22 33 44", "8 rue des Acacias"
    with app.app_context():
        db.session.add(Participant(nom=nom, prenom=prenom, email=email, telephone=telephone,
                                   adresse=adresse, ville="Creil"))
        db.session.commit()

    _ecrire_journal(journaux, [
        f"2026-10-14 10:00:00,000 WARNING [app] Import par {email} : fiche {prenom} {nom} créée",
        "2026-10-14 10:05:00,000 ERROR [app] Exception on /participants/12/edit [POST]",
        "Traceback (most recent call last):",
        '  File "/srv/mcs/app/participants/routes.py", line 120, in edit_participant',
        "    raise ValueError(message)",
        f"ValueError: téléphone {telephone} invalide pour {nom.upper()} ({adresse}, Creil)",
    ])

    contexte = f"J'éditais la fiche de {prenom} {nom}, {email}"
    r = admin_client.post("/admin/sante/sos", data={"action": "afficher", "contexte": contexte})
    assert r.status_code == 200
    rapport = _rapport_affiche(r)
    fichier = admin_client.post("/admin/sante/sos", data={"action": "telecharger", "contexte": contexte})
    for donnee in (nom, prenom, email, "07 61 22 33 44", "Acacias"):
        assert donnee.lower() not in rapport.lower(), donnee
        assert donnee.lower() not in fichier.get_data(as_text=True).lower(), donnee
    assert "[NOM]" in rapport and "[EMAIL]" in rapport and "[TÉLÉPHONE]" in rapport and "[ADRESSE]" in rapport
    # Ce qui sert à réparer reste : type, lieu, code source, URL sans paramètre.
    assert "ValueError" in rapport and "app/participants/routes.py:120" in rapport
    assert "raise ValueError(message)" in rapport and "/participants/12/edit" in rapport


def test_les_erreurs_identiques_sont_regroupees(app, admin_client, journaux):
    trace = [
        "Traceback (most recent call last):",
        '  File "/srv/mcs/app/services/senacs.py", line 42, in publics_annee',
        "    total = calcul()",
        "ZeroDivisionError: division by zero",
    ]
    lignes = []
    for minute in range(3):
        lignes += [f"2026-10-14 10:0{minute}:00,000 ERROR [app] Exception on /bilans/senacs [GET]", *trace]
    lignes += ["2026-10-14 11:00:00,000 WARNING [app] Lot 12 : 3 fiches ignorées",
               "2026-10-14 11:01:00,000 WARNING [app] Lot 13 : 4 fiches ignorées"]
    _ecrire_journal(journaux, lignes)

    with app.test_request_context("/"):
        from app.services.rapport_sos import lire_erreurs, regrouper

        groupes = regrouper(lire_erreurs())
    assert [(g.type, g.occurrences) for g in groupes] == [("Avertissement", 2), ("ZeroDivisionError", 3)]
    assert groupes[1].lieu == "app/services/senacs.py:42"
    assert (groupes[1].premiere, groupes[1].date) == ("2026-10-14 10:00:00", "2026-10-14 10:02:00")

    rapport = _rapport_affiche(admin_client.post("/admin/sante/sos", data={"action": "afficher"}))
    assert "×3, du 2026-10-14 10:00:00 au 2026-10-14 10:02:00" in rapport


def test_rapport_sans_erreur_et_telechargement(app, admin_client, journaux):
    r = admin_client.post("/admin/sante/sos", data={"action": "telecharger", "contexte": ""})
    assert r.status_code == 200
    assert r.mimetype == "text/markdown"
    assert "attachment" in r.headers["Content-Disposition"] and ".md" in r.headers["Content-Disposition"]
    texte = r.get_data(as_text=True)
    assert texte.startswith("# Rapport SOS")
    assert "## Consigne pour l'IA" in texte and "AGENTS.md" in texte
    assert "Aucune erreur dans le journal" in texte
    assert "Base de données** : " in texte and "migration" in texte


def test_version_lue_dans_le_manifest_de_l_installateur(app, tmp_path):
    from app.services.rapport_sos import version_installee

    (tmp_path / "manifest.json").write_text(json.dumps({"version": "1.0.0-rc2", "source_commit": "abc123"}))
    with app.app_context():
        assert version_installee(tmp_path) == {
            "version": "1.0.0-rc2", "commit": "abc123", "source": "installateur Windows"}
        assert version_installee(tmp_path / "vide")["version"] == "inconnue"


def test_journaux_du_service_windows_inclus_et_anonymises(app, admin_client, journaux):
    (journaux / "service.log").write_text("Démarrage\nConnexion de jean.martin@exemple.fr refusée\n", encoding="utf-8")
    rapport = _rapport_affiche(admin_client.post("/admin/sante/sos", data={"action": "afficher"}))
    assert "Fin de `service.log`" in rapport and "jean.martin@exemple.fr" not in rapport


def test_reserve_aux_administrateurs(app, client):
    assert client.get("/admin/sante/sos").status_code in (302, 401, 403)
    assert client.post("/admin/sante/sos", data={"action": "afficher"}).status_code in (302, 401, 403)


def test_la_sante_du_systeme_mene_au_rapport(admin_client):
    page = admin_client.get("/admin/sante").get_data(as_text=True)
    assert "/admin/sante/sos" in page


def test_un_vrai_journal_ecrit_par_l_application_est_lu(app, admin_client, journaux):
    """Le format lu est bien celui qu'écrit _configure_error_logging."""
    gestionnaire = logging.FileHandler(journaux / "erreurs.log", encoding="utf-8")
    gestionnaire.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    journal = logging.getLogger(f"test.sos.{uuid.uuid4().hex}")
    journal.addHandler(gestionnaire)
    try:
        try:
            {}["absente"]
        except KeyError:
            journal.exception("Exception on /test [GET]")
    finally:
        gestionnaire.close()
        journal.removeHandler(gestionnaire)
    rapport = _rapport_affiche(admin_client.post("/admin/sante/sos", data={"action": "afficher"}))
    assert "### 1. KeyError" in rapport and "Exception on /test [GET]" in rapport
