# Sécurité de la distribution Mon Centre Social

Version : 1.0.0-rc1, septembre 2026. Branche issue de `main` :
`codex/consolidation-installateur-migration`. Ce document décrit les constats corrigés
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
| MCS-14 | Annuaire et actions publiques du kiosque insuffisamment bornés. | Recherche et émargement limités au secteur de la séance ou aux inscrits de son atelier ; aucun accès arbitraire par `highlight`. Limites par adresse et séance pour recherche, création, émargement et avis ; suppression du verrou global qui permettait de bloquer tous les appareils. Nouveaux PIN à six chiffres et expiration des ouvertures après 12 heures. |
| MCS-15 | Lien de réinitialisation du mot de passe construit depuis l'en-tête `Host` lorsqu'aucune URL publique n'est configurée (installations hors distribution Windows) : le jeton pouvait partir vers un site choisi par l'attaquant. | URL publique seule ; à défaut, seul un accès local au serveur est accepté, sinon aucun lien n'est envoyé (journalisé). |
| MCS-16 | Veille financements : une source en `file://` faisait lire au serveur ses propres fichiers ; flux XML analysés sans durcissement. | Seules les adresses `http(s)://` avec un hôte sont acceptées, à la saisie et au téléchargement ; `defusedxml` (ajouté au verrou Windows, utilisé aussi par openpyxl). |
| MCS-17 | Redirections ouvertes : seule celle de l'écran de mode était corrigée ; une vingtaine d'écrans suivent encore un champ `next`/`retour` ou le Referer. `Referrer-Policy: same-origin` bloquait les tuiles OpenStreetMap des cartes. | Filet global : toute redirection vers un autre hôte (`//site`, `/\site`, `////site`, `https:site`, caractère de contrôle…) est remplacée par l'accueil et journalisée. `strict-origin-when-cross-origin`. |
| MCS-18 | `/setup-start` affichait une page d'administration sans connexion ; `/launcher/qr` fabriquait, sous le nom de la structure, un QR code vers n'importe quelle adresse. | Connexion et droit de contrôle exigés ; QR codes limités à l'adresse de l'application et à l'adresse kiosque du réseau local. |
| MCS-20 | Le droit de lecture de l'annuaire complet servait aussi de passe-droit en écriture. | `participants:view_all` permet la lecture prévue par le rôle, pas la modification intersectorielle. Les écritures exigent la permission métier et la portée du secteur, sauf `scope:all_secteurs`. Même règle appliquée aux orientations, impayés, répartition, passeport, questionnaires, HART, bénévolat et défis. |
| MCS-19 | Deux actions de l'inventaire sans contrôle de droit ; script de comptes de test (mots de passe publics) utilisable en production ; `bootstrap_user.py` affichait l'URL de la base mot de passe compris. | `inventaire:edit` exigé ; refus en production ; URL masquée et mot de passe demandé au clavier. |

Les références MCS-13 à MCS-19 proviennent de l'audit de la branche
`Claude-Exe` et de sa relecture indépendante ; leurs tests sont dans
`tests/test_securite_complements.py`. Parcours simplifiés (profils, socle
toujours actif, adhésions séparées des finances, rôles « animateur » et
« accueil », page « Outil non activé ») : `tests/test_parcours_simplifies.py`.

## Protections du paquet

- Deux comptes de service Windows virtuels, application et HTTPS séparés,
  sans droits administrateur ni `SeImpersonatePrivilege` ; processus
  enfants associés à un Job Object pour leur arrêt lors d'une terminaison du service.
- PostgreSQL sur la boucle locale uniquement, SCRAM et compte applicatif sans
  privilèges superutilisateur, création de base ou création de rôles.
- Secrets chiffrés par DPAPI machine. La configuration administrative est
  réservée à SYSTEM et aux administrateurs ; la copie du service exclut les
  secrets de provisionnement après initialisation. Le rapport ne contient
  aucun mot de passe ni clé de session. Un dossier précréé avec un propriétaire
  ou des droits d'écriture non autorisés bloque la reprise automatique.
- HTTPS pour l'accès réseau ; pare-feu limité au sous-réseau et aux profils
  Privé/Domaine. L'autorité locale nécessite un déploiement de confiance sur les
  postes clients, expliqué dans le guide. Le mode ordinateur reste sur 127.0.0.1.
- Clé privée de l'autorité locale inaccessible au compte du service web.
  Désinstallation : retrait du certificat de confiance du serveur ; retrait
  sur les autres postes à organiser par la DSI.
- Téléchargements de construction vérifiés par SHA-256, dépendances Python
  verrouillées avec hashes. Bootstrap et Chart.js servis localement, licences
  incluses. L'audit npm de ces composants ne signale aucune vulnérabilité connue.
- Aucun changement de PATH ni d'autorisation PowerShell. Priorité aux outils
  PostgreSQL du paquet pour éviter de dépendre d'une autre version installée.

## Consolidation de septembre 2026

- La façade reconnaît l'en-tête imposé par Tailscale Funnel et normalise les
  noms d'hôtes. Lorsque la façade est configurée, les hôtes inconnus restent
  restreints. Le port kiosque du proxy Windows filtre aussi les chemins,
  indépendamment de l'application. Référence du comportement de l'en-tête :
  [code Tailscale, `addTailscaleIdentityHeaders`](https://github.com/tailscale/tailscale/blob/main/ipn/ipnlocal/serve.go).
- Anonymisation soumise à sa permission propre et au secteur, tracée dans la
  même transaction. Effacement élargi aux coordonnées, données de séjour,
  notes, signatures et pièces individuelles ; suppression des fichiers après
  commit avec reprise en cas d'erreur disque. Voir les limites documentaires
  dans [suppression-participant.md](suppression-participant.md).
- Sauvegarde des uploads et fichiers métier d'instance ; manifeste vérifié
  avant restauration. Échec SQL PostgreSQL annulé en transaction. Les tests
  utilisent des dossiers temporaires, y compris pour la rotation des lots.
- Paramètres SQL et détails d'erreurs du pilote retirés des journaux Flask.
  Les messages métier explicitement rédigés par d'autres modules ne sont pas
  transformés en garantie d'absence de toute donnée personnelle.
- Signatures PNG vérifiées et bornées, modèles Word échappés, protection des
  exports CSV contre les formules et des cellules nominatives des exports
  RGPD/questionnaires XLSX. Les autres familles d'exports XLSX restent à revoir.
- Invalidation des sessions au changement de mot de passe, même coût de
  vérification pour un compte absent, verrouillage des essais par adresse et
  compte. Minimum de 12 caractères pour les nouveaux mots de passe ; ceux
  importés ne sont pas changés.
- Numérotation transactionnelle des dons et factures, refus des dons futurs
  et de la suppression des réservations facturées. Les encaissements sont
  conservés lors de la suppression d'une fiche participant.
- Observations de séance conservées dans l'agenda interne, exclues du flux
  iCal ; partage Google soumis à un choix explicite. Les textes de titres et
  créneaux saisis pour l'agenda restent sous la responsabilité des utilisateurs.

Le lot ne clôt pas tous les constats de l'audit : voir la liste des points
ouverts dans [CONSOLIDATION.md](CONSOLIDATION.md).

## Vérifications et limites

Les tests automatisés couvrent notamment les accès directs aux modules,
l'intersection avec les droits, CSRF, la réactivation, les pièces privées,
les redirections et SMTP. La CI compile et installe l'EXE, puis
`desktop/SystemSmoke.cs` et `desktop/migration_smoke.py` vérifient les services,
HTTPS avec validation du certificat, les ACL, une reprise d'ancien schéma sur
un cluster neuf, les comptes et pièces conservés, la source intacte et une
connexion réelle avec CSRF. Les tests PostgreSQL provoquent également un échec
au milieu d'une restauration et vérifient données et contraintes après rollback.
Le rapport livré à côté de l'EXE donne le résultat de la recette système.

L'audit du verrou Python après correction ne signale aucune vulnérabilité connue
au moment de la vérification. Cela ne garantit pas l'absence de défaut inconnu et
ne remplace pas la veille sur Python, PostgreSQL, Caddy et les autres composants.
Les intégrations externes (cartes, agendas, messagerie et veille) gardent leurs
besoins réseau propres ; le cœur installé n'a pas besoin d'un CDN.

Cette version candidate n'est pas signée Authenticode. Une
[recette système de consolidation sur Windows Server](https://github.com/informatiquecgbcreil/erp_api/actions/runs/35892487524)
a installé le véritable EXE, configuré et démarré le service, vérifié HTTPS,
DPAPI et les ACL du fichier confidentiel, puis effectué arrêt, redémarrage,
reprise PostgreSQL, connexion avec le compte importé, réinstallation et
désinstallation avec conservation de la base et du dossier DSI.
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
