# Document de reprise / passation — App Gestion (Mon Centre Social)

> ### ⚠️ À lire en premier : ce document a des trous
>
> **1. Onze informations `[À COMPLÉTER]` restent vides** (tableau du §0).
> Seul le mainteneur actuel les détient. Tant qu'elles manquent, un
> repreneur suit les procédures ci-dessous jusqu'au premier mot de passe ou
> à la première adresse, puis reste bloqué. Pour les retrouver toutes :
> `grep -n "À COMPLÉTER" docs/PASSATION.md`. **Aucun secret ne s'écrit
> ici** (dépôt public, voir l'encadré du §0) : ce document dit *où* sont
> les secrets, jamais *quels* ils sont.
>
> **2. Deux installations coexistent.** La méthode de distribution est
> désormais l'**installateur Windows** `Mon-Centre-Social-1.0.0-rc2-Setup-x64.exe`
> (version définie dans `desktop/installer.iss`, construit par
> `.github/workflows/windows-installer.yml`). Mais **la production (servisa)
> tourne toujours sur l'ancienne installation** (service NSSM `AppGestion`,
> `C:\AppGestion`) : rien de la consolidation n'y est déployé
> (`docs/AUDIT-SUIVI.md`, ligne « servisa »). Les procédures sont donc
> données pour l'installateur, avec à chaque fois une section
> **« Ancienne installation NSSM (servisa, avant bascule) »**. Après la
> bascule : renseigner sa date en tête du §3, puis retirer ces sections.

> **À quoi sert ce document ?** À ce que quelqu'un d'autre que le mainteneur
> actuel puisse **faire revivre l'application** (panne, départ, absence longue)
> puis **la maintenir** sans rien casser. Il complète le `README.md` (référence
> technique exhaustive) et `docs/GUIDE-WINDOWS.md` (installation, reprise,
> sauvegarde et mise à jour avec l'installateur) : ici, on donne la vue
> d'ensemble, la production réelle, les procédures d'urgence et les règles à
> ne jamais enfreindre.
>
> **Règle d'or du repreneur :** avant TOUTE intervention sur le serveur,
> faire une sauvegarde (bouton « Sauvegarder maintenant » dans
> Administration → Sauvegardes ; sur l'ancienne installation, aussi
> `backup_now.bat`). Toutes les procédures ci-dessous supposent que c'est
> fait.

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
| 2 | Mot de passe PostgreSQL de la base `appgestion` (ancienne installation) | **coffre**, jamais ici | §3 |
| 3 | Comment `gestion.cgb` est résolu (fichier `hosts` des postes ? DNS interne ?) | ici, §3 | §3 |
| 4 | Compte Tailscale (identifiant + où sont les identifiants) | **coffre** | §3, §7.2 |
| 5 | Destinations de sauvegarde hors serveur réellement en place | ici, §3 et §4.2 | §5 |
| 6 | Qui a accès au dépôt GitHub `informatiquecgbcreil/erp_api`, et comment en obtenir | ici, §3 | §3 |
| 7 | Paramètres SMTP (serveur, compte d'envoi) | **coffre** pour le mot de passe | §3, §6 |
| 8 | **Emplacement du coffre à mots de passe** et qui en détient la clé | ici, §3 | §3, §11 |
| 9 | Copie du fichier `.env` de production (ancienne installation) ; après bascule, les secrets SMTP / intégrations et le jeton cPanel éventuel | **coffre** | §3, §6 |
| 10 | Un contact technique de secours (nom, téléphone, structure) | ici, §11 | §11 |
| 11 | Date de bascule de servisa vers l'installateur (vide = pas encore faite) | ici, §3 | §3, §8.3 |

> **Règle de sécurité :** aucun mot de passe, jeton ou `SECRET_KEY` ne doit
> être écrit dans ce fichier. Le dépôt est public et sous licence AGPL (§9) :
> tout ce qui y entre est diffusé. Ce document dit **où** trouver les secrets,
> jamais **quels** ils sont. Un coffre `KeePass` posé sur un partage réseau,
> dont la direction détient le mot de passe maître dans une enveloppe scellée,
> fait très bien l'affaire.

---

## 1. Ce qu'est cette application

**App Gestion** (distribuée sous le nom **Mon Centre Social**) est l'outil de
gestion interne du **Centre Georges Brassens (Creil)**, centre
socio-culturel. Elle centralise :

- les publics (participants, familles, insertion) ;
- les activités et les **présences** (émargement kiosque sur tablette,
  signature à distance par lien personnel, saisie en grille) ;
- la pédagogie (parcours, compétences, échelle de Hart) ;
- les **subventions** et leur justification (feuilles de temps par financeur,
  dossiers de justification, radar d'échéances) ;
- les budgets, dépenses, cotisations, caisse ;
- les bilans (SENACS, bilans financeurs, statistiques d'impact) ;
- l'**espace salarié** (heures supplémentaires, récupérations, frais
  kilométriques, profils salariaux confidentiels, coffre-fort de
  documents) — détail dans `docs/ESPACE-SALARIE.md` ;
- l'agenda personnel des salariés (flux iCal consommé par Google Agenda,
  lui-même relevé par la plateforme CSAT le 10 de chaque mois — ce flux
  est donc **la feuille de temps officielle** de certains salariés :
  sa fiabilité est critique).

**Criticité :** l'application contient des données personnelles de
participants (dont mineurs et suivi social), des signatures manuscrites,
des données financières et salariales. Elle est le support des
justifications envoyées aux financeurs (CAF, État, Ville…). Sa perte sans
sauvegarde serait grave ; sa fuite le serait davantage.

**Volumétrie du code (repère, octobre 2026) :** ~83 000 lignes de Python
dans `app/` (plus ~36 000 lignes de tests), 284 templates, ~560 routes,
155 modèles SQLAlchemy, 98 migrations, ~2 020 tests.
(Ces chiffres se recalculent en une commande — voir §10 — et servent surtout
à mesurer l'écart quand ce document commence à dater.)

---

## 2. L'architecture en cinq minutes

- **Monolithe Flask** (app factory dans `app/__init__.py`), découpé en
  blueprints par domaine métier (`app/activite/`, `app/main/`,
  `app/kiosk/`, `app/participants/`, `app/budget/`, `app/salaries/`…).
- **Rendu serveur Jinja2**, pas de framework JavaScript, **aucune étape de
  build front** : on modifie un template, on recharge la page.
- **Base de données : PostgreSQL en production**, SQLite en développement.
  Pilote **psycopg 3** (`psycopg[binary]` dans `requirements.txt` ; plus de
  psycopg2) : `config.py` convertit de lui-même une URL `postgresql://` en
  `postgresql+psycopg://`. L'ORM est SQLAlchemy, les migrations Alembic
  (Flask-Migrate), appliquées automatiquement au démarrage
  (`DB_AUTO_UPGRADE_ON_START=1`). La suite de tests tourne sur SQLite en
  local et, en CI, **aussi sur PostgreSQL 16 et 18** (§8.2).
- **Serveur d'application : Waitress** (`run_waitress.py`). Deux façons de
  l'exploiter sous Windows :
  - **Installateur Windows (méthode de distribution actuelle)** :
    `Mon-Centre-Social-1.0.0-rc2-Setup-x64.exe`, produit à partir de
    `desktop/` (gestionnaire C# `desktop/MonCentreSocial.cs`, orchestration
    `desktop/runtime.py`, paquet Inno Setup `desktop/installer.iss`). Il
    embarque Python 3.13, PostgreSQL 18 (17 conservé pour un cluster 17
    existant), le serveur HTTPS Caddy et toutes les bibliothèques. Deux
    services Windows : `MonCentreSocial` (l'application, compte virtuel
    `NT SERVICE\MonCentreSocial`) et `MonCentreSocialHTTPS` (Caddy). Pas de
    `.env` : la configuration est chiffrée par Windows (DPAPI, §6).
    Guide : `docs/GUIDE-WINDOWS.md` ; construction : `desktop/README.md`.
  - **Ancienne installation (servisa, avant bascule)** : Waitress exécuté
    comme **service Windows via NSSM** (`AppGestion`, `C:\AppGestion`),
    PostgreSQL installé à part, configuration dans un `.env`. Mise en place
    par `installation/Installer.ps1` et `deploy/windows/`. Ces scripts **ne
    sont plus la méthode de distribution** ; ils ne servent plus qu'à cette
    instance tant qu'elle n'a pas basculé.
- **Droits : RBAC maison** (rôles → permissions, ~100 permissions),
  initialisé au démarrage. Décorateur `require_perm("...")` sur les routes,
  helper `can("...")` dans les templates. Les **outils du centre** (modules)
  s'activent ou se désactivent dans l'application (`app/services/modules.py`).
- **Dépendances volontairement minces** (voir `requirements.txt`) : Flask,
  SQLAlchemy, Alembic, Waitress, psycopg 3, openpyxl/docxtpl pour les
  exports, Pillow, segno (QR codes), defusedxml, cryptography (autorité HTTPS
  de l'installateur). Rien d'exotique.

Pour la carte détaillée du dépôt : section « Structure du dépôt » du `README.md`.

---

## 3. La production réelle

**Bascule de servisa vers l'installateur :** `[À COMPLÉTER : date de la
bascule — tant que ce champ est vide, la production est l'ancienne
installation ci-dessous]`. Suivi détaillé : `docs/AUDIT-SUIVI.md`.

### 3.1 Commun aux deux installations

| Élément | Valeur |
|---|---|
| Serveur | Windows Server 2019 (appelé « servisa » dans `docs/AUDIT-SUIVI.md`) — `[À COMPLÉTER : nom réseau / IP exacts, et comment s'y connecter : RDP ? console ?]` |
| URL LAN | `gestion.cgb` `[À COMPLÉTER : confirmer host/port exacts et comment le nom est servi — fichier hosts, DNS interne ?]` |
| Exposition publique | **Tailscale Funnel**, uniquement pour la façade kiosque (voir §7.2) — compte Tailscale : `[À COMPLÉTER]` |
| Copie hors serveur des sauvegardes | **automatique** depuis l'application (voir §5) — destinations réellement en service : `[À COMPLÉTER : quel disque, quel partage, quel dossier cloud, et qui y a accès]` |
| Compte GitHub du dépôt | `informatiquecgbcreil/erp_api` — dépôt **public**, licence AGPL-3.0 (§9) : le code est lisible par tous, donc **jamais de secret versionné**. Accès en écriture : `[À COMPLÉTER : qui est propriétaire, comment obtenir les droits]` |
| SMTP (emails) | `[À COMPLÉTER : serveur et compte d'envoi ; mot de passe au coffre]` |
| Coffre à mots de passe | `[À COMPLÉTER : emplacement du coffre, et qui en détient la clé]` |

### 3.2 Installation par l'installateur (cible, et toute nouvelle machine)

| Élément | Valeur |
|---|---|
| Programmes | `C:\Program Files\Mon Centre Social` (remplacés en entier à chaque mise à jour) |
| Données | `C:\ProgramData\MonCentreSocial` : `postgresql\` (base), `uploads\`, `instance\`, `backups\`, `logs\`, `runtime\` (registres), `private\` (configuration chiffrée), `public\` (adresses, certificat du centre), `Direction-DSI\` (dossier confidentiel) |
| Services Windows | `MonCentreSocial` (application) et `MonCentreSocialHTTPS` (Caddy) — journaux dans `logs\service.log`, `logs\erreurs.log`, `logs\proxy.log` |
| Base de données | PostgreSQL embarqué, sur `127.0.0.1` uniquement (port choisi à partir de 55432), base `moncentresocial`, compte `mcs`. Mot de passe généré et gardé chiffré par l'installateur : **personne n'a besoin de le connaître** |
| Adresses | `public\url.txt` (administration en HTTPS, par IP : `https://<IP>:8443`), `public\kiosk-url.txt` (kiosque du réseau local). Un nom comme `gestion.cgb` s'ajoute par le menu Démarrer → **Adresses d'accès au serveur** |
| Configuration | `private\configuration.dpapi` (chiffrée par la machine, **non transportable**) — voir §6 |
| Dossier confidentiel | `Direction-DSI\Installation-confidentielle.txt` : adresses, chemins, ports (aucun mot de passe). Accès réservé à SYSTEM et aux administrateurs Windows |
| Sauvegarde | faite **par le service**, une fois par jour, dans `backups\`, puis copiée vers les destinations hors serveur réglées dans Administration → Sauvegardes |
| Outils d'exploitation | menu Démarrer : **Configurer Mon Centre Social**, **Adresses d'accès au serveur**, **Certificat reconnu (nom public)** ; icône de notification (Redémarrer / Arrêter le service) ; `MonCentreSocial.exe --restore-test` (en administrateur) |

### 3.3 Ancienne installation NSSM (servisa, avant bascule)

| Élément | Valeur |
|---|---|
| Dossier d'installation | `C:\AppGestion` (convention des scripts `deploy/windows/` et `installation/Installer.ps1`) |
| Service Windows | `AppGestion` (NSSM) — logs dans `C:\AppGestion\logs\service-out.log` et `service-err.log` |
| Base de données | PostgreSQL locale, base `appgestion` — mot de passe : `[À COMPLÉTER : coffre]` |
| URL LAN | `http://gestion.cgb` (HTTP nu, port 8000 par défaut des scripts) |
| Configuration | fichier `.env` à la racine de `C:\AppGestion` (non versionné — c'est LE fichier à sauvegarder à part, il contient `SECRET_KEY` et `DATABASE_URL`) |
| Tâche planifiée sauvegarde | `AppGestionBackupDaily` (Planificateur de tâches Windows), quotidienne vers 2h00–2h30 |
| Copie hors serveur | variable `BACKUP_OFFSITE_DIRS` du `.env` |

### 3.4 Où sont les données ?

| Quoi | Installateur | Ancienne installation | Dans la sauvegarde ? |
|---|---|---|---|
| Base PostgreSQL (tout le métier) | `postgresql\` | PostgreSQL local | oui (dump `.sql`) |
| Pièces jointes, logos, signatures | `uploads\` | `APP_UPLOAD_DIR`, par défaut `static\uploads` | oui (`_uploads.zip`) |
| Fichiers métier d'`instance` (archives d'émargement, modèles…) | `instance\` | `instance\` | oui sur l'installateur ; non dans les lots anciens |
| Registres des numéros émis et des effacements RGPD | `runtime\` | non tenus (ils exigent `APP_DATA_DIR`) | installateur : une copie par lot (`_registres.json`) |
| Configuration et secrets | `private\*.dpapi` | `.env` | **non** — secrets au coffre |

Base, uploads et fichiers d'instance sont couverts par la sauvegarde
automatique, **et recopiés hors du serveur** à chaque sauvegarde (§5). La
configuration, elle, n'est dans aucune sauvegarde : sur l'ancienne
installation, le `.env` doit avoir une copie au coffre ; sur l'installateur,
la configuration chiffrée ne se transporte pas, ce sont donc les **secrets**
(SMTP, intégrations, jeton cPanel) qui doivent être au coffre —
`[À COMPLÉTER : où ?]`. C'est la seule pièce qu'une panne de serveur peut
faire disparaître définitivement par simple négligence.

---

## 4. Procédure d'urgence : tout redéployer de zéro

Scénario : le serveur est mort, on repart d'une machine Windows vierge
(Windows 10 1809+, 11 ou Server 2019/2022/2025 **x64 avec interface
graphique**). Objectif réaliste : **application de nouveau en service en
moins d'une heure**, avec les données de la dernière sauvegarde.

**Quelle voie ?** On réinstalle **avec l'installateur** (§4.1 à §4.3), même
si le serveur perdu était l'ancienne installation NSSM : c'est la voie que
le projet maintient et recette. Un lot de l'ancienne installation a le même
format (dump PostgreSQL sans propriétaire, archive des uploads, empreintes)
et une base plus ancienne que le programme est mise à niveau pendant la
restauration. **Ce cas précis — un vrai lot de servisa restauré dans
l'installateur — n'a jamais été joué** : c'est l'exercice du §11.7, à faire
avant d'en avoir besoin. La voie ancienne (§4.4) reste en repli.

### 4.1 Réinstaller l'application

1. Récupérer l'installateur et son empreinte `.sha256` :
   - de préférence la **copie de l'installateur en service**, rangée avec
     les sauvegardes hors serveur (à prévoir dès la bascule : les artefacts
     GitHub expirent au bout de 30 jours) ;
   - sinon l'artefact `Mon-Centre-Social-Windows-x64` du dernier passage
     vert du workflow **« Installateur Windows »** (onglet *Actions* du
     dépôt) ; s'il a expiré, relancer ce workflow (*Run workflow*, droits
     d'écriture sur le dépôt nécessaires) ou construire sur un poste Windows
     (`desktop/README.md`).

   L'installateur n'est pas signé Authenticode : **vérifier l'empreinte
   SHA-256** avant de le lancer.
2. Double-cliquer sur l'installateur, accepter l'élévation, choisir
   **Nouvelle installation**. Saisir la structure et un compte de direction
   **provisoire** (il disparaîtra à la restauration : ce sont les comptes de
   la sauvegarde qui reviennent).
3. Choisir le profil **Tous les outils** : une base restaurée qui ne porte
   pas encore de sélection d'outils (cas d'une ancienne installation) prend
   celle de l'installation.
4. Choisir le **réseau de la structure**, vérifier l'adresse IP proposée
   (celle de la carte reliée à la box, pas celle d'un VPN) et déclarer dans
   **Autres adresses de ce serveur** le nom `gestion.cgb` et les adresses
   Tailscale/VPN éventuelles : sur une installation neuve, l'autorité HTTPS
   ne signe que pour les noms connus à sa création. SMTP : le reprendre du
   coffre maintenant ou plus tard.
5. Conserver le rapport d'installation avec la documentation de la
   direction. En cas d'interruption : menu Démarrer → **Configurer Mon
   Centre Social**.

### 4.2 Restaurer la dernière sauvegarde

1. Récupérer le dernier **lot** de sauvegarde depuis une destination hors
   serveur — en pratique `[À COMPLÉTER : emplacement]`. C'est le moment où
   toute la chaîne se joue : si aucune destination n'a jamais été
   renseignée, les sauvegardes étaient sur le serveur perdu et il n'y a rien
   à restaurer. Un lot =
   - `<Structure>_<horodatage>.sql` (dump PostgreSQL)
   - `<Structure>_<horodatage>_uploads.zip` (pièces jointes, et fichiers
     d'`instance` pour les lots de l'installateur)
   - `<Structure>_<horodatage>.sha256` (empreintes)
   - `<Structure>_<horodatage>_registres.json` (s'il existe : registres des
     numéros et des effacements)
2. Copier ces fichiers, **sans les renommer**, dans
   `C:\ProgramData\MonCentreSocial\backups\`.
3. Se connecter avec le compte provisoire → **Administration →
   Sauvegardes**. Le lot apparaît dans la liste : **Vérifier** (contrôle à
   blanc : empreintes, dump complet, archive lisible) doit répondre
   « RESTAURABLE », puis **Restaurer**. L'application fait d'abord une
   sauvegarde de sécurité de l'état courant ; la base est restaurée dans une
   transaction ; pour un lot plus ancien que le programme, le centre passe
   en maintenance le temps de mettre la base au niveau, et revient à l'état
   d'avant si cela échoue.
4. Se reconnecter avec un compte **de la sauvegarde**.

### 4.3 Vérifier et remettre en service

1. Ouvrir l'adresse de `public\url.txt` suivie de `/healthz` →
   `{"status":"ok"}`.
2. Ouvrir le tableau de bord, une fiche participant, une feuille
   d'émargement, un document joint.
3. **Contrôle → Registres** : si le lot n'avait pas de `_registres.json`
   (lot ancien), l'émission de reçus et factures reste suspendue tant que le
   dernier numéro de chaque série, relevé sur les documents papier, n'est
   pas déclaré. Rejouer aussi les demandes d'effacement RGPD reçues depuis
   la date du lot.
4. Reprendre les secrets du coffre : SMTP, intégrations (Google Agenda,
   portail, publication FTP…), certificat reconnu s'il y en avait un (menu
   Démarrer → **Certificat reconnu (nom public)**). Tester l'envoi d'e-mail
   depuis Administration → Santé du système.
5. Refaire pointer le nom LAN (`gestion.cgb`) : DNS ou fichier `hosts` des
   postes vers la nouvelle IP (s'il n'a pas été déclaré à l'installation,
   l'ajouter par **Adresses d'accès au serveur**). Déployer le nouveau
   `public\Certificat-du-centre.cer` sur les postes qui utilisent
   l'administration : l'autorité a changé avec la machine.
6. Rebrancher la façade kiosque si elle servait : Tailscale Funnel vers le
   **port kiosque** (`tailscale funnel --bg http://127.0.0.1:<port
   kiosque>`, port dans le dossier confidentiel), voir
   `docs/kiosque-hors-les-murs.md`.
7. **Rebrancher la copie hors serveur** : Administration → Sauvegardes,
   saisir la ou les destinations, **Enregistrer et tester l'écriture** (le
   test se fait sous le compte du service). Puis « Sauvegarder maintenant »
   et, en administrateur, `MonCentreSocial.exe --restore-test` : il doit
   conclure que le lot se restaure correctement (rapport dans
   `private\essai-restauration.json`). Une instance remise en service sans
   copie externe est une instance qui attend la prochaine panne dans les
   mêmes conditions.

### 4.4 Ancienne installation NSSM (servisa, avant bascule) — voie de repli

À n'utiliser que si l'installateur est inutilisable. Ces scripts ne sont
plus maintenus comme méthode de distribution.

1. Récupérer le code : `git clone` du dépôt GitHub, ou ZIP
   (**Code → Download ZIP**) si pas d'accès git.
2. Lancer `installation/Installer.ps1` (clic droit → *Exécuter avec
   PowerShell*). Il installe Python, PostgreSQL, l'environnement virtuel,
   le service Windows NSSM et la tâche planifiée de sauvegarde. Accepter les
   valeurs par défaut (`C:\AppGestion`, port 8000), répondre **o** à
   « accessible depuis d'autres postes ». **Noter le mot de passe
   PostgreSQL** affiché en fin d'installation. À la fin, le navigateur
   s'ouvre sur `/setup/` : **ne pas créer de compte**.
3. Arrêter le service : `nssm stop AppGestion` (ou services.msc).
4. Restaurer : `restore_now.bat` à la racine de `C:\AppGestion`, en donnant
   le chemin du `.sql` et du `_uploads.zip`. (Équivalent :
   `python tools\restore_instance.py --db <fichier.sql> --uploads <fichier.zip>`.)
   Le dump est produit avec `--clean --if-exists` : il est rejouable même
   sur une base déjà peuplée.
5. Restaurer le fichier `.env` depuis le coffre (ou le recréer : §6), puis
   `nssm start AppGestion`.
6. Vérifier `http://localhost:8000/healthz`, se connecter, puis
   `python tools\preflight_deploy.py` et
   `python tools\run_reliability_checks.py --require-offsite` (après avoir
   renseigné `BACKUP_OFFSITE_DIRS`).

---

## 5. Sauvegardes : fonctionnement et vérification

Le code fait foi : `app/services/sauvegarde.py` (logique commune) et la page
**Administration → Sauvegardes** (bouton « Sauvegarder maintenant »,
vérification, restauration, réglage des destinations hors serveur).
Le déclencheur quotidien diffère :

- **installateur** : le service lui-même (`backup()` dans
  `desktop/runtime.py`) fait **un lot par jour** (pas un nouveau lot à
  chaque redémarrage), le **vérifie**, puis le recopie hors serveur. L'état
  de la dernière sauvegarde quotidienne s'affiche en haut de l'écran
  Sauvegardes. Le délai accordé est proportionné au volume (15 min + 1 min
  par 100 Mo, 8 h au plus) ; un lot coupé en route est refusé à la
  restauration ;
- **ancienne installation** : tâche planifiée `AppGestionBackupDaily`
  → `tools/backup_instance.py`, (ré)installable par
  `deploy/windows/register_backup_task.ps1`.

Dans les deux cas :

- Chaque sauvegarde produit dans `backups/` un **lot** : dump `.sql`
  (`pg_dump --clean --if-exists --no-owner --no-privileges`), zip des
  uploads (et des fichiers d'`instance` sur l'installateur), empreinte
  `.sha256`, copie des registres sur l'installateur.
- **Rotation automatique** : `BACKUP_RETENTION_LOTS` lots conservés (30 par
  défaut). **Alerte** dans l'application si aucune sauvegarde depuis
  `BACKUP_ALERT_DAYS` jours (2 par défaut).
- La restauration depuis l'application **vérifie l'intégrité** et crée
  d'abord une **sauvegarde de sécurité de l'état courant** — elle est donc
  le chemin le plus sûr pour une restauration à chaud.

### La copie hors serveur — le maillon qui décide de tout

Une sauvegarde rangée sur la machine qu'elle protège disparaît avec elle.
C'est le scénario §4 en entier : panne de disque, vol, rançongiciel,
réinstallation « propre » par un prestataire pressé.

La copie externe est donc **faite par l'application elle-même**, à chaque
sauvegarde, vers les destinations saisies dans **Administration →
Sauvegardes** (installateur) ou listées dans `BACKUP_OFFSITE_DIRS` du `.env`
(ancienne installation) — disque amovible, partage réseau/NAS en chemin UNC,
dossier synchronisé par un client cloud ; plusieurs destinations possibles :

- le bouton **« Enregistrer et tester l'écriture »** écrit, relit et efface
  un fichier d'essai **sous le compte du service**
  (`NT SERVICE\MonCentreSocial` sur l'installateur) : un partage que seul
  l'administrateur peut écrire est signalé tout de suite. Pour un partage,
  accorder l'écriture au compte ordinateur du serveur (`DOMAINE\SERVEUR$`) ;
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

**Comment savoir si l'on est protégé :**

| Où | Ce qu'on y voit |
|---|---|
| Administration → Sauvegardes | état de la dernière sauvegarde quotidienne ; tableau « Copies hors serveur » : date de la dernière copie, nombre de lots, état par destination |
| Digest de notifications | une ligne d'alerte si une destination est injoignable, vide ou en retard — **et si aucune n'est configurée** |
| `MonCentreSocial.exe --restore-test` (installateur, en administrateur) | rejoue réellement le dernier lot dans une base jetable, vérifie tables et documents, puis la supprime |
| `python tools\run_reliability_checks.py --require-offsite` (ancienne installation) | sort en erreur si les sauvegardes ne quittent pas le serveur ; la tâche planifiée sort elle-même en **code 1** si une copie échoue, ce qui la fait apparaître en échec dans le Planificateur de tâches |

> ⚠️ Si aucune destination n'est réglée, tout ce qui précède ne s'applique
> pas et l'application le dit à chaque écran. C'est le premier réglage à
> vérifier en prenant la main sur l'instance.

**Rituels recommandés au repreneur :**

- **après chaque mise à jour, et au moins chaque trimestre** :
  `MonCentreSocial.exe --restore-test` sur le serveur ;
- **chaque mois** : prendre le dernier lot **depuis la destination hors
  serveur** (pas depuis `backups/` : c'est la copie externe qu'on veut
  éprouver), le restaurer sur un poste de test (installateur en mode « cet
  ordinateur », puis §4.2), vérifier que l'application démarre et que les
  données sont là. Une sauvegarde jamais restaurée est une hypothèse, pas
  une sauvegarde.

---

## 6. Configuration

### 6.1 Installateur : pas de `.env`

L'installateur génère la configuration (dont `SECRET_KEY` et le mot de passe
de la base) et la garde **chiffrée par Windows (DPAPI machine)** dans
`private\configuration.dpapi`, lisible seulement par SYSTEM et les
administrateurs ; le service n'en reçoit qu'une copie limitée
(`private\service.dpapi`). On la modifie par les outils prévus, jamais à la
main :

- menu Démarrer → **Configurer Mon Centre Social** (assistant, SMTP compris) ;
- **Adresses d'accès au serveur** (noms et adresses acceptés, certificat) ;
- **Certificat reconnu (nom public)** (Let's Encrypt par DNS cPanel) ;
- dans l'application : Administration → Sauvegardes, Outils du centre,
  Paramètres.

Cette configuration **ne se transporte pas** sur une autre machine : après
sinistre, on réinstalle et on ressaisit. D'où l'importance du coffre pour
les secrets SMTP, des intégrations et du jeton cPanel (§0, ligne 9).

Lors de la **reprise** d'une ancienne installation, les réglages du `.env`
listés dans `desktop/migration.py` sont repris ; les variables définies
uniquement dans NSSM ou dans le compte de service ne sont pas devinées : les
reporter dans le `.env` source avant la reprise.

### 6.2 Ancienne installation NSSM (servisa, avant bascule) : le fichier `.env`

Lu automatiquement au démarrage (voir `config.py`, qui documente chaque
variable ; modèle commenté : `.env.example`). Le minimum vital en
production :

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

`DATABASE_URL` peut rester en `postgresql://` : avec psycopg 3 installé
(`requirements.txt`), `config.py` la convertit en `postgresql+psycopg://`.

### 6.3 Points d'attention (les deux installations)

- **`SECRET_KEY`** signe les sessions et les jetons de réinitialisation de
  mot de passe. La changer déconnecte tout le monde (bénin) ; la perdre
  n'est pas grave ; la laisser à sa valeur par défaut est interdit en
  production (l'application refuse de démarrer).
- **`ERP_PUBLIC_BASE_URL`** sert aux QR codes et aux liens dans les emails
  (adresse LAN).
- **`KIOSK_PUBLIC_HOST`** active la **façade publique** (voir §7.2). Vide =
  aucune restriction par hôte (ne convient que si rien n'est exposé).
- **Destinations hors serveur** : décident si les sauvegardes survivent à
  la perte du serveur (§5). Séparateur dans `BACKUP_OFFSITE_DIRS` : le
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
  `python -c "from alembic.script import ScriptDirectory; from alembic.config import Config; c = Config('migrations/alembic.ini'); c.set_main_option('script_location', 'migrations'); print(ScriptDirectory.from_config(c).get_heads())"`
  → une seule valeur. Deux têtes = créer une révision de fusion (il en
  existe déjà une dans l'historique : `32d3e4f5a6b7`).
- Une sauvegarde PostgreSQL ancienne se restaure par-dessus le schéma
  actuel : `app/services/sauvegarde.py` retire d'abord toutes les clés
  étrangères (dans la transaction de la restauration), sinon une clé
  ajoutée depuis sur une table existante bloquerait le `--clean` du dump.
  Après toute migration qui ajoute une clé étrangère,
  `tests/test_restauration_schema.py` doit passer **sur PostgreSQL** :
  la CI le fait (§8.2), en local voir `TESTS_DATABASE_URL`.
- Les migrations s'appliquent au démarrage du service : une migration qui
  plante empêche l'application de démarrer. Les tests les exécutent toutes
  (la fixture `app` de `tests/conftest.py` migre une base neuve) : si
  `pytest` passe sur SQLite **et** PostgreSQL, la chaîne de migrations est
  saine.

### 7.2 La façade publique : liste blanche, jamais élargie à la légère

Quand une requête arrive par la façade publique (en-tête posé par le tunnel
Tailscale Funnel ou Cloudflare, ou hôte `KIOSK_PUBLIC_HOST` — détection dans
`app/services/public_ingress.py`), un `before_request` de `app/__init__.py`
n'autorise que : `/kiosk…`, `/calendrier/…`, `/static/…`,
`/media/branding/…` (logos), `/healthz` et `/sources` (archive des sources
AGPL). **Tout le reste répond 403**, y compris la page de connexion. Sur
l'installateur, le port kiosque est en plus filtré par Caddy avant
d'atteindre l'application (`docs/kiosque-hors-les-murs.md`).

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
  (`app/rbac.py`) pour être accordée automatiquement aux rôles voulus sur
  une production existante — sinon personne ne l'a et la fonctionnalité
  est morte à la mise à jour.

### 7.6 bis Salaires : étanchéité de l'accès

Détail complet du module RH : `docs/ESPACE-SALARIE.md`.

- Les profils salariaux (`profil_salarial`) ne se lisent qu'avec la
  permission `salaires:gerer` (direction) ; chaque salarié voit le sien.
- **Cette permission ne se donne ni ne se retire que par quelqu'un qui l'a
  déjà** (garde-fou dans `app/admin/routes.py`, tous les chemins : création
  de compte, changement de rôle, permissions d'un rôle, suppression de
  rôle). Sans lui, quiconque gère les droits — l'admin technique compris —
  pourrait s'ouvrir les salaires de l'équipe. Testé dans
  `tests/test_espace_salarie.py`.
- Limite assumée : une personne qui a la main sur le serveur ou la base
  peut toujours lire les tables. L'étanchéité porte sur l'application.
- L'historique RH signé (heures, récupérations, frais km, documents) est
  rattaché à la fiche salarié : une fiche avec historique ne se supprime
  pas, on renseigne sa date de sortie.

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

Facultatif : `python -m playwright install chromium` pour les tests de
fumée navigateur (sinon ils se sautent proprement).

### 8.2 Tests

```bash
python -m pytest          # ~2 020 tests, base SQLite jetable
# La même suite sur PostgreSQL (bases de travail créées puis supprimées,
# la base nommée dans l'URL n'est jamais touchée) :
TESTS_DATABASE_URL=postgresql+psycopg://user:motdepasse@localhost:5432/erp python -m pytest
```

Compter **10 à 20 minutes** par passage (début octobre 2026 : en CI sur
`main`, ~12 min sur SQLite et 8 à 13 min sur PostgreSQL ; 18 min mesurées
sur un poste Linux à 4 cœurs, SQLite). Une vingtaine de tests se sautent
s'il manque Chromium (Playwright) ou Pebble (certificat reconnu).

- **CI `.github/workflows/tests.yml`**, à chaque push et pull request, trois
  jobs : **SQLite** (avec le test de fumée navigateur et le banc ACME
  Pebble), **PostgreSQL 16** et **PostgreSQL 18** (le moteur que livre
  l'installateur), en locale UTF-8. Le passage PostgreSQL existe parce que
  deux pannes venaient de comportements que SQLite pardonne et que
  PostgreSQL refuse (booléen « 1 », fonction SQL propre à SQLite), alors
  que la suite était au vert.
- **CI `.github/workflows/windows-installer.yml`** : construit
  l'installateur et le recette sur un Windows Server éphémère
  (installation, service, HTTPS, certificat reconnu, reprise d'une base
  PostgreSQL 18.1 ancienne, mise à jour, désinstallation). Déclenché sur
  `main` quand `desktop/`, `app/`, `migrations/`, `config.py` ou
  `requirements.txt` changent, et sur les pull requests qui y touchent ;
  artefact `Mon-Centre-Social-Windows-x64`, gardé 30 jours.
- **Ne jamais déployer un commit rouge en CI**, sur l'un ou l'autre
  workflow.
- Pièges connus des tests : une suite verte en local sur SQLite ne dit rien
  de PostgreSQL — dès qu'on touche au SQL (booléens, fonctions, `ILIKE`,
  accents), attendre la CI ou lancer la variante PostgreSQL ; les
  apostrophes françaises sont échappées en HTML (`&#39;`) — asserter sur
  des sous-chaînes sans apostrophe ; les messages flash sont consommés par
  le GET suivant.

### 8.3 Mise à jour de la production

#### Installateur (après bascule)

1. Sauvegarde **et** copie hors serveur à jour (Administration →
   Sauvegardes, « Sauvegarder maintenant », vérifier le tableau des copies).
2. Récupérer le nouvel installateur (artefact CI d'un commit vert de
   `main`) et vérifier son SHA-256. En garder une copie avec les
   sauvegardes hors serveur (§4.1).
3. Le lancer sur le serveur : il arrête le service, **remplace entièrement**
   les programmes, conserve données, configuration et comptes, applique les
   migrations (sans limite de temps tant qu'elles avancent) et redémarre.
   En mode silencieux (`/VERYSILENT`), le code de sortie **8** signifie que
   le centre n'a pas redémarré : voir `logs\mise-a-jour-erreur.txt`.
4. Vérifier `/healthz`, se connecter, lire `logs\service.log` et
   `logs\erreurs.log`, puis `MonCentreSocial.exe --restore-test`.
5. En cas de casse : réinstaller la version précédente et restaurer la
   sauvegarde de l'étape 1 (Administration → Sauvegardes). **Ne jamais**
   faire tourner un ancien programme sur un schéma plus récent.

#### La bascule de servisa (une seule fois)

Passage de l'ancienne installation à l'installateur par **« Reprendre une
ancienne installation de cet ERP »** : la base source est copiée, jamais
modifiée (elle passe en lecture seule), et l'ancien service est désactivé
après réussite. Procédure complète, vérifications et **retour arrière** :
`docs/AUDIT-SUIVI.md` (« Procédure : mise à jour, reprise, retour
arrière ») et `docs/GUIDE-WINDOWS.md` (« Reprendre une installation
existante »). En bref : sauvegarde externe, couper le Funnel, noter le nom
de l'ancien service (`AppGestion`), lancer la reprise, lire
`Direction-DSI\Reprise.json`, régler et tester la copie hors serveur,
`--restore-test`, rediriger le Funnel vers le port kiosque, traiter les
écrans de caisse signalés. Ensuite : renseigner la date en tête du §3 et
mettre ce document à jour.

#### Ancienne installation NSSM (servisa, avant bascule)

> Un `git pull` sur servisa y appliquerait d'un coup toutes les migrations
> accumulées depuis le dernier déploiement (rien de la consolidation n'y
> est passé). La voie prévue est la bascule ci-dessus, qui laisse la base
> source intacte et sait revenir en arrière. Si une mise à jour en place
> reste nécessaire avant, faire d'abord une sauvegarde **et** l'éprouver
> (restauration sur un poste de test).

1. Sauvegarde (`backup_now.bat` ou bouton dans l'application).
2. `git pull` dans `C:\AppGestion` (ou remplacement des fichiers depuis le
   ZIP).
3. Si `requirements.txt` a changé :
   `.venv\Scripts\pip install -r requirements.txt` (installe notamment le
   pilote psycopg 3).
4. Redémarrer le service : `nssm restart AppGestion`. Les migrations
   s'appliquent toutes seules au démarrage.
5. Vérifier `/healthz`, se connecter, surveiller
   `C:\AppGestion\logs\service-err.log` et `erreurs.log` quelques minutes.
6. En cas de casse : restaurer la sauvegarde de l'étape 1 (§4.4) et
   revenir au commit précédent (`git checkout <commit>`).

---

## 9. Dette et points de vigilance connus

Un repreneur honnête doit savoir où sont les faiblesses :

- **Deux installations à faire vivre tant que servisa n'a pas basculé** :
  l'installateur (cible, recetté en CI) et l'ancienne installation NSSM
  (scripts `installation/`, `deploy/windows/`, `*.bat`), qui n'est plus
  qu'une voie de repli. Rien de la consolidation n'est déployé en
  production, et plusieurs points ne sont vérifiables que sur servisa
  (choix de l'adresse IP, pare-feu des cartes VPN, durées réelles de
  sauvegarde et de migration) — voir « Ce qui n'a pas pu être vérifié
  ici » dans `docs/AUDIT-SUIVI.md`.
- **Points ouverts de l'audit** (tableau final de `docs/AUDIT-SUIVI.md`) :
  montants en virgule flottante (aucun écart observé), 115 écarts
  modèle/schéma mesurés et documentés, retour arrière non garanti pour les
  migrations antérieures à la consolidation, autorité HTTPS non contrainte
  sur une installation existante.
- **Installateur non signé Authenticode** : vérifier l'empreinte SHA-256
  avant chaque installation. Les artefacts CI expirent après 30 jours :
  garder une copie de la version en service.
- **SQLite en local, PostgreSQL en production** : la CI joue désormais la
  suite sur PostgreSQL 16 et 18, ce qui ferme l'essentiel de l'écart. Reste
  qu'un développeur qui ne lance que la suite SQLite peut pousser un commit
  qui casse sur PostgreSQL : c'est la CI qui fait foi (§8.2).
- **De la logique métier vit dans les routes** plutôt que dans
  `app/services/` pour les modules les plus anciens. Les modules récents
  (agenda, justification, intersecteur, espace salarié) sont mieux
  découpés — prendre ceux-là comme modèle.
- **Hétérogénéité de finition** : les modules développés en dernier
  (agenda, feuilles de temps, signature à distance, espace salarié) sont
  les mieux testés ; certains écrans anciens le sont moins.
- **Pas de 2FA** ; la sécurité d'accès repose sur mots de passe +
  verrouillage anti-force-brute + réseau LAN. Toute idée d'exposer
  l'application entière sur Internet exige de reposer la question.
- **HTTP nu sur le LAN** pour l'ancienne installation (l'installateur sert
  l'administration en HTTPS) : acceptable sur un réseau maîtrisé, mais ne
  jamais faire transiter l'interface complète par le tunnel public sans
  passer par la façade (§7.2).
- **Performance jamais testée en charge** : dimensionné pour ~10
  utilisateurs simultanés et quelques milliers de participants. Largement
  suffisant aujourd'hui ; à re-vérifier si le périmètre change.
- **Licence AGPL-3.0** (fichier `LICENSE`) : toute personne à qui
  l'application est fournie, **y compris via le réseau**, peut exiger le code
  source correspondant (l'installateur l'embarque et le sert sur
  `/sources`). Une autre structure peut donc la reprendre, à condition de
  rester sous la même licence. Corollaire opérationnel : **aucun secret ne
  doit être versionné** — ils vivent dans le `.env` (exclu par
  `.gitignore`) ou dans la configuration chiffrée de l'installateur.
- **Mainteneur unique** : c'est la faiblesse structurelle qui reste après
  toutes les autres. Le code est documenté, testé et redéployable, mais
  personne d'autre n'a jamais exécuté les procédures de ce document. Une
  procédure jamais jouée par quelqu'un d'autre n'est pas une procédure,
  c'est de la littérature — d'où l'exercice du §11.7, à faire faire par
  **quelqu'un qui n'est pas le mainteneur**, celui-ci regardant sans toucher
  au clavier.

---

## 10. Où trouver quoi

| Besoin | Emplacement |
|---|---|
| Référence technique complète (config, dépannage) | `README.md` |
| Installation, reprise, sauvegarde, mise à jour avec l'installateur | `docs/GUIDE-WINDOWS.md` |
| Construction de l'installateur | `desktop/README.md`, `desktop/` ; CI `.github/workflows/windows-installer.yml` |
| Sécurité de la distribution Windows | `docs/SECURITE-DISTRIBUTION-WINDOWS.md` |
| État de l'audit et de la consolidation, procédure de bascule et de retour arrière | `docs/AUDIT-SUIVI.md` (suites : `docs/CONSOLIDATION-APRES-PR59.md`, `docs/CONSOLIDATION-APRES-PR60.md`) |
| Espace salarié / ressources humaines | `docs/ESPACE-SALARIE.md` |
| Ce document (reprise/passation) | `docs/PASSATION.md` |
| Façade kiosque hors les murs | `docs/kiosque-hors-les-murs.md` |
| Intégration portail apprenants | `docs/INTEGRATION_PORTAIL.md` |
| Ancienne installation (servisa, avant bascule) | `installation/Installer.ps1`, `deploy/windows/` (service NSSM, tâche de sauvegarde) ; raccourcis `backup_now.bat`, `restore_now.bat` à la racine |
| Variantes Linux (systemd, nginx, cron) | `deploy/linux/` |
| Diagnostic avant/après déploiement | `tools/preflight_deploy.py`, `tools/run_reliability_checks.py` ; sur l'installateur, Administration → Santé du système |
| Migration SQLite → PostgreSQL | `migrate_sqlite_to_postgres.py` |
| Aide utilisateur | dans l'application : menu Aide, glossaire, guides, parcours par métier |
| Licence | `LICENSE` (AGPL-3.0) |

**Recalculer les chiffres du §1** (pour mesurer à quel point ce document
date) :

```bash
git ls-files -z 'app/*.py' | xargs -0 cat | wc -l              # lignes de Python de l'application
git ls-files -z 'tests/*.py' | xargs -0 cat | wc -l            # lignes de tests
git ls-files 'app/templates/*.html' | wc -l                    # templates
grep -rEn '@[A-Za-z_]+\.(route|get|post|put|patch|delete)\(' --include='*.py' app/ | wc -l   # routes
grep -rn '^class .*db\.Model' --include='*.py' app/ | wc -l    # modèles SQLAlchemy
ls migrations/versions/*.py | wc -l                            # migrations
python -m pytest --collect-only -q -o addopts= | tail -1       # tests
```

---

## 11. Checklist « premier jour » du repreneur

1. [ ] Obtenir les accès : serveur Windows (RDP/console), compte GitHub,
       coffre à mots de passe `[À COMPLÉTER : où ?]`, compte Tailscale.
2. [ ] Lire l'encadré en tête de ce document et `docs/AUDIT-SUIVI.md` :
       savoir si servisa a basculé, et donc quelle moitié des procédures
       s'applique.
3. [ ] Se connecter au serveur, vérifier que le service tourne
       (`MonCentreSocial` et `MonCentreSocialHTTPS` ; avant bascule :
       `AppGestion`) et que `/healthz` répond.
4. [ ] Ouvrir Administration → Sauvegardes : vérifier la date du dernier
       lot (< 2 jours) et l'intégrité.
5. [ ] Dans le même écran, section **Copies hors serveur** : chaque
       destination doit être « à jour ✓ ». Si le tableau annonce qu'aucune
       destination n'est configurée, **c'est l'urgence n°1** : rien de ce
       qui suit ne protège de la perte du serveur (§5). Ouvrir
       physiquement la destination externe et constater que les fichiers y
       sont vraiment. Un écran vert n'a jamais sauvé personne.
6. [ ] Cloner le dépôt sur un poste, `pip install -r requirements-dev.txt`,
       `python -m pytest` → tout vert ; vérifier que les trois jobs du
       dernier passage CI sur `main` (SQLite, PostgreSQL 16, PostgreSQL 18)
       sont verts.
7. [ ] **Restaurer sur un poste de test la dernière sauvegarde prise depuis
       la destination hors serveur** (installateur en mode « cet
       ordinateur », puis §4.2) et démarrer l'application dessus : c'est
       l'exercice qui prouve que la chaîne de survie fonctionne de bout en
       bout. Tant que servisa n'a pas basculé, c'est aussi la seule preuve
       qu'un lot de l'ancienne installation se restaure dans
       l'installateur. Tout le reste n'en est que la préparation.
8. [ ] Lire le §7 (invariants) deux fois.
9. [ ] Se créer un compte `admin_tech` nominatif sur la production et
       désactiver les comptes de la personne partie.
10. [ ] Noter ici un **contact technique de secours** joignable en cas de
       blocage : `[À COMPLÉTER : nom, structure, téléphone]`. L'application
       est un monolithe Flask/Jinja/PostgreSQL sans build front, avec
       ~2 000 tests et une CI verte sur SQLite et PostgreSQL : n'importe
       quel développeur Python la reprend en une semaine — encore faut-il
       qu'un nom soit écrit quelque part.

---

*Document créé en juillet 2026, révisé en septembre 2026 (copies hors
serveur automatisées, chiffres et licence remis à jour) puis en octobre 2026
(installateur Windows et section « ancienne installation » en attendant la
bascule de servisa, pilote psycopg 3, CI PostgreSQL 16/18, chiffres). À
maintenir à chaque changement d'infrastructure (serveur, sauvegarde,
tunnel) : un document de passation périmé est plus dangereux que pas de
document du tout, parce qu'on lui fait confiance.*

*Les chiffres du §1 et la commande de contrôle du §10 servent à repérer la
dérive : quand l'écart devient gênant, c'est que le reste du document a
vieilli aussi.*
