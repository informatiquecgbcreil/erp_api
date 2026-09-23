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
Les résultats effectifs et limites sont complétés au fil de la consolidation.
