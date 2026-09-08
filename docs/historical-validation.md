# Validation du classeur réel STATS 2026

Analyse réalisée le 7 septembre 2026, à partir du fichier réel `STATS_2026_par_activite.xlsx`, sans modification du classeur.

SHA-256 : `64486d62e5d0b967e135ac102b2f77fdbd0d17dea06d84537a6b2ab15f39c0eb`.

Base de validation : SQLite locale isolée, initialement vide de participants, activités, séances et présences. Aucune base de production, aucun serveur PostgreSQL et aucune copie de données de production n'ont été utilisés. Les nombres de correspondances avec une base existante valent donc zéro dans ce rapport ; ils ne préjugent pas du résultat sur une copie récente de la vraie base.

## Résultats du dry-run

| Mesure | Résultat |
|---|---:|
| Feuilles analysées intégralement | 69 |
| Feuilles d'activité reconnues | 66 |
| Feuilles exclues | 3 |
| Nouvelles activités proposées sur la base vide | 66 |
| Activités à affecter à un secteur après analyse | 66 |
| Séances source détectées | 880 |
| Séances dont la date est exploitable | 834 |
| Séances dont la date doit être validée | 46 |
| Lignes source de participants | 1 464 |
| EXACT / HIGH_CONFIDENCE sans décisions | 0 / 0 |
| Lignes REVIEW | 970 |
| Nouveaux participants proposés sans candidat crédible | 494 |
| Cellules de présence conservées | 7 031 |
| Présences avec identité et date résolues | 2 501 |
| Présences en attente de résolution d'identité ou de date | 4 530 |
| Présences existantes dans la base de validation | 0 |
| Présences explicitement ignorées | 0 |
| Regroupements automatiques d'identités / présences redondantes établies | 0 / 0 |
| Totaux de feuilles cohérents | 66 sur 66 |
| Anomalies territoriales | 1 |
| Lignes de total ignorées comme personnes | 66 |
| Erreurs de lecture | 0 |

Les trois exclusions sont `TOTAL JANV A dec 2026`, `ADULTES` et `ADULTES SAVEURS`. Chaque activité possède une valeur ACTIVITE unique et cohérente, utilisée comme nom métier. La cartographie nominative complète des feuilles, les noms métier et les comparaisons de totaux figurent dans le rapport local détaillé.

Toutes les cellules de présence du vrai classeur utilisent le nombre **1**. Toutes les 880 colonnes de séances portent au moins une présence. Créneaux : **410 M**, **401 AM**, **68 ME**, **1 sans créneau**. Aucun horaire n'est déduit de ces libellés.

Le total des présences lues est identique à la somme des totaux Excel enregistrés : **7 031**. Les contrôles par ligne et colonne concordent aussi. Cela ne constitue pas un recalcul des formules Excel.

## Anomalies qui nécessitent une décision

Les 46 anomalies de date se répartissent entre **13 jours absents/illisibles/ambigus**, **8 dates impossibles dans le mois indiqué** et **25 contradictions du jour de semaine**. Les valeurs d'origine et les dates alternatives possibles sont conservées dans le rapport. Elles ne sont pas corrigées arbitrairement.

**11 lignes d'identité incomplète portent 40 présences**, toutes conservées dans la représentation temporaire. Une fiche existante peut être choisie ou le nom manquant renseigné explicitement. Aucune identité fictive n'est créée.

Les **970 REVIEW désignent des lignes, pas 970 personnes différentes**. Le fichier ne fournit pas les contacts nécessaires aux règles automatiques strictes. Les motifs incluent le manque de preuves d'identité, les homonymes possibles, les divergences de naissance, de genre ou de territoire et les chaînes de ressemblances insuffisantes. Parmi ces lignes : 20 portent un conflit d'année de naissance, 7 une année invalide et 11 une identité incomplète. Ces motifs peuvent se recouvrir et ne doivent pas être additionnés.

La seule valeur territoriale non reconnue est **MOULIN**. Elle n'est pas arbitrairement transformée en quartier de Creil. Les communes extérieures reconnues deviennent des villes. Les informations manquantes restent manquantes.

Aucun doublon d'identité ne peut être déclaré certain à partir des seuls noms et années. Le zéro de regroupements automatiques ne signifie donc pas « aucun doublon dans Excel ». Les regroupements validés feront ensuite apparaître les cellules de présence redondantes, qui partageront une seule présence cible.

Les secteurs n'ont pas été attribués arbitrairement : les **66 affectations restent à choisir activité par activité dans la prévisualisation**. L'import réel reste bloqué tant que ces choix et les ambiguïtés ne sont pas validés.

## Vérifications réalisées

- **130 tests historiques** couvrent parseur, matching, transaction, interface et migration. Le fichier privé active trois intégrations : lecture complète, dry-run sur base isolée et dépôt/prévisualisation/formulaire réel dans l'interface. Il n'est pas remplacé par les petites fixtures de tests aux limites.
- Une exécution élargie a validé **164 tests**, incluant l'historique, SENACS, les indicateurs, l'anonymisation et les suppressions. Le test supplémentaire de non-déclenchement des tâches automatiques pendant la prévisualisation a passé séparément.
- Après la dernière correction des correspondances d'activités et de séances, les **31 tests du service et 17 tests d'interface** ont été réexécutés avec succès, intégrations du fichier réel activées.
- L'import d'essai avec décisions sur des fixtures isolées, puis le réimport identique, ne double ni participants, ni activités, ni séances, ni présences. Les présences complémentaires et leur provenance sont vérifiées.
- Les deux séances d'un même jour restent distinctes, y compris si leur créneau source est identique. Deux colonnes ne peuvent pas être affectées à la même séance cible.
- La sélection d'un secteur ne valide pas les noms d'activité proches entre feuilles. Le contrôle est testé dans les deux ordres de lecture, avec confirmation d'un nom commun, création distincte, autre secteur ou exclusion. Une séance cible sans date ou de type mensuel est refusée, de même qu'un identifiant booléen transmis dans les décisions.
- Le dry-run est contrôlé pour absence d'INSERT/UPDATE/DELETE. Une erreur injectée après création de lignes annule toute la transaction. Un changement de base ou d'affectation invalide l'aperçu précédent.
- La migration Alembic a été exécutée en aller-retour sur SQLite et préserve les données métier préexistantes.
- Suite générale : **836 réussites, 4 tests ignorés, 1 échec préexistant** dans `tests/test_sauvegarde_hors_serveur.py::test_destinations_separees_par_points_virgules_et_lignes`. Cet échec de représentation de chemins Windows/UNC a été reproduit sur le commit initial `d67da0b` dans un checkout isolé. Les fichiers concernés n'ont pas été modifiés par ce chantier.

Les journaux détaillés et rapports privés sont conservés localement sous `instance/historical_validation/`, hors Git. Le vrai classeur n'a pas été importé définitivement : ce serait prématuré sans validation des identités, dates et secteurs. Les tests d'idempotence et de rollback portent sur des bases jetables, pas sur la production.

Sources : fichier réel identifié par l'empreinte ci-dessus ; `tests/test_historical_parser.py`, `test_historical_matching.py`, `test_historical_import.py`, `test_historical_import_routes.py`, `test_historical_migration.py` ; rapport d'analyse complet généré par `tools/historical_import.py` ; dépôt initial `d67da0b` pour la comparaison de non-régression. Procédure et règles dans [historical-import.md](historical-import.md) ; regroupement des blocages en dossiers et propositions de décisions dans [historical-triage.md](historical-triage.md).
