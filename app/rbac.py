from __future__ import annotations

from functools import wraps
from typing import Iterable

from flask import abort, current_app
from flask_login import current_user

from app.extensions import db
from app.models import User, Role, Permission


# Liste canonique des permissions de l'ERP.
# (On peut en rajouter sans casser l'existant.)
# Liste canonique des permissions de l'ERP (codes stables) + libellés humains.
# ⚠️ Les restrictions "par secteur" ne sont PAS dans le RBAC : elles doivent être appliquées
# dans les requêtes/contrôles des routes (ex: can_see_secteur, _can_edit_participant, etc.).
DEFAULT_PERMS: list[tuple[str, str]] = [
    # Dashboard
    ("dashboard:view", "Accéder au tableau de bord"),
    ("secteurs:view", "Voir / lister les secteurs"),
    ("secteurs:edit", "Créer / modifier / activer les secteurs"),

    # Portée / secteurs
    ("scope:all_secteurs", "Accéder à tous les secteurs"),

    # Projets
    ("projets:view", "Voir les projets"),
    ("projets:edit", "Créer / modifier un projet"),
    ("projets:delete", "Supprimer un projet"),
    ("projets:files", "Gérer les pièces jointes projet (CR, docs)"),

    # AAP / Budget par projet
    ("aap:view", "Accéder au budget AAP (par projet)"),
    ("aap:charges_view", "Voir les charges AAP"),
    ("aap:charges_edit", "Ajouter / modifier / supprimer les charges AAP"),
    ("aap:produits_view", "Voir les produits / financeurs AAP"),
    ("aap:produits_edit", "Ajouter / modifier / supprimer les produits / financeurs AAP"),
    ("aap:ventilation_view", "Voir la ventilation AAP"),
    ("aap:ventilation_edit", "Saisir / modifier la ventilation AAP"),
    ("aap:synthese_view", "Voir la synthèse AAP"),
    ("aap:export", "Exporter le budget AAP (PDF/Excel/Doc)"),

    # Subventions (catalogue)
    ("subventions:view", "Voir les subventions"),
    ("subventions:edit", "Créer / modifier une subvention"),
    ("subventions:delete", "Supprimer une subvention"),
    ("subventions:link", "Lier / délier une subvention à un projet"),

    # Dépenses (engagement / réel)
    ("depenses:view", "Voir les dépenses"),
    ("depenses:create", "Créer une dépense"),
    ("depenses:edit", "Modifier une dépense"),
    ("depenses:delete", "Supprimer une dépense"),
    ("depenses:imputer_aap", "Imputer une dépense à une charge AAP"),

    # Ateliers / sessions / émargement
    ("ateliers:view", "Voir les ateliers"),
    ("ateliers:edit", "Créer / modifier un atelier"),
    ("ateliers:delete", "Supprimer un atelier"),
    ("ateliers:sync", "Synchroniser / importer les ateliers (émargement)"),
    ("emargement:view", "Voir l’émargement"),
    ("emargement:edit", "Saisir / modifier l’émargement"),
    ("inscriptions:view", "Voir les inscriptions et listes d’attente"),
    ("inscriptions:edit", "Inscrire / annuler / pointer les inscrits"),
    ("transitions:view", "Voir le tableau de bord transitions"),
    ("transitions:edit", "Étiqueter les ateliers, gérer défis et mesures transitions"),

    # Participants
    ("participants:view", "Voir les participants (secteur)"),
    ("participants:view_all", "Voir les participants (tous secteurs)"),
    ("participants:edit", "Créer / modifier un participant"),
    ("participants:delete", "Supprimer un participant"),
    ("participants:anonymize", "Anonymiser un participant"),
    ("insertion:view", "Voir les données d’insertion"),
    ("insertion:edit", "Modifier les données d’insertion"),
    ("insertion:sensitive_view", "Voir les données sensibles d’insertion"),
    ("insertion:admin_refs", "Gérer les référentiels insertion"),
    ("insertion:export", "Exporter les données insertion"),

    # Quartiers
    ("quartiers:view", "Voir les quartiers"),
    ("quartiers:edit", "Créer / modifier un quartier"),
    ("quartiers:delete", "Supprimer un quartier"),

    # Partenaires
    ("partenaires:view", "Voir l’annuaire partenaires"),
    ("partenaires:edit", "Créer / modifier un partenaire"),
    ("partenaires:delete", "Supprimer un partenaire"),

    # Questionnaires
    ("questionnaires:view", "Voir les questionnaires d’impact"),
    ("questionnaires:edit", "Créer / modifier un questionnaire"),
    ("questionnaires:delete", "Supprimer un questionnaire"),
    ("questionnaires:respond", "Saisir des réponses aux questionnaires"),
    ("questionnaires:export", "Exporter les réponses questionnaires"),

    # Inventaire
    ("inventaire:view", "Voir l’inventaire"),
    ("inventaire:edit", "Créer / modifier inventaire"),
    ("inventaire:delete", "Supprimer inventaire"),

    # Pédagogie / stats / bilans
    ("pedagogie:view", "Voir la pédagogie"),
    ("pedagogie:edit", "Créer / modifier la pédagogie"),
    ("stats:view", "Voir les statistiques (secteur)"),
    ("stats:view_all", "Voir les statistiques (tous secteurs)"),
    ("statsimpact:view", "Voir les stats impact (secteur)"),
    ("statsimpact:view_all", "Voir les stats impact (tous secteurs)"),
    ("statsimpact:insertion_export", "Exporter les stats insertion nominatives"),
    ("bilans:view", "Voir les bilans"),

    # Dons & reçus fiscaux
    ("dons:view", "Voir les dons et le registre des reçus fiscaux"),
    ("dons:edit", "Enregistrer / annuler des dons et générer les reçus"),

    # Bénévolat / RH
    ("benevolat:taux", "Modifier le taux horaire de valorisation du bénévolat"),
    ("rh:view", "Voir le module RH (salariés, ETP, masse salariale)"),
    ("rh:edit", "Gérer les salariés (RH : créer, affecter, importer)"),

    # Glossaire (dico du social)
    ("glossaire:edit", "Modifier le glossaire (ajouter, corriger, importer des mots)"),

    # Adhésions, participation & foyers
    ("cotisations:view", "Voir les adhésions, la participation et les règlements"),
    ("cotisations:edit", "Enregistrer des adhésions, la participation, les règlements et les foyers"),
    ("cotisations:tarifs", "Modifier le barème des tarifs (adhésions, participation)"),

    # Caisse (espèces & chèques)
    ("caisse:view", "Voir l'état de la caisse et son journal"),
    ("caisse:edit", "Compter la caisse, faire les dépôts, régler le fond de caisse"),

    # Contrôle / activité / admin
    ("controle:view", "Accéder au module contrôle"),
    ("activite:delete", "Supprimer une activité"),
    ("activite:restore", "Restaurer une activité mise en corbeille"),
    ("activite:purge", "Purger définitivement des activités"),
    ("admin:users", "Gérer les utilisateurs"),
    ("admin:rbac", "Gérer les droits (RBAC)"),
]

# Alias historiques tolérés dans le code / la base, mais à masquer dans l'UI RBAC
# pour éviter les doublons de cases à cocher. Seules les CLÉS servent
# (via HIDDEN_LEGACY_PERMS). Nom distinct de PERM_EQUIVALENTS (plus bas) pour
# éviter toute collision de noms au niveau module.
PERM_ALIAS_MASQUES_UI: dict[str, str] = {
    "users:edit": "admin:users",
    "budget:delete": "aap:charges_edit",
    "projets_edit": "projets:edit",
    "participants:update": "participants:edit",
    "participants:write": "participants:edit",
    "participant:edit": "participants:edit",
    "bilan:view": "bilans:view",
    "bilans:lourds:view": "bilans:view",
}

HIDDEN_LEGACY_PERMS = set(PERM_ALIAS_MASQUES_UI.keys())


ROLE_TEMPLATES: dict[str, dict[str, Iterable[str]]] = {
    # Super-admin technique: gestion users + RBAC (pas besoin d'être finance)
    "admin_tech": {
        "perms": [
            "dashboard:view",
            "admin:users",
            "admin:rbac",
            "controle:view",
            "scope:all_secteurs",
            # Accès en lecture aux données métier : un super-admin doit pouvoir
            # naviguer (sans ces droits, la liste des participants renvoie 403).
            "participants:view",
            "participants:view_all",
            "insertion:view",
            "ateliers:view",
            "emargement:view",
            "pedagogie:view",
            "stats:view",
            "statsimpact:view",
            "bilans:view",
            "secteurs:view",
            "secteurs:edit",
            "quartiers:view",
            "quartiers:edit",
            "quartiers:delete",
            "partenaires:view",
            "partenaires:edit",
            "partenaires:delete",
            "questionnaires:view",
            "questionnaires:edit",
            "questionnaires:delete",
            "questionnaires:respond",
            "questionnaires:export",
        
        ],
    },

    # Direction/directrice: accès global total
    "direction": {
        "perms": [p for (p, _) in DEFAULT_PERMS],
    },
    "directrice": {
        "perms": [p for (p, _) in DEFAULT_PERMS],
    },

    # Finance: accès global total (pilotage complet) — SAUF le module RH,
    # réservé à la direction (données salariales).
    "finance": {
        "perms": [p for (p, _) in DEFAULT_PERMS if not p.startswith("rh:")],
    },

    # Responsable secteur: "presque direction" MAIS borné au secteur (contrôlé dans les routes)
    # Exception demandée: peut voir les participants de tous secteurs, mais ne peut modifier/supprimer
    # que ceux "de son secteur" (contrôle déjà géré via created_secteur / _can_edit_participant).
    "responsable_secteur": {
        "perms": [
            "dashboard:view",

            # Projets (CRUD)
            "projets:view", "projets:edit", "projets:delete", "projets:files",

            # Budget AAP (CRUD complet)
            "aap:view", "aap:charges_view", "aap:charges_edit",
            "aap:produits_view", "aap:produits_edit",
            "aap:ventilation_view", "aap:ventilation_edit",
            "aap:synthese_view",

            # Subventions (CRUD + lien projet) MAIS uniquement sur son secteur (routes)
            "subventions:view", "subventions:edit", "subventions:delete", "subventions:link",

            # Dépenses (CRUD) MAIS uniquement sur son secteur (routes)
            "depenses:view", "depenses:create", "depenses:edit", "depenses:delete", "depenses:imputer_aap",

            # Inventaire (CRUD) MAIS uniquement sur son secteur (routes)
            "inventaire:view", "inventaire:edit", "inventaire:delete",

            # Ateliers + sessions (CRUD via module activité) + synchro
            "ateliers:view", "ateliers:edit", "ateliers:delete", "ateliers:sync",
            "emargement:view", "emargement:edit",
            "inscriptions:view", "inscriptions:edit",
            "transitions:view", "transitions:edit",

            # Participants: vue globale, mais edit/delete bornés au secteur via _can_edit_participant
            "participants:view_all", "participants:edit", "participants:delete", "participants:anonymize",
            "insertion:view",

            # Adhésions & participation (pas le barème des tarifs, réservé direction/finance)
            "cotisations:view", "cotisations:edit",

            # Quartiers (lecture seule)
            "quartiers:view",

            # Partenaires
            "partenaires:view", "partenaires:edit",

            # Questionnaires
            "questionnaires:view", "questionnaires:edit", "questionnaires:respond",

            # Pédagogie / passeport
            "pedagogie:view", "pedagogie:edit",

            # Stats/bilans sur son secteur
            "stats:view", "statsimpact:view", "bilans:view",

            # Activité : suppression / restauration OK, mais purge NON (réservée direction/tech)
            "activite:delete", "activite:restore",
        ],
    },
}


# Permissions introduites APRÈS des mises en production : au moment précis de
# leur première création en base, on les accorde aux rôles listés (AJOUT
# uniquement — les personnalisations faites via l'UI ne sont jamais retirées).
# Sans cela, seules les nouvelles installations en bénéficieraient (les
# gabarits de rôles ne sont pas réappliqués sur les rôles existants).
PERMS_AUTO_GRANT = {
    "dons:view": ("direction", "directrice", "finance"),
    "dons:edit": ("direction", "directrice", "finance"),
    "benevolat:taux": ("direction", "directrice", "finance"),
    "rh:view": ("direction", "directrice"),
    "rh:edit": ("direction", "directrice"),
    "glossaire:edit": ("direction", "directrice"),
    "cotisations:view": ("direction", "directrice", "finance", "responsable_secteur"),
    "cotisations:edit": ("direction", "directrice", "finance", "responsable_secteur"),
    "cotisations:tarifs": ("direction", "directrice", "finance"),
    "caisse:view": ("direction", "directrice", "finance"),
    "caisse:edit": ("direction", "directrice", "finance"),
    "inscriptions:view": ("direction", "directrice", "finance", "responsable_secteur", "admin_tech"),
    "inscriptions:edit": ("direction", "directrice", "finance", "responsable_secteur"),
    "transitions:view": ("direction", "directrice", "finance", "responsable_secteur", "admin_tech"),
    "transitions:edit": ("direction", "directrice", "finance", "responsable_secteur"),
}


def _category_from_code(code: str) -> str:
    """Retourne une catégorie lisible depuis un code 'module:action'."""
    module = (code.split(":", 1)[0] if ":" in code else code).strip()
    mapping = {
        "dashboard": "Accueil et navigation",
        "secteurs": "Secteurs et portée",
        "scope": "Secteurs et portée",
        "admin": "Administration",
        "controle": "Contrôle",
        "activite": "Activité",
        "ateliers": "Ateliers",
        "emargement": "Émargement",
        "inscriptions": "Inscriptions",
        "transitions": "Transitions",
        "participants": "Participants",
        "benevolat": "Bénévolat",
        "dons": "Dons & reçus fiscaux",
        "rh": "Ressources humaines",
        "glossaire": "Glossaire",
        "cotisations": "Adhésions & participation",
        "caisse": "Caisse",
        "insertion": "Insertion",
        "quartiers": "Quartiers",
        "partenaires": "Partenaires",
        "questionnaires": "Questionnaires",
        "inventaire": "Inventaire",
        "pedagogie": "Pédagogie",
        "stats": "Statistiques et bilans",
        "statsimpact": "Statistiques et bilans",
        "bilans": "Statistiques et bilans",
        "projets": "Projets",
        "aap": "Budget AAP",
        "subventions": "Subventions",
        "depenses": "Dépenses",
    }
    return mapping.get(module, module.capitalize())


def bootstrap_rbac() -> None:
    """Initialise RBAC (création tables + permissions + rôles) de manière idempotente."""

    try:
        db.create_all()
    except Exception:
        # En prod on évite d'exploser le démarrage juste pour RBAC.
        current_app.logger.exception("RBAC: db.create_all() a échoué")
        return

    # --- Permissions ---
    existing = {p.code: p for p in Permission.query.all()}
    changed = False

    nouvelles_perms: set[str] = set()
    for code, label in DEFAULT_PERMS:
        if code not in existing:
            db.session.add(Permission(code=code, label=label, category=_category_from_code(code)))
            nouvelles_perms.add(code)
            changed = True
        else:
            p = existing[code]
            # Mise à jour label / catégorie si besoin
            new_cat = _category_from_code(code)
            if p.label != label:
                p.label = label
                changed = True
            if getattr(p, "category", None) != new_cat:
                p.category = new_cat
                changed = True

    if changed:
        db.session.commit()

    perms_by_code = {p.code: p for p in Permission.query.all()}

    
        # --- Rôles ---
    import os
    apply_templates = os.getenv("RBAC_APPLY_TEMPLATES", "").lower() in ("1", "true", "yes")

    for role_code, cfg in ROLE_TEMPLATES.items():
        role = Role.query.filter_by(code=role_code).first()
        created = False
        if not role:
            role = Role(code=role_code, label=role_code)
            db.session.add(role)
            db.session.flush()
            created = True

        # ✅ IMPORTANT :
        # - Si le rôle vient d'être créé : on applique le template
        # - Si RBAC_APPLY_TEMPLATES=1 : on force l'écrasement (utile en dev / maintenance)
        # - Sinon : on ne touche pas aux permissions existantes (donc tes modifs via l'UI restent)
        if created or apply_templates:
            desired = set(cfg.get("perms", []))
            role.permissions = [perms_by_code[c] for c in desired if c in perms_by_code]

    db.session.commit()

    # Attribution additive des permissions nouvellement introduites : un rôle
    # existant en production reçoit la permission à sa première apparition,
    # sans que ses autres réglages soient touchés.
    for code in nouvelles_perms:
        cibles = PERMS_AUTO_GRANT.get(code)
        perm = perms_by_code.get(code) or Permission.query.filter_by(code=code).first()
        if not cibles or perm is None:
            continue
        for role_code in cibles:
            role = Role.query.filter_by(code=role_code).first()
            if role is not None and perm not in role.permissions:
                role.permissions = list(role.permissions) + [perm]
    if nouvelles_perms:
        db.session.commit()

    # Auto-réparation du compte technique : garantir qu'admin_tech dispose
    # toujours des permissions de son gabarit (AJOUT seulement, jamais de
    # retrait, pour ne pas écraser des réglages manuels). Répare les
    # installations où admin_tech avait été créé avec un gabarit plus restreint
    # (ex. sans participants:view_all -> 403 sur la liste des participants).
    at = Role.query.filter_by(code="admin_tech").first()
    if at is not None:
        actuels = {p.code for p in at.permissions}
        ajouts = [
            perms_by_code[c]
            for c in ROLE_TEMPLATES["admin_tech"]["perms"]
            if c in perms_by_code and c not in actuels
        ]
        if ajouts:
            at.permissions = list(at.permissions) + ajouts
            db.session.commit()


    # Rattrapage: si un utilisateur n'a aucun rôle RBAC, on l'aligne sur User.role (legacy).
    # C'est l'unique pont avec la colonne legacy: ailleurs, seuls les rôles RBAC font foi.
    legacy_aliases = {
        "directrice": "direction",
        "directeur": "direction",
        "financiere": "finance",
        "financière": "finance",
        "responsable_secteurs": "responsable_secteur",
    }
    users = User.query.all()
    for u in users:
        if getattr(u, "roles", None) is None:
            continue
        if len(u.roles) == 0:
            legacy = (u.role or "responsable_secteur").strip().lower()
            legacy = legacy_aliases.get(legacy, legacy)
            role = Role.query.filter_by(code=legacy).first()
            if role:
                u.roles.append(role)

    db.session.commit()



# ---------------------------------------------------------------------------
# RBAC helpers: équivalences / alias de permissions
# ---------------------------------------------------------------------------

# Certaines routes historiques peuvent exiger des codes différents (ex: statsimpact:view)
# On tolère des équivalences pour éviter les 403 incompréhensibles.
# La sécurité reste côté serveur: on ne "donne" pas un droit, on accepte un alias.
# ---------------------------------------------------------------------------
# RBAC helpers: équivalences / alias de permissions
# ---------------------------------------------------------------------------

# Tolérance progressive: on accepte des anciens codes encore présents dans les routes/templates
# le temps de migrer proprement vers les nouveaux codes.
PERM_EQUIVALENTS: dict[str, set[str]] = {
    # --- Legacy projets/budget AAP ---
    # Les routes Budget AAP utilisent parfois "projets_edit" (ancien). On accepte le nouveau.
    "projets_edit": {"projets_edit", "projets:edit", "aap:view"},

    # --- Admin users (legacy) ---
    "users:edit": {"users:edit", "admin:users"},

    # --- Budget (legacy) ---
    "budget:delete": {"budget:delete", "aap:charges_edit"},  # supprimer une ligne budget ≈ agir sur charges AAP

    # --- Statsimpact ---
    "statsimpact:view": {"statsimpact:view", "stats:view", "statsimpact:view_all"},
    "statsimpact:view_all": {"statsimpact:view_all"},

    # --- Stats ---
    "stats:view": {"stats:view", "stats:view_all"},
    "stats:view_all": {"stats:view_all"},

    # --- Bilans ---
    "bilan:view": {"bilan:view", "bilans:view"},
    "bilans:lourds:view": {"bilans:lourds:view", "bilans:view"},

    # --- Participants: variantes ---
    # Une vue "tous secteurs" satisfait naturellement la vue simple : sans
    # cela, responsable_secteur (view_all sans view) prenait un 403 sur
    # /participants/ alors que la carte du menu lui était affichée.
    "participants:view": {"participants:view", "participants:view_all"},
    "participants:update": {"participants:update", "participants:edit"},
    "participants:write": {"participants:write", "participants:edit"},
    "participant:edit": {"participant:edit", "participants:edit"},
}

def _expand_perm(code: str) -> set[str]:
    code = (code or "").strip()
    if not code:
        return set()
    # si on connaît une équivalence, on accepte n'importe lequel
    if code in PERM_EQUIVALENTS:
        return set(PERM_EQUIVALENTS[code])
    return {code}


def require_perm(code: str):
    """Décorateur: exige une permission RBAC (sinon 403).
    Supporte des alias/équivalences (voir PERM_EQUIVALENTS)."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            has_perm_fn = getattr(current_user, "has_perm", None)
            if not callable(has_perm_fn):
                abort(403)

            wanted = _expand_perm(code)
            if not wanted:
                abort(403)

            if not any(has_perm_fn(c) for c in wanted):
                abort(403)

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def can(code: str) -> bool:
    """Helper Jinja: can('perm:code'). Supporte les alias/équivalences."""
    if not current_user.is_authenticated:
        return False

    has_perm_fn = getattr(current_user, "has_perm", None)
    if not callable(has_perm_fn):
        return False

    wanted = _expand_perm(code)
    if not wanted:
        return False

    return any(has_perm_fn(c) for c in wanted)


def can_access_secteur(secteur: str | None) -> bool:
    """Retourne True si l'utilisateur peut accéder à un secteur donné.
    - Les restrictions par secteur restent du côté routes/services.
    - Le RBAC décide de la portée via la permission 'scope:all_secteurs'."""
    if not current_user.is_authenticated:
        return False

    has_perm_fn = getattr(current_user, "has_perm", None)
    if callable(has_perm_fn) and has_perm_fn("scope:all_secteurs"):
        return True

    # Si on n'a pas de secteur à comparer (ex: item sans secteur), on autorise.
    if not secteur:
        return True

    return getattr(current_user, "secteur_assigne", None) == secteur
