"""Rapport SOS : tout ce qu'il faut pour faire réparer une panne, sans donnée personnelle.

Quand l'application plante dans un centre, la personne qui la maintient
n'est pas sur place, et ne lit pas forcément le Python : elle colle le
rapport dans une IA de programmation, ou l'envoie à quelqu'un qui le fera.
Le rapport dit donc, en Markdown :

- ce que l'utilisateur faisait (son texte, anonymisé) ;
- quelle version tourne (commit exact), sur quelle base, à quelle migration,
  avec quels modules, et dans quel état (sauvegarde, disque) ;
- les dernières erreurs du journal ``erreurs.log``, regroupées (une erreur
  répétée trente fois apparaît une fois, « ×30 »), traces comprises ;
- la fin des journaux du service Windows quand ils existent.

Tout le texte venu des journaux passe par
:mod:`app.services.anonymisation_texte` ; seules les lignes de code source
citées par les traces restent telles quelles (le code est public).
"""
from __future__ import annotations

import json
import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from flask import current_app

from app.services.anonymisation_texte import anonymiser, identites_connues

#: Combien d'erreurs distinctes garder, et combien de lignes de trace par erreur.
NB_ERREURS = 50
LIGNES_TRACE = 30
LIGNES_JOURNAL_SERVICE = 40

DEPOT = "https://github.com/informatiquecgbcreil/erp_api"

_DEBUT_ENTREE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d{3})? (?P<niveau>WARNING|ERROR|CRITICAL) "
    r"\[(?P<origine>[^\]]*)\] (?P<message>.*)$")
_LIGNE_FICHIER = re.compile(r'^\s*File "(?P<fichier>[^"]+)", line (?P<ligne>\d+), in (?P<fonction>.+)$')
_DERNIERE_LIGNE_TRACE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Fault)\b)")


@dataclass
class Erreur:
    date: str
    niveau: str
    origine: str
    message: str
    trace: list[str] = field(default_factory=list)
    occurrences: int = 1
    premiere: str = ""

    @property
    def type(self) -> str:
        for ligne in reversed(self.trace):
            trouve = _DERNIERE_LIGNE_TRACE.match(ligne.strip())
            if trouve:
                return trouve.group("type")
        return {"WARNING": "Avertissement", "ERROR": "Erreur", "CRITICAL": "Erreur critique"}.get(
            self.niveau, self.niveau)

    @property
    def lieu(self) -> str:
        """Le dernier endroit du code de l'application traversé par la trace."""
        lieux = []
        for ligne in self.trace:
            trouve = _LIGNE_FICHIER.match(ligne)
            if trouve:
                fichier = trouve.group("fichier").replace("\\", "/")
                if "/app/" in fichier and "site-packages" not in fichier:
                    lieux.append(f"app/{fichier.split('/app/', 1)[1]}:{trouve.group('ligne')}")
        return lieux[-1] if lieux else ""

    @property
    def signature(self) -> tuple:
        if self.trace:
            return (self.type, self.lieu)
        # Sans trace : le message, chiffres neutralisés (« 12 fiches », « 13 fiches »).
        return (self.niveau, re.sub(r"\d+", "#", self.message))


def dossier_journaux() -> Path:
    """Le dossier des journaux : celui que l'application écrit (voir app/__init__.py)."""
    import os

    return Path(os.environ.get("ERP_LOG_DIR") or Path(current_app.instance_path) / "logs")


def _fichiers_erreurs(dossier: Path) -> list[Path]:
    """erreurs.log puis ses rotations, du plus récent au plus ancien."""
    fichiers = [dossier / "erreurs.log"] + [dossier / f"erreurs.log.{i}" for i in range(1, 11)]
    return [f for f in fichiers if f.is_file()]


def _lire(fichier: Path) -> list[str]:
    try:
        return fichier.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def lire_erreurs(dossier: Path | None = None) -> list[Erreur]:
    """Toutes les entrées des journaux d'erreurs, de la plus ancienne à la plus récente."""
    dossier = dossier or dossier_journaux()
    entrees: list[Erreur] = []
    for fichier in reversed(_fichiers_erreurs(dossier)):
        courante = None
        for ligne in _lire(fichier):
            debut = _DEBUT_ENTREE.match(ligne)
            if debut:
                courante = Erreur(date=debut.group("date"), niveau=debut.group("niveau"),
                                  origine=debut.group("origine"), message=debut.group("message"))
                entrees.append(courante)
            elif courante is not None and ligne.strip():
                courante.trace.append(ligne)
    return entrees


def regrouper(entrees: list[Erreur], nombre: int = NB_ERREURS) -> list[Erreur]:
    """Une ligne par erreur distincte, la plus récente d'abord, avec son compte."""
    groupes: dict[tuple, Erreur] = {}
    for entree in entrees:  # de la plus ancienne à la plus récente
        cle = entree.signature
        deja = groupes.pop(cle, None)
        if deja is not None:
            entree.occurrences = deja.occurrences + 1
            entree.premiere = deja.premiere or deja.date
        groupes[cle] = entree  # réinsérée : l'ordre suit la dernière occurrence
    return list(reversed(list(groupes.values())))[:nombre]


def _anonymiser_trace(lignes: list[str], identites) -> list[str]:
    """Les lignes « File … » et le code cité restent ; le reste est anonymisé."""
    sortie = []
    code_attendu = False
    for ligne in lignes[-LIGNES_TRACE:]:
        if _LIGNE_FICHIER.match(ligne):
            sortie.append(anonymiser(ligne))  # chemin : seul le compte Windows est masqué
            code_attendu = True
            continue
        if code_attendu and ligne.startswith("    "):
            sortie.append(ligne)  # code source public
        else:
            sortie.append(anonymiser(ligne, identites))
        code_attendu = False
    return sortie


def version_installee(racine: Path | None = None) -> dict:
    """Version et commit : manifest de l'installateur, sinon dépôt git, sinon inconnus."""
    racine = racine or Path(current_app.root_path).parent
    manifest = racine / "manifest.json"
    if manifest.is_file():
        try:
            contenu = json.loads(manifest.read_text(encoding="utf-8"))
            return {"version": contenu.get("version") or "inconnue",
                    "commit": contenu.get("source_commit") or "inconnu", "source": "installateur Windows"}
        except (OSError, ValueError):
            pass
    tete = racine / ".git" / "HEAD"
    try:
        reference = tete.read_text(encoding="utf-8").strip()
        if reference.startswith("ref: "):
            chemin = racine / ".git" / reference[5:]
            commit = chemin.read_text(encoding="utf-8").strip() if chemin.is_file() else "inconnu"
        else:
            commit = reference
        return {"version": "développement", "commit": commit, "source": "dépôt git"}
    except OSError:
        return {"version": "inconnue", "commit": "inconnu", "source": "introuvable"}


def _installation() -> list[tuple[str, str]]:
    """L'état de l'installation, ligne par ligne (aucune donnée personnelle)."""
    import shutil

    from sqlalchemy import text

    from app.extensions import db
    from app.services.modules import CATALOG, enabled_modules

    lignes = []
    version = version_installee()
    lignes.append(("Version", f"{version['version']} (commit {version['commit'][:12]}, {version['source']})"))

    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "") or ""
    moteur = "PostgreSQL" if uri.startswith("postgresql") else ("SQLite" if uri.startswith("sqlite") else "autre")
    try:
        revision = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        base = f"{moteur}, joignable, migration {revision}"
    except Exception as exc:  # noqa: BLE001 — le rapport sert justement quand ça va mal
        db.session.rollback()
        base = f"{moteur}, ERREUR : {type(exc).__name__}"
    lignes.append(("Base de données", base))
    lignes.append(("Python / système", f"{sys.version.split()[0]} / {platform.system()} {platform.release()}"))

    try:
        modules = sorted(enabled_modules())
        lignes.append(("Modules actifs", ", ".join(CATALOG[m][0] for m in modules if m in CATALOG) or "aucun"))
    except Exception:  # noqa: BLE001
        lignes.append(("Modules actifs", "illisibles"))

    try:
        from app.services.territoire import territoire

        lignes.append(("Territoire réglé", "oui" if territoire().ville_siege else "non"))
    except Exception:  # noqa: BLE001
        pass

    try:
        from app.services.sauvegarde import dossier_sauvegardes, jours_depuis_derniere

        jours = jours_depuis_derniere()
        lignes.append(("Dernière sauvegarde", "aucune" if jours is None else f"il y a {jours} jour(s)"))
        usage = shutil.disk_usage(str(dossier_sauvegardes()))
        lignes.append(("Disque (dossier des sauvegardes)",
                       f"{usage.free // 1_000_000_000} Go libres sur {usage.total // 1_000_000_000} Go"))
    except Exception:  # noqa: BLE001
        lignes.append(("Sauvegardes", "état illisible"))
    return lignes


def generer(contexte: str | None = None, maintenant: datetime | None = None) -> str:
    """Le rapport complet, en Markdown, prêt à copier ou à télécharger."""
    from app.services.instance_settings import resolve_identity

    maintenant = maintenant or datetime.now()
    try:
        identites = identites_connues()
    except Exception:  # noqa: BLE001 — sans la base, les filets génériques restent
        identites = set()
    app_name, _org, _logo, _logo_org = resolve_identity(
        current_app.config.get("APP_NAME", "Mon Centre Social"), "")

    dossier = dossier_journaux()
    entrees = lire_erreurs(dossier)
    erreurs = regrouper(entrees)

    md = [
        f"# Rapport SOS — {anonymiser(app_name, identites)}",
        "",
        f"Généré le {maintenant.strftime('%d/%m/%Y à %H:%M')}. "
        "Les noms, e-mails, téléphones, adresses et jetons ont été remplacés par [NOM], [EMAIL], "
        "[TÉLÉPHONE], [ADRESSE], [JETON]… Ce rapport peut être collé dans une IA de programmation "
        "ou transmis à la personne qui maintient l'application.",
        "",
        "## Consigne pour l'IA",
        "",
        f"Tu aides à réparer l'application « Mon Centre Social » (code public : {DEPOT}, "
        "Flask + SQLAlchemy, PostgreSQL en production). Lis d'abord `AGENTS.md` à la racine du dépôt. "
        "À partir des informations ci-dessous :",
        "1. explique la cause probable en français simple, pour une personne qui ne lit pas le code ;",
        "2. propose le correctif minimal, avec le test qui le prouve, sur la version indiquée (commit) ;",
        "3. ne demande jamais de données personnelles : si une information manque, dis laquelle, "
        "sans nom ni coordonnées.",
        "",
        "## Ce que faisait l'utilisateur",
        "",
        anonymiser(contexte, identites).strip() or "_(non précisé)_",
        "",
        "## Installation",
        "",
    ]
    md += [f"- **{cle}** : {valeur}" for cle, valeur in _installation()]
    md += ["", f"## Erreurs récentes ({len(erreurs)} distinctes sur {len(entrees)} entrées du journal)", ""]
    if not erreurs:
        md.append("_Aucune erreur dans le journal `erreurs.log`._")
    for numero, erreur in enumerate(erreurs, start=1):
        titre = f"### {numero}. {erreur.type}"
        if erreur.lieu:
            titre += f" — `{erreur.lieu}`"
        md += [titre, ""]
        quand = f"le {erreur.date}"
        if erreur.occurrences > 1:
            quand = f"×{erreur.occurrences}, du {erreur.premiere} au {erreur.date}"
        md.append(f"- {erreur.niveau}, {quand} ({erreur.origine})")
        md.append(f"- Message : {anonymiser(erreur.message, identites)}")
        if erreur.trace:
            md += ["", "```", *_anonymiser_trace(erreur.trace, identites), "```"]
        md.append("")

    for nom in ("runtime.log", "service.log"):
        lignes = _lire(dossier / nom)[-LIGNES_JOURNAL_SERVICE:]
        if lignes:
            md += [f"## Fin de `{nom}`", "", "```", *[anonymiser(l, identites) for l in lignes], "```", ""]
    return "\n".join(md).rstrip() + "\n"
