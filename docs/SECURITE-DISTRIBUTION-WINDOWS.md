# Sécurité de la distribution Mon Centre Social

Version : 1.0.0-rc1, septembre 2026. Branche issue de `main` :
`codex/mon-centre-social-windows`. Ce document décrit les constats corrigés
pendant la préparation de la distribution ; ce travail ne constitue pas un
audit exhaustif ni une certification de sécurité.

## Constats et corrections

| Référence | Constat et conséquence | Correction livrée |
| --- | --- | --- |
| MCS-01 | La route générique `/media` pouvait servir des pièces privées sans authentification. | Seuls les logos effectivement configurés restent publics. Les justificatifs, factures et projets doivent passer par leurs routes métier ; les photos de bilan vérifient le droit et le secteur. L'accès historique `/static/uploads` est bloqué. |
| MCS-02 | La comparaison de chemins par préfixe acceptait des chemins voisins du dossier autorisé. | Vérification du chemin réel et de sa racine commune, avec prise en compte des liens et des volumes Windows. |
| MCS-03 | Le rôle finance possédait les droits d'administration des comptes et rôles, permettant une élévation vers les droits RH. | Retrait du gabarit et migration de révocation des droits administratifs de ce rôle. Les rôles direction et administrateur technique restent distincts. |
| MCS-04 | Masquer un menu ne suffit pas à désactiver un module ; les rapports transversaux pouvaient encore agréger ses données. | Contrôle serveur des routes et des permissions. Bilans financiers rattachés aux finances ; sections et feuilles SENACS filtrées pour RH, finances et partenaires. Choix réservé à la direction/administration technique, protégé par CSRF. |
| MCS-05 | L'ancien installateur et les commandes de sauvegarde pouvaient exposer des mots de passe dans les arguments de processus. | Ancien installateur retiré, configuration DPAPI et canal stdin privé ; mot de passe PostgreSQL de sauvegarde transmis dans l'environnement du processus enfant. Aucun secret dans les arguments. |
| MCS-06 | Une instance de production pouvait démarrer avec une clé de session par défaut. | Refus des clés absentes, connues par défaut ou trop courtes. L'assistant génère les secrets avec le générateur cryptographique Windows. |
| MCS-07 | La première configuration par le Web exposait un mécanisme de création du premier compte. | Dans la distribution Windows, `/setup/` est désactivé ; le premier compte est créé avant l'ouverture du serveur HTTP. |
| MCS-08 | Des appels SMTP utilisaient un paramètre `timeout` invalide pour STARTTLS ; la vérification TLS n'était pas explicitement imposée partout. | Timeout sur la connexion et contexte TLS avec validation de la chaîne et du nom du serveur sur les chemins corrigés (authentification, notifications, réglages et activités). |
| MCS-09 | Le paramètre de retour de l'interface autorisait une redirection vers un site extérieur. | Redirections limitées aux chemins locaux, rejet des URL externes, doubles barres, antislashs et caractères de contrôle. |
| MCS-10 | Une copie brute de SQLite pendant des écritures pouvait produire une sauvegarde incohérente. | API transactionnelle de sauvegarde SQLite et fermeture explicite des connexions ; noms de lots avec microsecondes. PostgreSQL utilise `pg_dump`. |
| MCS-11 | Des médias actifs pouvaient être interprétés sans politique restrictive. | En-tête `nosniff`, CSP avec sandbox pour `/media`, absence de mise en cache des pièces privées. |
| MCS-12 | Pillow 12.2.0 était signalé par l'audit des dépendances. | Passage à 12.3.0 et verrouillage des roues Windows par empreintes. Les identifiants et les versions contrôlées sont dans `AUDIT-DEPENDANCES-WINDOWS.json`. |
| MCS-13 | XSS : les pastilles de filtres recopiaient par `innerHTML` des valeurs issues de l'adresse de la page (un lien piégé exécutait du JavaScript avec les droits de la personne connectée) ; légendes du tableau de bord et infobulle de la carte des partenaires affichées sans échappement (XSS stockée par un nom d'atelier ou de quartier) ; types et liens de la recherche globale non échappés. | Construction par le DOM (`textContent`), échappement systématique, liens de recherche limités aux chemins internes. |
| MCS-14 | Annuaire des participants (`/participants/search`) ouvert à tout compte connecté, même sans aucun droit ; code PIN du kiosque sans limite d'essais ; recherche du kiosque sans frein ; façade « hors les murs » listant les séances ouvertes. | Recherche réservée aux profils qui en ont l'usage ; 10 PIN faux par appareil en 10 minutes ; 120 recherches par minute, par appareil et par séance ; aucune liste sur la façade publique. Derrière Caddy, l'adresse réelle de chaque appareil est bien prise en compte. |
| MCS-15 | Lien de réinitialisation du mot de passe construit depuis l'en-tête `Host` lorsqu'aucune URL publique n'est configurée (installations hors distribution Windows) : le jeton pouvait partir vers un site choisi par l'attaquant. | URL publique seule ; à défaut, seul un accès local au serveur est accepté, sinon aucun lien n'est envoyé (journalisé). |
| MCS-16 | Veille financements : une source en `file://` faisait lire au serveur ses propres fichiers ; flux XML analysés sans durcissement. | Seules les adresses `http(s)://` avec un hôte sont acceptées, à la saisie et au téléchargement ; `defusedxml` (ajouté au verrou Windows, utilisé aussi par openpyxl). |
| MCS-17 | Redirections ouvertes : seule celle de l'écran de mode était corrigée ; une vingtaine d'écrans suivent encore un champ `next`/`retour` ou le Referer. `Referrer-Policy: same-origin` bloquait les tuiles OpenStreetMap des cartes. | Filet global : toute redirection vers un autre hôte (`//site`, `/\site`, `////site`, `https:site`, caractère de contrôle…) est remplacée par l'accueil et journalisée. `strict-origin-when-cross-origin`. |
| MCS-18 | `/setup-start` affichait une page d'administration sans connexion ; `/launcher/qr` fabriquait, sous le nom de la structure, un QR code vers n'importe quelle adresse. | Connexion et droit de contrôle exigés ; QR codes limités à l'adresse de l'application et à l'adresse kiosque du réseau local. |
| MCS-19 | Deux actions de l'inventaire sans contrôle de droit ; script de comptes de test (mots de passe publics) utilisable en production ; `bootstrap_user.py` affichait l'URL de la base mot de passe compris. | `inventaire:edit` exigé ; refus en production ; URL masquée et mot de passe demandé au clavier. |

Les références MCS-13 à MCS-19 proviennent de l'audit de la branche
`Claude-Exe` et de sa relecture indépendante ; leurs tests sont dans
`tests/test_securite_complements.py`. Parcours simplifiés (profils, socle
toujours actif, adhésions séparées des finances, rôles « animateur » et
« accueil », page « Outil non activé ») : `tests/test_parcours_simplifies.py`.

## Protections du paquet

- Compte de service Windows virtuel, sans droits administrateur ; processus
  enfants associés à un Job Object pour leur arrêt lors d'une terminaison du service.
- PostgreSQL sur la boucle locale uniquement, SCRAM et compte applicatif sans
  privilèges superutilisateur, création de base ou création de rôles.
- Secrets de configuration chiffrés par DPAPI machine. Le dossier confidentiel
  contient volontairement les secrets en clair, comme demandé pour la DSI :
  ses ACL limitent la lecture à SYSTEM et aux administrateurs Windows. Il doit
  être conservé dans le coffre-fort de la structure, pas diffusé avec le logiciel.
- HTTPS pour l'accès réseau ; pare-feu limité au sous-réseau et aux profils
  Privé/Domaine. L'autorité locale nécessite un déploiement de confiance sur les
  postes clients, expliqué dans le guide. Le mode ordinateur reste sur 127.0.0.1.
- Téléchargements de construction vérifiés par SHA-256, dépendances Python
  verrouillées avec hashes. Bootstrap et Chart.js servis localement, licences
  incluses. L'audit npm de ces composants ne signale aucune vulnérabilité connue.
- Aucun changement de PATH ni d'autorisation PowerShell. Priorité aux outils
  PostgreSQL du paquet pour éviter de dépendre d'une autre version installée.

## Vérifications et limites

Les tests automatisés couvrent notamment les accès directs aux modules,
l'intersection avec les droits, CSRF, la réactivation, les pièces privées,
les redirections et SMTP. `desktop/smoke.py` vérifie sur un dossier neuf le
PostgreSQL livré, les migrations, HTTPS avec validation du certificat, une
connexion avec CSRF, les pages principales, une sauvegarde puis un arrêt et un
redémarrage avec conservation des comptes. Il utilise un chemin avec espaces et
accents. Le rapport de recette livré à côté de l'EXE donne les résultats effectifs.

L'audit du verrou Python après correction ne signale aucune vulnérabilité connue
au moment de la vérification. Cela ne garantit pas l'absence de défaut inconnu et
ne remplace pas la veille sur Python, PostgreSQL, Caddy et les autres composants.
Les intégrations externes (cartes, agendas, messagerie et veille) gardent leurs
besoins réseau propres ; le cœur installé n'a pas besoin d'un CDN.

Cette version candidate n'est pas signée Authenticode. Une
[recette système sur Windows Server 2025](https://github.com/informatiquecgbcreil/erp_api/actions/runs/35600495904)
a installé le véritable EXE, configuré et démarré le service, vérifié HTTPS,
DPAPI et les ACL du fichier confidentiel, puis effectué arrêt, redémarrage,
réinstallation et désinstallation avec conservation de la base et du dossier DSI.
Le harnais `SystemSmoke.cs` est compilé seulement dans la machine CI temporaire ;
il n'est pas inclus dans les exécutables livrés. Cette recette s'exécute déjà
avec des droits élevés : elle ne valide pas l'interaction humaine avec la boîte
UAC. La recette du parcours graphique complet et des versions Windows 10/11 et
Server 2019/2022 reste à effectuer en environnement pilote. Server Core n'est
pas pris en charge. Le fonctionnement de la règle réseau entre deux machines
et la messagerie avec le véritable SMTP de la structure restent à valider sur site.

Les sauvegardes locales ne couvrent pas la perte du poste. Prévoir une copie
hors machine et une recette de restauration métier. Le certificat de signature
éditeur, le DNS local et la configuration du serveur SMTP ne peuvent pas être
inventés par l'installateur.
