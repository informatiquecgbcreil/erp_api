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

Cette version candidate n'est pas signée Authenticode. La recette de bout en
bout avec élévation UAC, installation SCM, contrôle des ACL effectives, pare-feu,
mise à jour et désinstallation sur machines propres Windows et Windows Server
reste nécessaire avant une diffusion de production. La machine de construction
ne dispose pas d'une session administrateur élevée ; les tests du runtime ne
valident donc pas ces opérations système. Server Core n'est pas pris en charge.

Les sauvegardes locales ne couvrent pas la perte du poste. Prévoir une copie
hors machine et une recette de restauration métier. Le certificat de signature
éditeur, le DNS local et la configuration du serveur SMTP ne peuvent pas être
inventés par l'installateur.
