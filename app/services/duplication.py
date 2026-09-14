"""Refaire ce qui existe déjà, sans le ressaisir.

Constat d'usage : rien ne se dupliquait dans l'application (à l'exception
des questionnaires et des budgets prévisionnels). Chaque septembre, un
atelier reconduit à l'identique était recréé de zéro — nom, secteur, type,
capacité, durée, salle de référence, motifs, compétences, modèles
d'émargement. Chaque mois, une charge récurrente était retapée avec son
fournisseur, son mode de paiement et sa répartition entre financeurs.

Deux principes tenus ici :

1. **On ne recopie que ce qui se reconduit.** L'historique (séances,
   présences, justificatifs) reste attaché à l'original : une copie qui
   emporterait les présences de l'an dernier fausserait les bilans.
2. **On ne relie jamais deux objets dans le dos de l'utilisateur.** La
   continuité statistique entre deux ateliers (``continuity_parent_id``)
   fusionne leurs chiffres : dupliquer pour créer une variante — « atelier
   informatique débutants » à partir de « atelier informatique » — ne doit
   pas additionner silencieusement leurs publics. Le lien reste un choix
   explicite, proposé sur le formulaire d'édition juste après la copie.
"""
from __future__ import annotations

import calendar
from datetime import date

from app.extensions import db
from app.models import AtelierActivite, Depense, DepenseAffectation


def _nom_de_copie(nom: str, suffixe: str = "(copie)") -> str:
    """« Atelier informatique » -> « Atelier informatique (copie) ».

    Le nom est borné à la longueur de la colonne : un libellé déjà long ne
    doit pas faire échouer la duplication au moment du commit.
    """
    base = (nom or "Sans nom").strip()
    propose = f"{base} {suffixe}".strip()
    if len(propose) <= 200:
        return propose
    return f"{base[: 200 - len(suffixe) - 1]} {suffixe}"


def dupliquer_atelier(atelier: AtelierActivite) -> AtelierActivite:
    """Recrée un atelier avec le même paramétrage, sans son historique.

    Copié : secteur et imputation, type, capacité ou heures disponibles,
    durée par défaut, salle de référence, motifs, modèles d'émargement,
    compétences et modules pédagogiques.

    Non copié : les séances et tout ce qui y pend (présences, bilans), le
    lien de continuité statistique, la corbeille. La copie naît active,
    même si l'original a été désactivé en fin d'année — c'est bien pour la
    rouvrir qu'on la duplique.

    L'objet est ajouté à la session mais pas committé : l'appelant reste
    maître de la transaction.
    """
    copie = AtelierActivite(
        secteur=atelier.secteur,
        est_intersecteur=bool(atelier.est_intersecteur),
        nom=_nom_de_copie(atelier.nom),
        description=atelier.description,
        type_atelier=atelier.type_atelier,
        capacite_defaut=atelier.capacite_defaut,
        heures_dispo_defaut_mois=atelier.heures_dispo_defaut_mois,
        duree_defaut_minutes=atelier.duree_defaut_minutes,
        espace_id=atelier.espace_id,
        motifs_json=atelier.motifs_json,
        modele_docx_collectif=atelier.modele_docx_collectif,
        modele_docx_individuel=atelier.modele_docx_individuel,
        is_active=True,
        is_deleted=False,
        continuity_parent_id=None,
    )
    # Entrer dans la session AVANT de poser les associations : un autoflush
    # déclenché par la lecture des compétences trouverait sinon un objet
    # détaché et abandonnerait silencieusement le rattachement inverse.
    db.session.add(copie)
    copie.competences = list(atelier.competences or [])
    copie.modules = list(atelier.modules or [])
    return copie


def mois_suivant(reference: date) -> date:
    """Même jour le mois suivant, ramené au dernier jour quand il n'existe pas.

    Un loyer payé le 31 janvier se reconduit le 28 (ou 29) février, pas le
    3 mars : décaler d'un nombre fixe de jours ferait glisser la charge d'un
    mois sur l'autre au fil de l'année.
    """
    annee = reference.year + (1 if reference.month == 12 else 0)
    mois = 1 if reference.month == 12 else reference.month + 1
    dernier = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, min(reference.day, dernier))


def dupliquer_depense(depense: Depense, *, a_la_date: date | None = None) -> Depense:
    """Reconduit une charge : mêmes libellé, montant, fournisseur, imputation.

    La date de paiement avance d'un mois par défaut — c'est le geste visé,
    la charge mensuelle qu'on retape douze fois par an. Une dépense sans
    date prend la date du jour : on ne devine pas une échéance.

    La répartition entre financeurs (``affectations``) est recopiée telle
    quelle : c'est la partie la plus longue à ressaisir, et celle qu'on
    oublie le plus souvent. Les justificatifs, eux, ne sont PAS recopiés —
    la facture de janvier ne prouve pas la dépense de février.
    """
    if a_la_date is not None:
        echeance = a_la_date
    elif depense.date_paiement:
        echeance = mois_suivant(depense.date_paiement)
    else:
        echeance = date.today()

    copie = Depense(
        ligne_budget_id=depense.ligne_budget_id,
        charge_projet_id=depense.charge_projet_id,
        libelle=depense.libelle,
        montant=depense.montant,
        fournisseur=depense.fournisseur,
        mode_paiement=depense.mode_paiement,
        type_depense=depense.type_depense,
        date_paiement=echeance,
        statut=depense.statut or "valide",
        # Ni la référence de pièce ni le lien vers la facture d'origine : un
        # même numéro de facture sur deux dépenses est une anomalie comptable.
        reference_piece=None,
        facture_ligne_id=None,
        est_supprimee=False,
    )
    db.session.add(copie)
    db.session.flush()  # il faut l'id pour rattacher les affectations

    for affectation in depense.affectations or []:
        db.session.add(DepenseAffectation(
            depense_id=copie.id,
            source_type=affectation.source_type,
            subvention_id=affectation.subvention_id,
            ligne_budget_id=affectation.ligne_budget_id,
            libelle_source=affectation.libelle_source,
            montant=affectation.montant,
            commentaire=affectation.commentaire,
        ))
    return copie
