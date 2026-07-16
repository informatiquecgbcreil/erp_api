# P0.3B — Notes de refonte menu

## Objectif

Rendre le menu compréhensible sans connaître la structure technique de l'application.

## Menu simple

Entrées principales :
- Accueil
- Participants
- Présences
- Activités
- Finances
- Bilans
- Autres

## Menu expert

Entrées principales :
- Accueil
- Publics & parcours
- Activités & présences
- Finances
- Bilans & exports
- Ressources
- Administration

## Principes retenus

- Les routes existantes sont conservées.
- Les permissions RBAC restent les gardes-fous.
- Les écrans avancés restent accessibles, mais moins exposés en mode simple.
- Les finances sont regroupées autour du hub `/finance`.

## À tester

- Connexion admin technique.
- Connexion utilisateur direction / finance.
- Connexion utilisateur secteur.
- Mode Simple puis mode Expert.
- Accès à :
  - Finances ;
  - Vue simple ;
  - Pilotage annuel ;
  - Prévisionnels ;
  - Dépenses ;
  - Participants ;
  - Présences ;
  - Administration.
