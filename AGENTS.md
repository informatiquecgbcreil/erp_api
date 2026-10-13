# Consignes pour les agents de code (Claude, Codex, Cursor…)

Ce fichier est lu automatiquement par les assistants de programmation au
début de chaque session. Il vaut pour **toute** modification du dépôt.

## Pour qui vous travaillez

La personne responsable du projet en est l'**architecte fonctionnelle** :
animatrice ou animateur numérique en centre social, elle connaît le métier
sur le bout des doigts mais **ne relit pas le code Python**. Concrètement :

- Ce sont les tests et la CI qui relisent à sa place. Une modification sans
  test qui la prouve n'est pas terminée.
- Expliquez ce que vous avez fait **en français simple** : ce qui change
  pour les utilisateurs, ce qui a été vérifié et comment, ce qui ne l'a pas
  été. Ne dites jamais « c'est fait » sans avoir lancé les tests.
- Une question métier (CAF, SENACS, QPV, RGPD, financeurs) se pose à
  cette personne avant de coder. Ne devinez pas une règle de déclaration.
- Un sujet = une branche = une demande. N'élargissez pas une demande de
  vous-même ; signalez ce que vous avez remarqué à côté.

L'application est distribuée à d'autres centres sociaux (installateur
Windows) : un bug ne touche pas qu'un poste, il touche des structures sans
informaticien.

## À lire avant de modifier du code

**`docs/PASSATION.md` §7, « Les invariants à ne jamais casser »**, fait
foi : migrations défensives, façade publique en liste blanche, aucun nom
de participant sur une page publique, jetons à usage unique, colonne
`secteur` = clé d'imputation statistique, RBAC, étanchéité des salaires,
flux agenda = feuille de temps officielle. §8 décrit les tests et la CI.

## Les règles

1. **Ne jamais casser l'existant.** Pas de suppression de fonctionnalité,
   de colonne ou de table sans demande explicite. Des centres ont des
   données en production : une migration doit les conserver et les
   reprendre (voir `migrations/versions/ea3a8eed9c2f_territoire_du_centre.py`,
   qui pose le territoire historique sur les bases existantes et rien sur
   une base neuve).
2. **Migrations Alembic** : défensives (vérifier avant d'ajouter), rejouables,
   **une seule tête** (commande dans `docs/PASSATION.md` §7.1). Ne jamais
   modifier une migration déjà publiée : en écrire une nouvelle. Elles
   s'exécutent au démarrage du service — une migration qui plante empêche
   le centre de démarrer.
3. **Tests obligatoires.** Toute modification arrive avec ses tests
   (`tests/`, données de test suffixées par un identifiant aléatoire pour ne
   pas se marcher dessus). Avant de rendre la main :
   `python -m pytest tests/<fichiers concernés>`, puis la suite complète ou
   la CI. Dès qu'on touche au SQL (booléens, fonctions, `ILIKE`, accents),
   tester aussi sur PostgreSQL (`TESTS_DATABASE_URL`, `docs/PASSATION.md`
   §8.2) : SQLite pardonne ce que la production refuse.
4. **L'installateur Windows doit continuer à se construire.** Une nouvelle
   bibliothèque Python s'ajoute à `requirements.txt` **et** à
   `desktop/requirements-windows.lock` (avec ses dépendances et leurs
   empreintes, Python 3.13 Windows x64). Aucun outil que l'utilisateur
   devrait installer à la main, aucun CDN : le JavaScript vit dans
   `app/static/vendor`.
5. **Données personnelles et droits.** Toute route sensible porte
   `@require_perm("domaine:action")` ; une nouvelle permission va dans
   `PERMS_AUTO_GRANT` (`app/rbac.py`), sinon personne ne l'a après la mise
   à jour. Rien de nominatif dans les journaux, les exports de diagnostic
   ou les pages publiques (`app/services/participant_privacy.py`).
6. **Rien de propre à un centre en dur.** Pas de « Creil », de quartier ou de
   QPV écrit dans le code : le territoire se lit dans
   `app/services/territoire.py`, l'identité dans
   `app/services/instance_settings.py`. Ce qui reste propre à Creil est
   recensé dans `docs/AUDIT-SUIVI.md` (points ouverts).
7. **Aucun secret dans le dépôt.** Il est public et sous licence AGPL :
   tout ce qui y entre est diffusé.

## Conventions du code

- Monolithe Flask, blueprints par domaine, rendu Jinja2, **pas d'étape de
  build front**. SQLAlchemy + Alembic. PostgreSQL en production, SQLite en
  développement et en test.
- Noms et commentaires **en français**. Les commentaires expliquent le
  *pourquoi* (le bug ou la règle métier qu'ils évitent), pas le *quoi*.
- Textes affichés : français, **vouvoiement**, compréhensibles par une
  secrétaire d'accueil ou un bénévole. Un nouvel écran a son entrée d'aide
  dans `app/aide/contenu.py`.
- Les apostrophes sont échappées dans le HTML rendu (`&#39;`) : dans les
  tests, chercher un morceau de texte sans apostrophe.

## Commandes

```bash
pip install -r requirements-dev.txt
python run_waitress.py                      # application locale (SQLite, assistant /setup/)
python -m pytest                            # suite complète, SQLite, 10 à 20 minutes
python -m pytest tests/test_territoire.py   # un fichier
TESTS_DATABASE_URL=postgresql+psycopg://user:mdp@localhost:5432/erp python -m pytest
```

CI : `.github/workflows/tests.yml` (SQLite, PostgreSQL 16 et 18) à chaque
push ; `.github/workflows/windows-installer.yml` construit et recette
l'installateur **après** que les tests sont passés. Une croix rouge se
corrige avant toute livraison, jamais en désactivant un test.
