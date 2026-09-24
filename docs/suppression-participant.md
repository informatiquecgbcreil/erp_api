# Supprimer définitivement une fiche participant

Deux gestes différents, à ne pas confondre :

| | Anonymisation | Suppression définitive |
|---|---|---|
| Ce qui part | les identifiants et textes individuels traités par la fonction, signatures et pièces de passeport | fiche, présences et données individuelles rattachées |
| Ce qui reste | présences, compteurs, montants et certains attributs statistiques | trace d'audit minimale et pièces comptables encaissées, détachées de la fiche |
| Pour qui | une personne réellement venue qui demande l'effacement | une **erreur de saisie** (doublon, fiche de test) |
| Réversible | non | non |

La règle : si la personne est réellement passée par la structure, **anonymisez**.
Supprimer ferait baisser rétroactivement des chiffres déjà transmis aux
financeurs. La suppression est là pour retirer ce qui n'aurait jamais dû
exister.

## Comment faire

Fiche du participant → section **« Supprimer définitivement »** (en bas).
L'écran annonce d'abord ce qui sera détruit : nombre de présences,
d'inscriptions, d'adhésions, de notes… Si la fiche est vierge, il le dit
aussi — c'est le cas typique de l'erreur de saisie.

Pour valider, il faut **retaper le nom de famille exact** (la casse et les
espaces n'importent pas). Un motif facultatif peut être saisi ; il est
conservé au journal.

## Garde-fous

- Permission `participants:delete`.
- **Portée** : sans `scope:all_secteurs`, on ne peut pas supprimer une
  personne ayant des présences dans un autre secteur — l'anonymisation est
  proposée à la place.
- **Confirmation par le nom** : un bouton seul se clique par erreur.
- Les listes ne suppriment plus en un clic : leur bouton renvoie vers la
  fiche.

## Ce qui est journalisé

Une entrée `participant.delete` dans **Administration → Journal**, écrite
dans la même transaction que l'effacement : auteur, date, identifiant de la
fiche, secteur/date de création, décompte des éléments traités et motif éventuel.
Le nom, la naissance complète, l'adresse, le mail et le téléphone ne sont plus
recopiés dans les nouvelles traces. Éviter toute identité dans le motif libre.

Cette trace ne permet pas de recréer la fiche. Les instantanés nominatifs des
anciens journaux et leurs règles de conservation nécessitent encore une reprise
dédiée ; ce correctif ne les efface pas rétroactivement.

## Ce qui est effacé

Présences (et consommations de matériel associées), inscriptions,
évaluations, notes et pièces jointes de passeport, suivis d'objectifs,
évaluations Hart, heures de bénévolat, cotisations sans encaissement,
réponses aux questionnaires, orientations accès aux droits, défis
transition, et l'ensemble du dossier insertion. Les fichiers correspondants
(signatures, pièces jointes) sont retirés du disque après validation de la
transaction. Un échec disque reste en file d'attente pour réessai ; un rollback
ne détruit pas le fichier. Les tentatives de
connexion au portail sont conservées mais détachées de la personne.

Les cotisations déjà payées et leurs paiements sont conservés sans lien vers la
fiche supprimée : leur suppression aurait changé le théorique de caisse.

## Limites de l'anonymisation

L'anonymisation remplace le nom/prénom par `ANONYME P<id>`, retire les coordonnées,
la naissance complète (année conservée), la géolocalisation, les données de
séjour, textes individuels, notes, pièces et signatures traités par le service.
Les réponses numériques et historiques nécessaires aux statistiques restent.
Le foyer de la personne est détaché ; les autres membres gardent leurs fiches.

Cela ne garantit pas une anonymisation irréversible de tous les documents :
les feuilles collectives déjà éditées, copies envoyées, exports, anciennes traces
et sauvegardes peuvent encore identifier la personne. Leur traitement et leur
durée de conservation demandent une revue documentaire de la structure.

Le code vit dans `app/services/participant_suppression.py` ; l'inventaire
des données concernées y est déclaré en un seul endroit.
