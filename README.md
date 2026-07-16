# App Gestion

Application web de gestion interne pour structure associative, sociale ou centre social. Elle centralise le suivi des publics, des activités, des présences, des projets, des subventions, des dépenses, des bilans, de l'insertion, de la pédagogie, de l'inventaire et des indicateurs d'impact.

L'objectif principal est de relier le travail de terrain aux obligations de pilotage et de reporting : justificatifs de présence, suivi des financements, bilans financeurs, contrôle qualité des données et tableaux de bord.

> **Reprise / passation** : si vous devez reprendre la maintenance ou faire
> revivre l'application après une panne, commencez par
> [`docs/PASSATION.md`](docs/PASSATION.md) — production réelle, procédure de
> redéploiement d'urgence, sauvegardes et invariants à ne pas casser.

---

## Sommaire

- [Fonctionnalités principales](#fonctionnalités-principales)
- [Architecture technique](#architecture-technique)
- [Prérequis](#prérequis)
- [Installation locale](#installation-locale)
- [Configuration](#configuration)
- [Premier démarrage](#premier-démarrage)
- [Commandes utiles](#commandes-utiles)
- [Tests et intégration continue](#tests-et-intégration-continue)
- [Déploiement](#déploiement)
- [Sauvegarde et restauration](#sauvegarde-et-restauration)
- [Maintenance opérationnelle](#maintenance-opérationnelle)
- [Sécurité](#sécurité)
- [Structure du dépôt](#structure-du-dépôt)
- [Dépannage](#dépannage)
- [Pistes d'évolution](#pistes-dévolution)

---

## Fonctionnalités principales

### Accueil et navigation

- Tableau de bord avec indicateurs, alertes et actions rapides.
- Mode d'interface simple ou expert.
- Navigation filtrée selon les droits de l'utilisateur.
- Support des modes desktop, mobile, tablette et kiosk.
- Personnalisation des raccourcis et widgets du tableau de bord.

### Publics et participants

- Liste et recherche de participants.
- Création, édition et synthèse de fiche participant.
- Détection et fusion de doublons.
- Anonymisation et suppression contrôlée.
- Suivi insertion et parcours selon les modules activés.

### Activités, ateliers et présences

- Gestion des ateliers et sessions.
- Émargement et suivi des présences.
- Mode kiosk pour saisie terrain.
- Attestations de présence.
- Modèles d'émargement.
- Archivage de documents et envoi par email lorsque le SMTP est configuré.
- Suivi pédagogique, compétences et évaluations selon les parcours.

### Projets, budgets et subventions

- Gestion des projets et actions.
- Budget charges / produits / ventilation / synthèse.
- Suivi des dépenses et pièces justificatives.
- Pilotage des subventions.
- Liaison entre subventions, projets, dépenses et bilans.
- Exports CSV et XLSX selon les écrans.

### Bilans, indicateurs et qualité de données

- Bilans globaux, sectoriels et par subvention.
- Bilans lourds avec export documentaire.
- Statistiques d'activité et d'impact.
- Contrôles de qualité de données.
- Audit de navigation technique pour repérer les liens ou endpoints cassés.

### Administration

- Assistant de premier démarrage.
- Gestion des utilisateurs.
- RBAC : rôles et permissions.
- Paramètres d'instance : nom de l'application, structure, logos, URL publique, SMTP.
- Référentiels métiers selon les modules.

---

## Architecture technique

L'application est une application Flask structurée en blueprints.

Technologies principales :

- Python 3.13 recommandé en CI.
- Flask.
- Flask-SQLAlchemy.
- Flask-Migrate / Alembic.
- Flask-Login.
- Flask-WTF.
- Waitress pour un démarrage simple en environnement serveur.
- SQLite par défaut en local.
- PostgreSQL recommandé en production ou en multi-utilisateur.
- Pytest pour les tests.

Points techniques importants :

- L'application expose un endpoint de santé : `/healthz`.
- Les migrations peuvent être appliquées automatiquement au démarrage.
- Le RBAC est initialisé au démarrage lorsque les tables nécessaires existent.
- Les warnings et erreurs applicatives sont journalisés dans un fichier rotatif `erreurs.log`.
- Les fichiers utilisateurs sont stockés dans un dossier d'uploads configurable.

---

## Prérequis

### Obligatoires

- Python 3.11 ou plus récent. Python 3.13 est utilisé par la CI.
- `pip`.
- Un shell compatible : PowerShell / CMD sous Windows, Bash sous Linux/macOS.

### Recommandés en production

- PostgreSQL.
- Un reverse proxy HTTPS si l'application est exposée hors LAN.
- Une stratégie de sauvegarde automatisée.
- Un compte SMTP pour les emails de réinitialisation ou d'envoi de documents.

### Optionnels

- `pg_dump` et `psql` pour les sauvegardes/restaurations PostgreSQL.
- NSSM, systemd ou un autre superviseur de service pour exécuter l'application en continu.

---

## Installation locale

### 1. Cloner le dépôt

```bash
git clone <url-du-depot>
cd juin
```

### 2. Créer un environnement virtuel

Linux/macOS :

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows :

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

### 3. Installer les dépendances

Pour utiliser l'application :

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Pour développer et lancer les tests :

```bash
python -m pip install -r requirements-dev.txt
```

### 4. Configurer les variables d'environnement

En développement, l'application peut démarrer avec sa configuration par défaut et une base SQLite locale.

Pour une configuration plus propre, créez un fichier `.env` local ou exportez les variables dans votre shell. Le fichier `.env` est ignoré par Git.

Exemple minimal de `.env` local :

```env
ERP_ENV=development
SECRET_KEY=change-me-local-dev
ERP_HOST=127.0.0.1
ERP_PORT=8000
```

Exemple avec PostgreSQL :

```env
ERP_ENV=development
SECRET_KEY=change-me-local-dev
DATABASE_URL=postgresql://user:password@localhost:5432/app_gestion
ERP_HOST=127.0.0.1
ERP_PORT=8000
```

> Remarque : les scripts `start_linux.sh` et `start_windows.bat` tentent de copier `.env.example` si `.env` est absent. Si aucun `.env.example` n'est présent dans votre copie du dépôt, créez simplement `.env` manuellement ou exportez les variables dans l'environnement.

### 5. Démarrer l'application

Démarrage direct avec Waitress :

```bash
python run_waitress.py
```

Puis ouvrir :

```text
http://127.0.0.1:8000/
```

Sous Windows, le script fourni peut être utilisé :

```bat
start_windows.bat
```

Sous Linux/macOS :

```bash
./start_linux.sh
```

---

## Configuration

La configuration se fait principalement par variables d'environnement.

### Variables générales

| Variable | Exemple | Description |
|---|---|---|
| `ERP_ENV` | `development` ou `production` | Environnement applicatif. |
| `SECRET_KEY` | chaîne longue aléatoire | Clé secrète Flask. Obligatoire en production. |
| `APP_NAME` | `App Gestion` | Nom affiché de l'application. |
| `ORGANIZATION_NAME` | `Ma structure` | Nom de la structure. |
| `MAX_CONTENT_LENGTH` | `10485760` | Taille maximale des uploads, en octets. |
| `WTF_CSRF_TIME_LIMIT` | `28800` | Durée de validité CSRF, en secondes. |

### Serveur Waitress

| Variable | Exemple | Description |
|---|---|---|
| `ERP_HOST` | `127.0.0.1` ou `0.0.0.0` | Interface d'écoute. |
| `ERP_PORT` | `8000` | Port HTTP. |
| `ERP_THREADS` | `12` | Nombre de threads Waitress. |

### Base de données

| Variable | Exemple | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db` | URL DB prioritaire si `SQLALCHEMY_DATABASE_URI` absent. |
| `SQLALCHEMY_DATABASE_URI` | `sqlite:///instance/database.db` | URL SQLAlchemy explicite. |
| `DB_AUTO_UPGRADE_ON_START` | `1` | Lance les migrations Alembic au démarrage. |
| `DB_ENABLE_LEGACY_SCHEMA_PATCH` | `0` | Active des patchs legacy légers. À utiliser avec prudence. |

Si aucune URL de base n'est fournie, l'application utilise SQLite dans `instance/database.db`.

### URL publique, cookies et QR codes

| Variable | Exemple | Description |
|---|---|---|
| `ERP_PUBLIC_BASE_URL` | `https://erp.example.org` | URL publique utilisée pour emails et QR codes. |
| `SESSION_COOKIE_SECURE` | `1` | Force les cookies HTTPS-only. Auto-activé si l'URL publique commence par `https://`. |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Politique SameSite du cookie de session. |

### Uploads et médias

| Variable | Exemple | Description |
|---|---|---|
| `APP_UPLOAD_DIR` | `/srv/app-gestion/uploads` | Dossier de stockage des pièces jointes, logos et médias. |

Par défaut, les uploads sont stockés dans `static/uploads`.

### Journalisation

| Variable | Exemple | Description |
|---|---|---|
| `ERP_LOG_DIR` | `/var/log/app-gestion` | Dossier où écrire `erreurs.log`. |

Si `ERP_LOG_DIR` est absent, les logs applicatifs sont écrits dans `instance/logs`.

### SMTP et emails

| Variable | Exemple | Description |
|---|---|---|
| `MAIL_HOST` | `smtp.example.org` | Serveur SMTP. |
| `MAIL_PORT` | `587` ou `465` | Port SMTP. `465` utilise SSL implicite. |
| `MAIL_USERNAME` | `user@example.org` | Identifiant SMTP. |
| `MAIL_PASSWORD` | `secret` | Mot de passe SMTP. |
| `MAIL_USE_TLS` | `1` | Active STARTTLS sur les ports non SSL implicites. |
| `MAIL_SENDER` | `no-reply@example.org` | Adresse expéditeur. |
| `MAIL_TIMEOUT_SECONDS` | `10` | Timeout SMTP. |

### Réinitialisation de mot de passe

| Variable | Exemple | Description |
|---|---|---|
| `PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS` | `3600` | Durée de validité des liens de reset. |
| `PASSWORD_RESET_ALLOW_DEBUG_LINK` | `0` | Affiche un lien de reset en debug local si l'email ne part pas. Ne pas activer en production. |

---

## Premier démarrage

Au premier démarrage, si aucun utilisateur n'existe, l'application redirige vers l'assistant d'installation :

```text
/setup/
```

L'assistant permet de configurer :

- le nom de l'application ;
- le nom de la structure ;
- l'URL publique ;
- les paramètres SMTP ;
- le premier compte administrateur.

Après validation, connectez-vous avec le compte créé.

---

## Commandes utiles

### Lancer l'application

```bash
python run_waitress.py
```

### Lancer les tests

```bash
python -m pytest
```

### Lancer le préflight de déploiement

```bash
python tools/preflight_deploy.py
```

### Vérifier rapidement la fiabilité de l'installation

```bash
python tools/run_reliability_checks.py
```

Avec vérification du reset password :

```bash
python tools/run_reliability_checks.py --check-password-reset
```

Avec obligation de comptes de test spécifiques :

```bash
python tools/run_reliability_checks.py --require-test-users
```

### Créer des utilisateurs de test

```bash
python tools/create_test_users.py
```

### Importer un référentiel de compétences depuis CSV

```bash
python tools/import_skills_csv.py <chemin-du-fichier.csv>
```

### Sauvegarder l'instance

```bash
python tools/backup_instance.py
```

### Restaurer l'instance

```bash
python tools/restore_instance.py --db <sauvegarde.db-ou.sql> --uploads <uploads.zip>
```

---

## Tests et intégration continue

Les tests sont écrits avec Pytest et configurés via `pytest.ini`.

Ils couvrent notamment :

- démarrage applicatif ;
- endpoint `/healthz` ;
- routes principales ;
- installation initiale ;
- authentification ;
- RBAC ;
- pages sensibles ;
- exports ;
- journalisation des erreurs et avertissements.

Commande locale :

```bash
python -m pytest
```

La CI GitHub Actions lance automatiquement les tests sur :

- `push` ;
- `pull_request`.

La CI installe Python 3.13, installe `requirements-dev.txt`, puis exécute :

```bash
python -m pytest
```

---

## Déploiement

### Déploiement simple sur serveur interne ou LAN

1. Installer Python.
2. Cloner le dépôt.
3. Créer un environnement virtuel.
4. Installer `requirements.txt`.
5. Définir les variables d'environnement.
6. Lancer le préflight.
7. Lancer l'application avec Waitress.
8. Configurer un service système pour garder l'application active.

Exemple de variables pour un serveur LAN en HTTP :

```env
ERP_ENV=production
SECRET_KEY=remplacer-par-une-cle-tres-longue-et-aleatoire
ERP_HOST=0.0.0.0
ERP_PORT=8000
ERP_PUBLIC_BASE_URL=http://192.168.1.10:8000
APP_UPLOAD_DIR=/srv/app-gestion/uploads
ERP_LOG_DIR=/srv/app-gestion/logs
DB_AUTO_UPGRADE_ON_START=1
```

### Déploiement avec PostgreSQL

Exemple :

```env
ERP_ENV=production
SECRET_KEY=remplacer-par-une-cle-tres-longue-et-aleatoire
DATABASE_URL=postgresql://app_gestion:motdepasse@127.0.0.1:5432/app_gestion
ERP_HOST=127.0.0.1
ERP_PORT=8000
ERP_PUBLIC_BASE_URL=https://erp.example.org
SESSION_COOKIE_SECURE=1
APP_UPLOAD_DIR=/srv/app-gestion/uploads
ERP_LOG_DIR=/var/log/app-gestion
DB_AUTO_UPGRADE_ON_START=1
```

### Avec reverse proxy HTTPS

Pour une exposition hors LAN, placer l'application derrière un reverse proxy HTTPS :

- Nginx ;
- Apache ;
- Caddy ;
- Traefik ;
- proxy d'hébergement équivalent.

Dans ce cas :

- `ERP_HOST` peut rester à `127.0.0.1` ;
- le proxy écoute publiquement en HTTPS ;
- `ERP_PUBLIC_BASE_URL` doit commencer par `https://` ;
- `SESSION_COOKIE_SECURE=1` est recommandé.

### Préflight avant mise en production

Avant chaque déploiement ou mise à jour :

```bash
python tools/preflight_deploy.py
```

Le script vérifie notamment :

- l'environnement ;
- la présence d'une clé secrète ;
- le type de base ;
- l'URL publique ;
- l'accessibilité du dossier d'uploads.

---

## Sauvegarde et restauration

### Sauvegarde

```bash
python tools/backup_instance.py
```

Le script produit dans `backups/` :

- une sauvegarde de la base ;
- une archive zip des uploads.

Pour SQLite, la base est copiée en `.db`.

Pour PostgreSQL, le script utilise `pg_dump` et produit un `.sql`.

### Restauration

```bash
python tools/restore_instance.py --db backups/ma_sauvegarde.db --uploads backups/ma_sauvegarde_uploads.zip
```

ou, avec PostgreSQL :

```bash
python tools/restore_instance.py --db backups/ma_sauvegarde.sql --uploads backups/ma_sauvegarde_uploads.zip
```

### Recommandations

- Faire une sauvegarde avant chaque mise à jour.
- Tester régulièrement une restauration sur une instance de préproduction.
- Stocker une copie des sauvegardes hors du serveur applicatif.
- Ne pas versionner le dossier `backups/`.

---

## Maintenance opérationnelle

### Logs applicatifs

Les erreurs et avertissements sont écrits dans :

```text
$ERP_LOG_DIR/erreurs.log
```

ou, si `ERP_LOG_DIR` est absent :

```text
instance/logs/erreurs.log
```

Le fichier est rotatif : l'application conserve plusieurs fichiers de log pour éviter une croissance illimitée.

### Santé applicative

Endpoint :

```text
/healthz
```

Réponse attendue :

```json
{"status":"ok"}
```

### Migrations

Par défaut, `DB_AUTO_UPGRADE_ON_START=1` permet d'appliquer les migrations au démarrage.

En environnement sensible, une stratégie plus contrôlée est recommandée :

1. arrêter l'application ;
2. sauvegarder base et uploads ;
3. appliquer la mise à jour ;
4. démarrer l'application ;
5. vérifier `/healthz` ;
6. vérifier les logs ;
7. lancer les tests ou smoke checks disponibles.

### Contrôles recommandés après mise à jour

```bash
python tools/preflight_deploy.py
python tools/run_reliability_checks.py
python -m pytest
```

En production, `python -m pytest` doit être lancé uniquement si l'environnement est prévu pour cela et ne pointe pas vers une base réelle sensible. Les tests du dépôt sont conçus pour utiliser une base jetable lorsqu'ils sont lancés dans leur configuration standard.

---

## Sécurité

### À faire impérativement en production

- Définir `ERP_ENV=production`.
- Définir une `SECRET_KEY` longue, aléatoire et confidentielle.
- Utiliser HTTPS si l'application sort du LAN.
- Définir `SESSION_COOKIE_SECURE=1` derrière HTTPS.
- Restreindre l'accès réseau si l'application est interne.
- Sauvegarder régulièrement la base et les uploads.
- Vérifier les droits RBAC des utilisateurs.
- Surveiller `erreurs.log`.

### Données sensibles

L'application peut contenir :

- données personnelles de participants ;
- informations de suivi social ou insertion ;
- documents justificatifs ;
- données financières ;
- pièces jointes ;
- traces d'activité ;
- emails utilisateurs.

Il faut donc appliquer les pratiques habituelles :

- comptes nominatifs ;
- principe du moindre privilège ;
- mots de passe robustes ;
- sauvegardes protégées ;
- accès serveur limité ;
- suppression ou anonymisation selon les règles internes.

### RBAC

Les rôles principaux sont :

- `admin_tech` : administration technique, utilisateurs, RBAC, contrôle ;
- `direction` : accès global ;
- `finance` : accès global orienté pilotage ;
- `responsable_secteur` : accès métier large, généralement borné au secteur.

Les permissions sont initialisées automatiquement par le bootstrap RBAC.

---

## Structure du dépôt

```text
app/
  __init__.py                 # fabrique Flask, blueprints, init DB, contexte Jinja
  auth/                       # connexion, déconnexion, reset password
  admin/                      # administration utilisateurs, RBAC, instance
  activite/                   # ateliers, sessions, émargement, attestations, kiosk
  participants/               # fiches participants, recherche, doublons
  projets/                    # projets, actions, budgets, finance
  budget/                     # dépenses et justificatifs
  bilans/                     # bilans, exports, narratifs
  statsimpact/                # statistiques d'impact et exports
  insertion/                  # parcours insertion, certifications, référentiels
  pedagogie/                  # logique pédagogique
  previsionnel/               # budget prévisionnel et référentiels
  templates/                  # templates Jinja
  services/                   # services métier partagés
  utils/                      # helpers partagés

docs/
  navigation/                 # notes UX/navigation/responsive
  pedagogie/                  # notes pédagogie

migrations/
  versions/                   # migrations Alembic

tests/
  conftest.py                 # fixtures de tests
  test_*.py                   # tests Pytest

tools/
  preflight_deploy.py         # diagnostic avant déploiement
  backup_instance.py          # sauvegarde base + uploads
  restore_instance.py         # restauration base + uploads
  run_reliability_checks.py   # smoke checks de fiabilité

config.py                     # configuration applicative
run_waitress.py               # démarrage Waitress
wsgi.py                       # point d'entrée WSGI
requirements.txt              # dépendances runtime
requirements-dev.txt          # dépendances dev/test
pytest.ini                    # configuration Pytest
```

---

## Dépannage

### L'application redirige vers `/setup/`

C'est normal si aucun utilisateur n'existe encore. Créez le premier compte administrateur via l'assistant.

### Erreur `SECRET_KEY par défaut interdite en production`

En production, définissez une vraie clé :

```bash
export SECRET_KEY="une-cle-longue-aleatoire"
export ERP_ENV=production
```

Sous Windows :

```bat
set SECRET_KEY=une-cle-longue-aleatoire
set ERP_ENV=production
```

### Les emails de reset ne partent pas

Vérifier :

- `MAIL_HOST` ;
- `MAIL_PORT` ;
- `MAIL_USERNAME` ;
- `MAIL_PASSWORD` ;
- `MAIL_USE_TLS` ;
- `MAIL_SENDER` ;
- `ERP_PUBLIC_BASE_URL`.

Consulter aussi `erreurs.log`.

### Les QR codes ou liens email pointent vers la mauvaise adresse

Définir :

```env
ERP_PUBLIC_BASE_URL=https://votre-url-publique
```

ou, en LAN :

```env
ERP_PUBLIC_BASE_URL=http://adresse-ip-du-serveur:8000
```

### Les uploads ne fonctionnent pas

Vérifier :

- `APP_UPLOAD_DIR` ;
- les droits d'écriture du processus applicatif ;
- l'espace disque ;
- la taille maximale `MAX_CONTENT_LENGTH`.

Le préflight peut aider :

```bash
python tools/preflight_deploy.py
```

### Une mise à jour casse le démarrage

Procédure recommandée :

1. consulter `erreurs.log` ;
2. vérifier les variables d'environnement ;
3. vérifier la connexion DB ;
4. restaurer la sauvegarde si nécessaire ;
5. relancer le préflight ;
6. relancer les smoke checks.

---

## Pistes d'évolution

Évolutions à faible effort et fort impact :

- ajouter un `.env.example` versionné ;
- ajouter une page d'administration système ;
- remplacer les sorties console techniques par du logging propre ;
- ajouter un rate limiting sur connexion et reset password ;
- durcir la restauration des archives zip ;
- ajouter une page d'aide par parcours métier ;
- ajouter des états vides pédagogiques ;
- automatiser quelques captures responsive.

Évolutions moyen terme :

- audit log métier transverse ;
- assistant de préparation de bilan financeur ;
- assistant qualité de données ;
- refactor progressif des gros modules routes vers services ;
- exports PDF financeurs ;
- tests navigateur end-to-end.

Évolutions long terme :

- multi-structure / multi-tenant ;
- API interne ;
- workflows de validation ;
- intégration comptable ;
- tableaux de bord décisionnels avancés.

---

## Licence et usage

Aucune licence n'est déclarée dans ce dépôt au moment de la rédaction de ce README. Avant toute diffusion, réutilisation ou publication externe, ajouter une licence explicite adaptée au contexte du projet.

---

## Support opérationnel minimal

Pour qualifier rapidement l'état d'une instance :

```bash
git status --short --branch
python tools/preflight_deploy.py
python tools/run_reliability_checks.py
python -m pytest
```

Puis vérifier dans le navigateur :

- `/healthz` ;
- `/` ;
- `/dashboard` ;
- `/controle/navigation` ;
- les logs dans `erreurs.log`.
