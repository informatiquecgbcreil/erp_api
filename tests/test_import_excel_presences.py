"""Import Excel des présences (Administration) : plus rien de Creil en dur.

L'import mettait « Creil » à toute ligne sans ville et ne gardait les
quartiers que pour Creil ; un quartier créé était déclaré QPV s'il
contenait « ROUHER » ou « HAUT » + « CREIL ». Décisions du centre :
ville absente = ville vide, quartiers gardés pour toutes les villes.
"""
import datetime as dt
import io
import uuid

from openpyxl import Workbook

JOUR = dt.datetime(2019, 3, 12)


def _suffixe() -> str:
    return uuid.uuid4().hex[:6]


def _classeur(lignes, *, avec_ville=True, onglet="Atelier") -> bytes:
    """Le format des tableaux de présence : identité, puis une colonne par date."""
    wb = Workbook()
    ws = wb.active
    ws.title = onglet
    entetes = ["NOMS", "PRENOMS", "ANNEE", "", "SEXE", "QUARTIER"]
    if avec_ville:
        entetes.append("VILLE")
    ws.append(entetes + [JOUR])
    for ligne in lignes:
        ws.append(ligne)
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def _importer(app, contenu, tmp_path, secteur):
    from app.ateliers.excel_import import import_presences_from_xlsx

    chemin = tmp_path / "presences.xlsx"
    chemin.write_bytes(contenu)
    with app.app_context():
        return import_presences_from_xlsx(str(chemin), secteur=secteur)


def _participant(app, nom):
    from app.models import Participant

    with app.app_context():
        p = Participant.query.filter_by(nom=nom).one()
        q = p.quartier
        return {
            "ville": p.ville,
            "quartier": (q.nom, q.ville, q.qpv, bool(q.is_qpv)) if q else None,
        }


def test_une_ligne_sans_ville_reste_sans_ville(app, tmp_path, regler_territoire):
    regler_territoire(territoire_ville="Beauvais")
    suf = _suffixe()
    stats = _importer(app, _classeur([
        [f"SANSVILLE{suf}", "Léa", 1990, "", "F", f"Inconnu {suf}", None, "x"],
    ]), tmp_path, f"Sect{suf}")

    assert _participant(app, f"SANSVILLE{suf}") == {"ville": None, "quartier": None}
    assert stats["quartiers_ignores"] == 1
    assert stats["presences_created"] == 1


def test_les_quartiers_sont_gardes_pour_toutes_les_villes(app, tmp_path, regler_territoire):
    """Beauvais suit le QPV Saint-Jean : le quartier créé le porte."""
    regler_territoire(territoire_ville="Beauvais", territoire_qpv_suivis="Saint-Jean")
    suf = _suffixe()
    _importer(app, _classeur([
        [f"BEAUV{suf}", "Awa", 1995, "", "F", f"Saint-Jean ({suf})", "beauvais", "x"],
        [f"ALLON{suf}", "Paul", 1980, "", "M", f"Centre bourg {suf}", "Allonne", "x"],
    ]), tmp_path, f"Sect{suf}")

    assert _participant(app, f"BEAUV{suf}") == {
        "ville": "Beauvais", "quartier": (f"Saint-Jean ({suf})", "Beauvais", "Saint-Jean", True)}
    assert _participant(app, f"ALLON{suf}") == {
        "ville": "Allonne", "quartier": (f"Centre bourg {suf}", "Allonne", None, False)}


def test_plus_de_qpv_devine_avec_des_mots_de_creil(app, tmp_path, regler_territoire):
    """Sans territoire réglé, « Rouher » n'est qu'un nom ; « QPV » dans le
    nom reste un indice valable partout."""
    regler_territoire()
    suf = _suffixe()
    _importer(app, _classeur([
        [f"ROUH{suf}", "Inès", 2001, "", "F", f"Rouher {suf}", "Creil", "x"],
        [f"HAUT{suf}", "Sami", 1999, "", "M", f"Haut de Creil {suf}", "Creil", "x"],
        [f"QPVN{suf}", "Lina", 2003, "", "F", f"Quartier QPV {suf}", "Nogent-sur-Oise", "x"],
    ]), tmp_path, f"Sect{suf}")

    assert _participant(app, f"ROUH{suf}")["quartier"][3] is False
    assert _participant(app, f"HAUT{suf}")["quartier"][3] is False
    assert _participant(app, f"QPVN{suf}")["quartier"][3] is True


def test_creil_garde_ses_qpv_par_son_territoire(app, tmp_path, territoire_creil):
    suf = _suffixe()
    _importer(app, _classeur([
        [f"HDC{suf}", "Yanis", 1992, "", "M", f"Hauts de Creil ({suf})", "Creil", "x"],
        [f"ROU{suf}", "Mia", 1994, "", "F", f"Rouher ({suf})", "Creil", "x"],
    ]), tmp_path, f"Sect{suf}")

    assert _participant(app, f"HDC{suf}")["quartier"] == (
        f"Hauts de Creil ({suf})", "Creil", "Hauts de Creil", True)
    # Le Rouher est un quartier distingué : QPV, sans nom de QPV déduit.
    assert _participant(app, f"ROU{suf}")["quartier"] == (f"Rouher ({suf})", "Creil", None, True)


def test_sans_colonne_ville_un_quartier_connu_dans_une_seule_commune_est_repris(app, tmp_path,
                                                                               regler_territoire):
    from app.extensions import db
    from app.models import Quartier

    regler_territoire()
    suf = _suffixe()
    with app.app_context():
        db.session.add(Quartier(ville="Montataire", nom=f"Les Martinets {suf}", is_qpv=True))
        db.session.add_all([Quartier(ville=v, nom=f"Centre {suf}") for v in ("Creil", "Nogent-sur-Oise")])
        db.session.commit()

    stats = _importer(app, _classeur([
        [f"UNIQ{suf}", "Noa", 1990, "", "M", f"Les Martinets {suf}", "x"],
        [f"AMBI{suf}", "Eva", 1991, "", "F", f"Centre {suf}", "x"],
    ], avec_ville=False), tmp_path, f"Sect{suf}")

    # Ville jamais devinée, même quand le quartier est retrouvé.
    assert _participant(app, f"UNIQ{suf}") == {
        "ville": None, "quartier": (f"Les Martinets {suf}", "Montataire", None, True)}
    # Deux communes ont un « Centre » : on ne choisit pas au hasard.
    assert _participant(app, f"AMBI{suf}") == {"ville": None, "quartier": None}
    assert stats["quartiers_ignores"] == 1
    avertissements = " ".join(stats["sheets"]["Atelier"]["warnings"])
    assert "Colonne VILLE non détectée" in avertissements and "Creil" not in avertissements


def test_l_ecran_d_import_signale_les_quartiers_non_repris(app, admin_client, regler_territoire):
    from app.secteurs import get_secteur_labels

    regler_territoire()
    suf = _suffixe()
    with app.app_context():
        secteur = get_secteur_labels(active_only=True)[0]
    r = admin_client.post("/admin/import-excel", data={
        "secteur": secteur, "dry_run": "1",
        "xlsx_file": (io.BytesIO(_classeur([[f"ECRAN{suf}", "Jo", 1990, "", "M", f"Nulle part {suf}", None, "x"]])),
                      "presences.xlsx"),
    }, content_type="multipart/form-data")
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Quartiers non repris (ville manquante)" in page
