# Consolidation après la PR #59 : reprise des règlements et registres hors base

Base de travail : `main` au commit de fusion de la PR #59 (`dff7ff2`). `main` n'avait pas bougé
depuis : les trois défauts signalés y étaient présents (reproduction ci-dessous).

**Trois états à ne pas confondre :** ce qui suit est sur la branche
`claude/audit-mon-centre-social-gdy74x` (PR non fusionnée). Rien n'est déployé sur servisa.
Aucune donnée réelle n'a été utilisée : uniquement des bases synthétiques et jetables.

---

## 1. Ce qui change pour vous

| Où | Ce que vous verrez | Action |
| --- | --- | --- |
| **Caisse** | Nouvel encart « règlement(s) d'anciens bulletins à rapprocher » et page **Caisse → Rapprochement des bulletins**. | Pour chaque bulletin listé : « Déjà reporté » ou « Somme distincte » (voir §4). Rien n'est ajouté en caisse sans vous. |
| **Caisse → Rapprochement → Comparer avec une sauvegarde** | Relit une sauvegarde **sans la restaurer**. | Seulement si la version de la PR #59 a déjà tourné chez vous : choisir une sauvegarde **d'avant** cette mise à jour (voir §4.3). |
| **Contrôle → Registres** (nouveau) | État des registres des numéros émis et des effacements RGPD ; anciennes entrées d'effacement « à vérifier ». | Trancher les éventuelles entrées « à vérifier » (§5.4). Exporter les registres avant un changement de serveur (§6). |
| **Sauvegardes** | Chaque lot contient un fichier de plus : `<lot>_registres.json`. Contrôle supplémentaire « Copie des registres ». | Aucune. Il est copié hors serveur avec le lot. |
| **Restauration** | Peut être **refusée** si un effacement RGPD récent n'a pas encore pu être recopié dans le registre de l'installation. | Vérifier les droits du dossier des données (message affiché), puis réessayer. |
| **Reçus, factures, avoirs** | Si le registre des numéros ne peut pas être écrit (droits, disque), le document **n'est pas émis** et un message l'explique. | Rien en temps normal. |

---

## 2. Défauts, corrections, preuves, limites

### Reproduction sur `main` `dff7ff2` (avant correction)

Script jetable, base SQLite synthétique :

```
A : encaissements suivis : [(10.0, 'cheque', False)] | colonne du bulletin : 30.0
B : après rollback, nom = Repro | registre : {"anonymises": [{"cree_le": "…", "id": 2}], "supprimes": []}
B : après réapplication (restauration), nom = ANONYME
C : registre final : {"recu:2026": 42}17}
```

Pour C, l'entrelacement contrôlé (barrière) montre plus grave que la perte signalée : le nom de
fichier temporaire commun produit un **fichier corrompu**, que l'ancienne lecture traitait
ensuite comme un registre **vide** (toutes les séries perdues à l'écriture suivante).

### Tableau

| Défaut | Correction | Tests (fichier::test) | Limites |
| --- | --- | --- | --- |
| **A** — un règlement de bulletin ignoré dès qu'un versement quelconque existe | Migration corrective `d2e4f6a8b013` : table `rapprochement_bulletin` (valeurs d'origine figées, classement `reporte` / `suivi` / `a_rapprocher`, preuve). Aucun encaissement créé ni modifié. Page de rapprochement : « Déjà reporté » (rien d'ajouté ; somme « à qualifier » en double emploi contre-passée) ou « Somme distincte » (encaissement « à qualifier », hors caisse, clé unique). Comparaison avec une sauvegarde. | `tests/test_reprise_reglements.py` : 30 € + 10 € distinct (parcours complet jusqu'à la caisse), report intégral, partiel, modes mélangés, versement d'un autre membre du foyer, plusieurs bulletins, bulletin sans fiche, déjà dans le registre, anomalie déjà traitée + double emploi, réparation rejouée ×3, décisions simultanées, qualification simultanée, mise à jour depuis l'état exact de la PR #59 (downgrade → données → upgrade), valeur d'origine relue dans un lot, reprise d'une installation ancienne (révision `de23fa45bc67`). *CI Windows* : `migration_smoke.py` (source PostgreSQL 18.1, IPv6, mot de passe avec « ! »). | Une égalité exacte entre la somme du bulletin et le total versé (sans libellé « Inscription annuelle ») est classée « reporté » : c'est l'état normal de l'ancienne version (colonne recopiée) ; une coïncidence reste possible, d'où la liste « Reports déduits », révisable. Sur une base déjà passée par la PR #59, la valeur d'origine a pu être réécrite : seule une sauvegarde antérieure la contient (§4.3). |
| **B** — effacement annulé noté quand même, réappliqué après restauration | Ligne `effacement_rgpd` écrite **dans la transaction** de l'effacement (migration `c1d3e5f7a902`), recopiée hors base **après validation** (`runtime/registre-effacements.json`) ; recopie rejouée après chaque validation, au démarrage, à la maintenance, avant toute restauration (refus si impossible). Identification : n° de fiche + date de création à la microseconde. Ancien registre lu, jamais modifié, jamais réappliqué à l'aveugle. | `tests/test_registres_consolidation.py` : `test_b_*` (anonymisation validée / annulée, suppression validée / annulée, point de sauvegarde validé puis transaction annulée, fiche en échec dans une purge par lots, panne d'écriture visible puis rattrapée, interruption entre fichier et marquage, identifiant réutilisé, fiche sans date de création, ancien registre classé + décisions + double envoi, décision « écarter » jamais rejouée, écritures concurrentes, registre endommagé), restauration réelle d'un lot (SQLite, PostgreSQL via `psql`), restauration refusée tant qu'une recopie est en attente. | Restauration faite **hors de l'application** (outil manuel) pendant qu'une recopie est en attente : la base restaurée écrase la seule trace. L'outil `tools/restore_instance.py` recopie d'abord ; une restauration faite à la main avec `psql` non. Fenêtre en pratique réduite à une panne d'écriture non corrigée. |
| **C** — lecture-modification-écriture concurrente du registre des numéros | `registre_externe` : verrou exclusif inter-processus (`fcntl` / `msvcrt`) + inter-fils autour de toute la séquence, délai borné (10 s), temporaire propre à chaque écrivain, `fsync`, remplacement atomique (retenté 3 s si un antivirus tient le fichier), copie précédente, lecture stricte (absent = vide ; illisible ≠ vide). Numéros : écriture **avant** la validation SQL, échec = document non émis. Même mécanisme appliqué au registre des effacements. | `test_c_*` : 2 écrivains entrelacés (barrière), 8 fils même série, 4 processus indépendants (plusieurs séries), maximum ancien > base restaurée, attribution SQL annulée (numéro consommé), première installation, ancien format, fichier endommagé (mis de côté + reconstitué), fichier inaccessible (émission refusée, pas de reconstitution), émission refusée par l'application (message), arrêt brutal pendant l'écriture (processus tué) + résidu nettoyé, verrou borné, verrou entre processus. *CI Windows* : `desktop/recette_registres.py` avec le Python livré (processus concurrents, verrou borné, arrêt brutal, fichier tenu ouvert). | Registre endommagé : reconstitué depuis la copie précédente et les compteurs en base ; la dernière écriture peut manquer si elle a eu lieu juste après une restauration. L'incident est affiché dans Contrôle → Registres. |

### Problèmes directement liés, traités ici

| Problème | Conséquence | Correction |
| --- | --- | --- |
| Depuis la PR #59, ouvrir la liste des inscriptions recalcule la colonne du bulletin (miroir du total réglé) | La valeur d'origine d'un bulletin ignoré (défaut A) est **écrasée** sur les bases déjà passées par la PR #59 | Valeurs figées dans `rapprochement_bulletin` ; comparaison avec une sauvegarde antérieure (lecture seule, dumps PostgreSQL et SQLite). |
| SQLite : un point de sauvegarde en tête de transaction était **validé** par son `RELEASE` (pilote `sqlite3`) | Purge par lots, annulation après validation d'un point de sauvegarde : écritures définitives à tort (installations SQLite, tests) | Transaction ouverte juste avant le point de sauvegarde (`app/__init__.py`). Sans effet sur PostgreSQL. |
| Deux qualifications simultanées d'une somme « à qualifier » | Parts créées deux fois (doublon de caisse) sur PostgreSQL | Mise à jour conditionnelle en tête de `qualifier` : la seconde est refusée. |
| Une somme « à qualifier » contre-passée restait listée et comptée comme « à qualifier » | Montant d'alerte faux, somme impossible à traiter | Exclue de la liste et des totaux. |
| Restauration en ligne de commande (`tools/restore_instance.py`) | Ne recopiait pas les effacements en attente, ne reprenait pas la copie des registres | Recopie préalable (refus sinon), fusion de la copie jointe, réapplication au démarrage. |

---

## 3. Migrations ajoutées

| Révision | Contenu | Effet sur une installation existante |
| --- | --- | --- |
| `c1d3e5f7a902` | Table `effacement_rgpd`. | Création seule. L'ancien fichier `runtime/effacements.json` est classé au démarrage suivant (§5.4). |
| `d2e4f6a8b013` | Table `rapprochement_bulletin` + classement de tous les bulletins portant de l'argent. | Lecture seule des données existantes ; **aucun encaissement ni versement créé, modifié ou supprimé** (vérifié par `test_a_mise_a_jour_depuis_l_etat_exact_de_la_pr59`, avant/après ligne à ligne). Rejouable. |

**Deux parcours, un seul code.** La migration publiée `b5d8e3a1f264` n'est **pas** modifiée : une
base reprise d'une installation artisanale et une base déjà passée par la PR #59 arrivent à
`d2e4f6a8b013` dans le même état, et c'est la même fonction
(`app/services/reprise_reglements.py`, version notée sur chaque ligne) qui classe les bulletins.
La comparaison avec une sauvegarde appelle cette même fonction. Un changement de règle devra
passer par une nouvelle migration.

---

## 4. Procédure de rapprochement (Caisse → Rapprochement des bulletins)

1. Pour chaque bulletin « à rapprocher », la page montre : la somme notée, le dernier mode saisi,
   la date, la raison (« Des versements existent (10 €) mais aucun ne provient du bulletin »,
   « Report partiel », « Plusieurs bulletins », « double emploi probable ») et la liste des
   règlements enregistrés en face.
2. Vérifiez avec vos pièces (reçu, bordereau, cahier de caisse).
3. Choisissez, **avec une note** (conservée au journal) :
   - **Déjà reporté** : rien n'est ajouté. Si la même somme figurait aussi dans « À qualifier »,
     cette ligne en double est annulée par contre-passation motivée ;
   - **Somme distincte** : saisissez le montant manquant (proposé : l'écart). Il rejoint
     **Caisse → Sommes à qualifier**, hors du théorique de caisse ; là, indiquez le ou les modes
     (ex. 20 € espèces + 10 € chèque) et s'il entre en caisse ou a déjà été compté.
4. Un double clic, un renvoi ou deux personnes en même temps : une seule décision est retenue.
5. **Base déjà passée par la PR #59** : dépliez « Comparer avec une sauvegarde antérieure »,
   choisissez un lot fait **avant** cette mise à jour. Il est lu (jamais restauré) ; les bulletins
   dont la somme d'origine diffère sont ajoutés avec le même classement. Rejouable sans doublon.

---

## 5. Registres hors base

### 5.1 Fichiers (dossier `C:\ProgramData\MonCentreSocial\runtime`)

| Fichier | Contenu | Écrit quand |
| --- | --- | --- |
| `numeros-emis.json` | `{"don:2026": 42, "facture:2026": 17, "avoir:2026": 3}` — même format qu'avant. | À chaque numéro, **avant** la validation SQL. |
| `registre-effacements.json` | Effacements validés (clé, nature, n° de fiche, date de création de la fiche, état). | Après la validation SQL (et reprises). |
| `effacements.json` | Ancien registre : lu, jamais modifié. | Plus jamais par cette version. |
| `*.lock`, `*.prec`, `*.illisible-*` | Verrous, copies précédentes, registres endommagés mis de côté. | Automatiquement. |

Droits : dossier `runtime` existant, en modification pour le compte du service
`NT SERVICE\MonCentreSocial`, sans accès des utilisateurs. Aucun nouveau dossier, aucune
nouvelle dépendance. *CI Windows* : le service crée lui-même les verrous et joint la copie au lot
(`REGISTRES_COPIES_DANS_LE_LOT_PAR_LE_SERVICE_OK`).

### 5.2 Garanties face à une interruption

| Moment de l'arrêt | Numéros | Effacements |
| --- | --- | --- |
| Pendant l'écriture du fichier | Ancienne version intacte ; temporaire supprimé au passage suivant. | Idem. |
| Après écriture, avant validation SQL | Numéro consommé (trou), jamais redonné. | Rien : la ligne n'existe qu'après validation. |
| Après validation SQL, avant recopie | — | Ligne « en attente » en base ; recopie au démarrage suivant. Restauration refusée tant qu'elle n'est pas faite. |
| Après recopie, avant marquage | — | Recopiée de nouveau (même clé, sans effet). |

### 5.3 Ce qui n'est jamais fait

- Considérer un registre illisible comme vide ; supprimer un registre (il est mis de côté).
- Rejouer une entrée de l'ancien registre dont la fiche est encore identifiée.
- Faire diminuer un numéro, ou revenir d'une décision (« confirmé », « écarté ») à « à vérifier ».

### 5.4 Anciennes entrées « à vérifier » (Contrôle → Registres)

L'ancien registre notait un effacement avant sa validation. Au premier démarrage, ses entrées
sont classées : fiche absente ou déjà anonymisée → confirmées ; fiche **présente avec son
identité** → « à vérifier ». Pour chacune : vérifiez la demande d'effacement et le journal, puis
« L'effacement était voulu : l'appliquer » ou « Il avait été annulé : écarter ».

---

## 6. Sauvegarde, restauration, transfert, sinistre

Chaque lot contient `<lot>_registres.json` (numéros + effacements, empreinte propre ; non ajouté
au `.sha256` pour qu'une version précédente sache encore restaurer le lot). À la restauration,
cette copie est **fusionnée**, jamais écrasante.

| Situation | Numéros | Effacements | Testé par |
| --- | --- | --- | --- |
| Restauration d'une sauvegarde ancienne, même installation | Registre de l'installation conservé (plus récent) ; la numérotation repart au-delà. | Effacements postérieurs au lot réappliqués. | `test_restaurer_un_lot_ancien_ne_fait_rien_reculer`, `test_restauration_d_un_lot_ancien_reapplique_les_effacements` |
| Réinstallation (mise à jour, désinstallation puis installation) | Dossier des données conservé : registres intacts. | Idem. | *CI Windows* (mise à jour, désinstallation : données conservées) |
| « Repartir de zéro » (`--reset`) | L'ancienne installation est mise de côté ; restaurer la dernière sauvegarde **puis** importer l'export des registres de l'ancienne installation (Contrôle → Registres). | Idem. | `test_transfert_export_import_et_dernier_numero_declare` |
| Reprise d'une installation artisanale | Pas de registre à l'origine ; les compteurs de la base reprise font foi. | Pas d'ancien registre. | *CI Windows* (`migration_smoke.py`) |
| Nouvelle machine | Restaurer le dernier lot (copie hors serveur) : registres **du lot** repris ; puis importer l'export fait sur l'ancienne machine si elle est encore accessible. | Idem. | `test_restaurer_sur_une_machine_neuve_rapporte_les_registres` |
| Perte complète du serveur | Registres du dernier lot survivant. Ce qui a été émis après **n'existe plus nulle part** : déclarer dans Contrôle → Registres le dernier numéro de chaque série relevé sur les reçus, factures et avoirs papier (jamais à la baisse). | Les effacements postérieurs au dernier lot survivant sont perdus : rejouer les demandes d'effacement reçues depuis. | `test_transfert_export_import_et_dernier_numero_declare`, `test_copie_des_registres_alteree_n_empeche_pas_la_restauration` |

---

## 7. Retour arrière

- **Avant d'installer** : sauvegarde (Administration → Sauvegardes) + copie hors serveur, et
  export des registres (Contrôle → Registres).
- **Revenir à la version de la PR #59** : réinstaller cette version et **restaurer la sauvegarde
  faite juste avant la mise à jour**. C'est la voie sûre. Les deux migrations savent se défaire
  (testé sur SQLite et PostgreSQL), mais le `downgrade` supprime `rapprochement_bulletin` et
  `effacement_rgpd` : les **décisions de rapprochement** prises depuis sont perdues (les
  encaissements qu'elles ont créés restent).
- **Registres** : le fichier des numéros garde son format, lu tel quel par la version précédente.
  Le registre des effacements de cette version (`registre-effacements.json`) est ignoré par la
  version précédente, qui continue d'utiliser l'ancien `effacements.json` (jamais modifié ici) ;
  en revenant ensuite à cette version, les entrées ajoutées entre-temps à l'ancien fichier sont
  classées comme au premier démarrage.
- Une sauvegarde faite par cette version se restaure avec la version précédente (le fichier
  `_registres.json` y est simplement ignoré).

---

## 8. Points encore ouverts

| Point | Pourquoi |
| --- | --- |
| Test avec une copie de la base réelle | Non fait : consigne (aucun accès à la production). Voir verdict ci-dessous. |
| Restauration manuelle hors application (`psql` à la main) pendant une recopie d'effacement en attente | La base écrase alors la seule trace ; l'application ne peut pas l'empêcher. Recopie immédiate après validation + refus dans les outils fournis réduisent la fenêtre à une panne d'écriture non traitée (visible dans Contrôle). |
| Égalité fortuite entre la somme d'un bulletin et le total versé | Classée « reporté » (état normal de l'ancienne version) ; révisable dans « Reports déduits ». |
| Installations sans `APP_DATA_DIR` (manuelles) | Registres hors base non tenus ; la base reste la seule protection (inchangé). |
| Valeurs d'origine écrasées sans sauvegarde antérieure | Impossibles à reconstituer ; aucun moyen de les inventer. |
| Sinistre sans copie récente | Numéros et effacements postérieurs à la dernière copie : déclaration manuelle / demandes à rejouer (§6). |

**Verdict — tester l'installateur avec une copie de la base réelle :** oui, c'est possible et
recommandé, **sur une machine de test isolée** (jamais servisa) : installer, choisir « Reprendre
une installation existante » en pointant une **copie** restaurée de `erp_pedagogie` (PostgreSQL
18.1, connexion `::1`, mot de passe avec « ! » : scénario recetté en CI), puis ouvrir Caisse →
Rapprochement des bulletins et Contrôle → Registres. Précautions : copie du dossier de l'ancienne
application et de la base, machine sans accès au réseau des usagers, destination de sauvegarde
hors serveur de test. La reprise met la **source** en lecture seule : ne jamais la pointer sur la
base de production.
