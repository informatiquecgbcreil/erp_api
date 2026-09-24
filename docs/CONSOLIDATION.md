# Consolidation de la distribution Windows

Base de travail : main, f2c46768d6a5ab10c33ab5fbd208170079f01225.
Branche : codex/consolidation-installateur-migration.

## Contrat de reprise

L'assistant propose une installation neuve ou la reprise d'une ancienne
installation **de cet ERP**. Aucun nom de base ni dossier historique n'est
imposé. Le dossier source contient normalement son fichier .env ; une connexion
PostgreSQL peut être renseignée dans l'assistant sans être affichée dans les logs.

Sources prises en charge : PostgreSQL 10 à 17, historique Alembic connu. Une
base sans historique ou provenant d'une version inconnue est refusée, sans
modification. Cela exige une analyse de schéma préalable, pas un stamp(head)
aveugle. La conversion SQLite vers PostgreSQL n'est pas proposée par ce parcours.

Avant la reprise, suspendre toutes les saisies et les tâches qui modifient la
source. L'assistant arrête le service Windows sélectionné. Si aucun service n'est
renseigné, l'opérateur confirme l'avoir arrêté lui-même. La source est lue avec
des transactions en lecture seule et un snapshot PostgreSQL partagé avec pg_dump.

Chaque tentative utilise une base de destination distincte. Le dump est restauré
en transaction ; les empreintes et effectifs de toutes les tables sont comparés
avant les migrations. Les comptes et mots de passe sont comparés après migration.
Les fichiers instance/uploads sont copiés avec empreintes ; les chemins absolus
des documents sont adaptés. La nouvelle application ne démarre pas si la reprise
n'est pas terminée. Les comptes et modules existants sont conservés.

Après succès, l'ancien service sélectionné est désactivé pour éviter deux
applications concurrentes après un redémarrage Windows. La source et les copies
de travail restent conservées. Les tentatives interrompues peuvent laisser des
bases mcs_reprise_* dans le cluster géré ; ne les supprimer qu'après validation.

## Recette avant bascule en exploitation

- Vérifier le rapport Reprise.json, les comptes, participants, présences et pièces.
- Tester les fonctions réellement utilisées, l'accès depuis un poste salarié et
  un téléphone, les exports et l'envoi d'un courriel d'essai.
- Effectuer une sauvegarde complète et une restauration sur une installation
  de test, puis vérifier les mêmes documents.
- Réouvrir les saisies sur une seule installation.

Avant la réouverture des saisies, le retour arrière consiste à arrêter le nouveau
service et à réactiver l'ancien sur sa base intacte. Après de nouvelles saisies,
ce retour exigerait une reprise de ces changements : il n'est plus automatique.

## Sauvegardes

Le fichier *_uploads.zip utilise désormais un format versionné comprenant
uploads/, instance/ et un manifeste avec empreintes. Les bases SQLite actives,
journaux et sauvegardes imbriquées ne sont pas archivés comme documents.
Les anciens lots restent lisibles, mais ne contiennent pas les fichiers instance
qu'ils omettaient historiquement. Les restaurations de fichiers ne sont pas une
transaction commune avec PostgreSQL : conserver le lot de sécurité préalable.

Le rapport d'installation ne contient plus les mots de passe ni la clé de session.
Les secrets actifs résident dans la configuration DPAPI protégée ; après sinistre
sur une autre machine, les réglages de messagerie et intégrations sont à reprendre
depuis le coffre de la structure.

## Conditions de livraison

Tests SQLite et PostgreSQL ; construction de l'EXE et recette du service Windows,
installation neuve, reprise, mise à jour, arrêt/redémarrage, sauvegarde/restauration.
Une réussite des seuls tests Python ne constitue pas une validation de l'EXE.
La PR [#50](https://github.com/informatiquecgbcreil/erp_api/pull/50) contient
les résultats de CI rattachés à chaque commit. Ne pas confondre un ancien résultat
vert avec la validation d'une modification ultérieure.

## Corrections livrées dans cette branche

| Domaine | Changement |
|---|---|
| Reprise Windows | Choix neuf/reprise, nom de base libre, copie PostgreSQL, comparaison des tables et des comptes, fichiers et réglages conservés, source en lecture seule, activation différée. |
| Accès | Contrôle partagé du secteur sur les écritures participants, orientations, passeport et pièces, impayés, HART, bénévolat, transitions et questionnaires. Anonymisation soumise au droit dédié et journalisée. |
| Kiosque | Filtrage du tunnel, port Windows dédié, expiration, PIN renforcé, recherche et émargement bornés, quotas, validation des signatures et nettoyage après échec. |
| Sauvegardes | `instance` et uploads archivés avec manifeste ; restauration PostgreSQL transactionnelle ; vérification des archives avant écriture ; lot de sécurité préalable ; dossiers des tests isolés. |
| Confidentialité | Effacement enrichi, fichiers supprimés après validation de la transaction, exports d'accès complétés, nouveaux journaux de suppression minimisés, paramètres et détails SQL masqués. |
| Sessions | Invalidation après changement de mot de passe, blocage de connexion par compte et adresse, même règle de 12 caractères pour les nouveaux mots de passe. Les mots de passe repris restent utilisables. |
| Documents | Formules neutralisées dans les CSV et principaux exports nominatifs XLSX, échappement Word, observations retirées du flux iCal ; partage Google des bilans explicitement choisi. |
| Comptabilité | Compteurs de numérotation concurrents, refus des dons futurs, numéros de factures non réutilisés, paiements conservés lors de la suppression d'un participant. |
| Distribution | Services web et HTTPS séparés ; clé CA inaccessible au service web ; privilèges réduits ; secrets séparés et absents du rapport ; pare-feu Privé/Domaine ; reprise de configuration confirmée ; paquet limité aux fichiers suivis par Git. |
| Sources | Archive des sources exactes accessible depuis l'interface installée et le kiosque, licence Leaflet livrée. |

## Ce qui a été effectivement exécuté

La CI Windows construit l'EXE puis vérifie une installation neuve, les services,
HTTPS avec vérification du certificat, DPAPI, les ACL et privilèges, les sauvegardes,
l'arrêt/redémarrage, la mise à jour et la désinstallation avec conservation des données.
Elle reprend ensuite une base PostgreSQL 17 nommée `erp_pedagogie`, à la révision
historique `de23fa45bc67`, avec comptes, documents et paramètres. Elle vérifie la
source inchangée et une connexion réelle en HTTPS avec le compte repris, CSRF actif.
Ce nom de base appartient uniquement au scénario de test ; le parcours ne l'impose pas.
Les tests de l'application passent aussi sur SQLite et PostgreSQL.

Cette recette synthétique ne constitue pas une reprise de la base réelle d'une
structure. Les autres anciennes révisions connues et versions PostgreSQL admises
ne sont pas toutes rejouées sur Windows. Le retour automatique vers l'ancien
service est codé, mais toutes les pannes matérielles et interruptions de bascule
n'ont pas été injectées en CI. La restauration complète après sinistre sur une
seconde machine doit encore faire partie de la recette du centre.

## Points encore ouverts

Cette branche ne clôt pas tous les constats du rapport d'audit. En particulier :

- Les anciennes traces d'audit nominatives, les anciens journaux, sauvegardes et
  documents collectifs ne sont pas réécrits. Les règles de conservation des
  donateurs, locataires et bulletins isolés restent à préciser.
- La restauration SQL et la restauration des fichiers n'ont pas de transaction
  commune. Conserver le lot préalable et maintenir les saisies arrêtées jusqu'au
  contrôle de la restauration. Une ancienne sauvegarde restaurée depuis
  l'interface doit être suivie d'un redémarrage pour appliquer les migrations.
- Les rapprochements de caisse des bulletins payés avant création de la fiche,
  l'usage de nombres flottants pour l'argent et l'immutabilité des factures après
  émission nécessitent encore un travail métier et des migrations dédiées.
- La veille de financements doit encore filtrer les destinations réseau et les
  liens de flux ; les opérations de finalisation encore accessibles en GET
  doivent passer en POST.
- Les performances SENACS, la pagination des participants et la sortie des
  purges quotidiennes hors des requêtes HTTP restent à traiter.
- Les migrations historiques descendantes, les écarts de contraintes entre
  schémas réels et modèles, les clés étrangères SQLite, les exceptions trop
  larges, l'ancien lanceur Windows de développement et les métadonnées Word
  restent à reprendre.
- Les salles restent partagées entre secteurs ; ce choix existant nécessite une
  décision fonctionnelle s'il doit changer. Une affectation de secteur absente
  donne désormais un refus plutôt qu'un accès global.

Le guide [GUIDE-WINDOWS.md](GUIDE-WINDOWS.md) décrit la reprise et les limites de
retour arrière. Ne pas supprimer l'ancienne installation avant la recette réelle.
