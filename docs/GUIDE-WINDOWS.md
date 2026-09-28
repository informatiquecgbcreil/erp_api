# Mon Centre Social — installation Windows

Version 1.0.0-rc2. Windows 10 à partir de 1809, Windows 11 et Windows Server
2019/2022/2025 **x64**, avec interface graphique. Server Core n'est pas pris en
charge par l'assistant graphique. Prévoir 2 Go d'espace disponible, puis l'espace
nécessaire aux données et sauvegardes. Le programme utilise .NET Framework 4.7.2
ou supérieur, fourni par ces versions de Windows.

## Installer

1. Double-cliquer sur `Mon-Centre-Social-1.0.0-rc2-Setup-x64.exe` et accepter
   l'élévation Windows avec le compte administrateur de la structure.
2. Choisir **Nouvelle installation** ou **Reprendre une ancienne installation
   de cet ERP**. Pour une installation neuve, saisir la structure et le premier
   compte de direction (mot de passe de 12 caractères minimum).
3. Choisir les outils nécessaires, en partant d'un profil : **Présences et
   statistiques** pour démarrer simplement, **Animation et accueil** (ajoute
   adhésions et caisse, salles, partenaires, pédagogie, questionnaires, sans
   les finances) ou **Tous les outils**. « Accueil et présences » est le
   socle, toujours inclus. Chaque outil reste modifiable à l'unité.
4. Choisir cet ordinateur ou le réseau de la structure. Le SMTP est facultatif
   et peut être ajouté plus tard. Le mode SMTP proposé est STARTTLS (souvent 587).
5. Conserver le rapport d'installation avec la documentation de la direction/DSI
   et les secrets de messagerie/intégrations dans son coffre-fort.

Python, PostgreSQL, les bibliothèques de l'application, le serveur HTTPS Caddy et
Visual C++ sont inclus. L'installation ne télécharge rien. Elle ne modifie ni le
PATH ni la politique d'exécution PowerShell. En cas d'interruption, relancer
**Configurer Mon Centre Social** dans le menu Démarrer. Un succès est annoncé
seulement après initialisation de la base et démarrage de l'application.

Le mode silencieux est réservé au préchargement par la DSI : il copie les
composants mais ne crée pas de compte ni d'instance. Terminer ensuite avec
**Configurer Mon Centre Social**. Pour l'installation habituelle, utiliser le
double-clic et suivre l'assistant.

## Reprendre une installation existante

La reprise copie une ancienne installation dans une nouvelle base gérée par le
paquet. Le nom de la source est libre : `erp_pedagogie` est un exemple testé,
pas un nom imposé. **La source n'est ni renommée, ni mise à jour, ni écrasée.**
Les anciens comptes et mots de passe sont conservés ; aucun nouveau compte de
direction n'est créé dans ce mode. Les outils actifs sont repris depuis la base.

1. Faire une sauvegarde externe de la base, des fichiers métier et de la
   configuration de l'ancienne installation. Prévoir l'espace pour le dump,
   l'archive, sa décompression, la nouvelle base et les sauvegardes.
2. Suspendre les saisies et les tâches qui modifient la source. Dans l'assistant,
   sélectionner le dossier de l'ancienne application, normalement celui de son
   `.env`. Une connexion PostgreSQL peut être renseignée séparément si nécessaire.
3. Indiquer le **nom du service de l'ancienne application**, pas celui de
   PostgreSQL. L'assistant l'arrête avant la copie. Si ce champ reste vide,
   arrêter soi-même l'application et ses tâches ; PostgreSQL doit rester joignable.
4. Choisir l'accès local ou réseau de la nouvelle installation et lancer la reprise.
5. Lire `Direction-DSI\Reprise.json`, ouvrir les fiches, présences, documents et
   exports utilisés par la structure. Tester les intégrations avant de réouvrir
   les saisies sur la nouvelle application.

Le logiciel vérifie un historique Alembic connu, compare les effectifs et
empreintes de toutes les tables avant migration, puis les comptes après migration.
Les pièces jointes, signatures, archives d'émargement et modèles situés dans
`instance` et le dossier d'uploads sont copiés et leurs chemins adaptés. Les
documents référencés ailleurs ne bloquent plus la reprise :

- chemin d'un ancien emplacement (dossier déplacé ou renommé), fichier présent
  sous `instance` ou `uploads` : retrouvé par la fin de son chemin et corrigé ;
- fichier lisible hors de ces dossiers (partage, autre disque) : recopié dans
  `instance\documents_repris` s'il s'agit d'un document (image, PDF, Word,
  tableur, texte) de 50 Mo au plus ;
- fichier introuvable : déjà perdu dans l'ancienne installation, la référence
  est conservée telle quelle.

`Direction-DSI\Reprise.json` indique le nombre de documents de chaque cas, par
table, sans aucun chemin.

Les paramètres SMTP, Google Agenda, portail, publication FTP, cartographie,
conservation et autres intégrations déclarées dans `.env` sont repris selon la
liste de `desktop/migration.py`. Les paramètres stockés en base suivent la base.
Les variables définies uniquement dans NSSM, PowerShell ou le compte de service
ne sont pas devinées : les reporter explicitement dans la configuration source
avant reprise. Les chemins de programmes externes (notamment LibreOffice), les
droits d'accès aux partages et les URI de retour OAuth restent à vérifier sur
la machine de destination.

Sources acceptées : PostgreSQL 10 à 18 avec historique de migrations reconnu.
La recette automatisée reprend PostgreSQL 18.1 vers 18.6 avec un ancien schéma
de l'ERP ; elle vérifie aussi le fonctionnement d'un cluster local 17 existant.
Une base sans version Alembic, d'une version inconnue, SQLite ou provenant d'un
autre logiciel nécessite une étude préalable ; l'assistant refuse de la marquer
artificiellement comme à jour.

Faire la reprise sur la machine qui héberge l'ancienne installation : les
documents référencés par un chemin absolu (`C:\...`) doivent être lisibles à ce
chemin. En cas d'échec, le message indique la cause principale (mot de passe
refusé, version, droits, connexion) sans afficher de secret. Les copies de
travail (sauvegarde de la base et des documents) sont supprimées à la fin de
chaque tentative, réussie ou non.

Après réussite, l'ancien service sélectionné est désactivé. En cas d'échec avant
activation, le nouveau service est arrêté et l'ancien est relancé s'il tournait
au départ. Si l'ancien service a été arrêté manuellement, sa remise en route est
manuelle également. Ne jamais laisser les deux applications ouvertes aux saisies.
Avant toute saisie sur la nouvelle instance, on peut revenir à l'ancienne base
intacte. Après de nouvelles saisies, un retour arrière nécessite de reprendre
ces changements.

## Reprise bloquée par la limite PostgreSQL 10–17 de la rc1

La rc2 embarque PostgreSQL 18.6 pour les nouvelles installations et les nouvelles
reprises. PostgreSQL 17.11 reste livré séparément : une instance 17 déjà activée
continue à fonctionner avec le moteur 17. Aucun fichier de données 17 n'est
ouvert directement par le moteur 18.

Si la rc1 a refusé une source 18.1, fermer son assistant, installer la rc2 au même
emplacement et relancer **Configurer Mon Centre Social**, puis confirmer la
reprise de la configuration existante. La connexion saisie précédemment est
conservée dans la configuration protégée. Ne pas désinstaller PostgreSQL source
et ne pas supprimer le dossier ProgramData.

Pour cette reprise non terminée, l'ancien cluster de destination 17 est conservé
sous `runtime/reprise-pg17-<identifiant>`, puis une nouvelle destination 18 est
créée. La source est de nouveau copiée et vérifiée. Un import déjà achevé et en
attente d'activation garde son cluster, sans nouvelle conversion implicite.
Les binaires de sauvegarde et de restauration suivent le moteur du cluster.

## Adresse des postes : l'adresse IP plutôt que le nom

Le certificat HTTPS couvre le nom du serveur et son adresse IPv4 sur le réseau
local. `public\url.txt`, l'icône et le rapport donnent l'adresse par IP (par
exemple `https://192.168.1.200:8443`) : elle ne dépend pas du DNS. Sur un
serveur qui porte aussi un VPN ou Tailscale, le nom peut renvoyer plusieurs
adresses, dont certaines injoignables depuis un poste ; l'accès par le nom
devient alors lent ou aléatoire. Réserver l'adresse du serveur dans le DHCP.

L'assistant **affiche l'adresse IP retenue et permet de la corriger** : c'est
celle de la carte reliée à la box ou au routeur (passerelle par défaut), hors
cartes virtuelles (TAP/OpenVPN, Wintun, WireGuard, Tailscale, Hyper-V,
vEthernet…) et hors 100.64.0.0/10. Une adresse retenue est conservée tant
qu'elle existe sur le serveur ; si elle disparaît, la détection est refaite au
prochain « Redémarrer ». Le champ **Autres adresses de ce serveur (VPN,
Tailscale)** ajoute ces adresses (ou noms) au certificat, aux hôtes acceptés
par l'application et au pare-feu (plage Tailscale 100.64.0.0/10, /24 de chaque
adresse VPN) : les collègues passant par le VPN gardent l'accès.

Les ports sont choisis libres sur **toutes** les adresses de la machine (un
programme qui écoute seulement sur l'adresse du VPN est vu). Si un programme
installé plus tard prend un port de l'application, « Redémarrer » ou
« Configurer » en choisit un autre (le port PostgreSQL, inscrit dans la base,
n'est jamais changé : un message l'indique).

## Utilisation quotidienne

L'icône maison et habitants apparaît dans la zone de notification, parfois sous
la flèche des icônes masquées. Son menu offre **Ouvrir la page d'administration**,
**Redémarrer**, **Arrêter le service pour tout le monde…** (avec confirmation)
et **Fermer l'icône**. Fermer l'icône ne touche plus au service : l'application
continue pour toute l'équipe. Redémarrer/arrêter le service demande les droits
administrateur Windows, car cela affecte tous les postes. Tant qu'une reprise
n'est pas terminée, **Redémarrer** ne fait rien et renvoie vers « Configurer Mon
Centre Social » : l'icône n'arrête jamais l'ancien service en production.

Le service fonctionne en arrière-plan avant toute ouverture de session. Les
personnes se connectent dans leur navigateur avec un compte propre. La direction
crée les comptes et attribue les rôles et les secteurs dans Administration.

**Outils du centre** permet d'ajouter/enlever des modules sans réinstallation et
sans suppression des données, en partant d'un profil si besoin. La désactivation
bloque également les adresses directes et les permissions associées, même pour la
direction ; une personne connectée qui suit un ancien lien voit une page
« Outil non activé » qui explique où l'activer. Le rôle attribué reste nécessaire
pour accéder à un module actif. « Accueil et présences » ne se désactive pas.
Les anciennes installations qui n'ont pas de sélection conservent leurs modules
actifs.

« Adhésions et caisse » (adhésions et participation des familles, règlements,
impayés, tarifs, caisse) est un outil distinct de « Finances et projets » : un
accueil peut encaisser les adhésions sans voir budgets ni subventions. Une
installation qui avait activé les finances avant cette séparation garde ses
écrans d'adhésion (ajout automatique à la mise à jour).

Deux rôles prêts à l'emploi facilitent les parcours de l'équipe (Administration ›
Équipe) : **Animateur / animatrice** (ateliers, émargement, pédagogie,
questionnaires, fréquentation, publics de son secteur) et **Accueil**
(inscriptions, inscriptions de rentrée, adhésions, caisse, salles, annuaire
complet ; ni finances ni administration). Ils sont créés au démarrage s'ils
n'existent pas, puis ajustables dans l'écran des droits.

## Dossier confidentiel

`C:\ProgramData\MonCentreSocial\Direction-DSI\Installation-confidentielle.txt`
contient les adresses d'accès, les chemins et des renseignements techniques.
Il ne contient ni mot de passe ni clé de session.
Les ACL NTFS accordent l'accès uniquement à SYSTEM et aux administrateurs
Windows. Ce fichier n'est jamais publié par le serveur et le compte du service
ne peut pas le lire. La DSI peut accorder un accès NTFS nominatif à la direction.
Un rôle dans l'ERP n'accorde pas automatiquement un droit sur un fichier Windows.

La configuration administrative est chiffrée par Windows (DPAPI machine) dans
`private\configuration.dpapi`, lisible uniquement par SYSTEM et les
administrateurs. Le service reçoit une copie limitée dans `private\service.dpapi` :
après initialisation, elle ne contient ni mot de passe administrateur PostgreSQL,
ni mot de passe initial de direction, ni connexion à l'ancienne base.
La configuration DPAPI n'est pas transportable telle quelle sur une autre machine.
Les secrets SMTP/intégrations doivent aussi être conservés dans le coffre-fort de
la structure pour une reprise après sinistre.

## Réseau et HTTPS

Le mode ordinateur écoute seulement sur `127.0.0.1`. Le mode réseau ajoute HTTPS
sur le nom de la machine et un port disponible à partir de 8443. Le pare-feu
autorise seulement le sous-réseau local sur les profils Privé/Domaine pour
l'administration HTTPS ; si des adresses VPN/Tailscale sont déclarées, leurs
plages s'ajoutent et la règle vaut pour tous les profils (les cartes VPN sont
souvent classées « Public »), la liste d'adresses restant limitée à ces plages. PostgreSQL reste sur `127.0.0.1`, port choisi à partir
de 55432 ; il n'est jamais publié.

L'installation crée une autorité de certification propre au centre et fait
confiance à son certificat sur le serveur. Sur une **installation neuve**, cette
autorité est **contrainte** (extension NameConstraints critique) : elle ne peut
signer que pour le nom du serveur, les autres noms déclarés, les adresses
privées (10/8, 172.16/12, 192.168/16, 100.64/10, 127/8) et les adresses
supplémentaires déclarées. Même volée, elle ne permet pas d'usurper un site de
l'Internet sur les postes qui lui font confiance. Une installation existante
garde son autorité (déjà déployée sur les postes) ; pour passer à une autorité
contrainte, repartir d'une installation neuve (`--reset` puis reprise) et
redéployer le nouveau certificat. En attendant, ne déployer
`Certificat-du-centre.cer` que sur les postes qui utilisent l'administration.
Changer ensuite le nom du serveur demande une nouvelle autorité (voir
« Ajouter un nom d'accès » ci-dessous pour un nom supplémentaire). La DSI déploie
`C:\ProgramData\MonCentreSocial\public\Certificat-du-centre.cer` dans les
**Autorités de certification racines de confiance** des postes (GPO possible).
Seul ce certificat public est à diffuser ; jamais le dossier `https\tls`.
Sa clé privée est réservée au service HTTPS distinct `MonCentreSocialHTTPS`,
à SYSTEM et aux administrateurs ; le service de l'application ne peut pas la lire.
Le DNS local doit résoudre le nom choisi. Ne pas ignorer une alerte de certificat.
Pour une exposition Internet, faire configurer le domaine, le certificat public
et les règles réseau par la DSI ; ce paquet est configuré pour le réseau local.

### Ajouter un nom d'accès après l'installation (ex. `gestion.cgb`)

**Ne pas modifier le Caddyfile à la main** : il est réécrit à chaque démarrage,
et l'application refuse un nom qu'elle ne connaît pas (page « Bad Request —
Host 'gestion.cgb:8443' is not trusted », code 400). Utiliser le menu Démarrer
**Adresses d'accès au serveur** (ou l'icône → « Adresses d'accès au serveur… »,
droits administrateur) : un nom ou une adresse par ligne ; une adresse collée
en entier (`https://gestion.cgb:8443/`) est ramenée au nom. Le service redémarre
et le nom entre à la fois dans les hôtes acceptés par l'application, le
Caddyfile et le certificat.

Si l'autorité du centre est **contrainte** et ne couvre pas ce nom (cas d'un nom
qui n'est ni celui du serveur ni un sous-nom de celui-ci), elle ne peut pas être
étendue : l'outil l'annonce et demande l'accord pour en créer une nouvelle.
Refus : rien ne change. Accord : l'ancienne autorité, son certificat
intermédiaire et ses certificats de site sont déplacés (jamais effacés) dans
`https\autorite\remplacees\<date>`, l'ancienne est retirée du magasin de
confiance du serveur, et **le nouveau `Certificat-du-centre.cer` doit être
installé sur chaque poste** (sinon « Non sécurisé »). Une installation plus
ancienne (autorité non contrainte) n'a pas ce problème.

Sur les postes, le nom doit désigner l'adresse IPv4 du serveur : enregistrement
DNS (box ou serveur DNS du centre) ou ligne `192.168.1.200 gestion.cgb` dans
`C:\Windows\System32\drivers\etc\hosts` (exemple d'adresse). Toujours
`https://…:8443` : `http://` sur ce port répond « Bad Request » (400).

En ligne de commande (administrateur) :
`MonCentreSocial.exe --adresses "gestion.cgb,10.8.0.4" [--renouveler-autorite]`
(code 2 si une nouvelle autorité est nécessaire sans l'option : rien n'a changé).

### Téléphones et tablettes : kiosque sans certificat

L'installation réseau crée aussi une **adresse kiosque locale** dans le dossier
confidentiel et dans `public\kiosk-url.txt`. Elle ressemble à
`http://192.168.1.20:8080/kiosk/`. Le bouton **Kiosque** et les QR codes de
l'émargement utilisent cette adresse. Un téléphone ou une tablette connecté au
même Wi-Fi peut donc ouvrir le kiosque sans installer le certificat HTTPS du
serveur. L'adresse IPv4 est détectée pendant l'installation ; si le serveur
change de carte réseau ou d'adresse, relancer **Configurer Mon Centre Social**
pour régénérer le lien.

Ce port HTTP ne publie que `/kiosk`, les feuilles de style, les logos et
`/healthz`. Les routes d'administration renvoient 403 et PostgreSQL reste
inaccessible. Le pare-feu limite le port au sous-réseau local, sur les profils
Windows **Privé et Domaine** (et aux plages VPN/Tailscale déclarées, voir plus haut). Ne pas
transférer ce port sur la box, ne pas l'utiliser depuis un
Wi-Fi invité isolé et ne pas considérer ce lien comme un accès Internet. Pour
une activité hors de la structure, utiliser le tunnel HTTPS décrit dans
`docs/kiosque-hors-les-murs.md`.

Les messages du kiosque (« Code invalide », « Tu es déjà émargé(e) »,
« Merci ! ») s'affichent aussi sur ce port HTTP : son cookie, qui ne porte
aucune session de compte, n'y est pas réservé au HTTPS. La page de lancement
(`/launcher/`) affiche elle aussi l'adresse et le QR code du kiosque local.

Si un appareil ne se connecte toujours pas : vérifier qu'il est sur le même
Wi-Fi que le serveur, que le réseau autorise les appareils à communiquer entre
eux (désactiver l'isolation « clients Wi-Fi » pour ce SSID), puis tester
l'adresse complète avec `/kiosk/`. L'administration continue d'utiliser
l'adresse HTTPS et le certificat déployé par la DSI.

## Sauvegarder, restaurer, mettre à jour

Le service fait **une sauvegarde par jour** (pas de nouveau lot à chaque
redémarrage s'il en existe déjà un du jour), avec 30 lots conservés dans
`backups`. Chaque lot est **vérifié** (base, archive, empreintes), puis
**recopié vers les dossiers hors serveur** indiqués dans Administration >
Sauvegardes. Le bouton « Enregistrer et tester l'écriture » écrit un fichier
d'essai avec le compte du service (`NT SERVICE\MonCentreSocial`) : un partage
réseau que seul l'administrateur peut écrire est signalé tout de suite. Pour un
partage, accorder l'écriture au compte ordinateur du serveur (`DOMAINE\SERVEUR$`)
ou utiliser un disque externe. L'état de la dernière sauvegarde quotidienne
(lot vérifié, copies réussies ou non) s'affiche en haut de l'écran. Une
sauvegarde située sur le même disque ne couvre pas la perte de ce disque.

Le délai de la sauvegarde est proportionné au volume (15 min + 1 min par
100 Mo, 8 h au plus) ; au-delà, la sauvegarde et son `pg_dump` sont arrêtés et
le lot partiel est **refusé à la restauration** (dump sans marqueur de fin,
archive illisible ou empreintes absentes : jamais restauré).

**Tester la restauration** : `MonCentreSocial.exe --restore-test` en
administrateur rejoue réellement le dernier lot dans une base PostgreSQL
jetable, vérifie les tables et les documents, puis supprime cette base. Rien
n'est modifié dans l'application en service. Le rapport est dans
`private\essai-restauration.json`. À faire après chaque mise à jour et au moins
une fois par trimestre.
Chaque lot comprend la base, les uploads, les fichiers métier d'`instance` et
des empreintes de contrôle. Les anciens lots restent lisibles mais ne peuvent
pas restituer les fichiers d'`instance` qu'ils n'avaient jamais sauvegardés.
PostgreSQL est restauré dans une transaction : une erreur SQL annule ses
modifications. La base et les fichiers n'ont pas de transaction commune ;
conserver le lot de sécurité préalable et fermer les saisies pendant l'opération.

Avant une mise à jour, réaliser une sauvegarde et sa copie externe. Relancer
l'installateur arrête le service puis **remplace entièrement** les programmes
(application, Python, Caddy : aucun ancien fichier ne reste mêlé aux nouveaux),
conserve les données, la configuration et les comptes, et applique les
migrations de schéma. Une migration longue n'est plus coupée : tant qu'elle
avance, le service et l'installateur patientent. En mode silencieux
(`/VERYSILENT`), l'installateur rend le code **8** si le centre n'a pas
redémarré (détail dans `logs\mise-a-jour-erreur.txt`).
La mise à jour ne réinitialise aucun mot de passe. Après un échec de migration,
conserver les journaux et restaurer une sauvegarde dans une installation de la
version correspondante ; ne pas forcer un retour à un ancien programme sur un
schéma plus récent.

**Registres des numéros émis et des effacements RGPD.** Ils vivent dans
`runtime` (hors des sauvegardes ordinaires) pour qu'une restauration ancienne
ne fasse jamais réutiliser un numéro de reçu ou de facture, ni revenir une
personne effacée. Chaque lot en emporte une **copie** (`<lot>_registres.json`),
copiée hors serveur avec lui ; à la restauration elle est **fusionnée** (rien
ne recule). Changement de serveur : Contrôle → Registres → « Exporter » sur
l'ancien, « Importer » sur le nouveau après la restauration. Une restauration
est refusée tant qu'un effacement récent n'a pas pu être recopié dans le
registre (droits du dossier des données à vérifier). Détail :
`docs/CONSOLIDATION-APRES-PR59.md`.

**Restauration d'une sauvegarde plus ancienne que le programme** : le centre passe
en maintenance, la base restaurée est mise au niveau de cette version, puis remise
en service. En cas d'échec, l'état d'avant est remis automatiquement (« rien n'a
changé »). Si la page « Maintenance en cours » reste affichée, un administrateur
restaure depuis Administration → Sauvegardes la sauvegarde de sécurité indiquée.

**Émission suspendue** : si le registre des numéros émis est endommagé ou a disparu
(nouvelle machine, dossier des données perdu), aucun reçu, facture ni avoir n'est
émis tant qu'un administrateur n'a pas rétabli, dans Contrôle → Registres, le
dernier numéro de chaque série relevé sur les documents.

Après sinistre : réinstaller, recréer le compte direction, restaurer les lots
depuis l'administration puis reconfigurer le SMTP et les secrets depuis le
coffre-fort. Si aucune copie récente n'a survécu, déclarer dans Contrôle →
Registres le dernier numéro de chaque série relevé sur les reçus, factures et
avoirs papier, et rejouer les demandes d'effacement RGPD reçues depuis la
dernière sauvegarde. Les clés HTTPS doivent être redéployées sur les clients si l'autorité
du centre a changé.

La désinstallation arrête/supprime les deux services et leurs règles de pare-feu, mais
**conserve les données et le dossier confidentiel**. Leur suppression définitive
reste une opération explicite de la DSI. Le certificat racine du centre est
retiré du magasin de confiance du serveur. La DSI doit aussi le retirer des
autres postes après l'arrêt définitif de l'instance.

## Maintenance et sources

Programmes : `C:\Program Files\Mon Centre Social`. Données :
`C:\ProgramData\MonCentreSocial`. Journaux : `logs`. Service :
`MonCentreSocial`, compte virtuel sans privilèges administrateur
`NT SERVICE\MonCentreSocial`. HTTPS : service et compte virtuel distincts
`MonCentreSocialHTTPS`. Le privilège d'impersonation est retiré des services.
Aucun mot de passe n'est passé en argument de
processus. L'archive des sources AGPL et les licences des composants sont incluses.

La version livrée n'est pas signée Authenticode : vérifier l'empreinte SHA-256
fournie et l'origine du fichier. Un certificat de signature de l'éditeur sera
nécessaire pour signer les prochaines distributions. La validation automatisée
et ses limites figurent dans le rapport de recette accompagnant l'installateur.

## Surveillance et journaux

Le service vérifie toutes les 30 secondes que PostgreSQL tourne ; s'il s'est
arrêté, le service s'arrête aussi et Windows le relance (au lieu d'erreurs 500
sans fin). `service.log`, `runtime.log`, `proxy.log` et
`postgresql-start.log` sont renouvelés au-delà de 5 Mo (un seul ancien journal
`.previous.log` est gardé). HTTP/3 n'est plus annoncé par le proxy (le
pare-feu n'ouvre que TCP).

## Accès « hors les murs » repris d'une ancienne installation

Si l'ancien `.env` contenait `KIOSK_PUBLIC_HOST` (tunnel Tailscale Funnel ou
Cloudflare), le réglage est repris, mais le tunnel vise encore l'ancien port.
La fin de l'assistant et le dossier confidentiel l'indiquent : redirigez le
tunnel vers `http://127.0.0.1:<port kiosque>` (avec Tailscale :
`tailscale funnel --bg http://127.0.0.1:<port kiosque>`, après avoir arrêté
l'ancien). `Direction-DSI\Reprise.json` liste les noms des réglages repris
(`reglages_importes`), jamais leurs valeurs.
