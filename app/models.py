
from app.utils.dates import utcnow
from datetime import date
import json
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db

# ---------- USERS ----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    nom = db.Column(db.String(120), nullable=False, default="Utilisateur")
    role = db.Column(db.String(40), nullable=False, default="responsable_secteur")
    secteur_assigne = db.Column(db.String(80), nullable=True)
    # Compte actif : un compte désactivé ne peut plus se connecter (sans
    # perdre son historique, contrairement à une suppression).
    actif = db.Column(db.Boolean, nullable=False, default=True)
    # Jeton secret du flux calendrier iCal personnel (abonnement Google
    # Agenda / Apple / Outlook). NULL tant que la personne n'a pas activé la
    # synchro ; régénérable pour révoquer un ancien lien.
    calendar_token = db.Column(db.String(64), nullable=True, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    # Flask-Login
    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        # Flask-Login refuse la session d'un utilisateur inactif.
        return bool(self.actif)

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # RBAC helpers (roles/permissions)
    def has_perm(self, code: str) -> bool:
        codes: set[str] = set()
        for role in getattr(self, "roles", []) or []:
            for p in getattr(role, "permissions", []) or []:
                codes.add(p.code)
        return code in codes

    @property
    def role_codes(self) -> list[str]:
        return sorted([r.code for r in getattr(self, "roles", []) or []])


    def has_role(self, code: str | None) -> bool:
        """True si l'utilisateur possède le rôle RBAC `code`.

        Seuls les rôles RBAC (User.roles -> Role.code) font foi. La colonne
        legacy `User.role` ne donne aucun droit : elle est migrée vers un
        rôle RBAC au démarrage par `bootstrap_rbac`.
        Gère quelques alias historiques (directrice -> direction, etc.).
        """
        if not code:
            return False

        c = (code or "").strip().lower()

        aliases = {
            # historiques
            "directrice": "direction",
            "directeur": "direction",
            "financiere": "finance",
            "financière": "finance",
            "responsable_secteurs": "responsable_secteur",
        }
        c = aliases.get(c, c)

        try:
            for r in (getattr(self, "roles", []) or []):
                rc = (getattr(r, "code", "") or "").strip().lower()
                rc = aliases.get(rc, rc)
                if rc == c:
                    return True
        except Exception:
            pass

        return False


class UserDashboardPreference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    ui_mode = db.Column(db.String(20), nullable=False, default="simple")
    quick_actions_json = db.Column(db.Text, nullable=True)
    widgets_json = db.Column(db.Text, nullable=True)
    customized = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", backref=db.backref("dashboard_pref", uselist=False, cascade="all, delete-orphan"))


# =========================================================
# RBAC (Roles & Permissions)
# ---------------------------------------------------------
# Objectif: remplacer progressivement la logique "role = string" par
# une vraie gestion fine des permissions.
# Le champ User.role reste (compatibilité), mais on le mappe vers
# un ou plusieurs Roles.

user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permission.id", ondelete="CASCADE"), primary_key=True),
)


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)  # ex: "finance", "admin_tech"
    label = db.Column(db.String(120), nullable=False, default="Rôle")

    permissions = db.relationship(
        "Permission",
        secondary=role_permissions,
        lazy="subquery",
        backref=db.backref("roles", lazy=True),
    )

    def __repr__(self) -> str:
        return f"<Role {self.code}>"


class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(120), unique=True, nullable=False, index=True)  # ex: "subventions:edit"
    label = db.Column(db.String(200), nullable=False, default="Permission")
    category = db.Column(db.String(60), nullable=True, index=True)  # ex: "Subventions"

    def __repr__(self) -> str:
        return f"<Perm {self.code}>"




class InstanceSettings(db.Model):
    """Paramètres d'instance modifiables depuis l'admin (1 ligne)."""

    id = db.Column(db.Integer, primary_key=True)
    app_name = db.Column(db.String(120), nullable=True)
    organization_name = db.Column(db.String(180), nullable=True)
    app_logo_path = db.Column(db.String(255), nullable=True)
    organization_logo_path = db.Column(db.String(255), nullable=True)
    public_base_url = db.Column(db.String(255), nullable=True)

    # SMTP (override optionnel des variables d'environnement)
    smtp_host = db.Column(db.String(255), nullable=True)
    smtp_port = db.Column(db.Integer, nullable=True)
    smtp_username = db.Column(db.String(255), nullable=True)
    smtp_password = db.Column(db.String(255), nullable=True)
    smtp_use_tls = db.Column(db.Boolean, nullable=True)
    smtp_sender = db.Column(db.String(255), nullable=True)

    # Taux horaire de valorisation du bénévolat (prioritaire sur la config env).
    benevolat_taux_horaire = db.Column(db.Float, nullable=True)

    # Purge RGPD (prioritaire sur les variables d'environnement) :
    # délai d'inactivité avant anonymisation automatique, et activation.
    purge_rgpd_annees = db.Column(db.Integer, nullable=True)
    purge_rgpd_auto = db.Column(db.Boolean, nullable=True)

    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Secteur(db.Model):
    """Secteur métier (liste administrable).

    ⚠️ Pour limiter le refacto, on conserve *label* comme valeur utilisée
    partout (Subvention.secteur, Projet.secteur, etc.).
    Le champ `code` sert surtout d'identifiant stable/slug.
    """

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    label = db.Column(db.String(120), unique=True, nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<Secteur {self.code} ({'on' if self.is_active else 'off'})>"


# Relation User.roles (déclarée après Role)
User.roles = db.relationship(
    "Role",
    secondary=user_roles,
    lazy="subquery",
    backref=db.backref("users", lazy=True),
)


# ---------- PEDAGOGIE ----------
class Referentiel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)


class Competence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referentiel_id = db.Column(db.Integer, db.ForeignKey("referentiel.id"), nullable=False)
    code = db.Column(db.String(40), nullable=False)
    nom = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    referentiel = db.relationship("Referentiel", backref=db.backref("competences", cascade="all, delete-orphan"))


projet_competence = db.Table(
    "projet_competence",
    db.Column("projet_id", db.Integer, db.ForeignKey("projet.id"), primary_key=True),
    db.Column("competence_id", db.Integer, db.ForeignKey("competence.id"), primary_key=True),
)


atelier_competence = db.Table(
    "atelier_competence",
    db.Column("atelier_id", db.Integer, db.ForeignKey("atelier_activite.id"), primary_key=True),
    db.Column("competence_id", db.Integer, db.ForeignKey("competence.id"), primary_key=True),
)


session_competence = db.Table(
    "session_competence",
    db.Column("session_id", db.Integer, db.ForeignKey("session_activite.id"), primary_key=True),
    db.Column("competence_id", db.Integer, db.ForeignKey("competence.id"), primary_key=True),
)


objectif_competence = db.Table(
    "objectif_competence",
    db.Column("objectif_id", db.Integer, db.ForeignKey("objectif.id"), primary_key=True),
    db.Column("competence_id", db.Integer, db.ForeignKey("competence.id"), primary_key=True),
)

module_competence = db.Table(
    "module_competence",
    db.Column("module_id", db.Integer, db.ForeignKey("pedagogie_module.id"), primary_key=True),
    db.Column("competence_id", db.Integer, db.ForeignKey("competence.id"), primary_key=True),
)

atelier_module = db.Table(
    "atelier_module",
    db.Column("atelier_id", db.Integer, db.ForeignKey("atelier_activite.id"), primary_key=True),
    db.Column("module_id", db.Integer, db.ForeignKey("pedagogie_module.id"), primary_key=True),
)

session_module = db.Table(
    "session_module",
    db.Column("session_id", db.Integer, db.ForeignKey("session_activite.id"), primary_key=True),
    db.Column("module_id", db.Integer, db.ForeignKey("pedagogie_module.id"), primary_key=True),
)


class PedagogieModule(db.Model):
    __tablename__ = "pedagogie_module"
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(160), nullable=False, unique=True, index=True)
    description = db.Column(db.Text, nullable=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    competences = db.relationship(
        "Competence",
        secondary=module_competence,
        backref=db.backref("modules", lazy="dynamic"),
    )


class ObjectifCompetenceMap(db.Model):
    __tablename__ = "objectif_competence_map"

    id = db.Column(db.Integer, primary_key=True)
    objectif_id = db.Column(db.Integer, db.ForeignKey("objectif.id", ondelete="CASCADE"), nullable=False, index=True)
    competence_id = db.Column(db.Integer, db.ForeignKey("competence.id", ondelete="CASCADE"), nullable=False, index=True)
    poids = db.Column(db.Float, nullable=False, default=1.0)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    objectif = db.relationship("Objectif")
    competence = db.relationship("Competence")

    __table_args__ = (
        db.UniqueConstraint("objectif_id", "competence_id", name="uq_objectif_competence_map"),
    )




class PlanProjetAtelierModule(db.Model):
    __tablename__ = "plan_projet_atelier_module"

    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id", ondelete="CASCADE"), nullable=False, index=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    module_id = db.Column(db.Integer, db.ForeignKey("pedagogie_module.id", ondelete="CASCADE"), nullable=False, index=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    projet = db.relationship("Projet")
    atelier = db.relationship("AtelierActivite")
    module = db.relationship("PedagogieModule")

    __table_args__ = (
        db.UniqueConstraint("projet_id", "atelier_id", "module_id", name="uq_plan_projet_atelier_module"),
    )

class Objectif(db.Model):
    __tablename__ = "objectif"
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("objectif.id"), nullable=True)
    type = db.Column(db.String(30), nullable=False)  # general | specifique | operationnel
    titre = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    seuil_validation = db.Column(db.Float, nullable=False, default=60.0)

    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=True)
    module_id = db.Column(db.Integer, db.ForeignKey("pedagogie_module.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)

    parent = db.relationship("Objectif", remote_side=[id], backref=db.backref("enfants", cascade="all, delete-orphan"))
    projet = db.relationship("Projet")
    atelier = db.relationship("AtelierActivite")
    session = db.relationship("SessionActivite")
    module = db.relationship("PedagogieModule")
    competences = db.relationship(
        "Competence",
        secondary=objectif_competence,
        backref=db.backref("objectifs", lazy="dynamic"),
    )


# ---------- PROJETS ----------
class Projet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False)
    secteur = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=True)

    cr_filename = db.Column(db.String(255), nullable=True)
    cr_original_name = db.Column(db.String(255), nullable=True)

    # Archivage (soft-delete) : un projet archivé sort des listes sans être détruit.
    is_archive = db.Column(db.Boolean, default=False, nullable=False, index=True)
    archived_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)

    subventions = db.relationship("SubventionProjet", back_populates="projet", cascade="all, delete-orphan")
    # AAP / Budget projet (charges/produits/ventilations)
    charges_projet = db.relationship("ChargeProjet", back_populates="projet", cascade="all, delete-orphan")
    produits_projet = db.relationship("ProduitProjet", back_populates="projet", cascade="all, delete-orphan")
    journal_entries = db.relationship("ProjetJournalEntry", back_populates="projet", cascade="all, delete-orphan")
    actions_detaillees = db.relationship("ProjetAction", back_populates="projet", cascade="all, delete-orphan")
    competences = db.relationship(
        "Competence",
        secondary=projet_competence,
        backref=db.backref("projets", lazy="dynamic"),
    )

    @property
    def total_demande(self):
        return round(sum(float(sp.subvention.montant_demande or 0) for sp in self.subventions), 2)

    @property
    def total_attribue(self):
        return round(sum(float(sp.subvention.montant_attribue or 0) for sp in self.subventions), 2)

    @property
    def total_recu(self):
        return round(sum(float(sp.subvention.montant_recu or 0) for sp in self.subventions), 2)

    @property
    def total_reel_lignes(self):
        return round(sum(float(sp.subvention.total_reel_lignes or 0) for sp in self.subventions), 2)

    @property
    def total_engage(self):
        return round(sum(float(sp.subvention.total_engage or 0) for sp in self.subventions), 2)

    @property
    def total_reste(self):
        return round(sum(float(sp.subvention.total_reste or 0) for sp in self.subventions), 2)


    # -----------------------------
    # Budget AAP (par projet)
    # -----------------------------
    @property
    def total_charges_previsionnel(self):
        return round(sum(float(c.montant_previsionnel or 0) for c in self.charges_projet), 2)

    @property
    def total_charges_reel(self):
        return round(sum(float(c.montant_reel or 0) for c in self.charges_projet), 2)

    @property
    def total_produits_demandes(self):
        return round(sum(float(p.montant_demande or 0) for p in self.produits_projet), 2)

    @property
    def total_produits_accordes(self):
        return round(sum(float(p.montant_accorde or 0) for p in self.produits_projet), 2)

    @property
    def total_produits_recus(self):
        return round(sum(float(p.montant_recu or 0) for p in self.produits_projet), 2)

    @property
    def reste_a_financer(self):
        # basé sur l'accordé (et non la demande)
        return round(float(self.total_charges_previsionnel or 0) - float(self.total_produits_accordes or 0), 2)




class ChargeProjet(db.Model):
    __tablename__ = "charge_projet"
    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=False)

    # bloc = directe / indirecte (comme le tableau AAP)
    bloc = db.Column(db.String(20), nullable=False, default="directe")  # directe | indirecte
    # code plan comptable : 60/61/62/63/64/65/...
    code_plan = db.Column(db.String(20), nullable=False, default="60")

    libelle = db.Column(db.String(255), nullable=False)

    montant_previsionnel = db.Column(db.Float, default=0.0)
    montant_reel = db.Column(db.Float, default=0.0)

    commentaire = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    projet = db.relationship("Projet", back_populates="charges_projet")
    ventilations = db.relationship("VentilationProjet", back_populates="charge", cascade="all, delete-orphan")
    depenses = db.relationship("Depense", back_populates="charge_projet", passive_deletes=True)

    @property
    def ventile(self):
        return round(sum(float(v.montant_ventile or 0) for v in self.ventilations), 2)

    @property
    def reste_a_financer(self):
        return round(float(self.montant_previsionnel or 0) - float(self.ventile or 0), 2)

    @property
    def engage(self):
        # engagement réel via les dépenses rattachées à cette charge
        return round(sum(float(d.montant or 0) for d in self.depenses if not d.est_supprimee), 2)

    @property
    def reste_a_engager(self):
        base = float(self.montant_reel or 0) if float(self.montant_reel or 0) > 0 else float(self.montant_previsionnel or 0)
        return round(base - float(self.engage or 0), 2)


class ProduitProjet(db.Model):
    __tablename__ = "produit_projet"
    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=False)

    financeur = db.Column(db.String(255), nullable=False)
    categorie = db.Column(db.String(50), nullable=False, default="autre")  # etat/region/departement/commune/caf/europe/prive/autofinancement/...
    statut = db.Column(db.String(30), nullable=False, default="prevu")  # prevu/demande/accorde/partiel/refuse

    montant_demande = db.Column(db.Float, default=0.0)
    montant_accorde = db.Column(db.Float, default=0.0)
    montant_recu = db.Column(db.Float, default=0.0)

    reference_dossier = db.Column(db.String(120), nullable=True)
    commentaire = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    projet = db.relationship("Projet", back_populates="produits_projet")
    ventilations = db.relationship("VentilationProjet", back_populates="produit", cascade="all, delete-orphan")

    @property
    def ventile(self):
        return round(sum(float(v.montant_ventile or 0) for v in self.ventilations), 2)

    @property
    def reste_a_ventiler(self):
        return round(float(self.montant_accorde or 0) - float(self.ventile or 0), 2)


class VentilationProjet(db.Model):
    __tablename__ = "ventilation_projet"
    id = db.Column(db.Integer, primary_key=True)
    charge_id = db.Column(db.Integer, db.ForeignKey("charge_projet.id", ondelete="CASCADE"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit_projet.id", ondelete="CASCADE"), nullable=False)
    montant_ventile = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=utcnow)

    charge = db.relationship("ChargeProjet", back_populates="ventilations")
    produit = db.relationship("ProduitProjet", back_populates="ventilations")

class BudgetPrevisionnel(db.Model):
    __tablename__ = "budget_previsionnel"
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False)
    annee = db.Column(db.Integer, nullable=False, index=True)
    secteur = db.Column(db.String(80), nullable=False, index=True)
    statut = db.Column(db.String(30), nullable=False, default="brouillon")  # brouillon / valide / archive
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    lignes = db.relationship("BudgetPrevisionnelLigne", back_populates="budget", cascade="all, delete-orphan")
    appels = db.relationship("AppelProjetBudget", back_populates="budget", cascade="all, delete-orphan")

    @property
    def total_charges(self):
        return round(sum(float(l.montant or 0) for l in self.lignes if l.nature == "charge"), 2)

    @property
    def total_produits(self):
        return round(sum(float(l.montant or 0) for l in self.lignes if l.nature == "produit"), 2)

    @property
    def solde(self):
        return round(float(self.total_produits or 0) - float(self.total_charges or 0), 2)


class BudgetPrevisionnelLigne(db.Model):
    __tablename__ = "budget_previsionnel_ligne"
    id = db.Column(db.Integer, primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey("budget_previsionnel.id"), nullable=False, index=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=True, index=True)

    nature = db.Column(db.String(10), nullable=False, default="charge")  # charge / produit
    compte = db.Column(db.String(20), nullable=False, default="60")
    libelle = db.Column(db.String(255), nullable=False)
    montant = db.Column(db.Float, default=0.0)
    commentaire = db.Column(db.Text, nullable=True)
    ordre = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    budget = db.relationship("BudgetPrevisionnel", back_populates="lignes")
    projet = db.relationship("Projet", backref=db.backref("lignes_previsionnelles", lazy="dynamic"))
    lignes_appel = db.relationship("AppelProjetBudgetLigne", back_populates="ligne_budget", cascade="all, delete-orphan")


class AppelProjetBudget(db.Model):
    __tablename__ = "appel_projet_budget"
    id = db.Column(db.Integer, primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey("budget_previsionnel.id"), nullable=False, index=True)
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id"), nullable=True, index=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=True, index=True)

    nom = db.Column(db.String(200), nullable=False)
    financeur = db.Column(db.String(200), nullable=True)
    statut = db.Column(db.String(30), nullable=False, default="preparation")
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    budget = db.relationship("BudgetPrevisionnel", back_populates="appels")
    subvention = db.relationship("Subvention", backref=db.backref("budgets_appel", lazy="dynamic"))
    projet = db.relationship("Projet", backref=db.backref("budgets_appel", lazy="dynamic"))
    lignes = db.relationship("AppelProjetBudgetLigne", back_populates="appel", cascade="all, delete-orphan")

    @property
    def total_charges(self):
        return round(sum(float(l.montant_retenu or 0) for l in self.lignes if l.ligne_budget and l.ligne_budget.nature == "charge"), 2)

    @property
    def total_produits(self):
        return round(sum(float(l.montant_retenu or 0) for l in self.lignes if l.ligne_budget and l.ligne_budget.nature == "produit"), 2)

    @property
    def solde(self):
        return round(float(self.total_produits or 0) - float(self.total_charges or 0), 2)


class AppelProjetBudgetLigne(db.Model):
    __tablename__ = "appel_projet_budget_ligne"
    id = db.Column(db.Integer, primary_key=True)
    appel_id = db.Column(db.Integer, db.ForeignKey("appel_projet_budget.id"), nullable=False, index=True)
    budget_ligne_id = db.Column(db.Integer, db.ForeignKey("budget_previsionnel_ligne.id"), nullable=False, index=True)
    montant_retenu = db.Column(db.Float, default=0.0)
    pourcentage_retenu = db.Column(db.Float, nullable=True)
    commentaire = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    appel = db.relationship("AppelProjetBudget", back_populates="lignes")
    ligne_budget = db.relationship("BudgetPrevisionnelLigne", back_populates="lignes_appel")

class SubventionProjet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=False)
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    projet = db.relationship("Projet", back_populates="subventions")
    subvention = db.relationship("Subvention", back_populates="projets")

    __table_args__ = (
        db.UniqueConstraint("projet_id", "subvention_id", name="uq_projet_subvention"),
    )



# ---------- LIENS PROJET <-> ATELIERS (activité) ----------
class ProjetAtelier(db.Model):
    __tablename__ = "projet_atelier"
    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=False, index=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    projet = db.relationship("Projet", backref=db.backref("ateliers", cascade="all, delete-orphan"))
    atelier = db.relationship("AtelierActivite")

    __table_args__ = (
        db.UniqueConstraint("projet_id", "atelier_id", name="uq_projet_atelier"),
    )


# ---------- INDICATEURS DE PROJET ----------
class ProjetIndicateur(db.Model):
    __tablename__ = "projet_indicateur"
    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id"), nullable=False, index=True)

    # template (V1)
    code = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    params_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    projet = db.relationship("Projet", backref=db.backref("indicateurs", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint("projet_id", "code", name="uq_projet_indicateur_code"),
    )

    def params(self):
        try:
            return json.loads(self.params_json or "{}")
        except Exception:
            return {}


class ProjetIndicateurValeur(db.Model):
    """Historique des valeurs saisies pour un indicateur (qui / quand / combien).

    Permet de tracer l'évolution d'un indicateur manuel et de répondre aux
    exigences d'audit (RGPD, contrôle financeur) : aucune valeur n'est écrasée
    sans laisser de trace.
    """
    __tablename__ = "projet_indicateur_valeur"

    id = db.Column(db.Integer, primary_key=True)
    indicateur_id = db.Column(db.Integer, db.ForeignKey("projet_indicateur.id", ondelete="CASCADE"), nullable=False, index=True)
    date_releve = db.Column(db.Date, nullable=False, default=date.today, index=True)
    valeur = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(30), nullable=False, default="manual")  # manual | stats
    commentaire = db.Column(db.Text, nullable=True)
    saisie_par_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    saisie_le = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    indicateur = db.relationship("ProjetIndicateur", backref=db.backref("historique", cascade="all, delete-orphan", order_by="ProjetIndicateurValeur.saisie_le.desc()"))
    user = db.relationship("User")


class ProjetJournalEntry(db.Model):
    __tablename__ = "projet_journal_entry"

    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    categorie = db.Column(db.String(40), nullable=False, default="fait_marquant", index=True)
    titre = db.Column(db.String(180), nullable=True)
    contenu = db.Column(db.Text, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    projet = db.relationship("Projet", back_populates="journal_entries")
    user = db.relationship("User")


class ProjetAction(db.Model):
    __tablename__ = "projet_action"

    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id", ondelete="CASCADE"), nullable=False, index=True)
    titre = db.Column(db.String(200), nullable=False)
    categorie = db.Column(db.String(50), nullable=False, default="atelier", index=True)
    statut = db.Column(db.String(30), nullable=False, default="prevue", index=True)
    referent = db.Column(db.String(160), nullable=True)
    date_debut = db.Column(db.Date, nullable=True, index=True)
    date_fin = db.Column(db.Date, nullable=True, index=True)
    lieu = db.Column(db.String(200), nullable=True)
    public_vise = db.Column(db.Text, nullable=True)
    territoire = db.Column(db.Text, nullable=True)
    objectifs = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    partenaires_text = db.Column(db.Text, nullable=True)
    bilan_qualitatif = db.Column(db.Text, nullable=True)
    # Contenu narratif de la « fiche action » au format imposé (JSON par section).
    # Permet de générer le document officiel pré-rempli avec les chiffres réels.
    fiche_json = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    projet = db.relationship("Projet", back_populates="actions_detaillees")
    user = db.relationship("User")
    atelier_links = db.relationship("ProjetActionAtelier", back_populates="action", cascade="all, delete-orphan")


class ProjetActionAtelier(db.Model):
    __tablename__ = "projet_action_atelier"

    id = db.Column(db.Integer, primary_key=True)
    action_id = db.Column(db.Integer, db.ForeignKey("projet_action.id", ondelete="CASCADE"), nullable=False, index=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    action = db.relationship("ProjetAction", back_populates="atelier_links")
    atelier = db.relationship("AtelierActivite")

    __table_args__ = (
        db.UniqueConstraint("action_id", "atelier_id", name="uq_projet_action_atelier"),
    )


class ProjetJalon(db.Model):
    """Échéance / jalon d'un projet (échéancier de pilotage)."""
    __tablename__ = "projet_jalon"

    id = db.Column(db.Integer, primary_key=True)
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id", ondelete="CASCADE"), nullable=False, index=True)
    libelle = db.Column(db.String(200), nullable=False)
    date_echeance = db.Column(db.Date, nullable=True, index=True)
    statut = db.Column(db.String(20), nullable=False, default="a_faire")  # a_faire | fait
    ordre = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    projet = db.relationship("Projet", backref=db.backref("jalons", cascade="all, delete-orphan", order_by="ProjetJalon.ordre.asc(), ProjetJalon.date_echeance.asc()"))




# ---------- SUBVENTIONS / BUDGET ----------
SUBVENTION_STATUTS = [
    ("sollicitee", "Sollicitée"),
    ("accordee", "Accordée"),
    ("versee", "Versée"),
    ("soldee", "Soldée"),
    ("refusee", "Refusée"),
]
SUBVENTION_STATUTS_DICT = dict(SUBVENTION_STATUTS)


class Subvention(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False)
    secteur = db.Column(db.String(80), nullable=False)
    annee_exercice = db.Column(db.Integer, nullable=False, default=2025)

    # Financeur (organisme qui verse) — distinct du nom du dispositif.
    financeur = db.Column(db.String(200), nullable=True)
    # Référence du dossier chez le financeur (n° convention, n° dossier...).
    reference = db.Column(db.String(120), nullable=True)

    # Cycle de vie du dossier (cf. SUBVENTION_STATUTS).
    statut_cycle = db.Column(db.String(30), nullable=False, default="sollicitee")

    # Échéances clés du dossier.
    date_depot = db.Column(db.Date, nullable=True)            # dépôt de la demande
    date_decision = db.Column(db.Date, nullable=True)         # notification / décision
    date_versement_prevu = db.Column(db.Date, nullable=True)  # versement attendu
    date_bilan_prevu = db.Column(db.Date, nullable=True)      # bilan / compte-rendu à rendre

    montant_demande = db.Column(db.Float, default=0.0)
    montant_attribue = db.Column(db.Float, default=0.0)
    montant_recu = db.Column(db.Float, default=0.0)

    est_archive = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def statut_label(self):
        return SUBVENTION_STATUTS_DICT.get(self.statut_cycle or "sollicitee", self.statut_cycle or "—")

    lignes = db.relationship("LigneBudget", backref="source_sub", cascade="all, delete-orphan")
    depense_affectations = db.relationship("DepenseAffectation", back_populates="subvention", cascade="all, delete-orphan")
    projets = db.relationship("SubventionProjet", back_populates="subvention", cascade="all, delete-orphan")

    @property
    def total_base_lignes(self):
        # compat: total des CHARGES (lignes nature=charge)
        return round(sum(float(l.montant_base or 0) for l in self.lignes if getattr(l, "nature", "charge") == "charge"), 2)

    @property
    def total_reel_lignes(self):
        # compat: total des CHARGES (lignes nature=charge)
        return round(sum(float(l.montant_reel or 0) for l in self.lignes if getattr(l, "nature", "charge") == "charge"), 2)


    @property
    def total_base_produits(self):
        return round(sum(float(l.montant_base or 0) for l in self.lignes if getattr(l, "nature", "charge") == "produit"), 2)

    @property
    def total_reel_produits(self):
        return round(sum(float(l.montant_reel or 0) for l in self.lignes if getattr(l, "nature", "charge") == "produit"), 2)

    @property
    def solde_base(self):
        # Produits - Charges
        return round(float(self.total_base_produits or 0) - float(self.total_base_lignes or 0), 2)

    @property
    def solde_reel(self):
        # Produits - Charges
        return round(float(self.total_reel_produits or 0) - float(self.total_reel_lignes or 0), 2)
    @property
    def total_engage(self):
        return round(sum(float(l.engage or 0) for l in self.lignes if getattr(l, "nature", "charge") == "charge"), 2)

    @property
    def total_reste(self):
        return round(sum(float(l.reste or 0) for l in self.lignes if getattr(l, "nature", "charge") == "charge"), 2)

    @property
    def total_impute_affectations(self):
        return round(sum(float(a.montant or 0) for a in self.depense_affectations if a.depense and not a.depense.est_supprimee), 2)

    @property
    def reste_imputable(self):
        base = float(self.montant_attribue or self.montant_recu or 0)
        if base <= 0:
            base = float(self.montant_demande or 0)
        return round(base - float(self.total_impute_affectations or 0), 2)


class LigneBudget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id"), nullable=False)

    # nature = charge (compte 6*) ou produit (compte 7*)
    nature = db.Column(db.String(10), nullable=False, default="charge")  # charge | produit

    compte = db.Column(db.String(20), nullable=False, default="60")
    libelle = db.Column(db.String(200), nullable=False)

    montant_base = db.Column(db.Float, default=0.0)
    montant_reel = db.Column(db.Float, default=0.0)

    created_at = db.Column(db.DateTime, default=utcnow)

    depenses = db.relationship("Depense", backref="budget_source", cascade="all, delete-orphan")
    depense_affectations = db.relationship("DepenseAffectation", back_populates="ligne_budget", cascade="all, delete-orphan")

    @property
    def engage(self):
        # engage / reste n'ont de sens que pour les CHARGES
        if getattr(self, "nature", "charge") != "charge":
            return 0.0

        # Nouveau modèle : on additionne les imputations rattachées à cette ligne.
        affecte = sum(
            float(a.montant or 0)
            for a in self.depense_affectations
            if a.depense and not a.depense.est_supprimee
        )

        # Compatibilité : les anciennes dépenses sans affectation explicite comptent encore à 100%.
        legacy = sum(
            float(d.montant or 0)
            for d in self.depenses
            if not d.est_supprimee and not getattr(d, "affectations", [])
        )
        return round(float(affecte or 0) + float(legacy or 0), 2)

    @property
    def reste(self):
        if getattr(self, "nature", "charge") != "charge":
            return 0.0
        return round(float(self.montant_reel or 0) - float(self.engage or 0), 2)


class Depense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ligne_budget_id = db.Column(db.Integer, db.ForeignKey("ligne_budget.id"), nullable=True)
    # Nouveau (AAP/Projets) : rattachement direct à une charge projet
    charge_projet_id = db.Column(db.Integer, db.ForeignKey("charge_projet.id", ondelete="SET NULL"), nullable=True)

    # Provenance facture / inventaire
    facture_ligne_id = db.Column(db.Integer, db.ForeignKey("facture_ligne.id", ondelete="SET NULL"), nullable=True)

    libelle = db.Column(db.String(255), nullable=False)
    montant = db.Column(db.Float, default=0.0)

    # infos finance-friendly (non obligatoires pour l’instant)
    fournisseur = db.Column(db.String(180), nullable=True)
    reference_piece = db.Column(db.String(120), nullable=True)  # n° facture / reçu / référence
    mode_paiement = db.Column(db.String(50), nullable=True)     # CB / Virement / Espèces / Autre

    date_paiement = db.Column(db.Date, nullable=True)
    type_depense = db.Column(db.String(80), default="Fonctionnement")

    # workflow / blindage
    statut = db.Column(db.String(30), nullable=False, default="valide")  # brouillon / valide
    anomalie = db.Column(db.String(255), nullable=True)
    est_supprimee = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=utcnow)

    documents = db.relationship("DepenseDocument", backref="depense", cascade="all, delete-orphan")
    affectations = db.relationship("DepenseAffectation", back_populates="depense", cascade="all, delete-orphan")
    inventaire_items = db.relationship("InventaireItem", backref="depense", passive_deletes=True)
    # relation SQLAlchemy (nécessaire pour back_populates depuis ChargeProjet)
    charge_projet = db.relationship("ChargeProjet", back_populates="depenses")

    @property
    def total_affecte(self):
        return round(sum(float(a.montant or 0) for a in self.affectations), 2)

    @property
    def reste_a_affecter(self):
        return round(float(self.montant or 0) - float(self.total_affecte or 0), 2)

    @property
    def statut_affectation(self):
        reste = float(self.reste_a_affecter or 0)
        if abs(reste) <= 0.01:
            return "ok"
        if reste > 0:
            return "partiel"
        return "depassement"


class DepenseAffectation(db.Model):
    __tablename__ = "depense_affectation"

    id = db.Column(db.Integer, primary_key=True)
    depense_id = db.Column(db.Integer, db.ForeignKey("depense.id", ondelete="CASCADE"), nullable=False, index=True)

    # source_type = subvention / fonds_propres / autre
    source_type = db.Column(db.String(30), nullable=False, default="subvention")
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id", ondelete="SET NULL"), nullable=True, index=True)
    ligne_budget_id = db.Column(db.Integer, db.ForeignKey("ligne_budget.id", ondelete="SET NULL"), nullable=True, index=True)

    libelle_source = db.Column(db.String(200), nullable=True)
    montant = db.Column(db.Float, nullable=False, default=0.0)
    commentaire = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    depense = db.relationship("Depense", back_populates="affectations")
    subvention = db.relationship("Subvention", back_populates="depense_affectations")
    ligne_budget = db.relationship("LigneBudget", back_populates="depense_affectations")

    @property
    def source_label(self):
        if self.source_type == "fonds_propres":
            return self.libelle_source or "Fonds propres"
        if self.subvention:
            return self.subvention.nom
        return self.libelle_source or "Source non renseignée"

    @property
    def ligne_label(self):
        if self.ligne_budget:
            return f"{self.ligne_budget.compte} — {self.ligne_budget.libelle}"
        return "—"


class DepenseDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    depense_id = db.Column(db.Integer, db.ForeignKey("depense.id"), nullable=False)

    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)

    uploaded_at = db.Column(db.DateTime, default=utcnow)


# ---------- FACTURES / INVENTAIRE ----------
class FactureAchat(db.Model):
    __tablename__ = "facture_achat"

    id = db.Column(db.Integer, primary_key=True)
    secteur_principal = db.Column(db.String(80), nullable=False)
    fournisseur = db.Column(db.String(180), nullable=True)
    reference_facture = db.Column(db.String(120), nullable=True)
    date_facture = db.Column(db.Date, nullable=True)

    statut = db.Column(db.String(30), nullable=False, default="brouillon")  # brouillon / valide

    filename = db.Column(db.String(255), nullable=True)
    original_name = db.Column(db.String(255), nullable=True)

    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    lignes = db.relationship("FactureLigne", backref="facture", cascade="all, delete-orphan")

    @property
    def total(self):
        return round(sum(float(l.montant_ligne or 0) for l in self.lignes), 2)


class FactureLigne(db.Model):
    __tablename__ = "facture_ligne"

    id = db.Column(db.Integer, primary_key=True)
    facture_id = db.Column(db.Integer, db.ForeignKey("facture_achat.id"), nullable=False)
    secteur = db.Column(db.String(80), nullable=False)

    financement_type = db.Column(db.String(30), nullable=False, default="subvention")  # subvention / fonds_propres / don / autre
    a_ventiler = db.Column(db.Boolean, default=False)

    libelle = db.Column(db.String(255), nullable=False)
    quantite = db.Column(db.Integer, nullable=False, default=1)
    prix_unitaire = db.Column(db.Float, default=0.0)
    montant_ligne = db.Column(db.Float, default=0.0)

    ligne_budget_id = db.Column(db.Integer, db.ForeignKey("ligne_budget.id"), nullable=True)
    # Nouveau (AAP/Projets) : rattachement direct à une charge projet
    charge_projet_id = db.Column(db.Integer, db.ForeignKey("charge_projet.id", ondelete="SET NULL"), nullable=True)
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow)

    depenses = db.relationship("Depense", backref="facture_ligne", passive_deletes=True)
    inventaire_items = db.relationship("InventaireItem", backref="facture_ligne", passive_deletes=True)


class InventaireItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    secteur = db.Column(db.String(80), nullable=False)
    id_interne = db.Column(db.String(64), nullable=False, unique=True)

    categorie = db.Column(db.String(120), nullable=True)
    designation = db.Column(db.String(255), nullable=False)
    marque = db.Column(db.String(120), nullable=True)
    modele = db.Column(db.String(120), nullable=True)

    quantite = db.Column(db.Integer, nullable=False, default=1)
    numero_serie = db.Column(db.String(180), nullable=True)
    etat = db.Column(db.String(50), nullable=False, default="OK")
    localisation = db.Column(db.String(255), nullable=True)

    valeur_unitaire = db.Column(db.Float, nullable=True)
    date_entree = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    facture_ligne_id = db.Column(db.Integer, db.ForeignKey("facture_ligne.id", ondelete="SET NULL"), nullable=True)
    depense_id = db.Column(db.Integer, db.ForeignKey("depense.id", ondelete="SET NULL"), nullable=True)

    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)


# ==========================================================
# ===============  ACTIVITÉ / ÉMARGEMENT  ==================
# ==========================================================

class Quartier(db.Model):
    __tablename__ = "quartier"
    id = db.Column(db.Integer, primary_key=True)
    ville = db.Column(db.String(80), nullable=False, default="Creil")
    nom = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_qpv = db.Column(db.Boolean, default=False)

    # Position du quartier sur la carte (centroïde). Renseignée par
    # géocodage automatique (BAN sur « nom, ville ») ou placement manuel.
    # geo_manuel=True protège un placement manuel d'un écrasement par l'auto.
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    geo_manuel = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (
        db.UniqueConstraint("ville", "nom", name="uq_quartier_ville_nom"),
    )


class Partenaire(db.Model):
    __tablename__ = "partenaire"
    id = db.Column(db.Integer, primary_key=True)

    nom = db.Column(db.String(180), nullable=False)
    contact_nom = db.Column(db.String(120), nullable=True)
    contact_prenom = db.Column(db.String(120), nullable=True)
    adresse = db.Column(db.String(255), nullable=True)

    email_contact = db.Column(db.String(180), nullable=True)
    email_general = db.Column(db.String(180), nullable=True)
    tel_contact = db.Column(db.String(60), nullable=True)
    tel_general = db.Column(db.String(60), nullable=True)

    description = db.Column(db.Text, nullable=True)
    competences_orientation_json = db.Column(db.Text, nullable=True)
    territoire_couvert = db.Column(db.Text, nullable=True)
    modalites_orientation = db.Column(db.Text, nullable=True)
    niveau_orientation = db.Column(db.String(40), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # --- Géolocalisation (carte des partenaires) -----------------------------
    # Coordonnées issues du géocodage de l'adresse (Base Adresse Nationale).
    # Facultatif : NULL = partenaire non localisé. ``geocode_query`` mémorise
    # la dernière adresse résolue (détection de changement).
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    geocode_precision = db.Column(db.String(20), nullable=True)
    geocode_score = db.Column(db.Float, nullable=True)
    geocoded_at = db.Column(db.DateTime, nullable=True)
    geocode_query = db.Column(db.String(255), nullable=True)

    secteurs = db.relationship("PartenaireSecteur", backref="partenaire", cascade="all, delete-orphan")
    interventions = db.relationship(
        "PartenaireIntervention",
        backref="partenaire",
        cascade="all, delete-orphan",
        order_by="desc(PartenaireIntervention.date_intervention)",
    )
    orientations = db.relationship("OrientationAccesDroit", back_populates="partenaire", passive_deletes=True)

    def competences_orientation(self):
        try:
            raw = json.loads(self.competences_orientation_json or "[]")
        except Exception:
            raw = []
        return [str(item).strip() for item in raw if str(item or "").strip()]


class PartenaireSecteur(db.Model):
    __tablename__ = "partenaire_secteur"
    id = db.Column(db.Integer, primary_key=True)
    partenaire_id = db.Column(db.Integer, db.ForeignKey("partenaire.id", ondelete="CASCADE"), nullable=False)
    secteur = db.Column(db.String(80), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("partenaire_id", "secteur", name="uq_partenaire_secteur"),
    )


class PartenaireIntervention(db.Model):
    __tablename__ = "partenaire_intervention"
    id = db.Column(db.Integer, primary_key=True)
    partenaire_id = db.Column(db.Integer, db.ForeignKey("partenaire.id", ondelete="CASCADE"), nullable=False)
    secteur = db.Column(db.String(80), nullable=True)
    date_intervention = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class OrientationAccesDroit(db.Model):
    __tablename__ = "orientation_acces_droit"

    id = db.Column(db.Integer, primary_key=True)
    date_orientation = db.Column(db.Date, nullable=False, default=date.today, index=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    ville = db.Column(db.String(120), nullable=True, index=True)
    domaine = db.Column(db.String(80), nullable=False, index=True)
    demande = db.Column(db.String(255), nullable=False)
    statut = db.Column(db.String(40), nullable=False, default="oriente", index=True)
    urgence = db.Column(db.String(30), nullable=False, default="normale", index=True)
    suite_prevue = db.Column(db.Date, nullable=True, index=True)
    note = db.Column(db.Text, nullable=True)
    demandeur_libre = db.Column(db.String(180), nullable=True)

    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True, index=True)
    partenaire_id = db.Column(db.Integer, db.ForeignKey("partenaire.id", ondelete="SET NULL"), nullable=True, index=True)
    quartier_id = db.Column(db.Integer, db.ForeignKey("quartier.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    participant = db.relationship("Participant", backref=db.backref("orientations_acces_droit", lazy="dynamic"))
    partenaire = db.relationship("Partenaire", back_populates="orientations")
    quartier = db.relationship("Quartier")
    user = db.relationship("User")


class SuiviRappel(db.Model):
    __tablename__ = "suivi_rappel"

    id = db.Column(db.Integer, primary_key=True)
    titre = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    categorie = db.Column(db.String(40), nullable=False, default="general", index=True)
    priorite = db.Column(db.String(20), nullable=False, default="warn", index=True)
    statut = db.Column(db.String(20), nullable=False, default="ouvert", index=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    echeance = db.Column(db.Date, nullable=True, index=True)
    lien_url = db.Column(db.String(500), nullable=True)
    is_private = db.Column(db.Boolean, default=False, nullable=False, index=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    done_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User")


QUESTIONNAIRE_TYPES = [
    ("autre", "Autre"),
    ("satisfaction", "Satisfaction"),
    ("avant", "Avant (état initial)"),
    ("apres", "Après (état final)"),
]
QUESTIONNAIRE_TYPES_DICT = dict(QUESTIONNAIRE_TYPES)


class Questionnaire(db.Model):
    __tablename__ = "questionnaire"
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    # Nature du questionnaire (satisfaction / avant / après…) pour le dépouillement.
    type_questionnaire = db.Column(db.String(30), nullable=False, default="autre")
    # Rattachement optionnel à un projet (mesure d'impact d'un projet).
    projet_id = db.Column(db.Integer, db.ForeignKey("projet.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    projet = db.relationship("Projet")

    @property
    def type_label(self):
        return QUESTIONNAIRE_TYPES_DICT.get(self.type_questionnaire or "autre", self.type_questionnaire or "—")

    secteurs = db.relationship("QuestionnaireSecteur", backref="questionnaire", cascade="all, delete-orphan")
    ateliers = db.relationship("QuestionnaireAtelier", backref="questionnaire", cascade="all, delete-orphan")
    questions = db.relationship("Question", backref="questionnaire", cascade="all, delete-orphan")
    response_groups = db.relationship("QuestionnaireResponseGroup", backref="questionnaire", cascade="all, delete-orphan")


class QuestionnaireSecteur(db.Model):
    __tablename__ = "questionnaire_secteur"
    id = db.Column(db.Integer, primary_key=True)
    questionnaire_id = db.Column(db.Integer, db.ForeignKey("questionnaire.id", ondelete="CASCADE"), nullable=False)
    secteur = db.Column(db.String(80), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("questionnaire_id", "secteur", name="uq_questionnaire_secteur"),
    )


class QuestionnaireAtelier(db.Model):
    __tablename__ = "questionnaire_atelier"
    id = db.Column(db.Integer, primary_key=True)
    questionnaire_id = db.Column(db.Integer, db.ForeignKey("questionnaire.id", ondelete="CASCADE"), nullable=False)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("questionnaire_id", "atelier_id", name="uq_questionnaire_atelier"),
    )


class Question(db.Model):
    __tablename__ = "question"
    id = db.Column(db.Integer, primary_key=True)
    questionnaire_id = db.Column(db.Integer, db.ForeignKey("questionnaire.id", ondelete="CASCADE"), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(30), nullable=False, default="text")  # scale/yesno/multi/text
    is_required = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    options_json = db.Column(db.Text, nullable=True)

    responses = db.relationship("QuestionResponse", backref="question", cascade="all, delete-orphan")


class QuestionnaireResponseGroup(db.Model):
    __tablename__ = "questionnaire_response_group"
    id = db.Column(db.Integer, primary_key=True)
    questionnaire_id = db.Column(db.Integer, db.ForeignKey("questionnaire.id", ondelete="CASCADE"), nullable=False)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id"), nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=True)
    secteur = db.Column(db.String(80), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    participant = db.relationship("Participant")
    session = db.relationship("SessionActivite")
    atelier = db.relationship("AtelierActivite")


class QuestionResponse(db.Model):
    __tablename__ = "question_response"
    id = db.Column(db.Integer, primary_key=True)
    response_group_id = db.Column(
        db.Integer,
        db.ForeignKey("questionnaire_response_group.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id = db.Column(db.Integer, db.ForeignKey("question.id", ondelete="CASCADE"), nullable=False)
    value_text = db.Column(db.Text, nullable=True)
    value_number = db.Column(db.Float, nullable=True)
    value_json = db.Column(db.Text, nullable=True)

    response_group = db.relationship("QuestionnaireResponseGroup")


class Participant(db.Model):
    __tablename__ = "participant"
    id = db.Column(db.Integer, primary_key=True)

    nom = db.Column(db.String(120), nullable=False)
    prenom = db.Column(db.String(120), nullable=False)
    adresse = db.Column(db.String(255), nullable=True)
    ville = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(180), nullable=True)
    telephone = db.Column(db.String(60), nullable=True)
    genre = db.Column(db.String(20), nullable=True)
    date_naissance = db.Column(db.Date, nullable=True)
    # Champs legacy insertion : conservés temporairement pour migration douce.
    # La source cible doit désormais vivre dans les tables dédiées du module insertion.
    pays_origine = db.Column(db.String(120), nullable=True)
    titre_sejour_type = db.Column(db.String(120), nullable=True)
    date_entree_dispositif = db.Column(db.Date, nullable=True)
    date_sortie_dispositif = db.Column(db.Date, nullable=True)
    diplome_obtenu = db.Column(db.String(180), nullable=True)
    cir_obtenu = db.Column(db.Boolean, nullable=True)

    # Code apprenant sur le portail externe (idempotent : 1 code par participant).
    portail_code = db.Column(db.String(120), nullable=True, index=True)

    # Type de public (ex: H/S/B/A/P). Par défaut: H
    type_public = db.Column(db.String(2), nullable=False, default="H")

    # Bénévole du centre (SENACS « vitalité démocratique », valorisation compte 87).
    est_benevole = db.Column(db.Boolean, nullable=False, default=False, index=True)

    quartier_id = db.Column(db.Integer, db.ForeignKey("quartier.id"), nullable=True)
    quartier = db.relationship("Quartier")

    # --- Droit à l'image (consentement RGPD dématérialisé) ---
    # Statuts: non_renseigne / accepte / refuse.
    # Preuve dématérialisée = statut + date + agent qui l'a recueilli.
    droit_image_statut = db.Column(db.String(20), nullable=False, default="non_renseigne")
    droit_image_date = db.Column(db.Date, nullable=True)
    droit_image_recueilli_par = db.Column(db.String(180), nullable=True)

    # Foyer (famille) : rapproche des participants pour l'adhésion familiale.
    foyer_id = db.Column(db.Integer, db.ForeignKey("foyer.id"), nullable=True, index=True)
    foyer = db.relationship("Foyer", back_populates="membres")

    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # Pour permettre la création "en avance" (avant toute présence) tout en respectant
    # le cloisonnement par secteur en rôle responsable_secteur.
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_secteur = db.Column(db.String(80), nullable=True)

    # --- Géolocalisation (carte des habitants) -------------------------------
    # Coordonnées issues du géocodage de l'adresse via la Base Adresse
    # Nationale. ENTIÈREMENT FACULTATIF : NULL = participant « non localisé »
    # (jamais bloquant). ``geocode_query`` mémorise la dernière adresse résolue
    # (détection de changement, évite de re-géocoder inutilement) et
    # ``geocoded_at`` l'horodatage de la dernière résolution.
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    geocode_precision = db.Column(db.String(20), nullable=True)
    geocode_score = db.Column(db.Float, nullable=True)
    geocoded_at = db.Column(db.DateTime, nullable=True)
    geocode_query = db.Column(db.String(255), nullable=True)

    @property
    def is_creil(self):
        return (self.ville or "").strip().lower() == "creil"

    @property
    def is_qpv(self):
        if not self.quartier:
            return False
        if self.quartier.is_qpv:
            return True
        ville = (self.quartier.ville or "").strip().lower()
        nom = (self.quartier.nom or "").strip().lower()
        if "qpv" in nom:
            return True
        if ville == "creil" and ("rouher" in nom or "hauts de creil" in nom):
            return True
        return False

    def age_au(self, reference: date | None = None):
        """Âge révolu à une date de référence (par défaut aujourd'hui).

        Utilisé par les bilans et indicateurs pour figer l'âge à la fin de la
        période analysée : un bilan d'une année passée ne doit pas dériver avec
        le temps. Pour une période = année civile, l'âge au 31/12 coïncide avec
        la convention SENACS (année - année de naissance)."""
        if not self.date_naissance:
            return None
        reference = reference or date.today()
        years = reference.year - self.date_naissance.year
        if (reference.month, reference.day) < (self.date_naissance.month, self.date_naissance.day):
            years -= 1
        return years

    @property
    def age(self):
        return self.age_au(date.today())

    @property
    def current_insertion_parcours(self):
        today = date.today()
        rows = sorted(
            list(getattr(self, "insertion_parcours", []) or []),
            key=lambda row: ((row.date_entree or date.min), row.id or 0),
            reverse=True,
        )
        for row in rows:
            start_ok = row.date_entree is None or row.date_entree <= today
            end_ok = row.date_sortie is None or row.date_sortie >= today
            if start_ok and end_ok:
                return row
        return rows[0] if rows else None


    @property
    def current_insertion_positionnement(self):
        rows = sorted(
            list(getattr(self, "insertion_positionnements", []) or []),
            key=lambda row: ((row.date_positionnement or date.min), row.id or 0),
            reverse=True,
        )
        return rows[0] if rows else None


class InsertionDispositifRef(db.Model):
    __tablename__ = "insertion_dispositif_ref"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(180), nullable=False, unique=True, index=True)
    code = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<InsertionDispositifRef {self.label}>"


class InsertionPrescripteurRef(db.Model):
    __tablename__ = "insertion_prescripteur_ref"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(180), nullable=False, unique=True, index=True)
    code = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<InsertionPrescripteurRef {self.label}>"


class InsertionTitreSejourTypeRef(db.Model):
    __tablename__ = "insertion_titre_sejour_type_ref"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(180), nullable=False, unique=True, index=True)
    code = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<InsertionTitreSejourTypeRef {self.label}>"


class InsertionDiplomeRef(db.Model):
    __tablename__ = "insertion_diplome_ref"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(180), nullable=False, unique=True, index=True)
    code = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<InsertionDiplomeRef {self.label}>"


class InsertionNiveauRef(db.Model):
    __tablename__ = "insertion_niveau_ref"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(180), nullable=False, unique=True, index=True)
    code = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<InsertionNiveauRef {self.label}>"


class ParticipantInsertionProfile(db.Model):
    __tablename__ = "participant_insertion_profile"
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), primary_key=True)
    pays_origine = db.Column(db.String(120), nullable=True)
    cir_obtenu = db.Column(db.Boolean, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    participant = db.relationship(
        "Participant",
        backref=db.backref("insertion_profile", uselist=False, cascade="all, delete-orphan", passive_deletes=True),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_user_id])


class ParticipantInsertionParcours(db.Model):
    __tablename__ = "participant_insertion_parcours"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    dispositif_id = db.Column(db.Integer, db.ForeignKey("insertion_dispositif_ref.id"), nullable=True)
    prescripteur_id = db.Column(db.Integer, db.ForeignKey("insertion_prescripteur_ref.id"), nullable=True)
    titre_sejour_type_id = db.Column(db.Integer, db.ForeignKey("insertion_titre_sejour_type_ref.id"), nullable=True)
    date_debut_titre_sejour = db.Column(db.Date, nullable=True)
    date_expiration_titre_sejour = db.Column(db.Date, nullable=True)
    date_entree = db.Column(db.Date, nullable=True)
    date_sortie = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    legacy_source = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    participant = db.relationship(
        "Participant",
        backref=db.backref("insertion_parcours", cascade="all, delete-orphan", passive_deletes=True),
    )
    dispositif = db.relationship("InsertionDispositifRef", backref=db.backref("parcours", lazy=True))
    prescripteur = db.relationship("InsertionPrescripteurRef", backref=db.backref("parcours", lazy=True))
    titre_sejour_type = db.relationship("InsertionTitreSejourTypeRef", backref=db.backref("parcours", lazy=True))
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_user_id])


class ParticipantInsertionPositionnement(db.Model):
    __tablename__ = "participant_insertion_positionnement"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    parcours_id = db.Column(db.Integer, db.ForeignKey("participant_insertion_parcours.id", ondelete="SET NULL"), nullable=True, index=True)
    niveau_id = db.Column(db.Integer, db.ForeignKey("insertion_niveau_ref.id"), nullable=True)
    date_positionnement = db.Column(db.Date, nullable=True)
    type_positionnement = db.Column(db.String(32), nullable=False, default="entree")
    commentaire = db.Column(db.Text, nullable=True)
    legacy_source = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    participant = db.relationship(
        "Participant",
        backref=db.backref("insertion_positionnements", cascade="all, delete-orphan", passive_deletes=True),
    )
    parcours = db.relationship("ParticipantInsertionParcours", backref=db.backref("positionnements", lazy=True))
    niveau = db.relationship("InsertionNiveauRef", backref=db.backref("positionnements", lazy=True))
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_user_id])


class ParticipantInsertionCertification(db.Model):
    __tablename__ = "participant_insertion_certification"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    parcours_id = db.Column(db.Integer, db.ForeignKey("participant_insertion_parcours.id", ondelete="SET NULL"), nullable=True, index=True)
    diplome_id = db.Column(db.Integer, db.ForeignKey("insertion_diplome_ref.id"), nullable=True)
    niveau_id = db.Column(db.Integer, db.ForeignKey("insertion_niveau_ref.id"), nullable=True)
    date_passage = db.Column(db.Date, nullable=True)
    date_obtention = db.Column(db.Date, nullable=True)
    resultat = db.Column(db.String(32), nullable=True)
    commentaire = db.Column(db.Text, nullable=True)
    legacy_source = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    participant = db.relationship(
        "Participant",
        backref=db.backref("insertion_certifications", cascade="all, delete-orphan", passive_deletes=True),
    )
    parcours = db.relationship("ParticipantInsertionParcours", backref=db.backref("certifications", lazy=True))
    diplome = db.relationship("InsertionDiplomeRef", backref=db.backref("certifications", lazy=True))
    niveau = db.relationship("InsertionNiveauRef", backref=db.backref("certifications", lazy=True))
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_user_id])


class AtelierActivite(db.Model):
    __tablename__ = "atelier_activite"
    id = db.Column(db.Integer, primary_key=True)
    secteur = db.Column(db.String(80), nullable=False, index=True)
    nom = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    type_atelier = db.Column(db.String(30), nullable=False, default="COLLECTIF")
    # COLLECTIF: nb places. INDIVIDUEL_MENSUEL: heures dispo / mois.
    capacite_defaut = db.Column(db.Integer, nullable=True)
    heures_dispo_defaut_mois = db.Column(db.Float, nullable=True)
    duree_defaut_minutes = db.Column(db.Integer, nullable=True)

    motifs_json = db.Column(db.Text, nullable=True)  # liste JSON de motifs (dropdown)

    # Atelier intersecteur : visible et utilisable par TOUS les secteurs
    # (animation globale, temps forts partagés). Le champ ``secteur`` devient
    # alors le secteur d'IMPUTATION des statistiques, choisi à la création —
    # pas forcément celui de la personne qui crée.
    est_intersecteur = db.Column(db.Boolean, nullable=False, default=False, index=True)

    modele_docx_collectif = db.Column(db.String(255), nullable=True)
    modele_docx_individuel = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)

    # Statut métier + soft-delete
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    # Continuité statistique (ex: atelier renommé d'une année à l'autre)
    continuity_parent_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=True, index=True)
    continuity_parent = db.relationship(
        "AtelierActivite",
        remote_side=[id],
        foreign_keys=[continuity_parent_id],
        post_update=True,
        backref=db.backref("continuity_children", lazy="dynamic"),
    )

    sessions = db.relationship("SessionActivite", backref="atelier", cascade="all, delete-orphan")
    competences = db.relationship(
        "Competence",
        secondary=atelier_competence,
        backref=db.backref("ateliers", lazy="dynamic"),
    )
    modules = db.relationship(
        "PedagogieModule",
        secondary=atelier_module,
        backref=db.backref("ateliers", lazy="dynamic"),
    )

    def motifs(self):
        try:
            return json.loads(self.motifs_json or "[]")
        except Exception:
            return []


class SessionActivite(db.Model):
    __tablename__ = "session_activite"
    id = db.Column(db.Integer, primary_key=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=False)
    secteur = db.Column(db.String(80), nullable=False, index=True)
    session_type = db.Column(db.String(30), nullable=False, default="COLLECTIF")
    # COLLECTIF
    date_session = db.Column(db.Date, nullable=True, index=True)
    heure_debut = db.Column(db.String(10), nullable=True)
    heure_fin = db.Column(db.String(10), nullable=True)
    capacite = db.Column(db.Integer, nullable=True)
    statut = db.Column(db.String(20), nullable=False, default="realisee")  # realisee / annulee

    # INDIVIDUEL_MENSUEL (rdv)
    rdv_date = db.Column(db.Date, nullable=True, index=True)
    rdv_debut = db.Column(db.String(10), nullable=True)
    rdv_fin = db.Column(db.String(10), nullable=True)
    duree_minutes = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)
    consommation_config_id = db.Column(
        db.Integer,
        db.ForeignKey("materiel_consommation_config.id"),
        nullable=True,
        index=True,
    )

    # Soft-delete (safe during tests)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    # KIOSQUE (public) : émargement via /kiosk sans exposer l'app complète
    kiosk_open = db.Column(db.Boolean, default=False, index=True)
    kiosk_pin = db.Column(db.String(10), nullable=True, index=True)
    kiosk_token = db.Column(db.String(64), nullable=True, index=True)
    kiosk_opened_at = db.Column(db.DateTime, nullable=True)

    # Pont manuel CSAT (portail sans API) : date de dernière inclusion dans
    # l'export « Sessions ». NULL = jamais exportée, remonte dans le prochain export.
    exported_csat_at = db.Column(db.DateTime, nullable=True)

    # Séance « événementielle » (fête de quartier, temps fort, sortie…) :
    # comptée à part dans le volet événementiel du SENACS.
    est_evenement = db.Column(db.Boolean, nullable=False, default=False, index=True)

    presences = db.relationship("PresenceActivite", backref="session", cascade="all, delete-orphan")
    competences = db.relationship(
        "Competence",
        secondary=session_competence,
        backref=db.backref("sessions", lazy="dynamic"),
    )
    modules = db.relationship(
        "PedagogieModule",
        secondary=session_module,
        backref=db.backref("sessions", lazy="dynamic"),
    )
    materiels = db.relationship("SessionMateriel", backref="session", cascade="all, delete-orphan")
    consommation_config = db.relationship(
        "MaterielConsommationConfig",
        backref="sessions",
        lazy="joined",
    )

    @property
    def date_reference(self):
        return self.rdv_date or self.date_session

    @property
    def duree_heures(self):
        if self.duree_minutes:
            return self.duree_minutes / 60

        if self.heure_debut and self.heure_fin:
            try:
                h1, m1 = map(int, self.heure_debut.split(":"))
                h2, m2 = map(int, self.heure_fin.split(":"))
                return ((h2 * 60 + m2) - (h1 * 60 + m1)) / 60
            except Exception:
                return 1

        return 1


class MaterielType(db.Model):
    __tablename__ = "materiel_type"
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    actif = db.Column(db.Boolean, default=True, nullable=False)
    ordre = db.Column(db.Integer, default=0, nullable=False)


class MaterielConsommationConfig(db.Model):
    __tablename__ = "materiel_consommation_config"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(120), nullable=False)
    date_debut = db.Column(db.Date, nullable=False, index=True)
    date_fin = db.Column(db.Date, nullable=True, index=True)
    co2_kg_par_kwh = db.Column(db.Float, nullable=False, default=0.06)
    actif = db.Column(db.Boolean, default=True, nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    lignes = db.relationship("MaterielConsommationLigne", backref="config", cascade="all, delete-orphan")


class MaterielConsommationLigne(db.Model):
    __tablename__ = "materiel_consommation_ligne"
    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(db.Integer, db.ForeignKey("materiel_consommation_config.id"), nullable=False, index=True)
    materiel_id = db.Column(db.Integer, db.ForeignKey("materiel_type.id"), nullable=False, index=True)
    watts = db.Column(db.Float, nullable=False, default=0.0)
    materiel = db.relationship("MaterielType")


class SessionMateriel(db.Model):
    __tablename__ = "session_materiel"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=False, index=True)
    materiel_id = db.Column(db.Integer, db.ForeignKey("materiel_type.id"), nullable=False, index=True)
    quantite = db.Column(db.Integer, nullable=False, default=1)
    materiel = db.relationship("MaterielType")


class SessionScheduleEditLog(db.Model):
    __tablename__ = "session_schedule_edit_log"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="SET NULL"), nullable=True, index=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)

    old_date = db.Column(db.Date, nullable=True)
    old_start = db.Column(db.String(10), nullable=True)
    old_end = db.Column(db.String(10), nullable=True)

    new_date = db.Column(db.Date, nullable=True)
    new_start = db.Column(db.String(10), nullable=True)
    new_end = db.Column(db.String(10), nullable=True)

    reason = db.Column(db.Text, nullable=False)
    edited_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    edited_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    session = db.relationship("SessionActivite")
    atelier = db.relationship("AtelierActivite")
    editor = db.relationship("User")

class AtelierCapaciteMois(db.Model):
    __tablename__ = "atelier_capacite_mois"
    id = db.Column(db.Integer, primary_key=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=False)
    annee = db.Column(db.Integer, nullable=False)
    mois = db.Column(db.Integer, nullable=False)
    heures_dispo = db.Column(db.Float, nullable=False, default=0.0)
    locked = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint("atelier_id", "annee", "mois", name="uq_atelier_capacite_mois"),
    )


class PresenceActivite(db.Model):
    __tablename__ = "presence_activite"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=False)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id"), nullable=False)
    participant = db.relationship("Participant")
    consommations_materiel = db.relationship(
        "PresenceMaterielConsommation",
        backref="presence",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Motif (liste + autre)
    motif = db.Column(db.String(180), nullable=True)
    motif_autre = db.Column(db.String(255), nullable=True)

    # Statut de présence : présent / en retard / absent excusé.
    presence_type = db.Column(db.String(20), nullable=False, default="present")

    # signature: stockée en fichier (temp), ici juste le chemin
    signature_path = db.Column(db.String(255), nullable=True)

    # Signature à distance : jeton personnel à usage unique envoyé au
    # participant (lien /signer/<jeton>). Effacé dès que la signature est
    # posée — le lien ne fonctionne qu'une fois.
    signature_token = db.Column(db.String(64), nullable=True, unique=True, index=True)

    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("session_id", "participant_id", name="uq_presence_session_participant"),
    )


STATUTS_INSCRIPTION = ["inscrit", "attente", "annule"]
STATUTS_INSCRIPTION_LABELS = {
    "inscrit": "Inscrit·e",
    "attente": "Liste d'attente",
    "annule": "Annulée",
}


class InscriptionActivite(db.Model):
    """Inscription PRÉALABLE à une activité — l'étage « avant la séance ».

    Deux portées :
    - ``session_id`` renseigné : inscription à une séance précise (sortie,
      événement à places limitées) ;
    - ``session_id`` vide : inscription à l'atelier pour la période (les
      habitué·es attendu·es à chaque séance).

    La jauge s'appuie sur la capacité existante (séance, sinon atelier) :
    au-delà, l'inscription bascule en liste d'attente ; une annulation
    promeut automatiquement le plus ancien de la liste d'attente.
    L'émargement se pré-remplit depuis les inscrits (« pointer présent »).
    """
    __tablename__ = "inscription_activite"

    id = db.Column(db.Integer, primary_key=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=True, index=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)

    statut = db.Column(db.String(20), nullable=False, default="inscrit", index=True)
    commentaire = db.Column(db.String(255), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    atelier = db.relationship(
        "AtelierActivite",
        backref=db.backref("inscriptions", cascade="all, delete-orphan", passive_deletes=True),
    )
    session = db.relationship(
        "SessionActivite",
        backref=db.backref("inscriptions", cascade="all, delete-orphan", passive_deletes=True),
    )
    participant = db.relationship(
        "Participant",
        backref=db.backref("inscriptions_activite", cascade="all, delete-orphan", passive_deletes=True),
    )

    __table_args__ = (
        db.Index("ix_inscription_activite_cible", "atelier_id", "session_id", "statut"),
    )

    @property
    def statut_label(self):
        return STATUTS_INSCRIPTION_LABELS.get(self.statut, self.statut)


class PresenceMaterielConsommation(db.Model):
    """Consommation individuelle figée pour une présence.

    Le référentiel matériel peut évoluer dans le temps. Les watts, la durée,
    les kWh et le CO2 sont donc copiés au moment du calcul pour que les
    anciennes feuilles d'émargement ne changent pas rétroactivement.
    """

    __tablename__ = "presence_materiel_consommation"

    id = db.Column(db.Integer, primary_key=True)
    presence_id = db.Column(db.Integer, db.ForeignKey("presence_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    materiel_id = db.Column(db.Integer, db.ForeignKey("materiel_type.id", ondelete="SET NULL"), nullable=True, index=True)

    quantite = db.Column(db.Integer, nullable=False, default=1)
    materiel_nom_snapshot = db.Column(db.String(120), nullable=True)
    watts_snapshot = db.Column(db.Float, nullable=False, default=0.0)
    duree_minutes_snapshot = db.Column(db.Integer, nullable=False, default=60)
    kwh_snapshot = db.Column(db.Float, nullable=False, default=0.0)
    co2_kg_snapshot = db.Column(db.Float, nullable=False, default=0.0)
    co2_kg_par_kwh_snapshot = db.Column(db.Float, nullable=False, default=0.06)
    mode_calcul = db.Column(db.String(40), nullable=False, default="manuel")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    materiel = db.relationship("MaterielType")
    session = db.relationship("SessionActivite")
    participant = db.relationship("Participant")

    __table_args__ = (
        db.UniqueConstraint("presence_id", "materiel_id", "mode_calcul", name="uq_presence_materiel_conso_ligne"),
    )


class Evaluation(db.Model):
    __tablename__ = "evaluation"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id"), nullable=False)
    competence_id = db.Column(db.Integer, db.ForeignKey("competence.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    etat = db.Column(db.Integer, nullable=False, default=0)  # 0=Non acquis, 1=En cours, 2=Acquis, 3=Expert
    date_evaluation = db.Column(db.Date, nullable=False, default=date.today)
    commentaire = db.Column(db.Text, nullable=True)

    participant = db.relationship("Participant")
    competence = db.relationship("Competence")
    session = db.relationship("SessionActivite")
    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("participant_id", "competence_id", "session_id", name="uq_eval_participant_competence_session"),
    )


class PasseportNote(db.Model):
    __tablename__ = "passeport_note"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="SET NULL"), nullable=True, index=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    categorie = db.Column(db.String(60), nullable=False, default="journal", index=True)
    contenu = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    participant = db.relationship("Participant")
    session = db.relationship("SessionActivite")
    user = db.relationship("User")


class PasseportPieceJointe(db.Model):
    __tablename__ = "passeport_piece_jointe"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="SET NULL"), nullable=True, index=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    categorie = db.Column(db.String(60), nullable=False, default="atelier", index=True)
    titre = db.Column(db.String(255), nullable=True)
    file_path = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    participant = db.relationship("Participant")
    session = db.relationship("SessionActivite")
    user = db.relationship("User")


class ObjectifSuivi(db.Model):
    __tablename__ = "objectif_suivi"

    id = db.Column(db.Integer, primary_key=True)
    objectif_id = db.Column(db.Integer, db.ForeignKey("objectif.id"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=False, index=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id"), nullable=True, index=True)

    mode = db.Column(db.String(20), nullable=False, default="ressenti")  # ressenti | competence | mixte
    etat = db.Column(db.Integer, nullable=False, default=1)  # 0=non atteint,1=en progression,2=atteint,3=depasse
    ressenti = db.Column(db.Integer, nullable=True)  # 1..5 (optionnel)
    commentaire = db.Column(db.Text, nullable=True)

    date_saisie = db.Column(db.Date, nullable=False, default=date.today, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    objectif = db.relationship("Objectif")
    session = db.relationship("SessionActivite")
    participant = db.relationship("Participant")
    user = db.relationship("User")


class ArchiveEmargement(db.Model):
    __tablename__ = "archive_emargement"
    id = db.Column(db.Integer, primary_key=True)
    secteur = db.Column(db.String(80), nullable=False, index=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id"), nullable=False)
    atelier = db.relationship("AtelierActivite")
    # pour collectif : session_id ; pour individuel mensuel : null
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id"), nullable=True)
    annee = db.Column(db.Integer, nullable=False)
    mois = db.Column(db.Integer, nullable=True)

    docx_path = db.Column(db.String(255), nullable=True)
    pdf_path = db.Column(db.String(255), nullable=True)

    # Option : version corrigée manuellement (upload après édition Word)
    corrected_docx_path = db.Column(db.String(255), nullable=True)
    corrected_pdf_path = db.Column(db.String(255), nullable=True)

    # Suivi envoi mail
    last_emailed_to = db.Column(db.String(255), nullable=True)
    last_emailed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open/locked
    created_at = db.Column(db.DateTime, default=utcnow)



class PeriodeFinancement(db.Model):
    """Périodes enregistrées (souvent calées sur un financeur) pour filtrer les stats.

    - sectorisée : une période peut être rattachée à un secteur (ou globale si secteur=None)
    - RGPD : ne contient pas de données personnelles
    """
    __tablename__ = "periode_financement"
    id = db.Column(db.Integer, primary_key=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)  # None = global
    nom = db.Column(db.String(255), nullable=False)
    date_debut = db.Column(db.Date, nullable=False, index=True)
    date_fin = db.Column(db.Date, nullable=False, index=True)

    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)

    def __repr__(self):
        return f"<PeriodeFinancement {self.id} {self.nom} {self.date_debut}..{self.date_fin} secteur={self.secteur}>"


class BilanLourdNarratif(db.Model):
    __tablename__ = "bilan_lourd_narratif"

    id = db.Column(db.Integer, primary_key=True)
    annee = db.Column(db.Integer, nullable=False, index=True)
    secteur = db.Column(db.String(80), nullable=False, index=True)

    faits_marquants = db.Column(db.Text, nullable=True)
    difficultes = db.Column(db.Text, nullable=True)
    perspectives = db.Column(db.Text, nullable=True)
    photos_json = db.Column(db.Text, nullable=True)
    timeline_json = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint("annee", "secteur", name="uq_bilan_lourd_narratif_annee_secteur"),
    )

# ---------------------------------------------------------------------
# RBAC COMPAT: provide User.role property
# ---------------------------------------------------------------------
# Depuis l'introduction du RBAC (User.roles), certaines branches/test DB
# peuvent ne plus avoir la colonne legacy `User.role`. Or beaucoup de routes
# utilisent encore `current_user.role`.
#
# Objectif: éviter les 500/AttributeError en fournissant un fallback READ-ONLY
# (et une compat "directrice" vs "direction") tant que la migration complète
# n'est pas terminée.
#
# IMPORTANT: on n'écrase PAS un attribut SQLAlchemy existant. On ne crée ce
# fallback QUE si `User.role` n'existe pas déjà.
# ---------------------------------------------------------------------

def _role_compat_get(u) -> str:
    # 1) si une colonne legacy existe (cas ancien), on la privilégie
    legacy = getattr(u, "__dict__", {}).get("role", None)
    if legacy:
        return legacy

    # 2) sinon, on dérive depuis RBAC: premier rôle, sinon responsable_secteur
    codes = []
    try:
        codes = [r.code for r in (getattr(u, "roles", []) or []) if getattr(r, "code", None)]
    except Exception:
        codes = []
    code = (codes[0] if codes else "responsable_secteur")

    # 3) compat historique : certaines routes comparent à "directrice"
    mapping = {
        "direction": "directrice",
    }
    return mapping.get(code, code)

def _role_compat_set(u, value: str):
    # Si la colonne legacy n'existe pas, on ignore (fallback read-only).
    try:
        if "role" in getattr(u, "__dict__", {}):
            u.__dict__["role"] = value
    except Exception:
        pass

try:
    # Ne créer la propriété QUE si SQLAlchemy n'a pas déjà mappé `role`
    if not hasattr(User, "role"):
        User.role = property(_role_compat_get, _role_compat_set)  # type: ignore[attr-defined]
except Exception:
    # On ne doit jamais empêcher l'app de démarrer pour un souci de compat.
    pass


# ---------------------------------------------------------------------------
# Référentiels de compétences (DigComp / Pix / CléA, etc.) + projets pédagogiques
# ---------------------------------------------------------------------------
#
# Objectif:
# - Importer des référentiels une seule fois (pas de saisie manuelle)
# - Lier les ateliers à plusieurs projets (Option B)
# - Tagger les compétences au niveau SESSION (ex: "Créer un mail") plutôt qu'au
#   niveau atelier large (ex: "Numérique par tous")
# - Évaluer par session de façon légère (NA / EN_COURS / ACQUIS)
#
# IMPORTANT : le passeport des habitants (PasseportNote / PasseportPieceJointe)
# reste INCHANGÉ et non négociable. Les tables ci-dessous n'écrasent rien et
# pourront ensuite alimenter le passeport via un pont/export.


class Framework(db.Model):
    __tablename__ = "framework"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    nom = db.Column(db.String(160), nullable=False)
    version = db.Column(db.String(32), nullable=True)
    lang = db.Column(db.String(8), nullable=True, default="fr")
    source_url = db.Column(db.String(512), nullable=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    skills = db.relationship("Skill", backref="framework", lazy=True)

    def __repr__(self):
        return f"<Framework {self.code} {self.version or ''}>"


class Skill(db.Model):
    __tablename__ = "skill"
    __table_args__ = (
        db.UniqueConstraint("framework_id", "code", name="uq_skill_framework_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    framework_id = db.Column(db.Integer, db.ForeignKey("framework.id"), nullable=False, index=True)
    code = db.Column(db.String(64), nullable=False, index=True)
    label = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    domain_code = db.Column(db.String(32), nullable=True, index=True)
    domain_label = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, nullable=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def __repr__(self):
        return f"<Skill {self.framework_id}:{self.code}>"


class LearningProject(db.Model):
    __tablename__ = "learning_project"

    id = db.Column(db.Integer, primary_key=True)
    titre = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    date_debut = db.Column(db.Date, nullable=True)
    date_fin = db.Column(db.Date, nullable=True)
    public_cible = db.Column(db.String(255), nullable=True)
    framework_id_default = db.Column(db.Integer, db.ForeignKey("framework.id"), nullable=True, index=True)
    seuil_reussite = db.Column(db.Float, nullable=False, default=0.6)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)

    framework_default = db.relationship("Framework", foreign_keys=[framework_id_default])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<LearningProject {self.id} {self.titre}>"


class LearningProjectSkill(db.Model):
    __tablename__ = "learning_project_skill"
    __table_args__ = (
        db.UniqueConstraint("project_id", "skill_id", name="uq_learning_project_skill"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id = db.Column(db.Integer, db.ForeignKey("skill.id", ondelete="CASCADE"), nullable=False, index=True)
    target_level = db.Column(db.String(32), nullable=True)
    poids = db.Column(db.Float, nullable=False, default=1.0)
    obligatoire = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, nullable=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    project = db.relationship("LearningProject", backref=db.backref("skills", lazy=True, cascade="all, delete-orphan"))
    skill = db.relationship("Skill")


class AtelierProject(db.Model):
    __tablename__ = "atelier_project"
    __table_args__ = (
        db.PrimaryKeyConstraint("atelier_id", "project_id"),
    )

    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    atelier = db.relationship("AtelierActivite", backref=db.backref("projects", lazy=True, cascade="all, delete-orphan"))
    project = db.relationship("LearningProject", backref=db.backref("ateliers", lazy=True))


class SessionSkill(db.Model):
    __tablename__ = "session_skill"
    __table_args__ = (
        db.PrimaryKeyConstraint("session_id", "skill_id"),
    )

    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skill.id", ondelete="CASCADE"), nullable=False)
    expected_level = db.Column(db.String(32), nullable=True)
    coverage = db.Column(db.Float, nullable=False, default=1.0)
    created_at = db.Column(db.DateTime, default=utcnow)

    session = db.relationship("SessionActivite", backref=db.backref("skills", lazy=True, cascade="all, delete-orphan"))
    skill = db.relationship("Skill")


class SessionAssessment(db.Model):
    __tablename__ = "session_assessment"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id", ondelete="SET NULL"), nullable=True, index=True)
    method = db.Column(db.String(32), nullable=False, default="OBSERVATION")
    notes = db.Column(db.Text, nullable=True)
    assessed_at = db.Column(db.DateTime, default=utcnow, index=True)
    assessed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)

    session = db.relationship("SessionActivite")
    project = db.relationship("LearningProject")
    assessed_by = db.relationship("User")


class SessionAssessmentSkill(db.Model):
    __tablename__ = "session_assessment_skill"
    __table_args__ = (
        db.UniqueConstraint("session_assessment_id", "skill_id", name="uq_session_assessment_skill"),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("session_assessment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_id = db.Column(db.Integer, db.ForeignKey("skill.id", ondelete="CASCADE"), nullable=False, index=True)

    # NA / EN_COURS / ACQUIS
    result = db.Column(db.String(16), nullable=False, default="EN_COURS", index=True)
    score = db.Column(db.Integer, nullable=True)
    observed_level = db.Column(db.String(32), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    assessment = db.relationship(
        "SessionAssessment",
        backref=db.backref("results", lazy=True, cascade="all, delete-orphan"),
    )
    skill = db.relationship("Skill")


# ---------- JOURNAL DES CONNEXIONS (sécurité + audit) ----------

class JournalConnexion(db.Model):
    """Trace chaque tentative de connexion (réussie ou non).

    Sert à la fois :
    - au verrouillage temporaire après échecs répétés (anti force brute),
    - à l'audit « qui s'est connecté quand » (recommandation CNIL/ANSSI).
    Les entrées anciennes sont purgées automatiquement (voir
    app.services.connexion_securite).
    """

    __tablename__ = "journal_connexion"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(180), nullable=False, index=True)
    adresse_ip = db.Column(db.String(45), nullable=True)
    succes = db.Column(db.Boolean, nullable=False, default=False)
    cree_le = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class AuditLog(db.Model):
    """Journal d'audit des actions sensibles (conformité RGPD / traçabilité).

    Enregistre QUI a fait QUOI et QUAND pour les actions à fort enjeu :
    gestion des comptes/rôles, restauration de sauvegarde, exports de données
    personnelles. ``user_email`` est un instantané (l'utilisateur peut être
    supprimé ensuite).
    """

    __tablename__ = "journal_audit"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    user_email = db.Column(db.String(180), nullable=True)
    action = db.Column(db.String(60), nullable=False, index=True)
    cible = db.Column(db.String(255), nullable=True)
    details = db.Column(db.Text, nullable=True)


# ---------- TÂCHES PLANIFIÉES INTERNES ----------

class TachePlanifiee(db.Model):
    """Mémorise la dernière exécution des tâches automatiques internes
    (ex: purge RGPD quotidienne), pour ne les lancer qu'une fois par jour
    sans dépendre d'un planificateur externe."""

    __tablename__ = "tache_planifiee"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(80), unique=True, nullable=False)
    derniere_execution = db.Column(db.DateTime, nullable=True)


class NotificationReglage(db.Model):
    """Réglage d'un type de notification automatique (digest e-mail).

    Chaque type (échéances financeurs, impayés, sauvegarde en retard…) est
    activable individuellement, avec ses destinataires et sa fréquence —
    RIEN n'est actif par défaut : une petite structure choisit exactement
    ce qui doit la prévenir, pour ne pas être noyée d'e-mails."""

    __tablename__ = "notification_reglage"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    actif = db.Column(db.Boolean, nullable=False, default=False)
    # Adresses e-mail, séparées par virgules ou retours à la ligne.
    destinataires = db.Column(db.Text, nullable=True)
    frequence = db.Column(db.String(20), nullable=False, default="quotidien")  # quotidien | hebdomadaire
    seuil_jours = db.Column(db.Integer, nullable=True)  # sens propre à chaque type
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


# ---------- OBJECTIFS SECTORIELS (cadre stratégique du projet social) ----------

class ObjectifSectoriel(db.Model):
    """Objectif sectoriel configurable, propre à chaque secteur.

    Ex. pour le secteur Transitions : OS1 « Comprendre », OS2 « Expérimenter »,
    OS3 « Coopérer ». Chaque secteur définit les siens. Les actions s'y
    rattachent (ActionObjectifSectoriel) pour produire les tableaux de
    contribution et consolider au niveau du projet social.
    """

    __tablename__ = "objectif_sectoriel"

    id = db.Column(db.Integer, primary_key=True)
    secteur = db.Column(db.String(80), nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)        # ex. OS1
    libelle = db.Column(db.String(255), nullable=False)    # ex. Comprendre
    axe = db.Column(db.String(160), nullable=True)         # regroupement optionnel
    ordre = db.Column(db.Integer, nullable=False, default=0)
    actif = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class ActionObjectifSectoriel(db.Model):
    """Rattachement d'une action à un objectif sectoriel, avec sa contribution."""

    __tablename__ = "action_objectif_sectoriel"

    id = db.Column(db.Integer, primary_key=True)
    action_id = db.Column(db.Integer, db.ForeignKey("projet_action.id", ondelete="CASCADE"), nullable=False, index=True)
    objectif_id = db.Column(db.Integer, db.ForeignKey("objectif_sectoriel.id", ondelete="CASCADE"), nullable=False, index=True)
    contribution = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    action = db.relationship("ProjetAction", backref=db.backref("objectif_links", cascade="all, delete-orphan", lazy="selectin"))
    objectif = db.relationship("ObjectifSectoriel")

    __table_args__ = (
        db.UniqueConstraint("action_id", "objectif_id", name="uq_action_objectif"),
    )


# ---------- PORTAIL DES APPRENANTS (intégration externe) ----------
class PortailAttempt(db.Model):
    """Résultat d'activité importé du portail des apprenants (externe).

    Dédoublonné par ``attempt_id`` (l'``id`` renvoyé par le portail, car la
    borne ``since`` est inclusive). Rapproché à un participant via l'externalId
    (= notre ``Participant.id``).
    """
    __tablename__ = "portail_attempt"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.String(255), nullable=False, unique=True, index=True)
    external_id = db.Column(db.String(64), nullable=True, index=True)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    activity = db.Column(db.String(255), nullable=True)
    activity_type = db.Column(db.String(120), nullable=True)
    theme = db.Column(db.String(255), nullable=True)
    score = db.Column(db.Float, nullable=True)
    max_score = db.Column(db.Float, nullable=True)
    pct = db.Column(db.Float, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    participant = db.relationship(
        "Participant", backref=db.backref("portail_attempts", lazy="dynamic")
    )


class PortailSyncState(db.Model):
    """État de la synchronisation périodique avec le portail (curseur ``since``).

    Une seule ligne (id=1) : mémorise le dernier ``generatedAt`` reçu, à
    renvoyer comme ``since`` au passage suivant.
    """
    __tablename__ = "portail_sync_state"

    id = db.Column(db.Integer, primary_key=True)
    last_since = db.Column(db.String(40), nullable=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(db.String(255), nullable=True)


class PortailCompetenceMap(db.Model):
    """Correspondance globale : un exercice du portail (champ ``activity``)
    est une composante d'une compétence de l'ERP, avec un seuil de réussite.

    Sert à alimenter la progression pédagogique : une tentative dont le
    pourcentage atteint ``seuil_pct`` rend l'exercice « réussi » pour la
    compétence visée.
    """
    __tablename__ = "portail_competence_map"

    id = db.Column(db.Integer, primary_key=True)
    activity = db.Column(db.String(255), nullable=False, index=True)
    competence_id = db.Column(
        db.Integer, db.ForeignKey("competence.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seuil_pct = db.Column(db.Integer, nullable=False, default=80)
    actif = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    competence = db.relationship("Competence")

    __table_args__ = (
        db.UniqueConstraint("activity", "competence_id", name="uq_portail_competence_map"),
    )


# ---------- ÉCHELLE DE HART (participation des habitants) ----------

HART_TYPES_EVALUATION = [
    ("initiale", "Première venue"),
    ("suivi", "Suivi (toutes les 5 séances)"),
    ("mi_parcours", "Mi-parcours"),
    ("fin_parcours", "Fin de parcours"),
]
HART_TYPES_DICT = dict(HART_TYPES_EVALUATION)


class HartEvaluation(db.Model):
    """Positionnement d'un participant sur l'échelle de Hart (1 à 8).

    L'échelle mesure le degré de participation des habitants (référence
    fédération des centres sociaux / agrément CAF - éducation populaire).
    Les niveaux 1-3 relèvent de la non-participation, 4-8 de la participation
    effective. L'évaluation est déclenchée par l'émargement : première venue,
    puis toutes les 5 séances ; le professionnel peut aussi poser une
    évaluation de mi-parcours ou de fin de parcours à tout moment.
    """
    __tablename__ = "hart_evaluation"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    niveau = db.Column(db.Integer, nullable=False)  # 1..8
    type_evaluation = db.Column(db.String(20), nullable=False, default="suivi", index=True)
    date_evaluation = db.Column(db.Date, nullable=False, default=date.today, index=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    temoignage = db.Column(db.Text, nullable=True)      # parole du participant
    remarque_pro = db.Column(db.Text, nullable=True)    # observation du professionnel
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    participant = db.relationship("Participant", backref=db.backref("hart_evaluations", cascade="all, delete-orphan", order_by="HartEvaluation.date_evaluation.asc(), HartEvaluation.id.asc()"))
    user = db.relationship("User")

    @property
    def type_label(self):
        return HART_TYPES_DICT.get(self.type_evaluation or "suivi", self.type_evaluation or "—")


# ---------- DONS & REÇUS FISCAUX (CERFA 11580) ----------

class Don(db.Model):
    """Don reçu par l'association, avec reçu fiscal numéroté (CERFA 11580).

    Un reçu émis est un document comptable : il ne se supprime pas, il
    s'annule (le numéro reste dans le registre). Les coordonnées de
    l'organisme sont figées sur le don pour que le reçu reste reproductible
    à l'identique même si l'organisme change d'adresse ensuite.
    """
    __tablename__ = "don"

    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(20), nullable=False, unique=True)   # ex. 2026-0001
    annee = db.Column(db.Integer, nullable=False, index=True)

    type_donateur = db.Column(db.String(20), nullable=False, default="particulier")  # particulier | entreprise
    donateur_civilite = db.Column(db.String(20), nullable=True)
    donateur_nom = db.Column(db.String(160), nullable=False)
    donateur_prenom = db.Column(db.String(120), nullable=True)
    donateur_adresse = db.Column(db.String(255), nullable=True)
    donateur_cp = db.Column(db.String(10), nullable=True)
    donateur_ville = db.Column(db.String(120), nullable=True)
    donateur_email = db.Column(db.String(180), nullable=True)

    montant = db.Column(db.Float, nullable=False, default=0.0)
    date_don = db.Column(db.Date, nullable=False, default=date.today)
    forme_don = db.Column(db.String(20), nullable=False, default="numeraire")   # numeraire | nature
    mode_versement = db.Column(db.String(20), nullable=False, default="virement")  # especes | cheque | virement | autre
    nature_description = db.Column(db.Text, nullable=True)   # si don en nature

    organisme_nom = db.Column(db.String(200), nullable=True)
    organisme_adresse = db.Column(db.String(255), nullable=True)

    est_annule = db.Column(db.Boolean, nullable=False, default=False)
    annulation_motif = db.Column(db.String(255), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")


# ---------- BÉNÉVOLAT (heures, missions, valorisation) ----------

class BenevoleHeures(db.Model):
    """Heures de bénévolat réalisées par un habitant.

    Alimente l'onglet « bénévolat / vitalité démocratique » du SENACS et la
    valorisation comptable des contributions volontaires (compte 87).
    """
    __tablename__ = "benevole_heures"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True)
    date_action = db.Column(db.Date, nullable=False, default=date.today, index=True)
    heures = db.Column(db.Float, nullable=False, default=0.0)
    mission = db.Column(db.String(200), nullable=True)     # ex. accueil, accompagnement scolaire, CA
    secteur = db.Column(db.String(80), nullable=True, index=True)
    commentaire = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    participant = db.relationship("Participant", backref=db.backref("benevolat_heures", cascade="all, delete-orphan"))
    user = db.relationship("User")


# ---------- LIEN SUBVENTION <-> ATELIERS (justificatif financeur) ----------

class SubventionAtelier(db.Model):
    """Rattache une subvention aux ateliers qu'elle finance.

    Permet de produire le justificatif financeur : « cette subvention a
    financé ces ateliers = X séances, Y heures, Z participants, soit N € par
    participant ». ``poids_pct`` = part de la subvention affectée à l'atelier.
    """
    __tablename__ = "subvention_atelier"

    id = db.Column(db.Integer, primary_key=True)
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id", ondelete="CASCADE"), nullable=False, index=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    poids_pct = db.Column(db.Float, nullable=False, default=100.0)
    justification = db.Column(db.Text, nullable=True)   # ex. « 40 jeunes QPV, hors temps scolaire »
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    subvention = db.relationship("Subvention", backref=db.backref("ateliers_finances", cascade="all, delete-orphan"))
    atelier = db.relationship("AtelierActivite")

    __table_args__ = (
        db.UniqueConstraint("subvention_id", "atelier_id", name="uq_subvention_atelier"),
    )


# ---------- SENACS : EMPLOIS / ETP ----------

SENACS_TYPES_CONTRAT = [
    ("cdi", "CDI"),
    ("cdd", "CDD"),
    ("emploi_aide", "Emploi aidé"),
    ("mise_a_disposition", "Mise à disposition"),
    ("autre", "Autre"),
]
SENACS_TYPES_CONTRAT_DICT = dict(SENACS_TYPES_CONTRAT)


class SenacsEmploi(db.Model):
    """Poste salarié déclaré pour l'onglet « Emplois » du SENACS.

    Saisie simple par exercice : fonction, type de contrat, quotité (ETP).
    Pas un module RH : juste ce qu'exige l'enquête annuelle.
    """
    __tablename__ = "senacs_emploi"

    id = db.Column(db.Integer, primary_key=True)
    annee = db.Column(db.Integer, nullable=False, index=True)
    intitule = db.Column(db.String(200), nullable=False)     # ex. Directeur, animatrice famille
    type_contrat = db.Column(db.String(30), nullable=False, default="cdi")
    etp = db.Column(db.Float, nullable=False, default=1.0)
    commentaire = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def contrat_label(self):
        return SENACS_TYPES_CONTRAT_DICT.get(self.type_contrat or "autre", self.type_contrat or "—")


# ---------- RH : SALARIÉS (réservé direction) ----------

class Salarie(db.Model):
    """Salarié du centre — module RH minimal, réservé à la direction.

    Nourrit le SENACS (emplois/ETP) et la masse salariale ; la direction
    affecte chaque salarié à son secteur et à son poste dans le secteur.
    Peut être alimenté par import depuis un outil RH externe (source_ref
    conserve l'identifiant externe pour les rapprochements).
    """
    __tablename__ = "salarie"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(160), nullable=False)
    prenom = db.Column(db.String(120), nullable=True)
    poste = db.Column(db.String(200), nullable=True)        # poste dans le secteur
    secteur = db.Column(db.String(80), nullable=True, index=True)
    type_contrat = db.Column(db.String(30), nullable=False, default="cdi")  # cf. SENACS_TYPES_CONTRAT
    etp = db.Column(db.Float, nullable=False, default=1.0)
    salaire_brut_charge = db.Column(db.Float, nullable=True)  # annuel, chargé (€)
    date_entree = db.Column(db.Date, nullable=True)
    date_sortie = db.Column(db.Date, nullable=True, index=True)
    commentaire = db.Column(db.Text, nullable=True)
    source_ref = db.Column(db.String(120), nullable=True, index=True)  # id dans l'outil RH externe
    recup_user_id = db.Column(db.String(120), nullable=True, unique=True, index=True)
    recup_email = db.Column(db.String(255), nullable=True)
    recup_active = db.Column(db.Boolean, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    recup_snapshots = db.relationship(
        "RecupRhSnapshot",
        back_populates="salarie",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def contrat_label(self):
        return SENACS_TYPES_CONTRAT_DICT.get(self.type_contrat or "autre", self.type_contrat or "—")

    def actif_sur(self, annee: int) -> bool:
        """Présent au moins un jour sur l'exercice."""
        debut, fin = date(annee, 1, 1), date(annee, 12, 31)
        if self.date_entree and self.date_entree > fin:
            return False
        if self.date_sortie and self.date_sortie < debut:
            return False
        return True


class RecupRhSnapshot(db.Model):
    """Instantane annuel lu depuis Recup, dedoublonne par salarie/exercice."""

    __tablename__ = "recup_rh_snapshot"
    __table_args__ = (
        db.UniqueConstraint("salarie_id", "year", name="uq_recup_rh_snapshot_salarie_year"),
    )

    id = db.Column(db.Integer, primary_key=True)
    salarie_id = db.Column(db.Integer, db.ForeignKey("salarie.id", ondelete="CASCADE"), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    balance_minutes = db.Column(db.Integer, nullable=False, default=0)
    overtime_minutes = db.Column(db.Integer, nullable=False, default=0)
    approved_recovery_minutes = db.Column(db.Integer, nullable=False, default=0)
    pending_recovery_minutes = db.Column(db.Integer, nullable=False, default=0)
    mileage_expense_count = db.Column(db.Integer, nullable=False, default=0)
    mileage_distance_km = db.Column(db.Integer, nullable=False, default=0)
    mileage_amount_cents = db.Column(db.Integer, nullable=False, default=0)
    hourly_unloaded_cents = db.Column(db.Integer, nullable=True)
    hourly_loaded_cents = db.Column(db.Integer, nullable=True)
    worked_weeks_per_year = db.Column(db.Integer, nullable=True)
    source_generated_at = db.Column(db.DateTime, nullable=True)
    synced_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    salarie = db.relationship("Salarie", back_populates="recup_snapshots")

    def annual_loaded_cost_eur(self, etp: float = 1.0) -> float | None:
        """Estimation non destructive: taux charge x 35 h x semaines x ETP."""
        if not self.hourly_loaded_cents or not self.worked_weeks_per_year:
            return None
        return round(
            self.hourly_loaded_cents * 35 * self.worked_weeks_per_year * float(etp or 0) / 100,
            2,
        )


# ---------- GLOSSAIRE : personnalisations locales ----------

class GlossaireTerme(db.Model):
    """Personnalisation du glossaire (« dico du social »).

    Le glossaire de base vit dans le code (app/aide/glossaire.py) et suit
    les mises à jour de l'application. Cette table ne stocke QUE les
    ajustements de la structure :
    - un terme du même nom qu'un terme de base le REMPLACE (modification) ;
    - ``masque=True`` retire un terme de base de l'affichage (suppression) ;
    - les autres lignes sont des mots AJOUTÉS par la structure.
    Supprimer la ligne d'une modification rétablit la version d'origine.
    """
    __tablename__ = "glossaire_terme"

    id = db.Column(db.Integer, primary_key=True)
    terme = db.Column(db.String(180), nullable=False, unique=True, index=True)
    definition = db.Column(db.Text, nullable=False, default="")
    categorie = db.Column(db.String(120), nullable=True)
    dans_app = db.Column(db.Text, nullable=True)
    masque = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------- ADHÉSIONS, PARTICIPATION & FOYERS ----------

class Foyer(db.Model):
    """Foyer (famille) : rapproche des participants pour l'adhésion familiale.

    L'adhésion familiale se règle une fois par foyer et par année scolaire ;
    elle couvre tous les membres du foyer pour cette année-là.
    """
    __tablename__ = "foyer"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    membres = db.relationship("Participant", back_populates="foyer")
    cotisations = db.relationship("Cotisation", back_populates="foyer", cascade="all, delete-orphan")


TYPES_TARIF = ["adhesion_individuelle", "adhesion_familiale", "participation"]
TYPES_TARIF_LABELS = {
    "adhesion_individuelle": "Adhésion individuelle",
    "adhesion_familiale": "Adhésion familiale",
    "participation": "Participation",
}

MODES_PAIEMENT = ["especes", "cheque", "carte", "virement", "autre"]
MODES_PAIEMENT_LABELS = {
    "especes": "Espèces",
    "cheque": "Chèque",
    "carte": "Carte bancaire",
    "virement": "Virement",
    "autre": "Autre",
}


class TarifBareme(db.Model):
    """Barème des tarifs (adhésions + participation) par année scolaire.

    Plusieurs lignes peuvent coexister pour un même (année, type) avec des
    dates de début différentes : le tarif « en vigueur » à une date donnée
    est celui dont la date de début est la plus récente sans dépasser cette
    date (permet de faire évoluer le prix de la participation en cours
    d'année, ex. moins cher en fin d'année scolaire).
    """
    __tablename__ = "tarif_bareme"

    id = db.Column(db.Integer, primary_key=True)
    annee_scolaire = db.Column(db.Integer, nullable=False, index=True)  # année de rentrée (ex. 2026 -> "2026-2027")
    type_tarif = db.Column(db.String(30), nullable=False, index=True)
    montant = db.Column(db.Float, nullable=False)
    date_debut = db.Column(db.Date, nullable=False)
    commentaire = db.Column(db.String(255), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        db.Index("ix_tarif_bareme_lookup", "annee_scolaire", "type_tarif", "date_debut"),
    )

    @property
    def type_label(self):
        return TYPES_TARIF_LABELS.get(self.type_tarif, self.type_tarif)

    @property
    def libelle_annee(self):
        return f"{self.annee_scolaire}-{self.annee_scolaire + 1}"


class Cotisation(db.Model):
    """Une obligation de règlement : adhésion (individuelle ou familiale) ou
    participation, pour une année scolaire donnée.

    - adhesion_individuelle / participation : rattachées à un participant ;
    - adhesion_familiale : rattachée à un foyer (couvre tous ses membres).
    Le montant dû est calculé depuis le barème en vigueur à la date de
    référence, mais reste modifiable au cas par cas (situations de
    précarité, tarifs réduits négociés).
    """
    __tablename__ = "cotisation"

    id = db.Column(db.Integer, primary_key=True)
    annee_scolaire = db.Column(db.Integer, nullable=False, index=True)
    type_cotisation = db.Column(db.String(30), nullable=False, index=True)

    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=True, index=True)
    foyer_id = db.Column(db.Integer, db.ForeignKey("foyer.id", ondelete="CASCADE"), nullable=True, index=True)

    montant_du = db.Column(db.Float, nullable=False, default=0.0)
    date_reference = db.Column(db.Date, nullable=False)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    participant = db.relationship("Participant", backref=db.backref("cotisations", cascade="all, delete-orphan"))
    foyer = db.relationship("Foyer", back_populates="cotisations")
    paiements = db.relationship("Paiement", back_populates="cotisation", cascade="all, delete-orphan", order_by="Paiement.date_paiement")

    __table_args__ = (
        db.Index("ix_cotisation_participant_annee", "participant_id", "annee_scolaire", "type_cotisation"),
        db.Index("ix_cotisation_foyer_annee", "foyer_id", "annee_scolaire"),
    )

    @property
    def type_label(self):
        return TYPES_TARIF_LABELS.get(self.type_cotisation, self.type_cotisation)

    @property
    def libelle_annee(self):
        return f"{self.annee_scolaire}-{self.annee_scolaire + 1}"

    @property
    def montant_regle(self):
        return round(sum(float(p.montant or 0) for p in self.paiements), 2)

    @property
    def reste_du(self):
        return round(max(0.0, float(self.montant_du or 0) - self.montant_regle), 2)

    @property
    def solde(self):
        return self.reste_du <= 0.009


class Paiement(db.Model):
    """Un versement (règlement partiel ou total) contre une cotisation."""
    __tablename__ = "paiement"

    id = db.Column(db.Integer, primary_key=True)
    cotisation_id = db.Column(db.Integer, db.ForeignKey("cotisation.id", ondelete="CASCADE"), nullable=False, index=True)
    montant = db.Column(db.Float, nullable=False)
    date_paiement = db.Column(db.Date, nullable=False)
    mode = db.Column(db.String(20), nullable=False, default="especes")
    commentaire = db.Column(db.String(255), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    cotisation = db.relationship("Cotisation", back_populates="paiements")

    @property
    def mode_label(self):
        return MODES_PAIEMENT_LABELS.get(self.mode, self.mode)


# ---------- CAISSE (espèces & chèques) ----------

TYPES_MOUVEMENT_CAISSE = ["fond", "depot", "comptage", "ajustement"]
TYPES_MOUVEMENT_CAISSE_LABELS = {
    "fond": "Fond de caisse",
    "depot": "Dépôt en banque",
    "comptage": "Comptage (arrêté de caisse)",
    "ajustement": "Ajustement",
}


class CaisseMouvement(db.Model):
    """Journal de la caisse (la boîte physique : espèces + chèques).

    Les ENCAISSEMENTS n'apparaissent pas ici : ce sont les règlements
    (Paiement) en espèces/chèque et les dons en numéraire déjà saisis
    ailleurs — la caisse les lit, jamais de double saisie. Cette table ne
    stocke que ce qui est propre à la caisse :
    - ``fond``       : nouveau montant du fond de caisse (le dernier fait foi) ;
    - ``depot``      : sortie vers la banque (montant déposé, positif) ;
    - ``comptage``   : arrêté de caisse (montant = constaté, ecart = constaté − théorique) ;
    - ``ajustement`` : correction signée du théorique (auto-créée par un
                       comptage en écart, ou manuelle avec motif).
    """
    __tablename__ = "caisse_mouvement"

    id = db.Column(db.Integer, primary_key=True)
    type_mouvement = db.Column(db.String(20), nullable=False, index=True)
    canal = db.Column(db.String(10), nullable=False, default="especes", index=True)  # especes | cheque
    montant = db.Column(db.Float, nullable=False, default=0.0)
    ecart = db.Column(db.Float, nullable=True)          # comptage uniquement
    nb_cheques = db.Column(db.Integer, nullable=True)   # dépôt de chèques
    date_mouvement = db.Column(db.Date, nullable=False, index=True)
    commentaire = db.Column(db.String(255), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")

    @property
    def type_label(self):
        return TYPES_MOUVEMENT_CAISSE_LABELS.get(self.type_mouvement, self.type_mouvement)


# ---------- AGENDA : préférences du flux + créneaux hors ateliers ----------

TYPES_CRENEAU = ["reunion", "preparation", "formation", "partenariat", "administratif", "autre"]
TYPES_CRENEAU_LABELS = {
    "reunion": "Réunion",
    "preparation": "Préparation",
    "formation": "Formation",
    "partenariat": "Partenariat",
    "administratif": "Administratif",
    "autre": "Autre",
}


class AgendaPreference(db.Model):
    """Réglages personnels du flux calendrier iCal (titre, description,
    fenêtre, préparation automatique…). Stockés en JSON : on peut ajouter
    des options sans migration."""
    __tablename__ = "agenda_preference"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    options_json = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("agenda_pref", uselist=False, cascade="all, delete-orphan"))


class AgendaCreneau(db.Model):
    """Créneau de travail hors ateliers (réunion, préparation, formation…).

    Complète les séances dans le flux calendrier : l'agenda extrait par les
    financeurs reflète alors TOUT le temps effectif, pas seulement le
    face-à-face. Personnel à chaque utilisateur. La répétition hebdomadaire
    est matérialisée à la création (une ligne par occurrence) : simple,
    fiable, chaque occurrence reste modifiable/supprimable individuellement.
    """
    __tablename__ = "agenda_creneau"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    type_creneau = db.Column(db.String(20), nullable=False, default="reunion")
    titre = db.Column(db.String(200), nullable=False)
    date_creneau = db.Column(db.Date, nullable=False, index=True)
    heure_debut = db.Column(db.String(10), nullable=True)
    heure_fin = db.Column(db.String(10), nullable=True)
    description = db.Column(db.String(500), nullable=True)
    # Rattachement facultatif à une subvention : le créneau compte alors dans
    # la feuille de temps du financeur (réunion projet, préparation dédiée…).
    subvention_id = db.Column(db.Integer, db.ForeignKey("subvention.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")
    subvention = db.relationship("Subvention")

    @property
    def type_label(self):
        return TYPES_CRENEAU_LABELS.get(self.type_creneau, self.type_creneau)

    @property
    def duree_minutes(self):
        try:
            h1, m1 = (self.heure_debut or "").split(":")[:2]
            h2, m2 = (self.heure_fin or "").split(":")[:2]
            d = (int(h2) * 60 + int(m2)) - (int(h1) * 60 + int(m1))
            return d if d > 0 else 0
        except Exception:
            return 0


# ---------- TRANSITIONS (thématiques, défis, mesures) ----------

#: Thématiques par défaut, créées au premier usage (modifiables ensuite).
TRANSITION_THEMATIQUES_DEFAUT = [
    ("alimentation", "Alimentation durable"),
    ("mobilite", "Mobilité douce"),
    ("energie", "Énergie & sobriété"),
    ("reemploi", "Déchets & réemploi"),
    ("nature", "Nature & biodiversité"),
    ("numerique", "Numérique responsable"),
]

STATUTS_DEFI = ["en_cours", "realise", "abandonne"]
STATUTS_DEFI_LABELS = {
    "en_cours": "En cours",
    "realise": "Réalisé",
    "abandonne": "Abandonné",
}

#: Ateliers ↔ thématiques transitions (un atelier peut toucher plusieurs).
atelier_transition_thematique = db.Table(
    "atelier_transition_thematique",
    db.Column("atelier_id", db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), primary_key=True),
    db.Column("thematique_id", db.Integer, db.ForeignKey("transition_thematique.id", ondelete="CASCADE"), primary_key=True),
)

#: Ateliers ↔ objectifs sectoriels (croisement thématique × OS demandé au cadrage).
atelier_transition_objectif = db.Table(
    "atelier_transition_objectif",
    db.Column("atelier_id", db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), primary_key=True),
    db.Column("objectif_id", db.Integer, db.ForeignKey("objectif_sectoriel.id", ondelete="CASCADE"), primary_key=True),
)


class TransitionThematique(db.Model):
    """Référentiel des thématiques de transition (alimentation, mobilité…).

    Sert d'axe de classement des ateliers étiquetés « transitions », des
    défis habitants et des mesures chiffrées. Seedé avec une liste standard,
    modifiable depuis le module (renommage, ajout, désactivation)."""

    __tablename__ = "transition_thematique"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    libelle = db.Column(db.String(160), nullable=False)
    ordre = db.Column(db.Integer, nullable=False, default=0)
    actif = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    ateliers = db.relationship(
        "AtelierActivite",
        secondary=atelier_transition_thematique,
        backref=db.backref("transition_thematiques", lazy="selectin"),
    )


class DefiTransition(db.Model):
    """Défi d'un habitant ou d'une famille (« un mois à vélo », « composter »).

    Le défi se rattache à une thématique (et, en option, à un objectif
    sectoriel). Porté par un participant OU un foyer. Cycle : en cours →
    réalisé / abandonné."""

    __tablename__ = "defi_transition"

    id = db.Column(db.Integer, primary_key=True)
    titre = db.Column(db.String(200), nullable=False)
    thematique_id = db.Column(db.Integer, db.ForeignKey("transition_thematique.id", ondelete="CASCADE"), nullable=False, index=True)
    objectif_id = db.Column(db.Integer, db.ForeignKey("objectif_sectoriel.id", ondelete="SET NULL"), nullable=True, index=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id", ondelete="CASCADE"), nullable=True, index=True)
    foyer_id = db.Column(db.Integer, db.ForeignKey("foyer.id", ondelete="CASCADE"), nullable=True, index=True)

    statut = db.Column(db.String(20), nullable=False, default="en_cours", index=True)
    date_engagement = db.Column(db.Date, nullable=False, index=True)
    date_realisation = db.Column(db.Date, nullable=True)
    commentaire = db.Column(db.String(500), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    thematique = db.relationship("TransitionThematique", backref=db.backref("defis", passive_deletes=True))
    objectif = db.relationship("ObjectifSectoriel")
    participant = db.relationship(
        "Participant",
        backref=db.backref("defis_transition", cascade="all, delete-orphan", passive_deletes=True),
    )
    foyer = db.relationship(
        "Foyer",
        backref=db.backref("defis_transition", cascade="all, delete-orphan", passive_deletes=True),
    )

    @property
    def statut_label(self):
        return STATUTS_DEFI_LABELS.get(self.statut, self.statut)

    @property
    def porteur_label(self):
        if self.participant is not None:
            return f"{self.participant.prenom} {self.participant.nom}"
        if self.foyer is not None:
            return self.foyer.nom or f"Foyer #{self.foyer_id}"
        return "—"


class TransitionMesure(db.Model):
    """Mesure chiffrée saisie à la main sur un atelier transitions.

    Ex. « km évités : 120 », « kg détournés de la benne : 45 »,
    « repas végétariens servis : 60 ». Libellé et unité libres : le tableau
    de bord et l'export additionnent par (libellé, unité) et par thématique.
    Valeurs indicatives, destinées au suivi et aux bilans — pas des mesures
    certifiées."""

    __tablename__ = "transition_mesure"

    id = db.Column(db.Integer, primary_key=True)
    atelier_id = db.Column(db.Integer, db.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False, index=True)
    thematique_id = db.Column(db.Integer, db.ForeignKey("transition_thematique.id", ondelete="SET NULL"), nullable=True, index=True)

    libelle = db.Column(db.String(160), nullable=False)
    valeur = db.Column(db.Float, nullable=False, default=0.0)
    unite = db.Column(db.String(40), nullable=True)
    date_mesure = db.Column(db.Date, nullable=False, index=True)
    commentaire = db.Column(db.String(255), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    atelier = db.relationship(
        "AtelierActivite",
        backref=db.backref("transition_mesures", cascade="all, delete-orphan", passive_deletes=True),
    )
    thematique = db.relationship("TransitionThematique")


# Rattachement tardif : ObjectifSectoriel est défini plus haut dans le fichier,
# la table d'association juste au-dessus. Expose atelier.transition_objectifs.
ObjectifSectoriel.ateliers_transitions = db.relationship(
    "AtelierActivite",
    secondary=atelier_transition_objectif,
    backref=db.backref("transition_objectifs", lazy="selectin"),
)
