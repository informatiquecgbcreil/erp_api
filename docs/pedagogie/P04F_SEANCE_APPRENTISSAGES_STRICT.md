# P0.4F — Séance et apprentissages explicites

## Problème

Le parcours simplifié affichait encore des objectifs déduits automatiquement depuis :

- projets ;
- ateliers ;
- modules ;
- plan Projet → Atelier → Module ;
- objectifs parents/enfants.

Résultat : une séance MLVO pouvait afficher des objectifs sans rapport avec MLVO.

## Décision de conception

On arrête de deviner.

Une séance affiche uniquement les apprentissages explicitement rattachés à cette séance.

## Nouvelle règle

Dans `/pedagogie/parcours` :

- l’utilisateur choisit une séance ;
- il ajoute ce qui est travaillé aujourd’hui ;
- seuls ces éléments deviennent évaluables ;
- les anciens objectifs détectés via projets/modules deviennent des suggestions ;
- une suggestion n’est active que si l’utilisateur clique sur “Ajouter”.

## Effet attendu

Le module devient cohérent :

```text
séance → apprentissages choisis → observation des participants → résultats
```

## Ce que ça ne change pas

- Pas de migration.
- Pas de nouvelle table.
- Les anciens objectifs restent en base.
- Le mode kiosk continue d’écrire dans `ObjectifSuivi`.
