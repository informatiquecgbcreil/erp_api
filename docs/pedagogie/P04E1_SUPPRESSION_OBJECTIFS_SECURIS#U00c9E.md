# P0.4E.1 — Suppression sécurisée des objectifs

## Problème

PostgreSQL bloque la suppression d’un objectif déjà utilisé dans `objectif_suivi`.

C’est normal : supprimer l’objectif casserait l’historique pédagogique.

## Correction

La suppression physique est maintenant bloquée si l’objectif possède :

- au moins un suivi d’objectif ;
- au moins un objectif enfant.

Dans la page objectifs :

- les objectifs utilisés affichent un bouton désactivé “Utilisé” ;
- les objectifs non utilisés restent supprimables ;
- en cas de contrainte imprévue, l’erreur est convertie en message utilisateur au lieu d’un crash.

## Limite

La table `objectif` ne possède pas encore de champ `actif` ou `archive`.
Donc on bloque la suppression des objectifs utilisés au lieu de les archiver.

## Suite recommandée

Une prochaine migration pourrait ajouter :

```text
objectif.actif
objectif.archived_at
```

pour permettre de masquer les anciens objectifs sans les supprimer.
