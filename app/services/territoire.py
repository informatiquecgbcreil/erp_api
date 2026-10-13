"""Le territoire du centre : ville siège, QPV suivis, quartiers distingués.

Les bilans (SENACS, indicateurs, tableaux de bord) et les feuilles
d'émargement ont longtemps porté le territoire du centre de Creil en dur :
« Habitants de Creil », une ligne « Rouher », une ligne « Hauts de Creil »,
une carte centrée sur Creil. Un autre centre social obtenait donc des
tableaux faux — ses habitants comptés « hors Creil », aucun de ses QPV
nommé — sans aucun moyen de le corriger.

Ce module lit ces réglages dans les paramètres de la structure
(Administration > Identité), et les rend sous une forme prête à servir :

- ``ville_siege`` : la commune du centre (« Habitants de … ») ;
- ``qpv_suivis`` : 0, 1 ou plusieurs QPV, chacun sa ligne dans les
  bilans ; tout autre QPV tombe dans « Autre QPV » ;
- ``quartiers_distingues`` : des quartiers que le centre veut voir à part
  À L'INTÉRIEUR de leur QPV (le Rouher, aux Hauts de Creil) — une demande
  interne, pas une exigence de la grille SENACS ;
- ``centre_carte`` : où centrer la carte quand aucun habitant n'est
  localisé, trouvé une fois pour toutes à partir de la ville siège.

Rien n'est réglé sur une installation neuve : les bilans parlent alors de
« QPV » et de « ville siège » sans en nommer aucun, ce qui est juste, au
lieu de nommer Creil, ce qui serait faux.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.recherche_texte import sans_accent
from app.services.villes import memes_villes

#: Libellé des QPV qui ne sont pas suivis à part.
AUTRE_QPV = "Autre QPV"
#: Libellé quand aucun QPV n'est suivi à part : il n'y a pas d'« autre ».
QPV_SANS_DETAIL = "QPV"


def lire_liste(texte: str | None) -> tuple[str, ...]:
    """Une valeur par ligne (ou séparée par « ; »), sans doublon ni vide.

    L'ordre saisi est conservé : c'est celui des lignes du bilan.
    """
    vus: set[str] = set()
    sortie: list[str] = []
    for brut in re.split(r"[\n;]+", texte or ""):
        valeur = re.sub(r"\s{2,}", " ", brut.strip())
        cle = sans_accent(valeur)
        if valeur and cle not in vus:
            vus.add(cle)
            sortie.append(valeur)
    return tuple(sortie)


def ecrire_liste(valeurs) -> str | None:
    """L'inverse de :func:`lire_liste`, pour le stockage : None si vide."""
    return "\n".join(valeurs) or None


@dataclass(frozen=True)
class Territoire:
    ville_siege: str | None = None
    code_postal: str | None = None
    qpv_suivis: tuple[str, ...] = ()
    quartiers_distingues: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None

    @property
    def libelle_ville(self) -> str:
        """« Creil », ou « Ville siège » tant qu'aucune n'est réglée."""
        return self.ville_siege or "Ville siège"

    @property
    def libelle_autre_qpv(self) -> str:
        return AUTRE_QPV if self.qpv_suivis else QPV_SANS_DETAIL

    @property
    def centre_carte(self) -> dict | None:
        if self.latitude is None or self.longitude is None:
            return None
        return {"lat": self.latitude, "lon": self.longitude}

    def habite_ville_siege(self, ville: str | None) -> bool:
        """La commune est-elle la ville siège ? Insensible à l'écriture
        (« CREIL », « Creil (60) », « creil » sont une seule commune)."""
        return bool(self.ville_siege) and memes_villes(ville, self.ville_siege)

    def quartier_distingue(self, nom_quartier: str | None) -> str | None:
        """Le quartier distingué que ce nom désigne, s'il y en a un.

        Recherche par sous-chaîne, comme l'a toujours fait le bilan : un
        quartier nommé « Rouher (QPV Hauts de Creil) » reste le Rouher.
        """
        nom = sans_accent(nom_quartier) or ""
        for distingue in self.quartiers_distingues:
            if sans_accent(distingue) in nom:
                return distingue
        return None

    def qpv_suivi(self, nom_qpv: str | None) -> str | None:
        """Le QPV suivi dont ``nom_qpv`` est le nom exact (sans accent ni casse)."""
        cle = (sans_accent(nom_qpv) or "").strip()
        if not cle:
            return None
        for suivi in self.qpv_suivis:
            if sans_accent(suivi) == cle:
                return suivi
        return None

    def qpv_suivi_dans_le_nom(self, nom_quartier: str | None) -> str | None:
        """Repli pour un quartier sans QPV renseigné : un QPV suivi écrit
        dans son nom (« Hauts de Creil », « hauts-de-creil »)."""
        nom = _sans_tirets(nom_quartier)
        for suivi in self.qpv_suivis:
            if _sans_tirets(suivi) in nom:
                return suivi
        return None

    def nomme_un_qpv(self, nom_quartier: str | None) -> bool:
        """Le nom du quartier cite-t-il un QPV suivi ou un quartier distingué ?"""
        return bool(self.quartier_distingue(nom_quartier) or self.qpv_suivi_dans_le_nom(nom_quartier))


def _sans_tirets(valeur: str | None) -> str:
    return re.sub(r"\s+", " ", (sans_accent(valeur) or "").replace("-", " ")).strip()


def depuis_reglages(reglages) -> Territoire:
    if reglages is None:
        return Territoire()
    return Territoire(
        ville_siege=(reglages.territoire_ville or "").strip() or None,
        code_postal=(reglages.territoire_code_postal or "").strip() or None,
        qpv_suivis=lire_liste(reglages.territoire_qpv_suivis),
        quartiers_distingues=lire_liste(reglages.territoire_quartiers_distingues),
        latitude=reglages.territoire_latitude,
        longitude=reglages.territoire_longitude,
    )


_CLE_CACHE = "mcs_territoire"


def territoire() -> Territoire:
    """Le territoire réglé pour cette installation.

    Lu une fois par requête : ``Participant.is_qpv`` peut le demander pour
    chacun des milliers d'habitants d'un tableau de bord. Quiconque modifie
    les réglages appelle :func:`oublier`.
    """
    from flask import g, has_app_context

    from app.models import InstanceSettings

    if has_app_context():
        deja = g.get(_CLE_CACHE)
        if deja is not None:
            return deja
    lu = depuis_reglages(InstanceSettings.query.first())
    if has_app_context():
        setattr(g, _CLE_CACHE, lu)
    return lu


def oublier() -> None:
    """À appeler après avoir modifié les réglages du territoire."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop(_CLE_CACHE, None)


def qpv_deja_saisis() -> list[str]:
    """Les QPV déjà inscrits sur les fiches quartier : suggestions du formulaire."""
    from app.extensions import db
    from app.models import Quartier

    lignes = db.session.query(Quartier.qpv).filter(Quartier.qpv.isnot(None)).distinct().all()
    return sorted({(qpv or "").strip() for (qpv,) in lignes if (qpv or "").strip()}, key=sans_accent)


def localiser_ville_siege(ville: str | None, code_postal: str | None) -> dict | None:
    """Coordonnées de la commune via la Base Adresse Nationale, ou None.

    Ne lève jamais : sans Internet, la carte se centre sur les habitants
    déjà localisés, et l'on peut réessayer en enregistrant de nouveau.
    """
    from app.services.geocodage import GeocodageError, geocoder

    ville = (ville or "").strip()
    if not ville:
        return None
    try:
        return geocoder(code_postal, ville)
    except GeocodageError:
        return None
    except Exception:  # noqa: BLE001 — un réglage ne doit jamais échouer pour la carte
        return None


def enregistrer(reglages, *, ville: str | None, code_postal: str | None,
                qpv_suivis: str | None = None, quartiers_distingues: str | None = None,
                localiser: bool = True) -> str | None:
    """Applique le formulaire « Territoire » sur la ligne de réglages.

    Un champ absent du formulaire (None) laisse le réglage tel quel ; un
    champ vidé (« ») l'efface. Ne valide pas la session. Renvoie un message
    d'avertissement à afficher si la ville n'a pas pu être placée sur la
    carte, sinon None.
    """
    from app.services.villes import normaliser

    if qpv_suivis is not None:
        reglages.territoire_qpv_suivis = ecrire_liste(lire_liste(qpv_suivis))
    if quartiers_distingues is not None:
        reglages.territoire_quartiers_distingues = ecrire_liste(lire_liste(quartiers_distingues))
    if ville is None:
        return None
    if code_postal is None:
        code_postal = reglages.territoire_code_postal

    nouvelle_ville = normaliser(ville)
    nouveau_cp = re.sub(r"\s+", "", code_postal or "") or None
    ville_changee = (
        not memes_villes(nouvelle_ville, reglages.territoire_ville)
        or nouveau_cp != (reglages.territoire_code_postal or None)
    )

    reglages.territoire_ville = nouvelle_ville
    reglages.territoire_code_postal = nouveau_cp

    if not nouvelle_ville:
        reglages.territoire_latitude = None
        reglages.territoire_longitude = None
        return None
    if not localiser or not (ville_changee or reglages.territoire_latitude is None):
        return None

    position = localiser_ville_siege(nouvelle_ville, nouveau_cp)
    if position is None:
        if ville_changee:
            reglages.territoire_latitude = None
            reglages.territoire_longitude = None
        return (f"La ville « {nouvelle_ville} » est enregistrée, mais n'a pas pu être placée sur la carte "
                "(pas d'accès à Internet ?). Enregistrez de nouveau plus tard pour réessayer.")
    reglages.territoire_latitude = position["lat"]
    reglages.territoire_longitude = position["lon"]
    return None


# ---------------------------------------------------------------------------
# Reprise des installations existantes
# ---------------------------------------------------------------------------

#: Ce que l'application calculait en dur avant que le territoire se règle :
#: c'est le territoire du centre social Georges Brassens, à Creil. Une base
#: qui contient déjà des habitants de Creil le reçoit tel quel, pour que ses
#: bilans restent identiques au chiffre près après la mise à jour.
TERRITOIRE_HISTORIQUE = {
    "territoire_ville": "Creil",
    "territoire_code_postal": "60100",
    "territoire_qpv_suivis": "Hauts de Creil",
    "territoire_quartiers_distingues": "Rouher",
    "territoire_latitude": 49.2583,
    "territoire_longitude": 2.4750,
}


def reprendre_territoire_historique(bind) -> bool:
    """Pose le territoire historique sur une base qui en a l'usage.

    Appelée par la migration. Une base neuve (ou d'un autre centre) n'est
    pas touchée : rien n'y parle de Creil, et c'est à elle de régler son
    territoire. Ne remplace jamais un réglage déjà fait. Rejouable.
    Renvoie True si le territoire historique a été posé.
    """
    import sqlalchemy as sa

    inspecteur = sa.inspect(bind)
    tables = set(inspecteur.get_table_names())
    if "instance_settings" not in tables:
        return False

    usage = False
    for table in ("participant", "quartier"):
        if table in tables and bind.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE LOWER(TRIM(ville)) = 'creil' LIMIT 1")
        ).first():
            usage = True
            break
    if not usage:
        return False

    ligne = bind.execute(sa.text(
        "SELECT id, territoire_ville FROM instance_settings ORDER BY id LIMIT 1"
    )).first()
    if ligne is None:
        bind.execute(sa.text("INSERT INTO instance_settings (territoire_ville) VALUES (NULL)"))
        ligne = bind.execute(sa.text(
            "SELECT id, territoire_ville FROM instance_settings ORDER BY id LIMIT 1"
        )).first()
    if (ligne[1] or "").strip():
        return False

    affectations = ", ".join(f"{colonne} = :{colonne}" for colonne in TERRITOIRE_HISTORIQUE)
    bind.execute(
        sa.text(f"UPDATE instance_settings SET {affectations} WHERE id = :id"),
        {**TERRITOIRE_HISTORIQUE, "id": ligne[0]},
    )
    return True
