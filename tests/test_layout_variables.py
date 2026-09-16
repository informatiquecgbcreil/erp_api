"""Le gabarit parent ne doit pas voler les noms de variables des pages.

Panne constatée en production, sur l'écran des dépenses :

    TypeError: must be real number, not str
    {{ "%.2f"|format(reste) }}

La route envoyait pourtant un nombre :

    reste = float(ligne.reste or 0) if ligne else 0.0

Ce n'était pas le sien qui arrivait dans le gabarit. Le fil d'Ariane de
``layout.html`` posait, quelques lignes avant ``{% block body %}`` :

    {% set reste = endpoint_action.split('_')[1:] | join(' ') %}

Un ``{% set %}`` posé à la racine du gabarit PARENT vit dans une portée qui
englobe les blocs des enfants : il y écrase la variable passée à
``render_template``. Sur l'endpoint ``budget.depense_edit``, « reste »
valait donc la chaîne « edit », et Jinja refusait de la formater en
« %.2f ».

Le plus méchant : la dépense était quand même créée, et l'écran plantait
APRÈS — impossible de la rouvrir ensuite pour la corriger.

Ce fichier interdit que ça se reproduise, pour n'importe lequel des noms
que le gabarit parent pose. Une collision devient une suite rouge plutôt
qu'un écran mort chez l'utilisateur.
"""
import pathlib
import re

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
LAYOUT = RACINE / "app" / "templates" / "layout.html"


def _noms_poses_par_le_parent() -> set[str]:
    """Tous les noms que layout.html affecte par {% set %}."""
    texte = LAYOUT.read_text(encoding="utf-8")
    return set(re.findall(r"{%-?\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", texte))


def _noms_passes_par_les_routes() -> dict[str, set[str]]:
    """Les mots-clés passés à render_template, et par quels fichiers."""
    trouves: dict[str, set[str]] = {}
    for fichier in (RACINE / "app").rglob("*.py"):
        texte = fichier.read_text(encoding="utf-8", errors="ignore")
        for bloc in re.findall(r"render_template\((.*?)\)", texte, re.S):
            for nom in re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", bloc, re.M):
                trouves.setdefault(nom, set()).add(
                    str(fichier.relative_to(RACINE)))
    return trouves


def test_le_parent_ne_vole_aucun_nom_de_page():
    """LE test qui aurait évité la panne.

    Si une page passe à render_template un nom que layout.html pose aussi,
    c'est la valeur du gabarit parent qui gagne — silencieusement, et
    seulement sur les endpoints où le {% set %} du parent s'exécute. Le
    symptôme est alors incompréhensible : « la route envoie un nombre, le
    gabarit reçoit du texte ».
    """
    parent = _noms_poses_par_le_parent()
    pages = _noms_passes_par_les_routes()
    collisions = {nom: sorted(fichiers) for nom, fichiers in pages.items()
                  if nom in parent}

    assert not collisions, (
        "layout.html pose des {% set %} portant des noms que des pages "
        "passent à render_template ; la valeur du parent écrasera celle de "
        "la page dans {% block body %}. Renommer côté layout (préfixe "
        f"explicite) : {collisions}"
    )


def test_le_fil_dariane_nutilise_plus_de_noms_nus():
    """« premier » et « reste » étaient les deux plus exposés : des mots
    courants, qu'une page de comptabilité ou de stock emploie naturellement."""
    texte = LAYOUT.read_text(encoding="utf-8")
    for nom in ("premier", "reste"):
        assert not re.search(rf"{{%-?\s*set\s+{nom}\s*=", texte), (
            f"le gabarit parent repose un {{% set {nom} %}} nu : "
            "c'est ce nom-là qui avait cassé l'écran des dépenses"
        )


def test_la_portee_du_parent_ecrase_bien_celle_de_la_page():
    """Le mécanisme, isolé — pour que le test du dessus ne passe pas pour
    de la superstition le jour où quelqu'un voudra l'assouplir."""
    from jinja2 import DictLoader, Environment

    env = Environment(loader=DictLoader({
        "parent.html": '{% set valeur = "texte" %}{% block body %}{% endblock %}',
        "page.html": '{% extends "parent.html" %}{% block body %}{{ valeur }}{% endblock %}',
    }))
    # La page passe un nombre, le parent impose sa chaîne.
    assert env.get_template("page.html").render(valeur=12.5) == "texte"


# ---------------------------------------------------------------------------
# L'écran qui plantait
# ---------------------------------------------------------------------------

@pytest.fixture()
def depense(app):
    """Une dépense rattachée à une ligne de budget, comme à la saisie."""
    import uuid
    from datetime import date

    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        from app.extensions import db
        from app.models import Depense, LigneBudget, Subvention

        sub = Subvention(nom=f"Sub{suf}", secteur="Numérique", annee_exercice=2026)
        db.session.add(sub)
        db.session.flush()
        ligne = LigneBudget(subvention_id=sub.id, nature="charge", compte="60",
                            libelle=f"Ligne{suf}", montant_base=1000.0,
                            montant_reel=1000.0)
        db.session.add(ligne)
        db.session.flush()
        dep = Depense(ligne_budget_id=ligne.id, libelle=f"Achat{suf}", montant=42.5,
                      date_paiement=date(2026, 9, 10))
        db.session.add(dep)
        db.session.commit()
        contexte = {"depense_id": dep.id, "ligne_id": ligne.id, "sub_id": sub.id}

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import Depense, LigneBudget, Subvention

        for modele, cle in ((Depense, "depense_id"), (LigneBudget, "ligne_id"),
                            (Subvention, "sub_id")):
            objet = db.session.get(modele, contexte[cle])
            if objet is not None:
                db.session.delete(objet)
        db.session.commit()


def test_lecran_de_la_depense_souvre(admin_client, depense):
    """Il rendait « TypeError: must be real number, not str », et la dépense
    restait impossible à rouvrir une fois créée."""
    r = admin_client.get(f"/depense/{depense['depense_id']}/edit")
    assert r.status_code == 200, r.status_code
    page = r.get_data(as_text=True)
    assert "Reste sur la ligne" in page
    # Le montant est bien formaté, pas un morceau de nom d'endpoint.
    assert "1000.00€" in page
    assert "edit€" not in page
