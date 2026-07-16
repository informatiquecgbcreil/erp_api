# Patch UX lot 5 final — 2026-04-09

## Objectif
Finaliser l’harmonisation UI sans toucher au cœur métier.

## Changements principaux
- harmonisation des libellés de navigation pour les rendre plus explicites ;
- passage du switch d’affichage de `Expert` à `Complet` ;
- amélioration des libellés de recherche globale (facettes plus humaines) ;
- conservation du filtre actif lors des recherches successives ;
- CTA de la page de résultats dédiés adaptés au type de résultat ;
- légers ajustements de vocabulaire sur les pages Dashboard, Personnes, Présences et Bilans.

## Fichiers modifiés
- app/templates/layout.html
- app/templates/dashboard.html
- app/templates/participants/list.html
- app/templates/activite/index.html
- app/templates/bilans_dashboard.html
- app/templates/search_results.html

## Vérifications recommandées
1. topbar en mode simple et complet ;
2. recherche globale avec facettes ;
3. page dédiée de résultats ;
4. page Personnes ;
5. page Présences ;
6. page Bilans et statistiques.
