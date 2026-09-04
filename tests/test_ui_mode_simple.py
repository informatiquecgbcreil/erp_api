"""Mode simple : repli des champs secondaires (macro `bloc_avance`).

L'application est utilisée au quotidien par des personnes peu à l'aise avec le
numérique. Un écran de saisie à vingt-cinq champs est un mur ; le mode simple
n'en montre d'abord que l'essentiel et range le reste dans des blocs repliés.

Ces tests protègent les deux promesses qui rendent le procédé acceptable :
**on replie, on ne supprime jamais** (aucun champ ne disparaît du formulaire),
et **aucun champ obligatoire n'est caché** (un champ requis invisible fait
échouer l'enregistrement en silence, sans que l'utilisateur comprenne pourquoi).
"""
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

RACINE = Path(__file__).resolve().parent.parent / "app" / "templates"

#: Contenu d'un `{% call bloc_avance(...) %} ... {% endcall %}`.
BLOC_REPLIE = re.compile(r"\{%-?\s*call\s+bloc_avance\b.*?\{%-?\s*endcall\s*-?%\}", re.S)


def _gabarits() -> list[Path]:
    return sorted(RACINE.rglob("*.html"))


def test_tous_les_gabarits_compilent():
    """Un gabarit mal fermé fait planter sa page en production, pas les tests.

    Le cas réel : un `{% endif %}` manquant dans
    `previsionnel/appel_detail.html` rendait la page inaccessible sans que rien
    ne le signale, parce qu'aucun test n'ouvrait cette page. Ce contrôle passe
    sur les 216 gabarits d'un coup, sans avoir à écrire une requête par page.
    """
    env = Environment(loader=FileSystemLoader(str(RACINE)))
    erreurs = []
    for gabarit in _gabarits():
        try:
            env.parse(gabarit.read_text(encoding="utf-8"), filename=str(gabarit))
        except Exception as exc:
            erreurs.append(f"{gabarit.relative_to(RACINE)} : {exc}")
    assert not erreurs, "gabarits invalides :\n  - " + "\n  - ".join(erreurs)


def test_aucun_champ_obligatoire_cache_dans_un_formulaire_ouvert():
    """Un champ requis invisible fait échouer l'enregistrement en silence.

    Le danger est précis : un champ obligatoire replié **alors que le
    formulaire auquel il appartient, lui, est visible**. L'utilisateur remplit
    ce qu'il voit, clique sur Enregistrer, et rien ne se passe — le navigateur
    ne sait pas signaler un champ qu'il ne peut pas afficher.

    Un bloc replié qui contient son formulaire *entier*, bouton compris, ne
    pose pas ce problème : il faut l'ouvrir pour s'en servir. C'est le cas des
    cartes secondaires (justificatifs, inventaire), qui restent donc permises.
    """
    fautifs = []
    for gabarit in _gabarits():
        source = gabarit.read_text(encoding="utf-8")
        for bloc in BLOC_REPLIE.findall(source):
            for balise in re.finditer(r"<(?:input|select|textarea)\b[^>]*>", bloc):
                if not re.search(r"\brequired\b", balise.group()):
                    continue
                # Le bloc porte-t-il son propre <form> avant ce champ ?
                if re.search(r"<form\b", bloc[: balise.start()]):
                    continue
                fautifs.append(f"{gabarit.relative_to(RACINE)} : {balise.group()[:90]}")
    assert not fautifs, (
        "champ(s) obligatoire(s) replié(s) hors de leur propre formulaire :\n  - "
        + "\n  - ".join(fautifs)
    )


def _page_depense(client, app, mode: str) -> str:
    """Ouvre le formulaire de dépense après avoir basculé le mode d'affichage.

    On passe par la vraie bascule (`POST /ui-mode`) plutôt que par la session :
    pour un utilisateur connecté, c'est la préférence enregistrée qui fait foi,
    la session seule ne suffirait pas.
    """
    with app.test_request_context():
        from flask import url_for
        url_bascule = url_for("main.set_ui_mode")
        url_page = url_for("budget.depense_new")
    client.post(url_bascule, data={"mode": mode, "next": url_page})
    reponse = client.get(url_page)
    assert reponse.status_code == 200, f"page dépense inaccessible en mode {mode}"
    return reponse.get_data(as_text=True)


def test_blocs_replies_en_mode_simple(admin_client, app):
    page = _page_depense(admin_client, app, "simple")
    assert "bloc-avance" in page, "le formulaire devrait utiliser des blocs repliables"
    assert "data-bloc-avance open" not in page, "en mode simple, les blocs sont repliés"


def test_blocs_ouverts_en_mode_expert(admin_client, app):
    page = _page_depense(admin_client, app, "expert")
    assert "data-bloc-avance open" in page, "en mode expert, tout est déplié d'emblée"


def test_aucun_champ_perdu_entre_les_deux_modes(admin_client, app):
    """La garantie centrale : replier n'est pas supprimer.

    Les deux modes servent exactement les mêmes champs — seul leur pliage
    change. Un champ qui disparaîtrait en mode simple, c'est une donnée que
    l'utilisateur ne pourrait plus saisir sans savoir qu'elle existe.
    """
    champs = re.compile(r'<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"')
    simple = sorted(champs.findall(_page_depense(admin_client, app, "simple")))
    expert = sorted(champs.findall(_page_depense(admin_client, app, "expert")))
    assert simple == expert, "les champs diffèrent entre mode simple et mode expert"
    assert "fournisseur" in simple, "les champs repliés doivent rester dans le formulaire"


def test_page_appel_detail_accessible(admin_client, app):
    """Régression : cette page ne s'ouvrait plus (gabarit mal fermé)."""
    env = Environment(loader=FileSystemLoader(str(RACINE)))
    source = (RACINE / "previsionnel" / "appel_detail.html").read_text(encoding="utf-8")
    env.parse(source)  # lève si le gabarit est cassé
    assert source.count("{% endif %}") >= source.count("{% if ") - source.count("{% elif ")
