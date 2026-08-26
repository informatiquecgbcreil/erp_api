# Supprimer définitivement une fiche participant

Deux gestes différents, à ne pas confondre :

| | Anonymisation | Suppression définitive |
|---|---|---|
| Ce qui part | l'identité (nom, coordonnées, insertion…) | **tout**, y compris présences et cotisations |
| Ce qui reste | présences, statistiques, bilans | rien |
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
avant l'effacement, avec : qui a supprimé, quand, l'identité complète de la
fiche (nom, prénom, date de naissance, coordonnées, secteur de création),
le décompte de chaque type de donnée détruite, et le motif éventuel.

C'est la seule trace qui subsiste : elle permet de savoir qui a été effacé,
et de recréer la fiche à l'identique en cas de fausse manœuvre.

## Ce qui est effacé

Présences (et consommations de matériel associées), inscriptions,
évaluations, notes et pièces jointes de passeport, suivis d'objectifs,
évaluations Hart, heures de bénévolat, adhésions et leurs paiements,
réponses aux questionnaires, orientations accès aux droits, défis
transition, et l'ensemble du dossier insertion. Les fichiers correspondants
(signatures, pièces jointes) sont retirés du disque. Les tentatives de
connexion au portail sont conservées mais détachées de la personne.

Le code vit dans `app/services/participant_suppression.py` ; l'inventaire
des données concernées y est déclaré en un seul endroit.
