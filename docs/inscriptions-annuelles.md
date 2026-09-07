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
| Identité | nom, prénom *(obligatoires)*, adresse, code postal, ville, e-mail, téléphone, **date de naissance**, genre |
| Orientation | **secteur qui fait venir** la personne, choisi dans le référentiel des secteurs |
| Foyer | **individuelle ou familiale** ; si familiale, autant de membres qu'il en faut (prénom, nom, date de naissance, lien de filiation facultatif) |
| Ateliers | cases à cocher sur les ateliers actifs **+ un champ libre** pour ce qui n'est pas encore programmé |
| Bénévolat | envie oui/non ; si oui : **pour quoi faire** (champ libre), **grille jour × demi-journée** à cocher, et **« je ne sais pas encore »** |
| Remarques | note libre sur la personne |

Seuls le nom et le prénom sont obligatoires : à l'accueil, on enregistre ce
qu'on a. Retirer l'envie de bénévolat efface mission et créneaux, pour qu'une
personne ne reste pas dans la grille des bénévoles par accident.

#### Les membres du foyer

Une ligne par personne, ajoutée à volonté (bouton « Ajouter une personne »).
Le **nom se reprend de l'inscription** s'il est laissé vide — le cas courant
d'une fratrie. Le **lien de filiation reste facultatif** : beaucoup de
configurations familiales n'entrent pas dans une case, et l'accueil n'a pas à
en faire une condition d'inscription.

Une ligne dont le prénom reste vide est ignorée : ajouter une ligne puis ne
pas la remplir n'est pas une erreur à signaler.

Un membre qui a **déjà une fiche participant** n'est plus supprimable depuis
le bulletin — même en repassant l'inscription en « individuelle ». Faire
disparaître une personne de l'application d'un coup de case à cocher n'est
jamais le bon comportement : on la détache de son foyer depuis sa fiche.

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

**Pour une inscription familiale**, la même opération :

- ouvre **une fiche participant par membre déclaré**, avec les coordonnées du
  foyer (adresse, ville, téléphone — le téléphone d'un enfant est celui de son
  parent). L'e-mail, lui, reste sur la fiche de l'inscrit·e principal·e : il
  est personnel et sert aux envois individuels ;
- **regroupe tout le monde dans un `Foyer`** en réutilisant
  `regrouper_en_foyer`, le service que déclenche déjà le rapprochement manuel
  depuis une fiche participant. La famille composée à l'inscription est donc
  strictement le même objet que celle composée après coup — pas une notion
  parallèle. Sa prudence s'applique aussi : il refuse de fusionner deux
  familles déjà constituées, et le message remonte tel quel ;
- **génère les cotisations** (voir plus bas).

Un membre déjà connu de l'application (mêmes nom et prénom **et** même date de
naissance) est **rattaché à sa fiche** plutôt que dupliqué. Le rattachement
n'a lieu que sur ce signal fort : un homonyme sans date de naissance reste une
personne différente — mieux vaut une fiche à fusionner ensuite (l'application
sait le faire) qu'un enfant rattaché à la mauvaise famille.

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

### 4. Ce que ça coûte

Le coût se calcule sur **deux étages**, tels que la structure facture :

| Étage | Tarif | Multiplicateur |
|---|---|---|
| Adhésion | `adhesion_individuelle` **ou** `adhesion_familiale` selon le type d'inscription | une fois |
| Participation | `participation` | **par personne du foyer** |

Avec un barème à 7 € / 10 € / 30 € :

- une personne seule : `7 + 30 × 1` = **37 €** ;
- une mère et son enfant : `10 + 30 × 2` = **70 €**.

Le détail est affiché ligne par ligne sur la fiche de l'inscription et sur la
feuille imprimable, et estimé **en direct pendant la saisie** (ajouter une
personne met le total à jour). Le montant qui fait foi reste celui calculé au
serveur à l'enregistrement.

#### Proratisation en cours d'année

Les trois montants sont lus dans le barème **à la date de référence** (la date
d'inscription). C'est là que se joue le prorata : une ligne de barème qui
démarre au 1er janvier remplace celle de septembre pour toutes les
inscriptions postérieures, sans toucher à celles déjà enregistrées — leur
montant dû est figé à la création.

Chaque type se prorate **indépendamment** : on peut faire tomber la
participation à 15 € en janvier sans bouger l'adhésion. Le barème se règle
dans **Ressources → Tarifs adhésions & participation**.

Un tarif absent du barème ne bloque rien : il compte pour 0 € et l'écran le
signale, avec un lien vers le barème.

### 5. Règlement — tout, une partie, ou rien

À la transformation, le bulletin **génère dans le module Adhésions &
participation** :

- **une adhésion** — portée par le **foyer** si l'inscription est familiale,
  par la personne sinon ;
- **une participation par personne ayant une fiche**.

Les montants sont figés au tarif en vigueur à la date de référence. La
génération est **idempotente** : la relancer (bouton « Mettre les cotisations
à jour ») complète ce qui manque — un membre qui reçoit sa fiche après coup,
un barème saisi trop tard — sans jamais créer de doublon.

À partir de là, **le module Adhésions fait foi**. L'accueil saisit la somme
reçue ; elle se **ventile** sur les cotisations impayées, l'adhésion d'abord,
puis les participations dans l'ordre des personnes. La personne tend un billet
pour « son inscription », pas une enveloppe par ligne comptable.

L'état se lit en trois mots :

| État | Sens |
|---|---|
| **Non réglé** | une somme est due, rien n'a été versé |
| **Partiellement réglé** | au moins un euro versé, il reste dû |
| **À jour** | tout est réglé |

Le bouton **« Solder »** encaisse exactement ce qu'il reste. Un surplus n'est
jamais affecté : il n'existe pas de trop-perçu qui traînerait sans obligation
en face.

**Avant la transformation**, quand aucune fiche n'existe encore, la somme
s'accumule sur le bulletin — on ne bloque pas l'accueil — et elle est
**reportée telle quelle** dans le module Adhésions à la création de la fiche.
Le bouton « Remettre à zéro » ne sert qu'à ce cas : une fois les versements
enregistrés dans Adhésions, ils s'annulent là où ils sont journalisés, pour
que la caisse et les bilans restent justes.

Les colonnes `reglement_statut` / `reglement_du` / `reglement_montant` du
bulletin sont un **miroir** : elles permettent de filtrer et d'exporter sans
recalculer, mais la vérité reste dans les cotisations. Elles sont recalées à
chaque ouverture de la liste, pour qu'un versement saisi depuis une fiche
participant se voie ici aussi.

### 6. Où l'état s'affiche

Le même calcul (`etat_reglement_participant`, dans le module Adhésions) sert
partout, pour qu'une personne ne soit jamais « à jour » sur un écran et
« impayée » sur un autre :

- **fiche participant** : un badge en tête de fiche, cliquable vers le détail
  des cotisations ;
- **émargement** : un badge sur chaque ligne de présence et sur les inscrits
  « à pointer » — l'accueil voit qui doit encore payer au moment où il coche ;
- **liste des inscriptions** : colonne « Règlement » avec le réglé sur le dû,
  et un filtre à quatre entrées (reste à régler / rien / partiel / complet).

Le total additionne tout ce que la personne doit pour l'année : ses
cotisations propres **et** l'adhésion familiale de son foyer, qui la couvre.
Vu de l'enfant d'une famille à 70 €, la dette est donc de 40 € (10 € d'adhésion
partagée + ses 30 € de participation).

### 7. Annulation

Un désistement s'**annule** (statut `annulee`), il ne se supprime pas : il
compte dans le bilan de la campagne et garde la trace du travail d'accueil.
La suppression pure est réservée aux saisies en double, et elle est **refusée**
dès qu'une fiche participant dépend du bulletin.

## Impression

**« Fiche à imprimer »** (`/inscriptions-annuelles/<id>/fiche`) sort la feuille
à remettre à l'accueil au moment du paiement : identité, **composition du
foyer**, ateliers choisis, bénévolat avec la grille des disponibilités,
remarques, et un cadre « partie réservée à l'accueil ».

Ce cadre porte le **détail chiffré** (adhésion, participation × nombre de
personnes, total dû, déjà réglé, reste à régler) puis les cases à cocher :
mode de paiement, et **intégralement réglé / acompte / rien réglé**. Une fois
l'inscription soldée, la feuille l'imprime au lieu des cases vides. Les menus
de l'application ne s'impriment pas.

## Export XLSX

**« Exporter en XLSX »** (`/inscriptions-annuelles/export.xlsx?annee=…`)
télécharge la campagne complète, dans le périmètre visible par la personne
connectée. Trois feuilles :

1. **Inscriptions** — une ligne par bulletin, **toutes** les données récoltées
   (identité, coordonnées, secteur, type d'inscription et composition du
   foyer, ateliers cochés, champ libre, bénévolat et disponibilités, détail
   du coût et état du règlement, fiche participant, date de 1re
   participation, qui a saisi et quand). Filtre automatique et en-têtes figés.
2. **Synthèse** — compteurs de campagne, dont le nombre de familles, les
   personnes couvertes, les trois états de règlement et les totaux dû /
   encaissé / restant. Répartition par secteur qui fait venir et ateliers les
   plus demandés.
3. **Foyers** — une ligne par personne : qui compose quelle famille, avec le
   lien de filiation, la date de naissance, l'âge et le numéro de fiche.
4. **Bénévolat** — la grille jour × demi-journée et la liste nominative avec
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
| `inscription_annuelle` | le bulletin (type d'inscription, foyer, état du règlement) |
| `inscription_annuelle_membre` | les autres membres du foyer déclarés |
| `inscription_annuelle_atelier` | ateliers souhaités (n-n vers `atelier_activite`) |
| `inscription_annuelle_dispo` | créneaux bénévolat (jour × demi-journée) |
| `participant.statut_inscription` | `actif` / `attente_premiere_participation` |

Le module **ne crée aucune table de foyer, de tarif ni de règlement** : il
réutilise `Foyer`, `TarifBareme`, `Cotisation` et `Paiement` du module
Adhésions & participation. Les fonctions partagées ajoutées là-bas
(`cout_inscription`, `etat_reglement_participant`,
`etats_reglement_par_participant`, `repartir_versement`) servent aussi bien à
ce module qu'à la fiche participant et à l'émargement.

Migrations : `cd12ef34ab56_inscriptions_annuelles.py` puis
`de23fa45bc67_inscription_annuelle_foyer.py` — défensives et purement
additives, aucune table ni colonne existante n'est supprimée ou retypée.

> Note d'implémentation : la relation `disponibilites` **n'utilise pas**
> `passive_deletes`. SQLite (base par défaut) n'applique pas les
> `ON DELETE CASCADE` sans `PRAGMA foreign_keys`, et des créneaux orphelins
> feraient sauter la contrainte d'unicité au prochain identifiant réutilisé.
> L'ORM supprime donc les lignes lui-même ; le `CASCADE` reste le filet
> PostgreSQL.
