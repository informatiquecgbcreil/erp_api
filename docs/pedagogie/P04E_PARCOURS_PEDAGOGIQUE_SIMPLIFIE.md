# P0.4E — Parcours pédagogique simplifié

## Objectif

Créer une façade simple au-dessus des tables existantes.

Au lieu de commencer par :

```text
Référentiels → Modules → Objectifs → Plan → Kiosk → Pilotage
```

l’utilisateur voit :

```text
Je prépare → J’évalue → Je regarde
```

## Nouvelle route

```text
/pedagogie/parcours
```

## Ce que la page fait

- choisir une séance ;
- filtrer par atelier ;
- afficher les projets liés ;
- afficher les modules détectés ;
- afficher les objectifs attendus ;
- afficher les participants présents ;
- afficher les premiers résultats issus des suivis d’objectifs ;
- ouvrir directement le kiosk avec la séance préfiltrée ;
- garder les outils experts accessibles mais secondaires.

## Ce que la page ne fait pas encore

- elle ne fusionne pas `Evaluation` et `ObjectifSuivi` ;
- elle ne refait pas les calculs globaux de pilotage ;
- elle ne crée pas de nouveaux objectifs en assistant guidé ;
- elle ne modifie pas les tables.

## Suite logique

Ce patch confirme une direction de conception :

```text
usage quotidien simple
+
mode expert pour structurer
```

La suite sera de consolider le moteur d’indicateurs avec cette même logique.
