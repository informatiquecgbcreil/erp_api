# Mon Centre Social — installation Windows

Version 1.0.0-rc1. Windows 10 à partir de 1809, Windows 11 et Windows Server
2019/2022/2025 **x64**, avec interface graphique. Server Core n'est pas pris en
charge par l'assistant graphique. Prévoir 2 Go d'espace disponible, puis l'espace
nécessaire aux données et sauvegardes. Le programme utilise .NET Framework 4.7.2
ou supérieur, fourni par ces versions de Windows.

## Installer

1. Double-cliquer sur `Mon-Centre-Social-1.0.0-rc1-Setup-x64.exe` et accepter
   l'élévation Windows avec le compte administrateur de la structure.
2. Saisir la structure et le premier compte de direction.
3. Choisir les outils nécessaires, en partant d'un profil : **Présences et
   statistiques** pour démarrer simplement, **Animation et accueil** (ajoute
   adhésions et caisse, salles, partenaires, pédagogie, questionnaires, sans
   les finances) ou **Tous les outils**. « Accueil et présences » est le
   socle, toujours inclus. Chaque outil reste modifiable à l'unité.
4. Choisir cet ordinateur ou le réseau de la structure. Le SMTP est facultatif
   et peut être ajouté plus tard. Le mode SMTP proposé est STARTTLS (souvent 587).
5. Conserver le dossier confidentiel dans le coffre-fort de la direction/DSI.

Python, PostgreSQL, les bibliothèques de l'application, le serveur HTTPS Caddy et
Visual C++ sont inclus. L'installation ne télécharge rien. Elle ne modifie ni le
PATH ni la politique d'exécution PowerShell. En cas d'interruption, relancer
**Configurer Mon Centre Social** dans le menu Démarrer. Un succès est annoncé
seulement après initialisation de la base et démarrage de l'application.

Le mode silencieux est réservé au préchargement par la DSI : il copie les
composants mais ne crée pas de compte ni d'instance. Terminer ensuite avec
**Configurer Mon Centre Social**. Pour l'installation habituelle, utiliser le
double-clic et suivre l'assistant.

## Utilisation quotidienne

L'icône maison et habitants apparaît dans la zone de notification, parfois sous
la flèche des icônes masquées. Son menu offre **Ouvrir la page d'administration**,
**Redémarrer** et **Fermer**. Redémarrer/fermer le service demande les droits
administrateur Windows, car cela affecte tous les postes. Fermer arrête le
service jusqu'à son redémarrage ou au prochain démarrage de Windows.

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
contient les identifiants initiaux, les mots de passe PostgreSQL (maintenance et
application distincts), la clé de session, les chemins, les modules et le SMTP.
Les ACL NTFS accordent l'accès uniquement à SYSTEM et aux administrateurs
Windows. Ce fichier n'est jamais publié par le serveur et le compte du service
ne peut pas le lire. La DSI peut accorder un accès NTFS nominatif à la direction.
Un rôle dans l'ERP n'accorde pas automatiquement un droit sur un fichier Windows.

Ce document décrit l'installation initiale : tenir à jour le coffre-fort après
toute modification des secrets ou de la messagerie. La configuration d'exécution
est chiffrée par Windows (DPAPI machine) dans `private\configuration.dpapi`.
Ce chiffrement n'est pas transportable tel quel sur une autre machine.

## Réseau et HTTPS

Le mode ordinateur écoute seulement sur `127.0.0.1`. Le mode réseau ajoute HTTPS
sur le nom de la machine et un port disponible à partir de 8443. Le pare-feu
autorise seulement le sous-réseau local sur les profils Privé/Domaine pour
l'administration HTTPS. PostgreSQL reste sur `127.0.0.1`, port choisi à partir
de 55432 ; il n'est jamais publié.

L'installation crée une autorité de certification propre au centre et fait
confiance à son certificat sur le serveur. La DSI déploie
`C:\ProgramData\MonCentreSocial\public\Certificat-du-centre.cer` dans les
**Autorités de certification racines de confiance** des postes (GPO possible).
Seul ce certificat public est à diffuser ; jamais le dossier `runtime\tls`.
Le DNS local doit résoudre le nom choisi. Ne pas ignorer une alerte de certificat.
Pour une exposition Internet, faire configurer le domaine, le certificat public
et les règles réseau par la DSI ; ce paquet est configuré pour le réseau local.

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
inaccessible. Le pare-feu limite le port au sous-réseau local, y compris sur le
profil Windows Public pour les réseaux où Windows classe le Wi-Fi de façon
stricte. Ne pas transférer ce port sur la box, ne pas l'utiliser depuis un
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

Le service effectue une sauvegarde au démarrage puis chaque jour, avec 30 lots
conservés dans `backups`. L'écran Administration > Sauvegardes permet de créer,
contrôler et restaurer les lots, et de configurer des copies hors machine. Une
sauvegarde située sur le même disque ne couvre pas la perte de ce disque.

Avant une mise à jour, réaliser une sauvegarde et sa copie externe. Relancer
l'installateur arrête le service puis remplace les programmes, conserve les
données, la configuration et les comptes, et applique les migrations de schéma.
La mise à jour ne réinitialise aucun mot de passe. Après un échec de migration,
conserver les journaux et restaurer une sauvegarde dans une installation de la
version correspondante ; ne pas forcer un retour à un ancien programme sur un
schéma plus récent.

Après sinistre : réinstaller, recréer le compte direction, restaurer les lots
depuis l'administration puis reconfigurer le SMTP et les secrets depuis le
coffre-fort. Les clés HTTPS doivent être redéployées sur les clients si l'autorité
du centre a changé.

La désinstallation arrête/supprime le service et la règle du pare-feu, mais
**conserve les données et le dossier confidentiel**. Leur suppression définitive
reste une opération explicite de la DSI. Le certificat racine installé peut être
retiré du magasin Windows par la DSI après l'arrêt définitif de l'instance.

## Maintenance et sources

Programmes : `C:\Program Files\Mon Centre Social`. Données :
`C:\ProgramData\MonCentreSocial`. Journaux : `logs`. Service :
`MonCentreSocial`, compte virtuel sans privilèges administrateur
`NT SERVICE\MonCentreSocial`. Aucun mot de passe n'est passé en argument de
processus. L'archive des sources AGPL et les licences des composants sont incluses.

La version livrée n'est pas signée Authenticode : vérifier l'empreinte SHA-256
fournie et l'origine du fichier. Un certificat de signature de l'éditeur sera
nécessaire pour signer les prochaines distributions. La validation automatisée
et ses limites figurent dans le rapport de recette accompagnant l'installateur.
