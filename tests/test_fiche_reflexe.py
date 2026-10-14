"""Fiche réflexe : une page A4 qui ne ment jamais.

La fiche est imprimée et collée près du poste ; si un bouton est renommé,
le papier envoie l'accueil chercher un libellé qui n'existe plus. Ces tests
vérifient que chaque libellé cité entre « … » existe dans un écran, et que
chaque écran de départ s'ouvre.
"""
import html
import re
from pathlib import Path

GABARITS = Path(__file__).resolve().parent.parent / "app" / "templates"


def _normaliser(texte: str) -> str:
    # Apostrophe droite ou typographique : les gabarits emploient les deux.
    return html.unescape(texte).replace("’", "'")


def libelles_absents(gestes: list[str]) -> list[str]:
    """Les libellés « … » des gestes introuvables dans les gabarits."""
    ecrans = _normaliser("\n".join(p.read_text(encoding="utf-8") for p in GABARITS.rglob("*.html")))
    cites = [m.strip() for geste in gestes for m in re.findall(r"«\s*([^»]+?)\s*»", geste)]
    return [libelle for libelle in cites if _normaliser(libelle) not in ecrans]


def test_chaque_libelle_cite_existe_dans_un_ecran():
    from app.aide.contenu import FICHE_REFLEXE

    assert libelles_absents([cas["geste"] for cas in FICHE_REFLEXE]) == []


def test_le_controle_attrape_un_bouton_renomme():
    """Sans quoi le test précédent pourrait passer à vide."""
    assert libelles_absents(["Cliquez sur « Bouton qui n'existe pas » puis « Rapport SOS »."]) == [
        "Bouton qui n'existe pas"]


def test_la_fiche_et_ses_ecrans_de_depart_s_ouvrent(admin_client):
    from flask import url_for

    from app.aide.contenu import FICHE_REFLEXE

    page = admin_client.get("/aide/fiche-reflexe")
    assert page.status_code == 200
    texte = _normaliser(page.get_data(as_text=True))
    assert "Imprimer la fiche" in texte and "Personne référente" in texte
    for cas in FICHE_REFLEXE:
        assert _normaliser(cas["situation"]) in texte
    with admin_client.application.test_request_context():
        departs = {url_for(cas["page"]) for cas in FICHE_REFLEXE if cas["page"]}
    for adresse in sorted(departs):
        assert adresse in texte
        assert admin_client.get(adresse).status_code == 200, adresse


def test_trouvable_depuis_l_aide_et_reservee_aux_connectes(admin_client, client):
    assert "/aide/fiche-reflexe" in admin_client.get("/aide/").get_data(as_text=True)
    assert client.get("/aide/fiche-reflexe").status_code in (302, 401, 403)
