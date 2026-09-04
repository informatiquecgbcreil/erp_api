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

---

## 0. À renseigner avant tout départ (le reste du document en dépend)

Les champs `[À COMPLÉTER]` du document sont des informations que **seul le
mainteneur actuel détient**. Tant qu'ils sont vides, ce document explique
parfaitement comment faire des choses que le repreneur ne pourra pas faire :
il restera bloqué au portail, faute d'un mot de passe ou d'une adresse.

C'est la seule partie de la continuité qui ne peut pas être codée. Une
demi-journée suffit à la traiter.

| # | Information | Où la mettre | Section |
|---|---|---|---|
| 1 | Nom / IP du serveur Windows, et comment s'y connecter (RDP ? console ?) | ici, §3 | §3 |
| 2 | Mot de passe PostgreSQL de la base `appgestion` | **coffre**, jamais ici | §3 |
| 3 | Comment `gestion.cgb` est résolu (fichier `hosts` des postes ? DNS interne ?) | ici, §3 | §3 |
| 4 | Compte Tailscale (identifiant + où sont les identifiants) | **coffre** | §3, §7.2 |
| 5 | Destinations de sauvegarde hors serveur réellement en place | ici, §5 | §5 |
| 6 | Qui a accès au dépôt GitHub `informatiquecgbcreil/erp_api`, et comment en obtenir | ici, §3 | §3 |
| 7 | Paramètres SMTP (serveur, compte d'envoi) | **coffre** pour le mot de passe | §3, §6 |
| 8 | **Emplacement du coffre à mots de passe** et qui en détient la clé | ici, §3 | §3, §11 |
| 9 | Copie du fichier `.env` de production | **coffre** | §3, §6 |
| 10 | Un contact technique de secours (nom, téléphone, structure) | ici, §11 | §11 |

> **Règle de sécurité :** aucun mot de passe, jeton ou `SECRET_KEY` ne doit
> être écrit dans ce fichier. Le dépôt est public et sous licence AGPL (§9) :
> tout ce qui y entre est diffusé. Ce document dit **où** trouver les secrets,
> jamais **quels** ils sont. Un coffre `KeePass` posé sur un partage réseau,
> dont la direction détient le mot de passe maître dans une enveloppe scellée,
> fait très bien l'affaire.

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

**Volumétrie du code (repère, septembre 2026) :** ~77 000 lignes de Python,
216 templates, 380 routes, 119 tables, 69 migrations, 670 tests.
(Ces chiffres se recalculent en une commande — voir §10 — et servent surtout
à mesurer l'écart quand ce document commence à dater.)

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
| Copie hors serveur des sauvegardes | **automatique** depuis l'application : variable `BACKUP_OFFSITE_DIRS` du `.env` (voir §5) — destinations réellement en service : `[À COMPLÉTER : quel disque, quel partage, quel dossier cloud, et qui y a accès]` |
| Compte GitHub du dépôt | `informatiquecgbcreil/erp_api` — dépôt **public**, licence AGPL-3.0 (§9) : le code est lisible par tous, donc **jamais de secret versionné**. Accès en écriture : `[À COMPLÉTER : qui est propriétaire, comment obtenir les droits]` |
| SMTP (emails) | `[À COMPLÉTER : renseigné dans .env / Administration → Paramètres ?]` |

**Où sont les données ?** Trois choses et trois seulement :

1. la base PostgreSQL (tout le métier) ;
2. le dossier d'uploads (pièces jointes, logos, signatures) —
   `APP_UPLOAD_DIR`, par défaut `static/uploads` sous le dossier d'installation ;
3. le fichier `.env` (secrets et configuration).

Base + uploads sont couverts par la sauvegarde automatique, **et recopiés
hors du serveur** à chaque sauvegarde (§5). Le `.env`, lui, n'est dans aucune
sauvegarde : il doit avoir une copie au coffre — `[À COMPLÉTER : où ?]`.
C'est le seul des trois qu'une panne de serveur peut faire disparaître
définitivement par simple négligence.

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

1. Récupérer le dernier **lot** de sauvegarde depuis une destination hors
   serveur — celles listées dans `BACKUP_OFFSITE_DIRS` de l'ancien `.env`,
   en pratique `[À COMPLÉTER : emplacement]`. C'est le moment où toute la
   chaîne se joue : si ces destinations n'ont jamais été renseignées, les
   sauvegardes étaient sur le serveur perdu et il n'y a rien à restaurer.
   Un lot = deux fichiers + une empreinte :
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
6. **Rebrancher la copie hors serveur** : renseigner `BACKUP_OFFSITE_DIRS`
   dans le nouveau `.env`, redémarrer le service, puis
   `python tools\run_reliability_checks.py --require-offsite` doit sortir en
   succès. Une instance remise en service sans copie externe est une instance
   qui attend la prochaine panne dans les mêmes conditions.

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
### La copie hors serveur — le maillon qui décide de tout

Une sauvegarde rangée sur la machine qu'elle protège disparaît avec elle.
C'est le scénario §4 en entier : panne de disque, vol, rançongiciel,
réinstallation « propre » par un prestataire pressé.

La copie externe est donc **faite par l'application elle-même**, à chaque
sauvegarde, vers les destinations listées dans `BACKUP_OFFSITE_DIRS`
(fichier `.env` — disque amovible, partage réseau/NAS en chemin UNC,
dossier synchronisé par un client cloud ; plusieurs destinations séparées
par des points-virgules) :

- les **empreintes sont revérifiées à l'arrivée** : une copie tronquée par
  un disque plein ou un lien réseau coupé est vue tout de suite, pas le jour
  de la panne ;
- chaque fichier transite par un `.part` renommé en dernier : une copie
  interrompue **ne laisse jamais un lot d'apparence complète** ;
- une destination dont le dossier parent est absent (disque débranché,
  partage non monté) fait **échouer bruyamment** la copie, au lieu de
  fabriquer un dossier local qui ressemble à une sauvegarde externe sans
  en être une ;
- une destination en panne n'empêche pas les autres d'être servies ;
- la rétention s'applique aussi là-bas (`BACKUP_OFFSITE_RETENTION_LOTS`).

**Trois façons de savoir si l'on est protégé :**

| Où | Ce qu'on y voit |
|---|---|
| Administration → Sauvegardes | tableau « Copies hors serveur » : date de la dernière copie, nombre de lots, état par destination |
| Digest de notifications | une ligne d'alerte si une destination est injoignable, vide ou en retard — **et si aucune n'est configurée** |
| `python tools\run_reliability_checks.py --require-offsite` | sort en erreur si les sauvegardes ne quittent pas le serveur (utilisable en tâche planifiée) |

La tâche planifiée quotidienne (`tools/backup_instance.py`) sort elle-même
en **code d'erreur 1** si une copie n'aboutit pas : c'est ce qui fait
apparaître un échec dans le Planificateur de tâches Windows, seule façon
d'apprendre qu'on a cessé d'être protégé sans avoir à y penser.

> ⚠️ Si `BACKUP_OFFSITE_DIRS` est vide, tout ce qui précède ne s'applique
> pas et l'application le dit à chaque écran. C'est le premier réglage à
> vérifier en prenant la main sur l'instance.

**Rituel recommandé au repreneur (mensuel) :** prendre le dernier lot
**depuis la destination hors serveur** (pas depuis `backups/` : c'est la
copie externe qu'on veut éprouver), le restaurer sur un poste de test
(SQLite ou PostgreSQL local), vérifier que l'application démarre et que les
données sont là. Une sauvegarde jamais restaurée est une hypothèse, pas une
sauvegarde.

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
BACKUP_OFFSITE_DIRS=<destinations hors serveur, séparées par des points-virgules>
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
- **`BACKUP_OFFSITE_DIRS`** décide si les sauvegardes survivent à la perte du
  serveur (§5). Vide = elles n'en sortent jamais. Séparateur : le
  point-virgule — la virgule et le deux-points figurent dans les chemins
  Windows (`D:\...`).
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
git clone https://github.com/informatiquecgbcreil/erp_api && cd erp_api
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate.bat
pip install -r requirements-dev.txt
python run_waitress.py    # SQLite locale créée automatiquement, assistant /setup/
```

### 8.2 Tests

```bash
python -m pytest          # 670 tests, base SQLite jetable, ~2 minutes
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
- **Licence AGPL-3.0** (fichier `LICENSE`) : toute personne à qui
  l'application est fournie, **y compris via le réseau**, peut exiger le code
  source correspondant. Une autre structure peut donc la reprendre, à
  condition de rester sous la même licence. Corollaire opérationnel :
  **aucun secret ne doit être versionné** — ils vivent dans le `.env`, exclu
  par `.gitignore`.
- **Mainteneur unique** : c'est la faiblesse structurelle qui reste après
  toutes les autres. Le code est documenté, testé et redéployable, mais
  personne d'autre n'a jamais exécuté les procédures de ce document. Une
  procédure jamais jouée par quelqu'un d'autre n'est pas une procédure,
  c'est de la littérature — d'où l'exercice du §11.6, à faire faire par
  **quelqu'un qui n'est pas le mainteneur**, celui-ci regardant sans toucher
  au clavier.

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
| Licence | `LICENSE` (AGPL-3.0) |

**Recalculer les chiffres du §1** (pour mesurer à quel point ce document
date) :

```bash
git ls-files '*.py' | xargs wc -l | tail -1          # lignes de Python
git ls-files '*.html' | wc -l                        # templates
grep -rn '\.route(' --include='*.py' app/ | wc -l     # routes
grep -rn '^class .*db\.Model' --include='*.py' app/ | wc -l   # tables
ls migrations/versions/*.py | wc -l                  # migrations
python -m pytest -q | tail -1                        # tests
```

---

## 11. Checklist « premier jour » du repreneur

1. [ ] Obtenir les accès : serveur Windows (RDP/console), compte GitHub,
       coffre à mots de passe `[À COMPLÉTER : où ?]`, compte Tailscale.
2. [ ] Se connecter au serveur, vérifier que le service `AppGestion`
       tourne et que `/healthz` répond.
3. [ ] Ouvrir Administration → Sauvegardes : vérifier la date du dernier
       lot (< 2 jours) et l'intégrité.
4. [ ] Dans le même écran, section **Copies hors serveur** : chaque
       destination doit être « à jour ✓ ». Si le tableau annonce qu'aucune
       destination n'est configurée, **c'est l'urgence n°1** : rien de ce
       qui suit ne protège de la perte du serveur (§5).
5. [ ] Ouvrir physiquement la destination externe et constater que les
       fichiers y sont vraiment. Un écran vert n'a jamais sauvé personne.
6. [ ] Cloner le dépôt sur un poste, `pip install -r requirements-dev.txt`,
       `python -m pytest` → tout vert.
7. [ ] **Restaurer sur ce poste de test la dernière sauvegarde prise depuis
       la destination hors serveur** puis démarrer l'application dessus :
       c'est l'exercice qui prouve que la chaîne de survie fonctionne de
       bout en bout. Tout le reste n'en est que la préparation.
8. [ ] Lire le §7 (invariants) deux fois.
9. [ ] Se créer un compte `admin_tech` nominatif sur la production et
       désactiver les comptes de la personne partie.
10. [ ] Noter ici un **contact technique de secours** joignable en cas de
       blocage : `[À COMPLÉTER : nom, structure, téléphone]`. L'application
       est un monolithe Flask/Jinja/PostgreSQL sans build front, avec 670
       tests et une CI verte : n'importe quel développeur Python la reprend
       en une semaine — encore faut-il qu'un nom soit écrit quelque part.

---

*Document créé en juillet 2026, révisé en septembre 2026 (copies hors
serveur automatisées, chiffres et licence remis à jour). À maintenir à chaque
changement d'infrastructure (serveur, sauvegarde, tunnel) : un document de
passation périmé est plus dangereux que pas de document du tout, parce qu'on
lui fait confiance.*

*Les chiffres du §1 et la commande de contrôle du §10 servent à repérer la
dérive : quand l'écart devient gênant, c'est que le reste du document a
vieilli aussi.*
