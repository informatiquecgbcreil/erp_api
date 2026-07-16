"""Recette metier courte sur une copie temporaire de la base.

Le crawler de liens teste les boutons GET et les POST sans saisie libre.
Ce script complete la couverture avec quelques formulaires essentiels en
injectant des donnees factices dans une copie SQLite jetable.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from smoke_post_buttons_copy import (  # noqa: E402
    cleanup_database_copy,
    configure_database,
    dispose_app,
    make_authenticated_client,
    prepare_database_copy,
)


class HiddenInputParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs):
        if tag != "input":
            return
        attrs = dict(attrs)
        if (attrs.get("type") or "").strip().lower() != "hidden":
            return
        name = (attrs.get("name") or "").strip()
        if name:
            self.fields[name] = attrs.get("value") or ""


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    detail: str


def hidden_fields(client, url: str) -> dict[str, str]:
    resp = client.get(url, follow_redirects=False)
    if resp.status_code >= 400:
        raise RuntimeError(f"GET {url} -> {resp.status_code}")
    parser = HiddenInputParser()
    parser.feed(resp.get_data(as_text=True))
    return parser.fields


def post_form(client, get_url: str, post_url: str, data: dict) -> int:
    payload = hidden_fields(client, get_url)
    payload.update(data)
    resp = client.post(post_url, data=payload, follow_redirects=False)
    return resp.status_code


def check_created(name: str, status: int, created: bool) -> ScenarioResult:
    ok = status in {200, 302, 303} and created
    detail = f"status={status}, creation={'oui' if created else 'non'}"
    return ScenarioResult(name, ok, detail)


def run(args) -> int:
    db_copy = prepare_database_copy(args.keep_copy)
    configure_database(db_copy)

    from app import create_app
    from app.models import (
        InventaireItem,
        OrientationAccesDroit,
        Partenaire,
        Participant,
        Projet,
        ProjetAction,
        ProjetIndicateur,
        ProjetJournalEntry,
        Quartier,
        SuiviRappel,
        User,
    )

    app, client = make_authenticated_client(create_app, User, args.user)
    suffix = datetime.now().strftime("%Y%m%d%H%M%S")
    results: list[ScenarioResult] = []

    try:
        with app.app_context():
            user = User.query.filter_by(email=args.user).first() if args.user else None
            user = user or User.query.order_by(User.id.asc()).first()
            projet = Projet.query.order_by(Projet.id.asc()).first()
            secteur = (
                getattr(projet, "secteur", None)
                or getattr(user, "secteur_assigne", None)
                or "Numerique"
            )

        quartier_nom = f"Smoke Quartier {suffix}"
        status = post_form(
            client,
            "/quartiers/",
            "/quartiers/new",
            {
                "ville": "Smokeville",
                "nom": quartier_nom,
                "description": "Quartier cree par recette automatisee.",
                "is_qpv": "1",
            },
        )
        with app.app_context():
            quartier = Quartier.query.filter_by(ville="Smokeville", nom=quartier_nom).first()
        results.append(check_created("Creer un quartier", status, quartier is not None))

        participant_nom = f"Smoke Participant {suffix}"
        status = post_form(
            client,
            "/participants/new",
            "/participants/new",
            {
                "nom": participant_nom,
                "prenom": "Recette",
                "telephone": "0102030405",
                "email": f"smoke.{suffix}@example.test",
                "ville": "Smokeville",
                "quartier_id": str(quartier.id) if quartier else "",
                "genre": "F",
                "type_public": "H",
                "created_secteur": secteur,
            },
        )
        with app.app_context():
            participant = Participant.query.filter_by(nom=participant_nom, prenom="Recette").first()
        results.append(check_created("Creer une personne", status, participant is not None))

        partenaire_nom = f"Smoke Partenaire {suffix}"
        status = post_form(
            client,
            "/partenaires/new",
            "/partenaires/new",
            {
                "nom": partenaire_nom,
                "secteur": secteur,
                "contact_prenom": "Contact",
                "contact_nom": "Recette",
                "email_general": f"partenaire.{suffix}@example.test",
                "competence_orientation": "logement",
                "niveau_orientation": "principal",
                "modalites_orientation": "Accueil sur rendez-vous.",
            },
        )
        with app.app_context():
            partenaire = Partenaire.query.filter_by(nom=partenaire_nom).first()
        results.append(check_created("Creer un partenaire", status, partenaire is not None))

        demande = f"Demande logement social smoke {suffix}"
        status = post_form(
            client,
            "/partenaires/orientations",
            "/partenaires/orientations",
            {
                "date_orientation": date.today().isoformat(),
                "secteur": secteur,
                "participant_id": str(participant.id) if participant else "",
                "ville": "Smokeville",
                "quartier_id": str(quartier.id) if quartier else "",
                "domaine": "logement",
                "partenaire_id": str(partenaire.id) if partenaire else "",
                "demande": demande,
                "statut": "oriente",
                "urgence": "prioritaire",
                "suite_prevue": (date.today() + timedelta(days=7)).isoformat(),
                "note": "Orientation creee par recette automatisee.",
            },
        )
        with app.app_context():
            orientation = OrientationAccesDroit.query.filter_by(demande=demande).first()
        results.append(check_created("Creer une orientation acces droits", status, orientation is not None))

        rappel_titre = f"Smoke Rappel {suffix}"
        status = post_form(
            client,
            "/suivi-rappels",
            "/suivi-rappels/rappel/new",
            {
                "titre": rappel_titre,
                "echeance": (date.today() + timedelta(days=3)).isoformat(),
                "priorite": "warn",
                "categorie": "orientation",
                "secteur": secteur,
                "description": "Rappel cree par recette automatisee.",
                "lien_url": "/suivi-rappels",
            },
        )
        with app.app_context():
            rappel = SuiviRappel.query.filter_by(titre=rappel_titre).first()
        results.append(check_created("Creer un rappel", status, rappel is not None))

        with app.app_context():
            projet_id = projet.id if projet else None

        if projet_id:
            journal_titre = f"Smoke Journal {suffix}"
            status = post_form(
                client,
                f"/projets/{projet_id}/synthese",
                f"/projets/{projet_id}/synthese",
                {
                    "action": "add_journal",
                    "entry_date": date.today().isoformat(),
                    "categorie": "fait_marquant",
                    "titre": journal_titre,
                    "contenu": "Note de journal creee par recette automatisee.",
                },
            )
            with app.app_context():
                journal = ProjetJournalEntry.query.filter_by(projet_id=projet_id, titre=journal_titre).first()
            results.append(check_created("Ajouter une note projet", status, journal is not None))

            action_titre = f"Smoke Fiche action {suffix}"
            status = post_form(
                client,
                f"/projets/{projet_id}/synthese",
                f"/projets/{projet_id}/synthese",
                {
                    "action": "add_project_action",
                    "titre": action_titre,
                    "categorie": "atelier",
                    "statut": "prevue",
                    "date_debut": date.today().isoformat(),
                    "date_fin": (date.today() + timedelta(days=30)).isoformat(),
                    "description": "Fiche action creee par recette automatisee.",
                },
            )
            with app.app_context():
                projet_action = ProjetAction.query.filter_by(projet_id=projet_id, titre=action_titre).first()
            results.append(check_created("Creer une fiche action projet", status, projet_action is not None))

            indicateur_label = f"Smoke Indicateur {suffix}"
            status = post_form(
                client,
                f"/projets/{projet_id}/indicateurs",
                f"/projets/{projet_id}/indicateurs",
                {
                    "action": "add_manual_number",
                    "label": indicateur_label,
                    "manual_value": "7",
                    "unit": "pers.",
                    "target_op": "ge",
                    "target": "5",
                },
            )
            with app.app_context():
                indicateur = ProjetIndicateur.query.filter_by(projet_id=projet_id, label=indicateur_label).first()
            results.append(check_created("Creer un indicateur manuel", status, indicateur is not None))
        else:
            results.append(ScenarioResult("Parcours projet", True, "ignore: aucun projet disponible"))

        inventaire_designation = f"Smoke Materiel {suffix}"
        status = post_form(
            client,
            "/inventaire/new",
            "/inventaire/new",
            {
                "secteur": secteur,
                "designation": inventaire_designation,
                "categorie": "Recette",
                "quantite": "1",
                "etat": "OK",
                "localisation": "Smoke",
                "marque": "Test",
                "modele": "Workflow",
                "valeur_unitaire": "10",
            },
        )
        with app.app_context():
            item = InventaireItem.query.filter_by(designation=inventaire_designation).first()
        results.append(check_created("Creer une entree inventaire", status, item is not None))

    finally:
        dispose_app(app)
        cleanup_database_copy(db_copy, args.keep_copy)

    print(f"Copie DB: {db_copy}")
    print(f"Scenarios testes: {len(results)}")
    failures = [row for row in results if not row.ok]
    print(f"Echecs: {len(failures)}")
    for row in results:
        prefix = "OK" if row.ok else "FAIL"
        print(f"[{prefix}] {row.name} - {row.detail}")

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="Email de l'utilisateur a injecter en session.")
    parser.add_argument("--keep-copy", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
