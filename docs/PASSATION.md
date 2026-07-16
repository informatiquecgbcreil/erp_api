# Document de reprise / passation — App Gestion

> **À quoi sert ce document ?** À ce que quelqu'un d'autre que le mainteneur
> actuel puisse **faire revivre l'application** (panne, départ, absence longue)
> puis **la maintenir** sans rien casser. Il complète le `README.md` (référence
> technique exhaustive) et `installation/LISEZMOI-INSTALLATION.md` (installation
> guidée grand public) : ici, on donne la vue d'ensemble, la production réelle,
> les procédures d'urgence et les règles à ne jamais enfreindre.
>
> **Règle d'or du repreneur :** avant TOUTE intervention sur le serveur,
> faire une sauvegarde (`backup_now.bat` ou bouton « Sauvegarder maintenant »
> dans Administration → Sauvegardes). Toutes les procédures ci-dessous
> supposent que c'est fait.

Les champs `[À COMPLÉTER]` sont des informations que seul le mainteneur
actuel détient : ils doivent être remplis (ou pointer vers le coffre à
mots de passe) pour que ce document soit réellement utilisable.

---

## 1. Ce qu'est cette application

**App Gestion** est l'outil de gestion interne du **Centre Georges Brassens
(Creil)**, centre socio-culturel. Elle centralise :

- les publics (participants, familles, insertion) ;
- les activités et les **présences** (émargement kiosque sur tablette,
  signature à distance par lien personnel, saisie en grille) ;
- la pédagogie (parcours, compétences, échelle de Hart) ;
- les **subventions** et leur justification (feuilles de temps par financeur,
  dossiers de justification, radar d'échéances) ;
- les budgets, dépenses, cotisations, caisse ;
- les bilans (SENACS, bilans financeurs, statistiques d'impact) ;
- l'agenda personnel des salariés (flux iCal consommé par Google Agenda,
  lui-même relevé par la plateforme CSAT le 10 de chaque mois — ce flux
  est donc **la feuille de temps officielle** de certains salariés :
  sa fiabilité est critique).

**Criticité :** l'application contient des données personnelles de
participants (dont mineurs et suivi social), des signatures manuscrites,
des données financières. Elle est le support des justifications envoyées
aux financeurs (CAF, État, Ville…). Sa perte sans sauvegarde serait grave ;
sa fuite le serait davantage.

**Volumétrie du code (repère, juillet 2026) :** ~49 000 lignes de Python,
201 templates, 377 routes, 105 tables, 61 migrations, ~490 tests.

---

## 2. L'architecture en cinq minutes

- **Monolithe Flask** (app factory dans `app/__init__.py`), découpé en
  blueprints par domaine métier (`app/activite/`, `app/main/`,
  `app/kiosk/`, `app/participants/`, `app/budget/`…).
- **Rendu serveur Jinja2**, pas de framework JavaScript, **aucune étape de
  build front** : on modifie un template, on recharge la page.
- **Base de données : PostgreSQL en production**, SQLite en développement
  et pour les tests. L'ORM est SQLAlchemy, les migrations Alembic
  (Flask-Migrate), appliquées automatiquement au démarrage
  (`DB_AUTO_UPGRADE_ON_START=1`).
- **Serveur d'application : Waitress** (`run_waitress.py`), exécuté comme
  **service Windows via NSSM** (démarre avec la machine, sans session ouverte).
- **Droits : RBAC maison** (rôles → permissions, ~80 permissions), initialisé
  au démarrage. Décorateur `require_perm("...")` sur les routes, helper
  `can("...")` dans les templates.
- **Dépendances volontairement minces** (voir `requirements.txt`) : Flask,
  SQLAlchemy, Alembic, Waitress, psycopg2, openpyxl/docxtpl pour les exports,
  Pillow, segno (QR codes). Rien d'exotique.

Pour la carte détaillée du dépôt : section « Structure du dépôt » du `README.md`.

---

## 3. La production réelle

| Élément | Valeur |
|---|---|
| Serveur | Windows Server 2019 — machine : `[À COMPLÉTER : nom/IP du serveur]` |
| Dossier d'installation | `C:\AppGestion` (convention des scripts `deploy/windows/` et `installation/Installer.ps1`) |
| Service Windows | `AppGestion` (NSSM) — logs dans `C:\AppGestion\logs\service-out.log` et `service-err.log` |
| Base de données | PostgreSQL locale, base `appgestion` — mot de passe : `[À COMPLÉTER : coffre]` |
| URL LAN | `http://gestion.cgb` `[À COMPLÉTER : confirmer host/port exacts et comment le nom DNS est servi — fichier hosts, DNS interne ?]` |
| Exposition publique | **Tailscale Funnel**, uniquement pour la façade kiosque (voir §7.2) — compte Tailscale : `[À COMPLÉTER]` |
| Configuration | fichier `.env` à la racine de `C:\AppGestion` (non versionné — c'est LE fichier à sauvegarder à part, il contient `SECRET_KEY` et `DATABASE_URL`) |
| Tâche planifiée sauvegarde | `AppGestionBackupDaily` (Planificateur de tâches Windows), quotidienne vers 2h00–2h30 |
| Copie hors serveur des sauvegardes | disque physique + cloud — `[À COMPLÉTER : quel disque, quel cloud, quel mécanisme de copie, quels identifiants]` |
| Compte GitHub du dépôt | `informatiquecgbcreil/juin` — accès : `[À COMPLÉTER]` |
| SMTP (emails) | `[À COMPLÉTER : renseigné dans .env / Administration → Paramètres ?]` |

**Où sont les données ?** Trois choses et trois seulement :

1. la base PostgreSQL (tout le métier) ;
2. le dossier d'uploads (pièces jointes, logos, signatures) —
   `APP_UPLOAD_DIR`, par défaut `static/uploads` sous le dossier d'installation ;
3. le fichier `.env` (secrets et configuration).

Base + uploads sont couverts par la sauvegarde automatique. Le `.env` doit
avoir une copie au coffre : `[À COMPLÉTER : où ?]`.

---

## 4. Procédure d'urgence : tout redéployer de zéro

Scénario : le serveur est mort, on repart d'une machine Windows vierge.
Objectif réaliste : **application de nouveau en service en moins d'une heure**,
avec les données de la dernière sauvegarde.

### 4.1 Réinstaller l'application

1. Récupérer le code : `git clone` du dépôt GitHub, ou ZIP
   (**Code → Download ZIP**) si pas d'accès git.
2. Lancer `installation/Installer.ps1` (clic droit → *Exécuter avec
   PowerShell*). Il installe **tout** : Python, PostgreSQL, l'environnement
   virtuel, le service Windows NSSM, la tâche planifiée de sauvegarde.
   Accepter les valeurs par défaut (`C:\AppGestion`, port 8000), répondre
   **o** à « accessible depuis d'autres postes ».
3. **Noter le mot de passe PostgreSQL** affiché en fin d'installation.

À la fin, le navigateur s'ouvre sur `/setup/` (assistant premier démarrage).
**Ne pas créer de compte** : on va restaurer les données à la place.

### 4.2 Restaurer la dernière sauvegarde

1. Récupérer le dernier **lot** de sauvegarde depuis la copie hors serveur
   (`[À COMPLÉTER : emplacement]`). Un lot = deux fichiers + une empreinte :
   - `<Structure>_<horodatage>.sql` (dump PostgreSQL)
   - `<Structure>_<horodatage>_uploads.zip` (pièces jointes)
   - `<Structure>_<horodatage>.sha256` (intégrité — optionnel mais recommandé)
2. Arrêter le service : `nssm stop AppGestion` (ou services.msc).
3. Restaurer : lancer `restore_now.bat` à la racine de `C:\AppGestion`
   et donner les deux chemins demandés. (Équivalent en ligne de commande :
   `python tools\restore_instance.py --db <fichier.sql> --uploads <fichier.zip>`.)
   Le dump est produit avec `--clean --if-exists` : il est rejouable même
   sur une base déjà peuplée.
4. Restaurer le fichier `.env` depuis le coffre (ou le recréer : voir §6).
5. Redémarrer : `nssm start AppGestion`.

### 4.3 Vérifier

1. `http://localhost:8000/healthz` → `{"status":"ok"}`.
2. Se connecter avec un compte existant, ouvrir le tableau de bord.
3. Ouvrir une fiche participant, une feuille d'émargement, la page
   Administration → Sauvegardes (elle doit lister les lots).
4. Diagnostic automatisé si doute :
   `python tools\preflight_deploy.py` puis `python tools\run_reliability_checks.py`.
5. Refaire pointer le nom LAN (`gestion.cgb`) vers la nouvelle machine
   et reconfigurer Tailscale Funnel (§7.2) si la machine a changé.

---

## 5. Sauvegardes : fonctionnement et vérification

Le code fait foi : `app/services/sauvegarde.py` (logique) +
`tools/backup_instance.py` (script appelé par la tâche planifiée) +
page **Administration → Sauvegardes** dans l'application (mêmes fonctions,
avec bouton « Sauvegarder maintenant », contrôle d'intégrité et restauration).

- Chaque sauvegarde produit dans `backups/` un **lot** : dump `.sql`
  (`pg_dump --clean --if-exists`), zip des uploads, empreinte `.sha256`.
- **Rotation automatique** : `BACKUP_RETENTION_LOTS` lots conservés (30 par
  défaut). **Alerte** dans l'application si aucune sauvegarde depuis
  `BACKUP_ALERT_DAYS` jours (2 par défaut).
- La tâche planifiée est (ré)installable par
  `deploy/windows/register_backup_task.ps1`.
- La restauration depuis l'application **vérifie l'intégrité** et crée
  d'abord une **sauvegarde de sécurité de l'état courant** — elle est donc
  le chemin le plus sûr pour une restauration à chaud.
- La copie **hors serveur** (disque physique + cloud) n'est pas gérée par
  l'application : `[À COMPLÉTER : décrire le mécanisme exact — robocopy ?
  client de synchro cloud ? fréquence ? et comment vérifier qu'il tourne]`.

**Rituel recommandé au repreneur (mensuel) :** prendre le dernier lot,
le restaurer sur un poste de test (SQLite ou PostgreSQL local), vérifier
que l'application démarre et que les données sont là. Une sauvegarde
jamais restaurée est une hypothèse, pas une sauvegarde.

---

## 6. Configuration : le fichier `.env`

Lu automatiquement au démarrage (voir `config.py`, qui documente chaque
variable). Le minimum vital en production :

```env
ERP_ENV=production
SECRET_KEY=<longue chaîne aléatoire — NE JAMAIS réutiliser celle par défaut>
DATABASE_URL=postgresql://appgestion:<mot de passe>@127.0.0.1:5432/appgestion
ERP_HOST=0.0.0.0
ERP_PORT=8000
ERP_PUBLIC_BASE_URL=http://gestion.cgb:8000
KIOSK_PUBLIC_HOST=<hôte public Tailscale Funnel, sans http:// ni port>
DB_AUTO_UPGRADE_ON_START=1
```

Points d'attention :

- **`SECRET_KEY`** signe les sessions et les jetons de réinitialisation de
  mot de passe. La changer déconnecte tout le monde (bénin) ; la perdre
  n'est pas grave ; la laisser à sa valeur par défaut est interdit en
  production (l'application refuse de démarrer).
- **`ERP_PUBLIC_BASE_URL`** sert aux QR codes et aux liens dans les emails
  (adresse LAN).
- **`KIOSK_PUBLIC_HOST`** active la **façade publique** (voir §7.2). Vide =
  aucune restriction par hôte (ne convient que si rien n'est exposé).
- Le tableau complet des variables (SMTP, uploads, logs, FTP programme,
  portail, géocodage…) est dans le `README.md`, section « Configuration ».

---

## 7. Les invariants à ne jamais casser

C'est la section la plus importante pour un repreneur qui va **modifier du
code**. Ces règles ne sont pas des préférences de style : chacune protège
une obligation légale, une donnée sensible ou la survie des mises à jour.

### 7.1 Migrations : défensives, toujours

La production a évolué pendant des mois avec des états de schéma variés.
Toute migration doit vérifier l'existence avant d'agir :

```python
bind = op.get_bind()
insp = sa.inspect(bind)
if not insp.has_table("ma_table"):
    op.create_table(...)
cols = {c["name"] for c in insp.get_columns("ma_table")}
if "ma_colonne" not in cols:
    op.add_column(...)
```

- **Jamais** de `drop_table` / `drop_column` sur des données métier sans
  décision explicite et sauvegarde préalable.
- **Une seule tête Alembic.** Après avoir créé une migration, vérifier :
  `python -c "from alembic.script import ScriptDirectory; from alembic.config import Config; print(ScriptDirectory.from_config(Config('migrations/alembic.ini')).get_heads())"`
  → une seule valeur. Deux têtes = créer une révision de fusion (il en
  existe déjà une dans l'historique : `32d3e4f5a6b7`).
- Les migrations s'appliquent au démarrage du service : une migration qui
  plante empêche l'application de démarrer. Les tests les exécutent toutes
  (la fixture `app` de `tests/conftest.py` migre une base neuve) : si
  `pytest` passe, la chaîne de migrations est saine.

### 7.2 La façade publique : liste blanche, jamais élargie à la légère

Quand une requête arrive par l'hôte `KIOSK_PUBLIC_HOST` (le tunnel Tailscale
Funnel, exposé à Internet), un `before_request` dans `app/__init__.py`
n'autorise que : `/kiosk…`, `/calendrier/…`, `/static/…`, `/healthz`.
**Tout le reste répond 403**, y compris la page de connexion.

- N'ajouter un chemin à cette liste blanche qu'après avoir répondu :
  « cette page peut-elle fuiter une donnée personnelle à un inconnu ? »
- Le test de non-régression existe (`tests/test_agenda_ics.py`,
  `tests/test_kiosk*.py`) : la façade doit laisser passer le flux agenda
  et bloquer le reste.

### 7.3 Confidentialité des pages publiques

**Aucun nom de participant ne doit apparaître sur une page ou un flux
accessible sans connexion**, à une exception près : la personne elle-même
sur SA page de signature (`/kiosk/signer/<jeton>`).

- Le flux iCal (`/calendrier/<jeton>.ics`) ne contient que des agrégats
  (« 12 présents »), jamais de noms. Testé explicitement.
- Le kiosque d'émargement n'affiche que ce qui est nécessaire à la séance
  en cours.

### 7.4 Jetons

- **Signature à distance** : `PresenceActivite.signature_token` est à
  **usage unique** — remis à `None` dès la signature enregistrée. Ne jamais
  le rendre réutilisable.
- **Flux agenda** : `User.calendar_token` est révocable par l'utilisateur
  (bouton « régénérer »). La révocation doit rester immédiate.
- Les jetons sont générés par `secrets.token_urlsafe` — jamais par un
  générateur prévisible.

### 7.5 Secteurs : `secteur` = clé d'imputation statistique

- Toutes les statistiques, bilans et feuilles de temps agrègent par la
  colonne `secteur` des séances/ateliers. **Ne pas détourner cette colonne.**
- Un atelier **intersecteur** (`AtelierActivite.est_intersecteur`) est
  *visible et utilisable par tous les secteurs*, mais ses stats vont au
  secteur porté par sa colonne `secteur` (le « secteur d'imputation »,
  choisi à la création). Visibilité et imputation sont deux choses
  distinctes : les helpers `_atelier_est_accessible` /
  `_session_est_accessible` (`app/activite/helpers.py`) gèrent la
  visibilité ; ne pas court-circuiter.

### 7.6 RBAC

- Toute nouvelle route sensible porte `@require_perm("domaine:action")`.
- Une **nouvelle permission** doit être ajoutée à `PERMS_AUTO_GRANT`
  (bootstrap RBAC) pour être accordée automatiquement aux rôles voulus sur
  une production existante — sinon personne ne l'a et la fonctionnalité
  est morte à la mise à jour.

### 7.7 Le flux agenda est une feuille de temps officielle

Le flux iCal des utilisateurs alimente leur Google Agenda, relevé par la
plateforme CSAT le 10 de chaque mois. Concrètement : **une régression sur
`app/services/calendrier.py` fausse des déclarations de temps de travail.**
Garder au moins ~45 jours de passé dans la fenêtre par défaut, ne pas
changer les UID des événements (`seance-<id>@…`, `creneau-<id>@…`,
`seance-<id>-prep@…` — des UID instables créent des doublons chez Google),
et faire tourner `tests/test_agenda_ics.py` après toute modification.

---

## 8. Développer et mettre à jour

### 8.1 Poste de développement

```bash
git clone <dépôt> && cd juin
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate.bat
pip install -r requirements-dev.txt
python run_waitress.py    # SQLite locale créée automatiquement, assistant /setup/
```

### 8.2 Tests

```bash
python -m pytest          # ~490 tests, base SQLite jetable, quelques minutes
```

- La CI GitHub Actions (`.github/workflows/tests.yml`) exécute la même
  suite à chaque push et pull request. **Ne jamais déployer un commit
  rouge en CI.**
- Pièges connus des tests : les tests roulent sur SQLite alors que la
  production est PostgreSQL (comportements légèrement différents sur les
  contraintes et le typage) ; les apostrophes françaises sont échappées
  en HTML (`&#39;`) — asserter sur des sous-chaînes sans apostrophe ;
  les messages flash sont consommés par le GET suivant.

### 8.3 Mise à jour de la production

1. Sauvegarde (`backup_now.bat` ou bouton dans l'application).
2. `git pull` dans `C:\AppGestion` (ou remplacement des fichiers depuis le
   ZIP — `Installer.ps1` sait aussi mettre à jour en réutilisant la base).
3. Si `requirements.txt` a changé :
   `.venv\Scripts\pip install -r requirements.txt`.
4. Redémarrer le service : `nssm restart AppGestion`. Les migrations
   s'appliquent toutes seules au démarrage.
5. Vérifier `/healthz`, se connecter, surveiller
   `C:\AppGestion\logs\service-err.log` et `erreurs.log` quelques minutes.
6. En cas de casse : restaurer la sauvegarde de l'étape 1 (§4.2) et
   revenir au commit précédent (`git checkout <commit>`).

---

## 9. Dette et points de vigilance connus

Un repreneur honnête doit savoir où sont les faiblesses :

- **Tests sur SQLite, production sur PostgreSQL** : une différence de
  comportement peut échapper à la suite. En cas de bug « impossible, les
  tests passent », soupçonner d'abord cet écart.
- **De la logique métier vit dans les routes** plutôt que dans
  `app/services/` pour les modules les plus anciens. Les modules récents
  (agenda, justification, intersecteur) sont mieux découpés — prendre
  ceux-là comme modèle.
- **Hétérogénéité de finition** : les modules développés en dernier
  (agenda, feuilles de temps, signature à distance) sont les mieux testés ;
  certains écrans anciens le sont moins.
- **Pas de 2FA** ; la sécurité d'accès repose sur mots de passe +
  verrouillage anti-force-brute + réseau LAN. Toute idée d'exposer
  l'application entière sur Internet exige de reposer la question.
- **HTTP nu sur le LAN** : acceptable sur un réseau maîtrisé, mais ne
  jamais faire transiter l'interface complète par le tunnel public sans
  passer par la façade (§7.2).
- **Performance jamais testée en charge** : dimensionné pour ~10
  utilisateurs simultanés et quelques milliers de participants. Largement
  suffisant aujourd'hui ; à re-vérifier si le périmètre change.
- **Pas de licence déclarée** sur le dépôt : à régler avant toute
  diffusion hors de la structure.

---

## 10. Où trouver quoi

| Besoin | Emplacement |
|---|---|
| Référence technique complète (config, dépannage) | `README.md` |
| Installation guidée grand public | `installation/LISEZMOI-INSTALLATION.md` + `installation/Installer.ps1` |
| Ce document (reprise/passation) | `docs/PASSATION.md` |
| Façade kiosque hors les murs | `docs/kiosque-hors-les-murs.md` |
| Intégration portail apprenants | `docs/INTEGRATION_PORTAIL.md` |
| Scripts serveur Windows (service, backup) | `deploy/windows/` ; raccourcis `backup_now.bat`, `restore_now.bat` à la racine |
| Variantes Linux (systemd, nginx, cron) | `deploy/linux/` |
| Diagnostic avant/après déploiement | `tools/preflight_deploy.py`, `tools/run_reliability_checks.py` |
| Migration SQLite → PostgreSQL | `migrate_sqlite_to_postgres.py` |
| Aide utilisateur | dans l'application : menu Aide, glossaire, guides |

---

## 11. Checklist « premier jour » du repreneur

1. [ ] Obtenir les accès : serveur Windows (RDP/console), compte GitHub,
       coffre à mots de passe `[À COMPLÉTER : où ?]`, compte Tailscale.
2. [ ] Se connecter au serveur, vérifier que le service `AppGestion`
       tourne et que `/healthz` répond.
3. [ ] Ouvrir Administration → Sauvegardes : vérifier la date du dernier
       lot (< 2 jours) et l'intégrité.
4. [ ] Localiser la copie hors serveur des sauvegardes et vérifier
       qu'elle est à jour.
5. [ ] Cloner le dépôt sur un poste, `pip install -r requirements-dev.txt`,
       `python -m pytest` → tout vert.
6. [ ] **Restaurer la dernière sauvegarde sur ce poste de test** et
       démarrer l'application dessus : c'est l'exercice qui prouve que la
       chaîne de survie fonctionne de bout en bout.
7. [ ] Lire le §7 (invariants) deux fois.
8. [ ] Se créer un compte `admin_tech` nominatif sur la production et
       désactiver les comptes de la personne partie.

---

*Document créé en juillet 2026. À maintenir à chaque changement
d'infrastructure (serveur, sauvegarde, tunnel) : un document de passation
périmé est plus dangereux que pas de document du tout, parce qu'on lui
fait confiance.*
