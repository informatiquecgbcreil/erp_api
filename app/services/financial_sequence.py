"""Réservation atomique d'un numéro dans la transaction du document.

Garde-fou contre la restauration d'une sauvegarde (audit, mineur caisse) :
les reçus fiscaux, factures et avoirs émis APRÈS cette sauvegarde existent
sur papier. Les renuméroter serait bien pire qu'un trou. Chaque numéro
attribué est donc aussi noté dans un registre hors base et hors des
sauvegardes ordinaires (dossier ``runtime`` des données) ; la numérotation
repart toujours au-delà du plus grand numéro jamais émis, base restaurée ou
non.

Politique : un numéro réservé puis abandonné (transaction annulée) reste
consommé et laisse un trou — c'est voulu. Le registre est donc écrit AVANT
la validation SQL, et durablement : si cette écriture échoue, le document
n'est pas émis (``RegistreErreur``), plutôt que d'émettre un numéro que le
registre ne protégerait plus.

Le registre est un objet JSON ``{"espace": plus grand numéro}`` (ex.
``"don:2026": 42``, ``"facture:2026": 17``, ``"avoir:2026": 3``) — même
format qu'avant, lisible par une version précédente en cas de retour
arrière. La mise à jour se fait sous verrou (``registre_externe``) et ne fait
jamais diminuer un maximum.

Registre endommagé, ou disparu alors que l'installation le tenait :
l'émission est SUSPENDUE (marque ``numeros-emis.bloque.json``, persistante
au redémarrage). La copie précédente et les compteurs de la base ne donnent
que des bornes basses — le dernier numéro réellement émis peut être plus
grand (consolidation après la PR #60 : 42 émis, copie 41, base 40 → le 42
était réémis). Les fichiers endommagés sont conservés ; une personne
habilitée rétablit le dernier numéro de chaque série (Contrôle → Registres),
jamais en dessous d'une borne connue. Première installation (aucune trace
d'un registre tenu, ni sur disque ni en base) : pas de blocage. Registre
inaccessible (droits) : l'émission est refusée, sans blocage durable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import case

from app.extensions import db
from app.models import FinancialSequence
from app.services import registre_externe as fichier_registre
from app.services.registre_externe import RegistreEndommage, RegistreErreur

NOM_FICHIER = "numeros-emis.json"

journal = logging.getLogger(__name__)


def _registre() -> Path | None:
    """Registre des numéros émis, dans le dossier des données de
    l'installation (APP_DATA_DIR, défini par l'installateur Windows), jamais
    dans instance/ ni uploads/ que les sauvegardes restaurent."""
    dossier = fichier_registre.dossier_runtime()
    return dossier / NOM_FICHIER if dossier else None


def _valider(donnees: dict, nom: str) -> dict[str, int]:
    propres: dict[str, int] = {}
    for espace, valeur in donnees.items():
        if not isinstance(espace, str) or isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
            raise RegistreEndommage("Le registre des numéros émis est endommagé.",
                                    f"entrée inattendue dans {nom}")
        propres[espace] = valeur
    return propres


def fusionner(*registres: dict) -> dict[str, int]:
    """Plus grand numéro par espace : jamais de retour en arrière."""
    resultat: dict[str, int] = {}
    for registre in registres:
        for espace, valeur in (registre or {}).items():
            if int(valeur) > resultat.get(espace, 0):
                resultat[espace] = int(valeur)
    return resultat


def _compteurs_base() -> dict[str, int]:
    try:
        return {ligne.namespace: int(ligne.value or 0) for ligne in FinancialSequence.query.all()}
    except Exception:  # noqa: BLE001 — table absente (base pas encore migrée)
        return {}


class RegistreBloque(RegistreErreur):
    """Dernier numéro émis impossible à établir avec certitude : émission
    suspendue jusqu'au rétablissement par une personne habilitée."""


NOM_BLOCAGE = "numeros-emis.bloque.json"
NOM_TEMOIN = "numeros-emis.tenu"
TACHE_TENU = "registre_numeros_tenu"
MESSAGE_BLOQUE = (
    "Émission des reçus, factures et avoirs suspendue : le registre des numéros déjà émis est "
    "endommagé ou a disparu, le dernier numéro ne peut pas être établi avec certitude. Un "
    "administrateur doit relever le dernier numéro de chaque série sur les documents papier et le "
    "rétablir dans Contrôle → Registres.")


def _chemin_blocage(chemin: Path) -> Path:
    return chemin.with_name(NOM_BLOCAGE)


def _registre_deja_tenu(chemin: Path) -> bool:
    """Une installation existante a-t-elle déjà tenu ce registre ? (témoin
    sur disque, copie précédente, ou trace en base — qui survit à la perte
    du dossier runtime). Sinon : première installation, ou mise à jour
    d'une version qui ne tenait pas de registre."""
    if chemin.with_name(NOM_TEMOIN).exists() or chemin.with_name(chemin.name + ".prec").exists():
        return True
    try:
        from app.models import TachePlanifiee
        return TachePlanifiee.query.filter_by(nom=TACHE_TENU).first() is not None
    except Exception:  # noqa: BLE001 — hors contexte applicatif / base pas encore migrée
        return False


def _noter_tenu(chemin: Path) -> None:
    temoin = chemin.with_name(NOM_TEMOIN)
    if not temoin.exists():
        temoin.write_text("registre des numéros émis tenu par cette installation\n", encoding="utf-8")
    try:
        from flask import has_app_context
        if not has_app_context():
            return
        from sqlalchemy.exc import IntegrityError
        from app.models import TachePlanifiee
        if TachePlanifiee.query.filter_by(nom=TACHE_TENU).first() is not None:
            return
        # Point de sauvegarde : deux premières émissions simultanées ne font
        # jamais échouer le document (la seconde trouve la ligne existante).
        try:
            with db.session.begin_nested():
                db.session.add(TachePlanifiee(nom=TACHE_TENU))
        except IntegrityError:
            pass
    except Exception:  # noqa: BLE001 — table absente : le témoin sur disque suffit
        pass


def _lire_blocage(chemin: Path) -> dict | None:
    try:
        return fichier_registre.lire(_chemin_blocage(chemin))
    except RegistreEndommage:
        return {"raison": "marque de blocage illisible", "bornes": {}}


def _bornes_connues(chemin: Path) -> dict[str, int]:
    """Minima certains : copie précédente et compteurs en base. Le vrai
    dernier numéro peut être PLUS GRAND (écritures perdues)."""
    try:
        copie = _valider(fichier_registre.lire(chemin.with_name(chemin.name + ".prec")) or {}, "copie précédente")
    except RegistreEndommage:
        copie = {}
    return fusionner(copie, _compteurs_base())


def _bloquer(chemin: Path, raison: str, detail: str = "", mis_de_cote: Path | None = None) -> None:
    from app.utils.dates import utcnow
    existant = _lire_blocage(chemin) or {}
    bornes = fusionner(existant.get("bornes") or {}, _bornes_connues(chemin))
    fichiers = list(existant.get("fichiers_mis_de_cote") or [])
    if mis_de_cote is not None:
        fichiers.append(mis_de_cote.name)
    fichier_registre.ecrire(_chemin_blocage(chemin), {
        "raison": existant.get("raison") or raison, "detail": existant.get("detail") or detail,
        "depuis": existant.get("depuis") or utcnow().isoformat(timespec="seconds"),
        "bornes": bornes, "fichiers_mis_de_cote": fichiers})
    journal.error("Registre des numéros émis %s (%s) : émission suspendue jusqu'au rétablissement.", raison, detail)


def _lire_ou_bloquer(chemin: Path) -> dict[str, int]:
    """Lecture sous verrou. Registre endommagé, ou disparu alors qu'il était
    tenu : les fichiers sont conservés, l'émission est suspendue (marque sur
    disque, persistante) — aucune reconstruction n'est prise pour une preuve."""
    blocage_en_cours = _lire_blocage(chemin)
    if blocage_en_cours is not None:
        raise RegistreBloque(MESSAGE_BLOQUE, f"registre {blocage_en_cours.get('raison')}")
    try:
        donnees = fichier_registre.lire(chemin)
    except RegistreEndommage as exc:
        # (Inaccessible — droits, disque — n'est PAS rattrapé : erreur passagère.)
        mis_de_cote = fichier_registre.mettre_de_cote(chemin)
        _bloquer(chemin, "endommagé", exc.detail, mis_de_cote)
        raise RegistreBloque(MESSAGE_BLOQUE, "registre endommagé") from exc
    if donnees is None:
        if _registre_deja_tenu(chemin):
            _bloquer(chemin, "disparu", "fichier absent alors que le registre était tenu")
            raise RegistreBloque(MESSAGE_BLOQUE, "registre disparu")
        return {}
    try:
        return _valider(donnees, chemin.name)
    except RegistreEndommage as exc:
        mis_de_cote = fichier_registre.mettre_de_cote(chemin)
        _bloquer(chemin, "endommagé", exc.detail, mis_de_cote)
        raise RegistreBloque(MESSAGE_BLOQUE, "registre endommagé") from exc


def blocage() -> dict | None:
    """État du blocage (raison, depuis, bornes connues, fichiers mis de côté).
    Contrôle le registre au passage : un registre abîmé depuis la dernière
    lecture est détecté ici aussi."""
    chemin = _registre()
    if chemin is None:
        return None
    try:
        lire_registre()
    except RegistreErreur:
        pass
    with fichier_registre.verrou(chemin):
        return _lire_blocage(chemin)


def maxima_connus() -> tuple[dict[str, int], bool]:
    """Maxima pour une copie des registres : ceux du registre, ou pendant un
    blocage les bornes connues (incomplètes, drapeau True)."""
    chemin = _registre()
    if chemin is None:
        return {}, False
    with fichier_registre.verrou(chemin):
        bloque = _lire_blocage(chemin)
        if bloque is not None:
            return dict(bloque.get("bornes") or {}), True
    try:
        return lire_registre(), False
    except RegistreBloque:
        return (blocage() or {}).get("bornes") or {}, True


def lire_registre() -> dict[str, int]:
    """Tous les maxima connus (sous verrou). Registre non tenu : {}.
    Lève RegistreBloque si le dernier numéro ne peut pas être établi."""
    chemin = _registre()
    if chemin is None:
        return {}
    with fichier_registre.verrou(chemin):
        fichier_registre.nettoyer_temporaires(chemin)
        return _lire_ou_bloquer(chemin)


def noter_maxima(maxima: dict[str, int], *, pendant_blocage: bool = False) -> dict[str, int]:
    """Fusionne des maxima dans le registre (max par espace), sous verrou.
    Sert à chaque émission, à la restauration d'un lot et au transfert.

    Pendant un blocage, l'émission (défaut) est refusée ; une fusion
    ``pendant_blocage=True`` (restauration, import) relève seulement les
    bornes connues, sans lever le blocage."""
    chemin = _registre()
    if chemin is None:
        return {}
    propres = _valider(dict(maxima), "maxima fournis")
    with fichier_registre.verrou(chemin):
        fichier_registre.nettoyer_temporaires(chemin)
        try:
            actuel = _lire_ou_bloquer(chemin)
        except RegistreBloque:
            if not pendant_blocage:
                raise
            bloque = _lire_blocage(chemin) or {}
            bloque["bornes"] = fusionner(bloque.get("bornes") or {}, propres)
            fichier_registre.ecrire(_chemin_blocage(chemin), bloque)
            return bloque["bornes"]
        nouveau = fusionner(actuel, propres)
        if nouveau != actuel or not chemin.exists():
            fichier_registre.ecrire(chemin, nouveau)
            _noter_tenu(chemin)
        return nouveau


def retablir(declares: dict[str, int]) -> dict[str, int]:
    """Lève le blocage avec les derniers numéros déclarés par une personne
    habilitée (relevés sur les documents papier). Chaque série connue doit
    être déclarée, jamais en dessous d'une borne connue. Sous verrou.
    Lève ValueError (message pour l'écran) sinon."""
    chemin = _registre()
    if chemin is None:
        raise ValueError("Registre non tenu sur cette installation.")
    propres = _valider(dict(declares), "déclaration")
    with fichier_registre.verrou(chemin):
        bloque = _lire_blocage(chemin)
        if bloque is None:
            raise ValueError("Aucun blocage en cours.")
        bornes = fusionner(bloque.get("bornes") or {}, _bornes_connues(chemin))
        trop_bas = [f"{espace} : au moins {bornes[espace]}" for espace in sorted(bornes)
                    if espace in propres and propres[espace] < bornes[espace]]
        if trop_bas:
            raise ValueError("Un numéro plus élevé est déjà connu (" + " ; ".join(trop_bas) + ").")
        manquantes = sorted(set(bornes) - set(propres))
        if manquantes:
            raise ValueError("Déclarez le dernier numéro émis pour chaque série connue : "
                             + ", ".join(manquantes) + ".")
        fichier_registre.ecrire(chemin, fusionner(bornes, propres))
        _noter_tenu(chemin)
        _chemin_blocage(chemin).unlink()
        return fusionner(bornes, propres)


def plus_haut_emis(namespace: str) -> int:
    return int(lire_registre().get(namespace, 0))


def next_number(namespace, existing_numbers):
    maximum = plus_haut_emis(namespace)
    for value in existing_numbers:
        try:
            maximum = max(maximum, int(str(value).rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = FinancialSequence.__table__
    statement = insert(table).values(namespace=namespace, value=maximum + 1)
    statement = statement.on_conflict_do_update(index_elements=[table.c.namespace], set_={
        "value": case((table.c.value < maximum, maximum + 1), else_=table.c.value + 1)
    }).returning(table.c.value)
    numero = db.session.execute(statement).scalar_one()
    # Avant la validation SQL : un numéro abandonné reste consommé, mais un
    # numéro émis n'échappe jamais au registre. Échec = document non émis.
    noter_maxima({namespace: numero})
    return numero


__all__ = ["RegistreBloque", "RegistreErreur", "blocage", "fusionner", "lire_registre", "maxima_connus",
           "next_number", "noter_maxima", "plus_haut_emis", "retablir"]
