# P0.4G — Recentrage vers le suivi des apprentissages

Base de travail : `AppGestion.zip` fournie le 30/04/2026 à 16h43.

## Décision

Le module ne doit plus exposer en première intention : référentiels, modules, objectifs généraux/spécifiques/opérationnels, ressentis et plans.

Le langage utilisateur devient :

```text
Séance → apprentissages choisis → observation des participants → progrès
```

## Routes ajoutées

```text
/pedagogie/
/pedagogie/parcours
/pedagogie/apprentissages
```

## Règle métier

Une séance n’affiche que les apprentissages explicitement rattachés à cette séance. Les anciens objectifs liés aux modules/projets deviennent des suggestions, pas des éléments actifs.

## Migration

Aucune.
