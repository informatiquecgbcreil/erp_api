"""Espace salarié : soldes, liens automatiques, signatures, compteurs.

Intégration de l'ancienne application « Récup » (heures supplémentaires,
récupérations, frais kilométriques, profils salariaux, coffre-fort). Ce
module porte la logique partagée par les pages et par l'accueil :

- le lien compte ↔ fiche salarié, établi tout seul quand le nom ne laisse
  aucun doute (sinon la direction le règle depuis la page RH) ;
- le solde d'heures : crédits (heures sup, ajustements négatifs compris)
  moins récupérations ACCEPTÉES ;
- les événements de l'agenda d'un jour (séances, réunions), proposés pour
  relier une déclaration à ce qui a réellement été fait ;
- les compteurs « à traiter » de l'équipe, calculés à partir des statuts
  (jamais de liste de tâches à tenir à jour en parallèle).

Durées en minutes et montants en centimes : des entiers, toujours.
"""
from __future__ import annotations

import hashlib
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app
from flask_login import current_user

from app.extensions import db
from app.utils.dates import utcnow

# ---------------------------------------------------------------------------
# Statuts du circuit des récupérations
# ---------------------------------------------------------------------------

STATUTS_RECUP = {
    "brouillon": {"label": "Brouillon", "icon": "📝", "tone": "muted"},
    "soumise": {"label": "Envoyée, à transmettre", "icon": "🕓", "tone": "warning"},
    "transmise": {"label": "Transmise à la direction", "icon": "🕓", "tone": "warning"},
    "acceptee": {"label": "Acceptée", "icon": "✅", "tone": "success"},
    "refusee": {"label": "Refusée", "icon": "❌", "tone": "danger"},
    "annulee": {"label": "Annulée", "icon": "⚫", "tone": "muted"},
}
EN_COURS = ("brouillon", "soumise", "transmise")
EN_ATTENTE = ("soumise", "transmise")
DECIDEES = ("acceptee", "refusee")


def statut_recup(demande) -> dict:
    """Libellé affiché, en précisant qui a refusé."""
    meta = dict(STATUTS_RECUP.get(demande.statut, {"label": demande.statut, "icon": "ℹ️", "tone": "muted"}))
    if demande.statut == "refusee":
        meta["label"] = "Refusée par l'assistant·e" if getattr(demande, "refusee_par_relais", False) \
            else "Refusée par la direction"
    elif demande.statut == "acceptee":
        meta["label"] = "Acceptée par la direction"
    return meta


def est_l_interesse(demande, user) -> bool:
    """La personne connectée est-elle celle que concerne la demande ?"""
    uid = getattr(demande.salarie, "user_id", None)
    return bool(uid and uid == getattr(user, "id", None))


def prochaine_etape(demande) -> str:
    s = demande.statut
    if s == "brouillon":
        return "À signer et envoyer"
    if s == "soumise":
        return "Assistant·e : transmettre à la direction ou refuser"
    if s == "transmise":
        return "Direction : accepter ou refuser"
    if s in DECIDEES and not demande.notifiee_le:
        return "Assistant·e : prendre connaissance de la décision"
    return "Terminé"


def minutes_effectives(origine) -> int:
    """Valeur actuelle d'une déclaration d'heures sup, corrections comprises."""
    from app.models import HeureSupplementaire
    corrections = db.session.query(db.func.coalesce(db.func.sum(HeureSupplementaire.minutes), 0)).filter(
        HeureSupplementaire.origine_id == origine.id).scalar()
    return int(origine.minutes) + int(corrections or 0)


def corrections_de(origines) -> dict[int, int]:
    """{id de déclaration: valeur actuelle} pour une liste de déclarations (une requête)."""
    from app.models import HeureSupplementaire
    ids = [h.id for h in origines if not h.est_ajustement]
    if not ids:
        return {}
    deltas = dict(db.session.query(HeureSupplementaire.origine_id, db.func.sum(HeureSupplementaire.minutes))
                  .filter(HeureSupplementaire.origine_id.in_(ids)).group_by(HeureSupplementaire.origine_id).all())
    return {h.id: int(h.minutes) + int(deltas.get(h.id) or 0) for h in origines if not h.est_ajustement}


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

def format_minutes(minutes) -> str:
    """180 → « 3h00 », -95 → « -1h35 »."""
    m = int(minutes or 0)
    signe = "-" if m < 0 else ""
    m = abs(m)
    return f"{signe}{m // 60}h{m % 60:02d}"


def format_euros(centimes) -> str:
    """123456 → « 1 234,56 € » (espace insécable fine pour les milliers)."""
    c = int(centimes or 0)
    signe = "-" if c < 0 else ""
    c = abs(c)
    euros, cts = divmod(c, 100)
    milliers = f"{euros:,}".replace(",", " ")
    return f"{signe}{milliers},{cts:02d} €"


def parse_heures(valeur: str | None) -> int:
    """Durée saisie → minutes. Accepte « 1.5 », « 1,5 », « 1:30 », « 1h30 », « 45min »."""
    brut = (valeur or "").strip().lower().replace(" ", "")
    if not brut:
        raise ValueError("Indiquez une durée, par exemple 1h30 ou 1,5.")
    try:
        if brut.endswith("min"):
            return int(brut[:-3])
        if "h" in brut or ":" in brut:
            sep = "h" if "h" in brut else ":"
            heures, _, minutes = brut.partition(sep)
            h = int(heures or 0)
            m = int(minutes or 0)
            if not 0 <= m < 60:
                raise ValueError
            return h * 60 + m
        from decimal import Decimal, ROUND_HALF_UP
        return int((Decimal(brut.replace(",", ".")) * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        raise ValueError("Durée illisible. Exemples : 1h30, 1:30 ou 1,5.") from None


def parse_euros(valeur: str | None, *, obligatoire: bool = False) -> int:
    """« 12,50 » → 1250 centimes (sans passer par un flottant)."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    brut = (valeur or "").strip().replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
    if not brut:
        if obligatoire:
            raise ValueError("Montant manquant.")
        return 0
    try:
        return int((Decimal(brut) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError(f"Montant illisible : « {valeur} ».") from None


def parse_date(valeur: str | None) -> date | None:
    brut = (valeur or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(brut, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Lien compte ↔ fiche salarié
# ---------------------------------------------------------------------------

def _normaliser(texte: str | None) -> str:
    brut = unicodedata.normalize("NFKD", texte or "")
    sans_accents = "".join(c for c in brut if not unicodedata.combining(c))
    return " ".join(sans_accents.lower().replace("-", " ").split())


def _variantes(salarie) -> set[str]:
    prenom, nom = salarie.prenom or "", salarie.nom or ""
    return {v for v in (_normaliser(f"{prenom} {nom}"), _normaliser(f"{nom} {prenom}")) if v}


def _correspondances() -> tuple[dict, dict]:
    """Index nom normalisé → fiches libres, et → comptes actifs sans fiche."""
    from app.models import Salarie, User

    fiches: dict[str, list] = {}
    for s in Salarie.query.filter(Salarie.user_id.is_(None)).all():
        for v in _variantes(s):
            fiches.setdefault(v, []).append(s)
    lies = {uid for (uid,) in db.session.query(Salarie.user_id).filter(Salarie.user_id.isnot(None)).all()}
    comptes: dict[str, list] = {}
    for u in User.query.filter(User.actif.is_(True)).all():
        if u.id in lies:
            continue
        cle = _normaliser(u.nom)
        if cle:
            comptes.setdefault(cle, []).append(u)
    return fiches, comptes


def _journaliser_lien(salarie, user, mode: str) -> None:
    from app.services.audit import journaliser
    journaliser("rh.compte_lie", cible=f"salarie#{salarie.id}",
                details={"user_id": user.id, "mode": mode})


def salarie_de(user, *, lier: bool = True):
    """Fiche salarié du compte ; la relie toute seule si le nom est sans ambiguïté.

    Sans ambiguïté = une seule fiche libre porte ce nom (prénom nom ou nom
    prénom, accents et majuscules ignorés) ET un seul compte actif le porte.
    """
    from app.models import Salarie

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    fiche = Salarie.query.filter_by(user_id=user.id).first()
    if fiche is not None or not lier:
        return fiche
    cle = _normaliser(getattr(user, "nom", ""))
    if not cle:
        return None
    fiches, comptes = _correspondances()
    candidates = fiches.get(cle, [])
    if len(candidates) == 1 and len(comptes.get(cle, [])) == 1:
        fiche = candidates[0]
        fiche.user_id = user.id
        db.session.commit()
        _journaliser_lien(fiche, user, "automatique")
        return fiche
    return None


def lier_automatiquement() -> dict[str, Any]:
    """Relie toutes les paires fiche/compte sans ambiguïté. Renvoie le bilan."""
    fiches, comptes = _correspondances()
    liees, ambigues = [], []
    deja: set[int] = set()
    for cle, liste_fiches in fiches.items():
        liste_comptes = comptes.get(cle, [])
        if not liste_comptes:
            continue
        uniques_fiches = {f.id: f for f in liste_fiches}
        if len(uniques_fiches) == 1 and len(liste_comptes) == 1:
            fiche = next(iter(uniques_fiches.values()))
            if fiche.id in deja:
                continue
            fiche.user_id = liste_comptes[0].id
            deja.add(fiche.id)
            liees.append((fiche, liste_comptes[0]))
        else:
            ambigues.append(cle)
    if liees:
        db.session.commit()
        for fiche, user in liees:
            _journaliser_lien(fiche, user, "automatique")
    return {"liees": liees, "ambigues": sorted(set(ambigues))}


def comptes_disponibles():
    """Comptes actifs non encore reliés à une fiche (pour la liste de la page RH)."""
    from app.models import Salarie, User

    lies = {uid for (uid,) in db.session.query(Salarie.user_id).filter(Salarie.user_id.isnot(None)).all()}
    return [u for u in User.query.filter(User.actif.is_(True)).order_by(User.nom.asc()).all() if u.id not in lies]


# ---------------------------------------------------------------------------
# Soldes et résumés
# ---------------------------------------------------------------------------

def _somme(colonne, *filtres) -> int:
    return int(db.session.query(db.func.coalesce(db.func.sum(colonne), 0)).filter(*filtres).scalar() or 0)


def solde_minutes(salarie_id: int) -> int:
    from app.models import DemandeRecuperation, HeureSupplementaire

    credit = _somme(HeureSupplementaire.minutes, HeureSupplementaire.salarie_id == salarie_id)
    debit = _somme(DemandeRecuperation.minutes, DemandeRecuperation.salarie_id == salarie_id,
                   DemandeRecuperation.statut == "acceptee")
    return credit - debit


def soldes_par_salarie() -> dict[int, int]:
    from app.models import DemandeRecuperation, HeureSupplementaire

    credits = dict(db.session.query(HeureSupplementaire.salarie_id, db.func.sum(HeureSupplementaire.minutes))
                   .group_by(HeureSupplementaire.salarie_id).all())
    debits = dict(db.session.query(DemandeRecuperation.salarie_id, db.func.sum(DemandeRecuperation.minutes))
                  .filter(DemandeRecuperation.statut == "acceptee")
                  .group_by(DemandeRecuperation.salarie_id).all())
    return {sid: int(credits.get(sid) or 0) - int(debits.get(sid) or 0) for sid in set(credits) | set(debits)}


def resume_recup(salarie_id: int, annee: int | None = None) -> dict[str, Any]:
    from app.models import DemandeRecuperation, HeureSupplementaire

    annee = annee or date.today().year
    debut, fin = date(annee, 1, 1), date(annee, 12, 31)
    return {
        "solde": solde_minutes(salarie_id),
        "credit_annee": _somme(HeureSupplementaire.minutes, HeureSupplementaire.salarie_id == salarie_id,
                               HeureSupplementaire.date_travail.between(debut, fin)),
        "pris_annee": _somme(DemandeRecuperation.minutes, DemandeRecuperation.salarie_id == salarie_id,
                             DemandeRecuperation.statut == "acceptee",
                             DemandeRecuperation.date_recuperation.between(debut, fin)),
        "en_attente": _somme(DemandeRecuperation.minutes, DemandeRecuperation.salarie_id == salarie_id,
                             DemandeRecuperation.statut.in_(EN_ATTENTE)),
        "nb_en_cours": DemandeRecuperation.query.filter(
            DemandeRecuperation.salarie_id == salarie_id, DemandeRecuperation.statut.in_(EN_COURS)).count(),
    }


def resume_frais_km(salarie_id: int, annee: int | None = None) -> dict[str, int]:
    from app.models import FraisKilometrique

    annee = annee or date.today().year
    q = FraisKilometrique.query.filter(FraisKilometrique.salarie_id == salarie_id, FraisKilometrique.annee == annee)
    return {
        "nb": q.count(),
        "km": _somme(FraisKilometrique.distance_km, FraisKilometrique.salarie_id == salarie_id,
                     FraisKilometrique.annee == annee),
        "montant": _somme(FraisKilometrique.montant_centimes, FraisKilometrique.salarie_id == salarie_id,
                          FraisKilometrique.annee == annee),
    }


def frais_km_par_salarie(annee: int) -> dict[int, dict[str, int]]:
    from app.models import FraisKilometrique

    lignes = (db.session.query(FraisKilometrique.salarie_id, db.func.count(FraisKilometrique.id),
                               db.func.sum(FraisKilometrique.distance_km),
                               db.func.sum(FraisKilometrique.montant_centimes))
              .filter(FraisKilometrique.annee == annee)
              .group_by(FraisKilometrique.salarie_id).all())
    return {sid: {"nb": int(n or 0), "km": int(km or 0), "montant": int(m or 0)} for sid, n, km, m in lignes}


# ---------------------------------------------------------------------------
# Liens avec l'agenda : ce qui a été fait ce jour-là
# ---------------------------------------------------------------------------

MAX_SUGGESTIONS = 12


def evenements_du_jour(user, jour: date) -> list[dict[str, Any]]:
    """Séances et créneaux de l'agenda de la personne pour ce jour.

    Même périmètre que son agenda (secteur, créneaux personnels) : on ne
    propose que ce qu'elle voit déjà. Une erreur d'agenda ne bloque jamais
    une déclaration : la liste est simplement vide.
    """
    if jour is None:
        return []
    try:
        from app.services.calendrier import evenements_pour_periode
        evenements = evenements_pour_periode(user, du=jour, au=jour)
    except Exception:
        current_app.logger.warning("Agenda indisponible pour les suggestions RH", exc_info=True)
        return []
    resultat = []
    for e in evenements:
        if e.get("annulee"):
            continue
        horaire = " – ".join(h for h in (e.get("heure_debut"), e.get("heure_fin")) if h)
        resultat.append({
            "cle": f"{e['type']}:{e['id']}",
            "type": e["type"],
            "id": e["id"],
            "titre": e.get("atelier") or e.get("titre") or "",
            "horaire": horaire,
        })
    return resultat[:MAX_SUGGESTIONS]


def lien_agenda_valide(user, jour: date, cle: str | None) -> tuple[int | None, int | None]:
    """« seance:12 » / « creneau:4 » → (session_id, creneau_id), si présent ce jour-là."""
    cle = (cle or "").strip()
    if not cle:
        return None, None
    for e in evenements_du_jour(user, jour):
        if e["cle"] == cle:
            return (e["id"], None) if e["type"] == "seance" else (None, e["id"])
    raise ValueError("L'activité choisie n'est pas dans votre agenda de ce jour-là.")


def libelle_lien(objet) -> str | None:
    """« Atelier numérique · 14:00 – 16:00 » pour une heure sup ou un trajet relié."""
    session = getattr(objet, "session", None)
    if session is not None:
        atelier = getattr(session, "atelier", None)
        nom = atelier.nom if atelier else f"Séance #{session.id}"
        debut = session.rdv_debut or session.heure_debut
        fin = session.rdv_fin or session.heure_fin
        horaire = " – ".join(h for h in (debut, fin) if h)
        return f"{nom}{' · ' + horaire if horaire else ''}"
    creneau = getattr(objet, "creneau", None)
    if creneau is not None:
        return f"{creneau.type_label} — {creneau.titre}"
    return None


# ---------------------------------------------------------------------------
# Signatures et fichiers
# ---------------------------------------------------------------------------

def dossier_rh(*parties: str) -> Path:
    """Sous-dossier des pièces jointes RH : sauvegardé avec les autres, jamais public."""
    from app.services.storage import ensure_upload_subdir
    return Path(ensure_upload_subdir("rh", *parties))


def chemin_relatif(chemin_absolu: str | Path) -> str:
    from app.services.storage import get_upload_root
    return Path(chemin_absolu).resolve().relative_to(Path(get_upload_root()).resolve()).as_posix()


def enregistrer_signature(donnees: str | None, *, contexte: str, objet_type: str, objet_id: int,
                          vide_autorise: bool = False):
    """Valide et range une signature dessinée, puis la trace (SignatureRh).

    ``vide_autorise`` : le cadre peut rester vierge si la personne a coché
    « je signe » (certains appareils ne transmettent pas le dessin) — la
    trace est alors créée sans image, avec le nom et l'heure.
    """
    from app.models import SignatureRh
    from app.services.signatures import save_signature

    chemin = save_signature(donnees, dossier_rh("signatures"), f"{objet_type}_{objet_id}_{contexte}",
                            vide_autorise=vide_autorise) if donnees else None
    if chemin is None and not vide_autorise:
        raise ValueError("Signez dans le cadre avant de valider.")
    empreinte = hashlib.sha256(Path(chemin).read_bytes()).hexdigest() if chemin else None
    signature = SignatureRh(
        signataire_user_id=getattr(current_user, "id", None),
        signataire_nom=(getattr(current_user, "nom", "") or "")[:160],
        contexte=contexte,
        objet_type=objet_type,
        objet_id=objet_id,
        chemin=chemin_relatif(chemin) if chemin else None,
        sha256=empreinte,
    )
    db.session.add(signature)
    db.session.flush()
    return signature


# ---------------------------------------------------------------------------
# Compteurs « à traiter » et accueil
# ---------------------------------------------------------------------------

def _peut(user, code: str) -> bool:
    has_perm = getattr(user, "has_perm", None)
    return bool(callable(has_perm) and has_perm(code))


def a_traiter_equipe(user) -> dict[str, Any]:
    """Ce qui attend l'équipe de direction, selon les droits de la personne."""
    from app.models import DemandeRecuperation, FraisKilometrique, HeureSupplementaire, Salarie

    res: dict[str, Any] = {}
    if _peut(user, "recup:transmettre") or _peut(user, "recup:decider"):
        res["a_transmettre"] = DemandeRecuperation.query.filter_by(statut="soumise").count()
        # Décisions de la direction dont l'assistant·e n'a pas encore pris connaissance.
        res["a_notifier"] = DemandeRecuperation.query.filter(
            DemandeRecuperation.statut.in_(DECIDEES), DemandeRecuperation.notifiee_le.is_(None)).count()
        # Heures sup déclarées : visibles en direct par l'assistant·e et la direction.
        res["heures_30j"] = _somme(HeureSupplementaire.minutes,
                                   HeureSupplementaire.created_at >= utcnow() - timedelta(days=30),
                                   HeureSupplementaire.est_ajustement.is_(False))
    if _peut(user, "recup:decider"):
        res["a_decider"] = DemandeRecuperation.query.filter_by(statut="transmise").count()
        debut_annee = datetime(date.today().year, 1, 1)
        res["decisions_par_interesse"] = DemandeRecuperation.query.filter(
            DemandeRecuperation.decision_par_interesse.is_(True),
            DemandeRecuperation.decidee_le >= debut_annee).count()
    if _peut(user, "frais_km:suivi"):
        debut_mois = date.today().replace(day=1)
        res["km_mois_nb"] = FraisKilometrique.query.filter(FraisKilometrique.date_trajet >= debut_mois).count()
        res["km_mois_montant"] = _somme(FraisKilometrique.montant_centimes,
                                        FraisKilometrique.date_trajet >= debut_mois)
        res["km_sans_depense"] = FraisKilometrique.query.filter(FraisKilometrique.depense_id.is_(None)).count()
    if _peut(user, "rh:edit"):
        res["fiches_sans_compte"] = Salarie.query.filter(
            Salarie.user_id.is_(None),
            db.or_(Salarie.date_sortie.is_(None), Salarie.date_sortie >= date.today())).count()
    return res


def build_espace_salarie(user) -> dict[str, Any]:
    """Bloc « Salarié » de l'accueil : mes chiffres, mes raccourcis, l'équipe."""
    from app.models import DemandeRecuperation, DocumentRh, DocumentRhAcces, ProfilSalarial

    ctx: dict[str, Any] = {"espace": _peut(user, "salarie:espace"), "salarie": None}
    if ctx["espace"]:
        salarie = salarie_de(user)
        ctx["salarie"] = salarie
        if salarie is not None:
            annee = date.today().year
            ctx["recup"] = resume_recup(salarie.id, annee)
            ctx["km"] = resume_frais_km(salarie.id, annee)
            ctx["profil"] = ProfilSalarial.query.filter_by(salarie_id=salarie.id).first() is not None
            ctx["dernieres_decisions"] = (
                DemandeRecuperation.query
                .filter(DemandeRecuperation.salarie_id == salarie.id,
                        DemandeRecuperation.statut.in_(DECIDEES))
                .order_by(DemandeRecuperation.id.desc())
                .limit(3).all()
            )
        acces = db.session.query(DocumentRhAcces.document_id).filter(DocumentRhAcces.user_id == user.id)
        ctx["documents_recents"] = (
            DocumentRh.query
            .filter(db.or_(DocumentRh.depose_par_user_id == user.id, DocumentRh.id.in_(acces)))
            .order_by(DocumentRh.created_at.desc()).limit(4).all()
        )
    ctx["equipe"] = a_traiter_equipe(user)
    ctx["visible"] = bool(ctx["espace"] or ctx["equipe"])
    from app.services.courriels_rh import courriels_actifs_pour
    ctx["courriels_actifs"] = courriels_actifs_pour(user)
    return ctx
