"""Veille financements : collecte automatique des appels à projets.

Objectif : automatiser ~90 % de la veille « financements » d'une association
loi 1901 (agréments CAF et Jeunesse & Éducation Populaire) — appels à
projets (AAP), appels à manifestation d'intérêt (AMI), appels à
candidatures, subventions, prix — publiés par les fondations privées et les
pouvoirs publics (villes, agglos, département de l'Oise, région
Hauts-de-France, FEDER/FSE, État...).

Trois collecteurs, AUCUNE dépendance externe (urllib/xml/html de la stdlib,
même principe que le géocodage) :

- ``aides_territoires`` : l'API publique nationale Aides-territoires
  (aides-territoires.beta.gouv.fr) qui agrège État, régions, départements,
  agences et fonds européens. Nécessite une clé API gratuite (compte sur le
  site → « Mon compte » → jeton API), collée dans la fiche source.
- ``rss`` : n'importe quel flux RSS/Atom (fondations, collectivités...).
- ``html_liens`` : pour les sites sans flux — on extrait de la page les
  liens dont le libellé ressemble à un appel (« appel à projets »...).

Chaque trouvaille est scorée contre le profil de l'association
(mots-clés pondérés : éducation populaire, CAF, jeunesse, Oise, HDF...)
puis dédoublonnée par empreinte d'URL : les statuts de suivi posés par
l'équipe (« à étudier », « déposé »...) survivent aux rafraîchissements.

Cadence : rafraîchissement automatique TOUS LES 3 JOURS, déclenché
paresseusement à la première requête (même mécanique que la purge RGPD :
aucun planificateur externe), exécuté dans un thread d'arrière-plan pour ne
jamais ralentir une page. Une erreur réseau sur une source n'interrompt
jamais les autres : elle est simplement journalisée sur la fiche source.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from flask import current_app

from app.extensions import db
from app.models import VeilleOpportunite, VeilleSource
from app.utils.dates import utcnow

# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------

INTERVALLE_JOURS = 3  # cadence de rafraîchissement automatique
DELAI_HTTP = 25  # secondes par requête
USER_AGENT = "AppGestion-ERP/1.0 (veille financements associative)"
MAX_PAGES_AIDES_TERRITOIRES = 3  # 3 pages de 50 = les ~150 aides les plus récentes

AIDES_TERRITOIRES_BASE = "https://aides-territoires.beta.gouv.fr"

# Profil de pertinence de l'association : mots-clés pondérés.
# Le score d'une opportunité est la somme des poids des mots-clés retrouvés
# dans son titre + sa description. Plus le score est haut, plus l'appel
# « ressemble » à l'association (loi 1901, CAF, JEP, Oise, HDF).
PROFIL_MOTS_CLES: dict[str, int] = {
    # Territoire (le plus discriminant)
    "oise": 5,
    "creil": 5,
    "hauts-de-france": 4,
    "picardie": 3,
    # Cœur de métier
    "education populaire": 5,
    "jeunesse": 3,
    "centre social": 4,
    "animation de la vie sociale": 4,
    "caf": 3,
    "parentalite": 3,
    "famille": 2,
    "cohesion sociale": 3,
    "politique de la ville": 3,
    "quartier": 2,
    "vie associative": 3,
    "fdva": 4,
    "insertion": 2,
    "mediation": 2,
    "inclusion numerique": 3,
    "numerique": 1,
    "benevolat": 2,
    "association": 1,
    "jeunes": 2,
    "enfance": 2,
    "culture": 1,
    "sport": 1,
    "solidarite": 2,
    # Europe
    "feder": 3,
    "fse": 3,
    "erasmus": 2,
}

# Détection du type de dispositif à partir du texte.
TYPES_DISPOSITIF: list[tuple[str, str]] = [
    ("ami", "appel a manifestation"),
    ("ami", "manifestation d'interet"),
    ("candidature", "appel a candidature"),
    ("aap", "appel a projet"),
    ("aap", "appels a projet"),
    ("prix", "concours"),
    ("prix", "prix "),
    ("prix", "trophee"),
    ("subvention", "subvention"),
    ("subvention", "aide financiere"),
    ("subvention", "fonds "),
]

# Libellés déclencheurs pour le collecteur ``html_liens`` : un lien n'est
# retenu que si son texte contient l'un de ces fragments (sans accents).
DECLENCHEURS_LIENS = [
    "appel a projet",
    "appels a projet",
    "appel a manifestation",
    "appel a candidature",
    "appels a candidature",
    "appel a initiative",
    "subvention",
    "fonds de soutien",
    "concours",
]

# ---------------------------------------------------------------------------
# Catalogue de sources livré par défaut (modifiable / désactivable dans l'UI).
# Chaque entrée : (code stable, nom, type, url)
# ---------------------------------------------------------------------------
SOURCES_PAR_DEFAUT: list[tuple[str, str, str, str]] = [
    (
        "aides_territoires",
        "Aides-territoires (État, régions, départements, Europe)",
        "aides_territoires",
        AIDES_TERRITOIRES_BASE + "/api/aids/?targeted_audiences=association&order_by=publication_date",
    ),
    (
        "oise_departement",
        "Département de l'Oise — aides et appels à projets",
        "html_liens",
        "https://www.oise.fr/",
    ),
    (
        "region_hdf_guide_aides",
        "Région Hauts-de-France — guide des aides",
        "html_liens",
        "https://guide-aides.hautsdefrance.fr/",
    ),
    (
        "europe_hdf",
        "L'Europe s'engage en Hauts-de-France (FEDER / FSE+)",
        "html_liens",
        "https://www.europe-en-hautsdefrance.eu/",
    ),
    (
        "caf_partenaires",
        "Caf.fr — espace partenaires (appels à projets)",
        "html_liens",
        "https://www.caf.fr/partenaires",
    ),
    (
        "fondation_de_france",
        "Fondation de France — appels à projets",
        "html_liens",
        "https://www.fondationdefrance.org/fr/appels-a-projets",
    ),
    (
        "associations_gouv",
        "Associations.gouv.fr (FDVA, vie associative)",
        "html_liens",
        "https://www.associations.gouv.fr/",
    ),
    (
        "acc_creil",
        "Agglomération Creil Sud Oise",
        "html_liens",
        "https://www.creilsudoise.fr/",
    ),
]


# ---------------------------------------------------------------------------
# Petits utilitaires texte
# ---------------------------------------------------------------------------

def _sans_accents(texte: str) -> str:
    """Minuscule + suppression des accents, pour des comparaisons robustes."""
    texte = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in texte if not unicodedata.combining(c)).lower()


def _nettoyer_html(fragment: str) -> str:
    """Réduit un fragment HTML en texte brut (descriptions de flux RSS...)."""
    sans_balises = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html_lib.unescape(sans_balises)).strip()


def calculer_score(texte: str) -> tuple[int, list[str]]:
    """Score de pertinence + liste des mots-clés du profil retrouvés."""
    corpus = _sans_accents(texte)
    score = 0
    trouves: list[str] = []
    for mot, poids in PROFIL_MOTS_CLES.items():
        if mot in corpus:
            score += poids
            trouves.append(mot)
    return score, trouves


def detecter_type(texte: str) -> str:
    corpus = _sans_accents(texte)
    for code, fragment in TYPES_DISPOSITIF:
        if fragment in corpus:
            return code
    return "autre"


def _hash_url(url: str) -> str:
    # Normalisation légère : schéma/hôte insensibles à la casse, pas de
    # slash final ni de fragment — la même page revue deux fois doit donner
    # la même empreinte.
    decoupe = urllib.parse.urlsplit((url or "").strip())
    normalisee = urllib.parse.urlunsplit(
        (decoupe.scheme.lower(), decoupe.netloc.lower(), decoupe.path.rstrip("/"), decoupe.query, "")
    )
    return hashlib.sha256(normalisee.encode("utf-8")).hexdigest()


def _parser_date(valeur) -> date | None:
    """Accepte ISO (2026-09-30), RFC 2822 (flux RSS) ou datetime — sinon None."""
    if not valeur:
        return None
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    brut = str(valeur).strip()
    try:
        return date.fromisoformat(brut[:10])
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(brut).date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def _telecharger(url: str, en_tetes: dict | None = None, timeout: int = DELAI_HTTP) -> bytes:
    entetes = {"User-Agent": USER_AGENT, "Accept-Language": "fr"}
    if en_tetes:
        entetes.update(en_tetes)
    requete = urllib.request.Request(url, headers=entetes)
    with urllib.request.urlopen(requete, timeout=timeout) as reponse:
        return reponse.read()


# ---------------------------------------------------------------------------
# Collecteur 1 : API Aides-territoires
# ---------------------------------------------------------------------------

def _collecter_aides_territoires(source: VeilleSource) -> list[dict]:
    """Interroge l'API Aides-territoires (agrégateur public national).

    Authentification en deux temps : la clé API (fiche source) est échangée
    contre un jeton Bearer sur /api/connexion/, puis on pagine sur l'URL de
    la source (pré-filtrée « associations », triée par date de publication).
    """
    if not (source.api_cle or "").strip():
        raise RuntimeError(
            "Clé API manquante. Créez un compte gratuit sur aides-territoires.beta.gouv.fr, "
            "récupérez votre jeton API (Mon compte → API) et collez-le dans cette source."
        )

    brut = _telecharger(
        AIDES_TERRITOIRES_BASE + "/api/connexion/",
        en_tetes={"X-AUTH-TOKEN": source.api_cle.strip(), "Accept": "application/json"},
    )
    jeton = (json.loads(brut.decode("utf-8", "replace")) or {}).get("token")
    if not jeton:
        raise RuntimeError("Connexion à l'API refusée : vérifiez la clé API.")

    entetes = {"Authorization": "Bearer " + jeton, "Accept": "application/json"}
    url = source.url
    items: list[dict] = []
    for _ in range(MAX_PAGES_AIDES_TERRITOIRES):
        if not url:
            break
        donnees = json.loads(_telecharger(url, en_tetes=entetes).decode("utf-8", "replace"))
        for aide in donnees.get("results") or []:
            lien = aide.get("url") or ""
            if lien.startswith("/"):
                lien = AIDES_TERRITOIRES_BASE + lien
            if not lien:
                continue
            financeurs = aide.get("financers") or []
            items.append(
                {
                    "titre": (aide.get("name") or "Sans titre").strip(),
                    "url": lien,
                    "financeur": ", ".join(str(f) for f in financeurs)[:200] or None,
                    "description": _nettoyer_html(aide.get("description") or "")[:2000] or None,
                    "date_cloture": _parser_date(aide.get("submission_deadline")),
                    "date_publication": _parser_date(
                        aide.get("date_published") or aide.get("date_created")
                    ),
                }
            )
        url = donnees.get("next")
    return items


# ---------------------------------------------------------------------------
# Collecteur 2 : flux RSS / Atom
# ---------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


def _collecter_rss(source: VeilleSource) -> list[dict]:
    racine = ET.fromstring(_telecharger(source.url))
    items: list[dict] = []

    # RSS 2.0 : <channel><item>...
    for item in racine.iter("item"):
        titre = (item.findtext("title") or "").strip()
        lien = (item.findtext("link") or "").strip()
        if not titre or not lien:
            continue
        items.append(
            {
                "titre": titre,
                "url": lien,
                "financeur": None,
                "description": _nettoyer_html(item.findtext("description") or "")[:2000] or None,
                "date_cloture": None,
                "date_publication": _parser_date(item.findtext("pubDate")),
            }
        )

    # Atom : <entry>...
    for entree in racine.iter(_ATOM + "entry"):
        titre = (entree.findtext(_ATOM + "title") or "").strip()
        lien = ""
        for balise_lien in entree.findall(_ATOM + "link"):
            if balise_lien.get("rel") in (None, "alternate"):
                lien = balise_lien.get("href") or ""
                break
        if not titre or not lien:
            continue
        items.append(
            {
                "titre": titre,
                "url": lien,
                "financeur": None,
                "description": _nettoyer_html(entree.findtext(_ATOM + "summary") or "")[:2000] or None,
                "date_cloture": None,
                "date_publication": _parser_date(
                    entree.findtext(_ATOM + "published") or entree.findtext(_ATOM + "updated")
                ),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Collecteur 3 : extraction de liens dans une page HTML
# ---------------------------------------------------------------------------

class _ExtracteurLiens(HTMLParser):
    """Récupère (href, texte du lien) pour chaque <a> de la page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.liens: list[tuple[str, str]] = []
        self._href: str | None = None
        self._texte: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._texte = []

    def handle_data(self, data):
        if self._href is not None:
            self._texte.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            texte = re.sub(r"\s+", " ", "".join(self._texte)).strip()
            if texte:
                self.liens.append((self._href, texte))
            self._href = None
            self._texte = []


def _collecter_html(source: VeilleSource) -> list[dict]:
    brut = _telecharger(source.url).decode("utf-8", "replace")
    extracteur = _ExtracteurLiens()
    extracteur.feed(brut)

    items: list[dict] = []
    deja_vus: set[str] = set()
    for href, texte in extracteur.liens:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        corpus = _sans_accents(texte)
        if len(texte) < 12 or not any(d in corpus for d in DECLENCHEURS_LIENS):
            continue
        absolu = urllib.parse.urljoin(source.url, href)
        empreinte = _hash_url(absolu)
        if empreinte in deja_vus:
            continue
        deja_vus.add(empreinte)
        items.append(
            {
                "titre": texte[:300],
                "url": absolu,
                "financeur": source.nom[:200],
                "description": None,
                "date_cloture": None,
                "date_publication": None,
            }
        )
    return items


COLLECTEURS = {
    "aides_territoires": _collecter_aides_territoires,
    "rss": _collecter_rss,
    "html_liens": _collecter_html,
}

TYPES_SOURCE_LABELS = {
    "aides_territoires": "API Aides-territoires",
    "rss": "Flux RSS / Atom",
    "html_liens": "Page web (extraction de liens)",
}


# ---------------------------------------------------------------------------
# Enregistrement / dédoublonnage
# ---------------------------------------------------------------------------

def enregistrer_items(source: VeilleSource, items: list[dict]) -> int:
    """Insère les nouveautés, met à jour l'existant. Retourne le nb de nouveaux.

    Le statut de suivi (« à étudier », « déposé »...) n'est JAMAIS écrasé :
    seule l'information factuelle (dates, description) est rafraîchie.
    """
    nouveaux = 0
    for item in items:
        empreinte = _hash_url(item["url"])
        existante = VeilleOpportunite.query.filter_by(url_hash=empreinte).first()
        texte_complet = (item.get("titre") or "") + " " + (item.get("description") or "")
        score, mots = calculer_score(texte_complet)
        if existante is None:
            db.session.add(
                VeilleOpportunite(
                    source_id=source.id,
                    titre=(item.get("titre") or "Sans titre")[:300],
                    financeur=(item.get("financeur") or None),
                    description=item.get("description"),
                    url=item["url"][:600],
                    url_hash=empreinte,
                    type_dispositif=detecter_type(texte_complet),
                    date_cloture=item.get("date_cloture"),
                    date_publication=item.get("date_publication"),
                    score=score,
                    mots_cles=", ".join(mots)[:300] or None,
                )
            )
            nouveaux += 1
        else:
            if item.get("date_cloture"):
                existante.date_cloture = item["date_cloture"]
            if item.get("description"):
                existante.description = item["description"]
                existante.score = score
                existante.mots_cles = ", ".join(mots)[:300] or None
    db.session.commit()
    return nouveaux


# ---------------------------------------------------------------------------
# Catalogue par défaut
# ---------------------------------------------------------------------------

def seed_sources_par_defaut() -> int:
    """Ajoute les sources du catalogue absentes de la base (idempotent).

    Une source par défaut supprimée ou modifiée par l'utilisateur n'est pas
    recréée tant que sa ligne existe ; supprimée, elle réapparaîtra au
    prochain appel — c'est le comportement voulu du bouton « réinitialiser ».
    """
    existants = {s.code_defaut for s in VeilleSource.query.filter(VeilleSource.code_defaut.isnot(None))}
    ajouts = 0
    for code, nom, type_source, url in SOURCES_PAR_DEFAUT:
        if code in existants:
            continue
        db.session.add(VeilleSource(nom=nom, type_source=type_source, url=url, code_defaut=code))
        ajouts += 1
    if ajouts:
        db.session.commit()
    return ajouts


# ---------------------------------------------------------------------------
# Rafraîchissement
# ---------------------------------------------------------------------------

def rafraichir_toutes_sources() -> dict:
    """Collecte toutes les sources actives. Retourne un résumé par source.

    Chaque source est isolée : une erreur (réseau, format, clé API...) est
    consignée sur sa fiche et n'empêche pas les suivantes.
    """
    seed_sources_par_defaut()
    resume = {"nouveaux": 0, "sources_ok": 0, "sources_erreur": 0}
    for source in VeilleSource.query.filter_by(actif=True).all():
        collecteur = COLLECTEURS.get(source.type_source)
        source.derniere_verification = utcnow()
        try:
            if collecteur is None:
                raise RuntimeError(f"Type de source inconnu : {source.type_source}")
            items = collecteur(source)
            nouveaux = enregistrer_items(source, items)
            source.dernier_statut = "ok"
            source.dernier_message = f"{len(items)} élément(s) lu(s), {nouveaux} nouveau(x)."
            source.dernieres_trouvailles = nouveaux
            resume["nouveaux"] += nouveaux
            resume["sources_ok"] += 1
        except Exception as exc:  # réseau, parsing, clé API... : tout est non fatal
            db.session.rollback()
            source.derniere_verification = utcnow()
            source.dernier_statut = "erreur"
            source.dernier_message = str(exc)[:500]
            source.dernieres_trouvailles = 0
            resume["sources_erreur"] += 1
        db.session.commit()
    return resume


def veille_est_due() -> bool:
    """Vrai si aucune source active n'a été vérifiée depuis INTERVALLE_JOURS."""
    if VeilleSource.query.count() == 0:
        return True  # premier passage : le seed + la collecte initiale
    derniere = (
        db.session.query(db.func.max(VeilleSource.derniere_verification))
        .filter(VeilleSource.actif.is_(True))
        .scalar()
    )
    if derniere is None:
        return True
    return derniere < utcnow() - timedelta(days=INTERVALLE_JOURS)


# Un seul rafraîchissement d'arrière-plan à la fois par processus.
_verrou_rafraichissement = threading.Lock()


def lancer_rafraichissement_arriere_plan(app) -> bool:
    """Rafraîchit en thread démon si la veille est due. Retourne True si lancé."""
    with app.app_context():
        try:
            if not veille_est_due():
                return False
        except Exception:
            # Table pas encore créée (tout premier démarrage) : on réessaiera.
            return False

    def _tache():
        if not _verrou_rafraichissement.acquire(blocking=False):
            return
        try:
            with app.app_context():
                try:
                    resume = rafraichir_toutes_sources()
                    app.logger.info("Veille financements rafraîchie : %s", resume)
                except Exception:
                    app.logger.exception("Veille financements : échec du rafraîchissement")
        finally:
            _verrou_rafraichissement.release()

    threading.Thread(target=_tache, name="veille-financements", daemon=True).start()
    return True
