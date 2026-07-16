from __future__ import annotations


from app.utils.dates import utcnow

from app.extensions import db


class BudgetCompteReferentiel(db.Model):
    __tablename__ = "budget_compte_referentiel"

    id = db.Column(db.Integer, primary_key=True)
    nature = db.Column(db.String(10), nullable=False, default="charge", index=True)
    code = db.Column(db.String(20), nullable=False, index=True)
    libelle = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    ordre = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    categories = db.relationship("BudgetCategorieReferentiel", back_populates="compte", cascade="all, delete-orphan")

    @property
    def label(self) -> str:
        return f"{self.code} - {self.libelle}".strip(" -")


class BudgetCategorieReferentiel(db.Model):
    __tablename__ = "budget_categorie_referentiel"

    id = db.Column(db.Integer, primary_key=True)
    compte_id = db.Column(db.Integer, db.ForeignKey("budget_compte_referentiel.id"), nullable=False, index=True)
    nature = db.Column(db.String(10), nullable=False, default="charge", index=True)
    code = db.Column(db.String(40), nullable=True)
    libelle = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    montant_defaut = db.Column(db.Float, nullable=False, default=0.0)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    ordre = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    compte = db.relationship("BudgetCompteReferentiel", back_populates="categories")

    @property
    def label(self) -> str:
        compte = self.compte.label if self.compte else "Compte"
        return f"{compte} > {self.libelle}"



class BudgetModeleReferentiel(db.Model):
    __tablename__ = "budget_modele_referentiel"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=True)
    secteur = db.Column(db.String(80), nullable=True, index=True)
    actif = db.Column(db.Boolean, nullable=False, default=True, index=True)
    ordre = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    lignes = db.relationship(
        "BudgetModeleLigneReferentiel",
        back_populates="modele",
        cascade="all, delete-orphan",
        order_by="BudgetModeleLigneReferentiel.ordre.asc()",
    )


class BudgetModeleLigneReferentiel(db.Model):
    __tablename__ = "budget_modele_ligne_referentiel"

    id = db.Column(db.Integer, primary_key=True)
    modele_id = db.Column(db.Integer, db.ForeignKey("budget_modele_referentiel.id"), nullable=False, index=True)
    categorie_id = db.Column(db.Integer, db.ForeignKey("budget_categorie_referentiel.id"), nullable=False, index=True)
    montant_defaut = db.Column(db.Float, nullable=True)
    ordre = db.Column(db.Integer, nullable=False, default=0)
    commentaire = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    modele = db.relationship("BudgetModeleReferentiel", back_populates="lignes")
    categorie = db.relationship("BudgetCategorieReferentiel")
