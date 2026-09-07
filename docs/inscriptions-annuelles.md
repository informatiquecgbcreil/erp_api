# Les inscriptions annuelles

Le module suit une personne depuis le bulletin rempli à l'accueil jusqu'à sa
première participation. Il est **séparé** des autres — il a sa page, ses
droits, sa base — mais il **parle** aux modules Participants, Activité et
Adhésions plutôt que de dupliquer leur travail.

Accès : **Espace Publics → Inscriptions annuelles** (`/inscriptions-annuelles/`).

## L'année scolaire

Une inscription vaut pour une **année scolaire**, de septembre à août, désignée
par son année de rentrée : `2026` s'affiche partout « 2026-2027 ». C'est
exactement la convention du module Adhésions & participation (`TarifBareme`,
`Cotisation`), donc un règlement d'inscription et une adhésion tombent
toujours sur la même année sans conversion.

Chaque campagne est indépendante : les bulletins d'une année passée restent
consultables et exportables, ils ne se mélangent jamais avec ceux en cours.

## Le parcours d'un bulletin

```
   saisie  ──créer la fiche──▶  en_attente  ──1re présence pointée──▶  active
      │                              │                                    │
      └──────────── annulee ◀────────┴────────────────────────────────────┘
```

### 1. Saisie

Le formulaire reprend le bulletin papier :

| Bloc | Contenu |
|---|---|
| Identité | nom, prénom *(obligatoires)*, adresse, code postal, ville, e-mail, téléphone |
| Compléments facultatifs | date de naissance, genre — non demandés sur le bulletin, mais recopiés dans la fiche participant s'ils sont là (âge et genre alimentent les bilans SENACS et financeurs) |
| Orientation | **secteur qui fait venir** la personne, choisi dans le référentiel des secteurs |
| Ateliers | cases à cocher sur les ateliers actifs **+ un champ libre** pour ce qui n'est pas encore programmé |
| Bénévolat | envie oui/non ; si oui : **pour quoi faire** (champ libre), **grille jour × demi-journée** à cocher, et **« je ne sais pas encore »** |
| Remarques | note libre sur la personne |

Seuls le nom et le prénom sont obligatoires : à l'accueil, on enregistre ce
qu'on a. Retirer l'envie de bénévolat efface mission et créneaux, pour qu'une
personne ne reste pas dans la grille des bénévoles par accident.

### 2. Transformation en fiche participant

Le bouton **« Créer la fiche participant »** :

- crée le `Participant` avec les coordonnées du bulletin ;
- lui pose le statut spécial `attente_premiere_participation` (colonne
  `participant.statut_inscription`) — la fiche est utilisable partout
  (annuaire, recherche, émargement), elle est simplement **marquée comme pas
  encore venue** ;
- crée une `InscriptionActivite` pour chaque atelier coché : la **jauge** et la
  **liste d'attente** du module Activité s'appliquent normalement. Un atelier
  complet ne bloque jamais la transformation : le message remonte et le reste
  passe.

**Anti-doublon** : avant de créer, l'application propose les fiches
ressemblantes (même détecteur que `/participants/new`). Le bouton
**« Rattacher »** relie le bulletin à la fiche existante au lieu d'en créer une
seconde. Le rattachement ne **comble que les trous** de la fiche : il ne
remplace jamais une donnée déjà présente par celle d'un bulletin, qui peut
être plus ancienne. Une fiche déjà venue au centre garde son statut « actif ».

### 3. Première participation : la bascule automatique

Dès qu'une présence est pointée pour la personne, le statut d'attente tombe :
le participant repasse `actif` et le bulletin passe `active` avec la date de
la séance.

C'est **automatique et sans point d'oubli** : les présences se créent depuis
cinq endroits (émargement, saisie en grille, kiosque, import Excel, pointage
des inscrits). Plutôt que de répéter l'appel dans chacun — et d'en oublier un
au prochain module — le module écoute la **session SQLAlchemy**
(`before_flush` sur les `PresenceActivite` nouvellement créées, voir
`app/services/inscriptions_annuelles.py`). Aucune porte d'entrée ne peut
passer à travers.

Un **filet de sécurité** (`rafraichir_statuts`) tourne à l'ouverture de la
page : il rattrape les présences arrivées autrement que par l'application
(reprise de données, import massif, correction en base).

### 4. Règlement

**« Confirmer le règlement »** enregistre montant, mode de paiement, date et
note sur le bulletin. Tant que ce n'est pas fait, l'inscription reste dans
« reste à régler ».

En cochant **« Créer aussi l'adhésion »**, l'application enregistre dans le
module Adhésions & participation :

- une `Cotisation` de type `adhesion_individuelle` pour l'année scolaire
  (montant repris du barème en vigueur, ou du montant saisi) ;
- un `Paiement` du montant encaissé.

Le règlement compte alors dans les impayés, la caisse et les bilans, **sans
double saisie**. L'adhésion n'est créée que si la fiche participant existe :
sinon le règlement est quand même confirmé sur le bulletin — on ne bloque pas
l'accueil — et un message dit ce qu'il reste à faire.

**Dé-confirmer** un règlement (erreur de saisie) remet le bulletin en attente
mais **ne touche pas** à l'adhésion ni aux versements déjà enregistrés : un
mouvement de caisse ne se réécrit pas depuis ici, il s'annule dans le module
qui en tient le journal.

### 5. Annulation

Un désistement s'**annule** (statut `annulee`), il ne se supprime pas : il
compte dans le bilan de la campagne et garde la trace du travail d'accueil.
La suppression pure est réservée aux saisies en double, et elle est **refusée**
dès qu'une fiche participant dépend du bulletin.

## Impression

**« Fiche à imprimer »** (`/inscriptions-annuelles/<id>/fiche`) sort la feuille
à remettre à l'accueil au moment du paiement : identité, ateliers choisis,
bénévolat avec la grille des disponibilités, remarques, et un cadre
« partie réservée à l'accueil » avec montant, cases de mode de paiement, date
et signatures. Le montant est pré-rempli si le tarif d'adhésion de l'année est
saisi au barème. Les menus de l'application ne s'impriment pas.

## Export XLSX

**« Exporter en XLSX »** (`/inscriptions-annuelles/export.xlsx?annee=…`)
télécharge la campagne complète, dans le périmètre visible par la personne
connectée. Trois feuilles :

1. **Inscriptions** — une ligne par bulletin, **toutes** les données récoltées
   (30 colonnes : identité, coordonnées, secteur, ateliers cochés, champ
   libre, bénévolat et disponibilités, règlement, fiche participant, date de
   1re participation, qui a saisi et quand). Filtre automatique et en-têtes
   figés.
2. **Synthèse** — compteurs de campagne, répartition par secteur qui fait
   venir, ateliers les plus demandés.
3. **Bénévolat** — la grille jour × demi-journée et la liste nominative avec
   coordonnées, prête pour la réunion d'équipe.

## Droits

| Permission | Ce qu'elle ouvre |
|---|---|
| `inscriptions_annuelles:view` | Voir la liste, le détail, imprimer une fiche |
| `inscriptions_annuelles:edit` | Saisir, modifier, créer/rattacher la fiche participant, annuler, supprimer |
| `inscriptions_annuelles:reglement` | Confirmer ou dé-confirmer un règlement |
| `inscriptions_annuelles:export` | Télécharger l'export XLSX |

Attribution par défaut : direction, finance et responsable de secteur ont
tout ; le compte technique (`admin_tech`) consulte seulement.

**Portée secteur** : sans `scope:all_secteurs`, on ne voit que les bulletins de
son secteur — ceux qu'on a saisis (`created_secteur`) et ceux dont on est le
secteur qui fait venir (`secteur_orienteur`). Les bulletins **sans secteur**
restent visibles de tous : à l'accueil on ne sait pas toujours qui envoie la
personne, et un bulletin invisible est un bulletin perdu.

## RGPD

Le bulletin recopie des données personnelles (identité, adresse, e-mail,
téléphone) : il est branché sur les trois circuits existants.

- **Anonymisation** d'une fiche (manuelle ou purge automatique) : les bulletins
  liés sont anonymisés en même temps — identité, coordonnées, date de
  naissance et remarques effacées. Ce qui décrit la **demande** (ateliers
  souhaités, mission de bénévolat, créneaux) survit : ces données ne désignent
  personne et les bilans de campagne restent justes.
- **Purge automatique** : une inscription annuelle **vaut activité**. Quelqu'un
  qui vient de s'inscrire à la rentrée n'a encore aucune présence — sans cela
  la purge l'anonymiserait avant sa première venue.
- **Suppression définitive** d'une fiche : les bulletins partent avec elle,
  créneaux de bénévolat et ateliers souhaités compris, et sont annoncés dans
  l'inventaire affiché avant de confirmer.
- **Droit d'accès (article 15)** : l'export RGPD d'une personne contient une
  feuille « Inscriptions annuelles ».

## Schéma de données

| Table | Rôle |
|---|---|
| `inscription_annuelle` | le bulletin |
| `inscription_annuelle_atelier` | ateliers souhaités (n-n vers `atelier_activite`) |
| `inscription_annuelle_dispo` | créneaux bénévolat (jour × demi-journée) |
| `participant.statut_inscription` | `actif` / `attente_premiere_participation` |

Migration : `cd12ef34ab56_inscriptions_annuelles.py` — défensive et purement
additive, aucune table ni colonne existante n'est modifiée.

> Note d'implémentation : la relation `disponibilites` **n'utilise pas**
> `passive_deletes`. SQLite (base par défaut) n'applique pas les
> `ON DELETE CASCADE` sans `PRAGMA foreign_keys`, et des créneaux orphelins
> feraient sauter la contrainte d'unicité au prochain identifiant réutilisé.
> L'ORM supprime donc les lignes lui-même ; le `CASCADE` reste le filet
> PostgreSQL.
