# Migration historique des présences

Le classeur est d'abord analysé **sans secteur global**. Après identification des feuilles d'activité, chaque activité est affectée à son secteur dans l'aperçu. L'affectation s'applique aux séances et à leurs présences. Une activité existante conserve son secteur ; l'import ne déplace aucune activité existante.

## Diagnostic de l'existant

Audit réalisé sur le dépôt `informatiquecgbcreil/erp_api`, base `d67da0b`, branche de travail `feat/historical-xlsx-import`.

Sources inspectées : `app/ateliers/excel_import.py`, les modèles `Participant`, `Quartier`, `AtelierActivite`, `SessionActivite`, `PresenceActivite` dans `app/models.py`, `app/admin/routes.py`, `app/templates/admin_import_excel.html`, `app/services/import_participants.py`, `app/services/doublons.py`, `app/rbac.py`, `app/secteurs.py`, les migrations Alembic et les tests correspondants.

L'import standard disposait déjà de la détection NOM/PRENOM, de dates Excel complètes, de marqueurs de présence, d'une transaction avec rollback en dry-run, d'un choix libre de secteur et de la contrainte unique `(session_id, participant_id)`. Ces mécanismes et les modèles relationnels existants sont conservés.

Limitations volontaires : import quotidien de fichiers propres, séances collectives, correspondances simples, liste de feuilles techniques exclues, limite de 5 000 lignes et arrêt après huit lignes vides. L'outil avancé traite l'ensemble du classeur et ne reprend pas ces arrêts silencieux.

Limitations accidentelles ou dangereuses pour l'historique :

- La recherche du premier participant par nom/prénom, éventuellement par 1er janvier d'une année inventé, peut fusionner des homonymes et dépend de l'ordre de lecture. Le service d'import d'annuaire possède aussi des règles plus permissives qui ne conviennent pas à cette migration.
- Une année seule devient une date fictive. Une année absente ou une date précise déjà en base peuvent provoquer des résultats incohérents.
- Les dates partielles et les changements de mois implicites sont mal interprétés. Le rapprochement activité + date fusionne les séances du même jour.
- Le nom d'onglet devient le nom de l'activité, même tronqué.
- Le repli ville = Creil peut créer un quartier portant le nom d'une autre commune. Le genre existant peut être écrasé.
- Les totaux Excel ne sont pas rapprochés ; les compteurs ne décrivent pas toujours les créations réelles. Aucune provenance jusqu'à la cellule ni décision de rapprochement réutilisable n'est enregistrée.

La migration avancée est séparée de l'import standard pour réutiliser les modèles, la normalisation de l'annuaire, les permissions et le référentiel secteurs sans appliquer ses règles de fusion aux données historiques.

## Architecture et fichiers

| Fichier | Rôle |
|---|---|
| `app/ateliers/historical_parser.py` | Lecture intégrale du vrai XLSX, classification, ACTIVITE, dates/slots, cellules de présence et contrôles des totaux. Aucun SQL. |
| `app/ateliers/historical_matching.py` | Résolution globale, normalisation, graphes de candidats, contradictions et décisions explicites. Aucun SQL. |
| `app/ateliers/historical_import.py` | Préchargement de la base, plan, affectation par activité, rapprochement des séances, blocages, transaction atomique et contrôle du lot. |
| `app/ateliers/historical_report.py` | Rapport Markdown détaillé à conserver en accès privé. |
| `app/ateliers/historical_privacy.py` | Effacement des copies d'identité historiques dans les procédures d'anonymisation et de suppression existantes. |
| `app/admin/historical_routes.py` | Dépôt privé temporaire, droits, aperçu, décisions, application et export. |
| `app/templates/admin_import_historical.html` | Choix des secteurs après analyse, rapprochements, dates, contrôles et validation finale. |
| `app/admin/routes.py`, `admin_import_excel.html` | Entrée vers le mode avancé ; référentiel des secteurs et dry-run par défaut pour le standard. |
| `app/__init__.py` | Les requêtes du mode avancé ne déclenchent pas les tâches automatiques de purge, notification, veille ou rattrapage agenda. |
| `app/models.py` et migration `ab72cd34ef56` | Année de naissance seule, créneau source, lots et provenance. |
| `app/services/participant_privacy.py`, `purge_rgpd.py`, `participant_suppression.py`, `app/activite/participants_secteur.py`, `app/participants/routes.py` | Maintien des voies d'effacement et exposition de l'année connue à la recherche. |
| `app/services/senacs.py` | Année seule utilisée pour l'âge au 31 décembre, sans inventer de jour de naissance. |
| `tools/historical_import.py` | Analyse/application/vérification en ligne de commande sur une base SQLite explicitement désignée. |
| `app/ateliers/historical_triage.py`, `tools/historical_triage.py` | Regroupement du rapport en dossiers, propositions de décisions et reprise des arbitrages au tableur. Aucune base, aucun classeur, aucune écriture. Exposé aussi par le bouton « Proposer un triage » de l'aperçu. |
| `tests/test_historical_*.py` | Tests du parseur, matching, transactions, interface et migration ; intégration du vrai fichier par variable d'environnement. |

## Règles de lecture

`TOTAL JANV A dec 2026` est une synthèse. `ADULTES` et `ADULTES SAVEURS` sont techniques. Les autres feuilles sont classées selon leurs en-têtes et leur contenu ; les structures inconnues restent signalées. Une feuille d'activité peut être exploitable tout en portant des anomalies.

Une valeur ACTIVITE unique après normalisation fournit le nom métier. Le nom de feuille, les valeurs originales et les références des cellules restent dans le plan. Les noms normalisés identiques retrouvent les activités existantes du secteur choisi. Une troncature ou une ressemblance proche fournit des candidats à valider. Aucun rapprochement flou ne crée ou ne fusionne automatiquement une activité.

Choisir un secteur ne confirme pas la création distincte de deux activités proches du même classeur. Les deux propositions restent à valider, quel que soit l'ordre des feuilles. Il est possible de confirmer leur distinction, de leur donner explicitement le même nom métier, ou d'ignorer une feuille. Des secteurs différents restent des affectations distinctes.

Les dates combinent mois/année explicites, jour, jour de semaine et ordre des colonnes. Une baisse du numéro de jour sans nouvel en-tête explicite avance le mois. Les impossibilités du calendrier, contradictions du jour de semaine et valeurs illisibles restent REVIEW ; les mois voisins compatibles sont seulement des suggestions. Une erreur comme 31 avril n'entraîne pas la translation de toutes les colonnes suivantes.

Une colonne = une séance source. Les créneaux M, AM et ME restent textuels dans `creneau_source`. Les heures restent NULL. Même deux colonnes du même jour et du même créneau restent distinctes. Pour une autre version du classeur, une séance existante du même jour est proposée à validation, pas fusionnée automatiquement. Deux colonnes d'un lot ne peuvent pas cibler la même séance existante.

Une séance existante doit être datée, de type COLLECTIF dans le modèle et appartenir à l'activité et au secteur retenus. Un conteneur de rendez-vous mensuel ne peut pas recevoir directement les présences d'une colonne datée.

Présence : booléen vrai, nombre **1**, ou texte normalisé `1`, `x`, `p`, `présent`, `present`, `oui`, `o`, `true`. Absence : vide, booléen faux, nombre **0**, ou `0`, `absent`, `a`, `non`, `n`, `false`. Les autres nombres et textes sont des anomalies, pas des présences devinées. Les présences redondantes restent plusieurs preuves source d'une seule relation unique.

Les totaux par ligne, par colonne et le grand total sont comparés aux valeurs enregistrées dans le fichier Excel. Le rapport distingue cohérent, écart, impossible à interpréter et total indisponible. Il ne prétend pas recalculer les formules Excel. Une valeur inconnue est conservée avec sa cellule dans les anomalies et doit être contrôlée ; la correction se fait dans une copie du XLSX si elle doit devenir une présence reconnue.

## Résolution des personnes

La normalisation de matching retire les différences de casse, accents, espaces, apostrophes et tirets, traite téléphones/emails et utilise naissance, genre et territoire. Les valeurs originales restent dans la provenance.

| Classe | Réutilisation automatique |
|---|---|
| EXACT | Noms normalisés identiques + date de naissance complète + contact concordant, sans candidat concurrent ni contradiction. Une liaison source déjà validée est également une preuve exacte au réimport. |
| HIGH_CONFIDENCE | Noms identiques ou une faute mineure sur un seul des deux + année commune + contact commun ; ou noms identiques + téléphone et email communs. Aucune contradiction ni concurrence. |
| REVIEW | Nom/prénom seuls, même avec année/quartier/genre ; homonymie, contradictions, candidats concurrents, chaîne de rapprochements insuffisamment concordants, identité incomplète ou invalide. Aucune écriture sans décision. |
| NEW | Aucun candidat crédible, ou représentant d'un nouveau groupe dont les autres lignes sont fortement concordantes. |

Le graphe de rapprochement est calculé sur tout le classeur et les participants existants préchargés. Une chaîne A–B–C ne permet pas de fusionner A et C lorsque les preuves ne concordent pas directement. L'ordre des feuilles ne décide pas de l'identité. Les catégories comptent des lignes source, pas des personnes physiques uniques déjà établies.

Les décisions possibles sont : même participant existant, même personne qu'une autre ligne source résolue, nouvelle personne distincte, regroupement explicite de plusieurs lignes, ou ignorer. Une identité incomplète peut être rattachée à une fiche existante, corrigée explicitement pour une nouvelle personne, ou ignorée. Une contradiction dans un groupe validé n'est pas tranchée arbitrairement : le champ conflictuel reste NULL et les sources restent disponibles. Les fiches existantes ne sont jamais enrichies ou écrasées automatiquement par cette migration.

Les quartiers connus de Creil sont reconnus par une liste limitée. Les communes identifiées, dont Nogent-sur-Oise, Montataire, Pont-Sainte-Maxence, Clermont et les autres libellés présents dans le vrai classeur, deviennent des villes, pas des quartiers de Creil. Un territoire inconnu reste sans affectation déduite et apparaît dans les anomalies. Les valeurs manquantes restent manquantes.

## Schéma et transactions

Migration additive `ab72cd34ef56`, après `de23fa45bc67` :

- `participant.annee_naissance` nullable : conserve une année seule, sans fabriquer le 1er janvier. La date complète, lorsqu'elle existe, reste prioritaire. Une année seule donne un âge certain au 31/12 pour les bilans annuels ; à une autre date, l'âge précis reste indisponible.
- `session_activite.creneau_source` nullable : libellé du fichier, sans horaire inventé.
- `historical_import_batch` : empreinte unique du fichier, nom, secteurs concernés, date, auteur, décisions, résumé et empreinte de l'aperçu.
- `historical_import_source` : lot, type d'objet, feuille, ligne, colonne/cellule, valeurs brutes, décision et références aux objets cibles. Les lignes/cellules explicitement ignorées des objets reconnus ont aussi une preuve sans cible.

Le plan constitue un staging structuré JSON. Le dry-run ne fait aucun INSERT/UPDATE/DELETE et termine toujours par rollback, dans une session indépendante de celle de l'appelant. L'application relit le classeur et la base sous transaction, recalcule l'empreinte, refuse les aperçus périmés et les blocages, puis effectue un seul commit. Toute exception annule le lot entier. SQLite utilise une transaction d'écriture exclusive entre importeurs ; PostgreSQL une transaction sérialisable et un verrou transactionnel d'import. Les contraintes d'unicité restent la dernière barrière.

Réimport identique : les décisions du lot validé sont réutilisées et les références vérifiées. Aucune création supplémentaire. Les décisions d'un lot appliqué sont immuables. Une version différente du XLSX a une autre empreinte : les identités et séances sont de nouveau contrôlées, sans réutilisation aveugle de numéros de lignes déplacés. Les cas supprimés ne sont pas recréés automatiquement.

Le downgrade retire les colonnes/tables ajoutées et préserve les participants, activités, séances et présences. **Exporter les preuves avant downgrade** : le retour de schéma efface les métadonnées d'import et les années seules. Aucune fonction d'annulation destructrice d'un lot n'est fournie. La restauration d'une sauvegarde reste une opération d'administration séparée.

## Interface

Ouvrir Administration → Importer depuis Excel → Migration historique, ou `/admin/import-historical`.

1. Déposer le classeur et lancer l'analyse sans secteur global.
2. Examiner la cartographie, les totaux et les anomalies.
3. Affecter chaque activité à son secteur, ou sélectionner l'activité existante correspondante.
4. Résoudre les participants et les séances ambigus. Pour regrouper deux nouvelles lignes, leur donner le même identifiant de regroupement explicite, ou désigner la première comme source de la seconde.
5. Enregistrer les décisions et refaire le dry-run. Répéter tant que des points bloquants subsistent.
6. Télécharger le rapport et les décisions, puis confirmer le lot lorsque l'aperçu est prêt.

L'accès utilise `ateliers:sync` et les secteurs autorisés. L'espace temporaire est réservé au propriétaire de l'aperçu, protégé des chemins arbitraires, des accès croisés et des doubles soumissions. Les formulaires sont protégés par CSRF. Les fichiers sont conservés dans `instance/historical_imports/<identifiant>` pendant sept jours par défaut, nettoyés lors d'un prochain dépôt. Prévoir des droits Windows/Linux restreints sur `instance`, qui contient des données personnelles. Un verrou laissé par l'arrêt brutal d'un processus nécessite le contrôle de l'administrateur avant suppression manuelle.

## Commandes exactes sur copie ou staging SQLite

Depuis la racine du dépôt, avec les dépendances installées. La CLI impose un chemin de base explicite et ne démarre ni le serveur ni les migrations automatiques de l'ERP. Elle n'utilise aucune URL de base héritée de l'environnement.

```powershell
python -m tools.historical_import --database instance/historical_validation/staging.db init-test-db
python -m tools.historical_import --database instance/historical_validation/staging.db analyze --file "C:\Users\infor\Desktop\STATS_2026_par_activite.xlsx" --output instance/historical_validation/rapport.json --markdown instance/historical_validation/rapport.md
```

`init-test-db` refuse tout fichier existant. Pour analyser une copie de la vraie base, fournir son chemin à la place et mettre son schéma à niveau avant l'analyse. Le dry-run sur une base vide ne peut évidemment pas établir les correspondances avec une base de production non consultée.

Exemple de décisions, à adapter aux clés exactes du rapport (jamais appliquer cet exemple en bloc au vrai classeur) :

```json
{
  "activities": {
    "Onglet informatique": {"secteur": "Numérique"},
    "Onglet cuisine": {"secteur": "Familles"},
    "Autre onglet": {"action": "existing", "atelier_id": 12}
  },
  "participants": {
    "Onglet informatique!7": {"action": "new", "group": "personne-verifiee-1"},
    "Onglet cuisine!9": {"action": "source", "source_id": "Onglet informatique!7"},
    "Autre onglet!8": {"action": "participant", "participant_id": 25}
  },
  "sessions": {
    "Onglet cuisine!I": {"action": "date", "date_session": "2026-02-03"}
  },
  "acknowledged_anomalies": []
}
```

Une décision d'activité peut utiliser `action: new` pour confirmer une activité distincte malgré une ressemblance. Une décision de séance `action: new` confirme une séance distincte d'une séance existante et peut inclure une date corrigée. `action: ignore` est disponible pour personne, activité ou séance. Les identifiants d'anomalies à acquitter sont ceux du dernier rapport.

```powershell
python -m tools.historical_import --database instance/historical_validation/staging.db analyze --file "C:\Users\infor\Desktop\STATS_2026_par_activite.xlsx" --decisions instance/historical_validation/decisions.json --output instance/historical_validation/rapport-valide.json --markdown instance/historical_validation/rapport-valide.md
python -m tools.historical_import --database instance/historical_validation/staging.db apply --file "C:\Users\infor\Desktop\STATS_2026_par_activite.xlsx" --decisions instance/historical_validation/decisions.json --preview instance/historical_validation/rapport-valide.json --output instance/historical_validation/resultat.json
python -m tools.historical_import --database instance/historical_validation/staging.db verify --batch ID_DU_LOT_RETOURNE --output instance/historical_validation/verification.json
```

L'application refuse une commande `apply` sans aperçu, avec blocages ou avec une empreinte différente de la base/fichier/décisions actuels. Après import, contrôler aussi les statistiques par secteur et les preuves `historical_import_source` du lot. Refaire l'analyse du même fichier puis réappliquer doit retourner le même lot sans création.

Mise à niveau d'une copie via l'outil existant de l'ERP, avant de démarrer le serveur :

```powershell
$env:SQLALCHEMY_DATABASE_URI="sqlite:///C:/CHEMIN/ABSOLU/copie-erp.db"
$env:DB_AUTO_UPGRADE_ON_START="0"
python -m flask --app wsgi:app db upgrade
```

Sur PostgreSQL, utiliser l'interface de staging après sauvegarde et mise à niveau du schéma. Les tests automatisés fournis exécutent SQLite ; aucun serveur PostgreSQL ni aucune production n'ont été utilisés pendant ce chantier.

## Triage assisté

Le rapport compte des lignes source, pas des personnes : sur le vrai classeur,
1 014 lignes REVIEW ne représentent que 221 dossiers réellement ambigus. Le
triage regroupe le rapport par identité, propose les décisions que le classeur
rend évidentes et exporte le reste dans un tableur. Il n'assouplit aucune règle
de rapprochement et n'écrit rien : sa sortie est un fichier de décisions à
relire, puis à rejouer par `analyze --decisions`. Le bouton « Proposer un
triage » de l'aperçu fait la même chose sans quitter le navigateur. Voir
[historical-triage.md](historical-triage.md).

## Tests et limites à valider

```powershell
$env:HISTORICAL_XLSX_PATH="C:\Users\infor\Desktop\STATS_2026_par_activite.xlsx"
python -m pytest tests/test_historical_parser.py tests/test_historical_matching.py tests/test_historical_import.py tests/test_historical_import_routes.py tests/test_historical_migration.py
python -m pytest
```

La variable active les tests d'intégration du fichier privé ; il n'est pas versionné. Les cas standards, mois explicites/implicites, séances multiples le même jour, présence unique/redondante, personne dans plusieurs activités, lignes complémentaires, faute de nom, homonymes, naissance manquante/contradictoire, territoire extérieur/Creil, synthèse, nom tronqué, réimport, dry-run sans DML et rollback sur erreur sont couverts. S'ajoutent affectation par activité après analyse, aperçu périmé, droits/CSRF/propriétaire et migration aller-retour.

Avant un import réel, il reste à valider les secteurs de chaque activité, les identités REVIEW, les dates ambiguës et les données territoriales inconnues. Les décisions prises sur une base de test vide doivent être rejouées et réexaminées sur une copie récente de la vraie base. Les exports ou écrans qui exigent une date de naissance complète la laisseront vide si seule l'année est connue ; les bilans au 31/12 peuvent exploiter l'année. Les durées exactes ne peuvent pas être établies à partir de M/AM/ME : les indicateurs horaires historiques restent à interpréter selon les conventions existantes de l'ERP.
