"""Notifications automatiques réglables — un digest e-mail, pas un déluge.

Principes, calibrés pour une petite association :
- RIEN n'est actif par défaut : chaque type se coche explicitement dans
  Administration → Notifications, avec ses destinataires et sa fréquence ;
- UN e-mail récapitulatif (digest) par destinataire, jamais un e-mail par
  événement ;
- silence = rien à signaler : si toutes les sections sont vides, aucun
  e-mail ne part ;
- même mécanique quotidienne que la purge RGPD (table ``tache_planifiee``,
  déclenchement au premier trafic du jour) : aucun planificateur externe.

Types disponibles (v1) :
- ``echeances_financeurs`` : versements attendus et bilans à rendre
  (retards + horizon proche), sur les subventions non archivées ;
- ``impayes``              : cotisations non soldées de l'année scolaire ;
- ``sauvegarde``           : dernière sauvegarde plus vieille que le seuil ;
- ``rappels``              : rappels « À traiter » ouverts dont l'échéance
  est dépassée.
"""
from __future__ import annotations

import smtplib
from datetime import date
from email.message import EmailMessage

from flask import current_app

from app.extensions import db
from app.models import Cotisation, NotificationReglage, Paiement, Subvention, SuiviRappel, TachePlanifiee
from app.utils.dates import utcnow

NOM_TACHE = "notifications_digest"

FREQUENCES = {"quotidien": "Tous les jours", "hebdomadaire": "Chaque lundi"}


# ---------------------------------------------------------------------------
# Collecteurs : chaque type sait produire ses lignes (liste vide = rien à dire)
# ---------------------------------------------------------------------------

def _lignes_echeances_financeurs(seuil_jours: int | None, today: date) -> list[str]:
    from app.main.subventions import alertes_echeances

    subs = Subvention.query.filter(Subvention.est_archive.is_(False)).all()
    horizon = seuil_jours or 30
    lignes = []
    for alerte in alertes_echeances(subs, today=today, horizon_jours=horizon):
        prefixe = "RETARD" if alerte["niveau"] == "danger" else "À venir"
        sub = alerte["sub"]
        lignes.append(f"[{prefixe}] {sub.nom} ({sub.financeur or 'financeur ?'}) — {alerte['message']}")
    return lignes


def _lignes_impayes(seuil_jours: int | None, today: date) -> list[str]:
    from app.services.cotisations import annee_scolaire_de, libelle_annee_scolaire

    annee = annee_scolaire_de(today)
    regle_subq = (
        db.session.query(
            Paiement.cotisation_id.label("cid"),
            db.func.coalesce(db.func.sum(Paiement.montant), 0.0).label("regle"),
        )
        .group_by(Paiement.cotisation_id)
        .subquery()
    )
    regle_col = db.func.coalesce(regle_subq.c.regle, 0.0)
    rows = (
        db.session.query(Cotisation, regle_col)
        .outerjoin(regle_subq, regle_subq.c.cid == Cotisation.id)
        .filter(
            Cotisation.annee_scolaire == annee,
            Cotisation.montant_du - regle_col > 0.009,
        )
        .all()
    )
    if not rows:
        return []
    total = round(sum(float(c.montant_du or 0) - float(r or 0) for c, r in rows), 2)
    return [
        f"{len(rows)} cotisation(s) non soldée(s) sur {libelle_annee_scolaire(annee)} — "
        f"reste à recouvrer : {total:.2f} €. Détail : page « Impayés »."
    ]


def _lignes_sauvegarde(seuil_jours: int | None, today: date) -> list[str]:
    from app.services.sauvegarde import jours_depuis_derniere

    seuil = seuil_jours if seuil_jours is not None else 2
    jours = jours_depuis_derniere()
    if jours is None:
        return ["Aucune sauvegarde n'existe. Créez-en une (Administration → Sauvegardes)."]
    if jours > seuil:
        return [
            f"Dernière sauvegarde il y a {jours} jour(s) — au-delà du seuil de {seuil} j. "
            "Vérifiez la tâche planifiée ou sauvegardez manuellement."
        ]
    return []


def _lignes_rappels(seuil_jours: int | None, today: date) -> list[str]:
    rows = (
        SuiviRappel.query
        .filter(
            SuiviRappel.statut == "ouvert",
            SuiviRappel.echeance.isnot(None),
            SuiviRappel.echeance < today,
        )
        .order_by(SuiviRappel.echeance.asc())
        .limit(15)
        .all()
    )
    return [
        f"[{(today - r.echeance).days} j de retard] {r.titre}" for r in rows
    ]


#: Registre des types : libellés pour l'écran de réglage + collecteur associé.
TYPES_NOTIFICATION: dict[str, dict] = {
    "echeances_financeurs": {
        "label": "Échéances financeurs",
        "description": "Versements attendus et bilans à rendre : retards et échéances dans l'horizon choisi.",
        "seuil_label": "Horizon d'alerte (jours avant l'échéance)",
        "seuil_defaut": 30,
        "collecteur": _lignes_echeances_financeurs,
    },
    "impayes": {
        "label": "Impayés (adhésions & participation)",
        "description": "Résumé des cotisations non soldées de l'année scolaire en cours.",
        "seuil_label": None,
        "seuil_defaut": None,
        "collecteur": _lignes_impayes,
    },
    "sauvegarde": {
        "label": "Sauvegarde en retard",
        "description": "Alerte quand la dernière sauvegarde dépasse le seuil (aucune sauvegarde = alerte aussi).",
        "seuil_label": "Seuil d'alerte (jours sans sauvegarde)",
        "seuil_defaut": 2,
        "collecteur": _lignes_sauvegarde,
    },
    "rappels": {
        "label": "Rappels « À traiter » en retard",
        "description": "Rappels ouverts dont la date d'échéance est dépassée.",
        "seuil_label": None,
        "seuil_defaut": None,
        "collecteur": _lignes_rappels,
    },
}


# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------

def reglages_effectifs() -> list[dict]:
    """Les types du registre, complétés de leur réglage en base (ou défauts)."""
    en_base = {r.code: r for r in NotificationReglage.query.all()}
    lignes = []
    for code, meta in TYPES_NOTIFICATION.items():
        row = en_base.get(code)
        lignes.append({
            "code": code,
            "label": meta["label"],
            "description": meta["description"],
            "seuil_label": meta["seuil_label"],
            "actif": bool(row.actif) if row else False,
            "destinataires": (row.destinataires or "") if row else "",
            "frequence": (row.frequence or "quotidien") if row else "quotidien",
            "seuil_jours": (row.seuil_jours if row and row.seuil_jours is not None else meta["seuil_defaut"]),
        })
    return lignes


def enregistrer_reglage(code: str, *, actif: bool, destinataires: str,
                        frequence: str, seuil_jours: int | None) -> NotificationReglage:
    if code not in TYPES_NOTIFICATION:
        raise ValueError(f"Type de notification inconnu : {code}")
    if frequence not in FREQUENCES:
        frequence = "quotidien"
    row = NotificationReglage.query.filter_by(code=code).first()
    if row is None:
        row = NotificationReglage(code=code)
        db.session.add(row)
    row.actif = bool(actif)
    row.destinataires = (destinataires or "").strip() or None
    row.frequence = frequence
    row.seuil_jours = seuil_jours
    db.session.commit()
    return row


def _emails(destinataires: str | None) -> list[str]:
    brut = (destinataires or "").replace(";", ",").replace("\n", ",")
    vus: set[str] = set()
    adresses = []
    for morceau in brut.split(","):
        adresse = morceau.strip().lower()
        if adresse and "@" in adresse and adresse not in vus:
            vus.add(adresse)
            adresses.append(adresse)
    return adresses


# ---------------------------------------------------------------------------
# Construction et envoi du digest
# ---------------------------------------------------------------------------

def _est_due_aujourdhui(frequence: str, today: date) -> bool:
    if frequence == "hebdomadaire":
        return today.weekday() == 0  # lundi
    return True


def construire_digest(today: date | None = None, *, forcer: bool = False) -> dict[str, list[dict]]:
    """Sections du digest par adresse e-mail : {email: [{titre, lignes}]}.

    Seuls les types actifs, dus aujourd'hui (sauf ``forcer``) et non vides
    produisent une section. Un destinataire sans section ne reçoit rien.
    """
    today = today or date.today()
    par_email: dict[str, list[dict]] = {}
    for reglage in reglages_effectifs():
        if not reglage["actif"]:
            continue
        if not forcer and not _est_due_aujourdhui(reglage["frequence"], today):
            continue
        adresses = _emails(reglage["destinataires"])
        if not adresses:
            continue
        collecteur = TYPES_NOTIFICATION[reglage["code"]]["collecteur"]
        try:
            lignes = collecteur(reglage["seuil_jours"], today)
        except Exception:  # un collecteur cassé ne doit pas bloquer les autres
            current_app.logger.exception("Notification %s : collecte échouée", reglage["code"])
            continue
        if not lignes:
            continue
        section = {"titre": reglage["label"], "lignes": lignes}
        for adresse in adresses:
            par_email.setdefault(adresse, []).append(section)
    return par_email


def _corps_digest(sections: list[dict], today: date) -> str:
    app_name = current_app.config.get("APP_NAME") or "Application"
    blocs = [f"Récapitulatif {app_name} du {today.strftime('%d/%m/%Y')}", ""]
    for section in sections:
        blocs.append(f"— {section['titre']} —")
        blocs.extend(section["lignes"])
        blocs.append("")
    blocs.append("Réglages : Administration → Notifications (vous pouvez désactiver chaque type).")
    return "\n".join(blocs)


def _envoyer_texte(to: str, subject: str, body: str) -> None:
    """Envoi SMTP texte simple via la configuration effective de l'instance."""
    from app.services.instance_settings import resolve_mail_settings

    cfg = resolve_mail_settings(current_app.config)
    if not cfg["host"] or not cfg["sender"]:
        raise RuntimeError("SMTP non configuré (hôte ou expéditeur manquant).")

    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    port = int(cfg["port"])
    timeout = float(current_app.config.get("MAIL_TIMEOUT_SECONDS", 10))
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["host"], port, timeout=timeout)
        server.ehlo()
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=timeout)
        server.ehlo()
        if cfg["use_tls"]:
            server.starttls(timeout=timeout)
            server.ehlo()
    try:
        if cfg["username"] and cfg["password"]:
            server.login(cfg["username"], cfg["password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def envoyer_digest(today: date | None = None, *, forcer: bool = False) -> dict:
    """Construit et envoie le digest. Retourne {"envoyes": n, "sections": n}.

    ``forcer`` ignore la fréquence (bouton « Envoyer un aperçu maintenant »).
    """
    today = today or date.today()
    par_email = construire_digest(today, forcer=forcer)
    app_name = current_app.config.get("APP_NAME") or "Application"
    envoyes = 0
    total_sections = 0
    for adresse, sections in par_email.items():
        total_sections += len(sections)
        _envoyer_texte(
            adresse,
            f"[{app_name}] Récapitulatif du {today.strftime('%d/%m/%Y')}",
            _corps_digest(sections, today),
        )
        envoyes += 1
    return {"envoyes": envoyes, "sections": total_sections, "destinataires": sorted(par_email)}


# ---------------------------------------------------------------------------
# Déclenchement quotidien (même mécanique que la purge RGPD)
# ---------------------------------------------------------------------------

def notifications_actives() -> bool:
    try:
        return db.session.query(NotificationReglage.id).filter(
            NotificationReglage.actif.is_(True)
        ).first() is not None
    except Exception:  # table pas encore migrée au premier démarrage
        db.session.rollback()
        return False


def digest_quotidien_si_necessaire() -> None:
    """Envoie le digest au plus une fois par jour. Ne casse jamais une requête."""
    try:
        tache = TachePlanifiee.query.filter_by(nom=NOM_TACHE).first()
        if tache is not None and tache.derniere_execution is not None \
                and tache.derniere_execution.date() >= utcnow().date():
            return
        if tache is None:
            tache = TachePlanifiee(nom=NOM_TACHE)
            db.session.add(tache)
        tache.derniere_execution = utcnow()
        db.session.commit()

        resultat = envoyer_digest()
        if resultat["envoyes"]:
            current_app.logger.info(
                "Digest de notifications envoyé à %s destinataire(s) : %s",
                resultat["envoyes"], ", ".join(resultat["destinataires"]),
            )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Échec de l'envoi du digest de notifications")
