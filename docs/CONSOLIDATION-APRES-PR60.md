# Consolidation après la PR #60 : report partiel, restauration ancienne, registre endommagé

Base de travail : `main` au commit de fusion de la PR #60 (`6a97206`). `main` n'avait pas bougé depuis ;
les trois défauts y étaient présents (reproduits par des tests avant correction). Branche
`claude/audit-mon-centre-social-gdy74x`, **non fusionnée**, rien déployé sur servisa, uniquement des
bases et fichiers de test jetables.

---

## 1. Ce qui change pour vous

| Où | Ce que vous verrez | Action |
| --- | --- | --- |
| **Caisse → Rapprochement des bulletins** | « Somme distincte » propose au plus la part **non reportée** (ex. 10 € sur 30 € dont 20 reportés). La somme historique « à qualifier » du bulletin est annulée dans le même geste (contre-passation motivée). | Rien de plus : le total du bulletin reste juste une fois tout qualifié. |
| Même page, encart **« À corriger ou vérifier »** | Rapprochements déjà faits avec la version précédente où la somme historique ferait compter l'argent deux fois. | Bouton « Annuler la somme historique en doublon » (avec note), ou « Vérifié » si la somme historique a déjà été qualifiée et que vous avez contrôlé la caisse. |
| **Caisse → Sommes à qualifier** | Une somme historique d'un bulletin pas encore rapproché ne peut plus être qualifiée : message « Rapprochez d'abord ce bulletin ». | Passer par le rapprochement. |
| **Restauration d'une sauvegarde** | L'application passe en **maintenance** pendant l'opération, met la base au niveau de cette version, puis la remet en service. En cas d'échec : **retour automatique** à l'état d'avant, message « rien n'a changé ». | Rien, sauf message contraire (voir §6). |
| **Contrôle → Registres** | Si le registre des numéros est endommagé ou a disparu : **« Émission suspendue »**, aucun reçu, facture ni avoir tant que ce n'est pas rétabli. | Relever le dernier numéro de chaque série sur les documents papier, le saisir, « Rétablir ». |
| **Contrôle → Registres** | « Registre des effacements reconstitué » si ce registre était endommagé ou a disparu. | Vérifier les demandes d'effacement récentes, puis « Vérifié ». |

---

## 2. Défaut 1 — report partiel : 30 € pouvaient devenir 60 € en caisse

**Reproduit** (`tests/test_rapprochement_report_partiel.py::test_reproduction_du_defaut_60_euros`, avant
correction : `assert 60.0 == 30.0`).

**Correction** (`app/services/rapprochement_reglements.py`) :

- « Somme distincte » est bornée à *somme notée − report prouvé* ; si rien ne manque, elle est refusée
  (« Déjà reporté ») ;
- dans la **même transaction** : la somme historique encore à qualifier est **contre-passée** (motif :
  « remplacée par 20,00 € déjà reportés + 10,00 € constatés… ») puis la part manquante est créée
  « à qualifier », hors caisse. Rien n'est supprimé ;
- somme historique **déjà qualifiée** alors qu'un report existe : aucune décision automatique, refus
  motivé ; « Vérifié » (note obligatoire) après contrôle humain ;
- qualification d'une somme historique d'un bulletin non rapproché : refusée ;
- double clic, deux personnes : verrou + mises à jour conditionnelles (une seule décision, une seule
  annulation).

**Bases ayant déjà reçu la PR #60** : migration `e5f7a9b1c235` (colonne `controle`) qui **signale sans
rien écrire en caisse** : `doublon_a_annuler` (correction en un clic) ou `a_verifier` (somme historique
déjà qualifiée). Les lignes classées « suivi » par la version 1 dans ce dernier cas repassent « à
rapprocher ». Classement : `VERSION = 2`.

**Tests** (totaux vérifiés jusqu'au bout de la qualification, argent « dans la caisse ») : scénario exact
30 € dont 20 reportés et 10 manquants ; report intégral ; aucun report ; plusieurs modes ; somme
historique déjà qualifiée ; déjà annulée ; double soumission et décisions simultanées ; installation
ayant déjà décidé avec la PR #60 ; montée de version depuis le schéma exact de la PR #60 (et retour).

**Évolution volontaire** : `test_a_report_partiel` attend « au plus 10.00 » au lieu de « au plus 30.00 ».

---

## 3. Défaut 2 — restauration d'une sauvegarde plus ancienne que l'application

**Reproduit sur SQLite** (`tests/test_restauration_schema.py::test_sauvegarde_pr59_restauree_par_le_parcours_web`,
avant correction : base restée à `b8d0f2a4c593`, restauration annoncée réussie).

**PostgreSQL, vérifié séparément — différent** : le `--clean` d'un ancien dump ne touche pas les tables
plus récentes (`effacement_rgpd`, `rapprochement_bulletin`) ; leurs clés étrangères vers
`inscription_annuelle` font échouer **toute** la restauration (transaction annulée) : une sauvegarde plus
ancienne était impossible à restaurer.

**Correction** (`app/services/sauvegarde.py`, `app/__init__.py`, `tools/restore_instance.py`) :

1. contrôles du lot, recopie des effacements en attente, sauvegarde de sécurité (inchangé) ;
2. **marque « restauration en cours »** sur disque (dossier des sauvegardes, jamais restauré) :
   maintenance (réponse 503) pour tout, sauf la connexion et Administration → Sauvegardes ; la marque
   survit à un arrêt brutal ;
3. PostgreSQL : dans la même transaction `psql`, suppression des tables absentes du dump (seulement si le
   dump contient les tables métier), puis le dump ;
4. **migrations jusqu'à la dernière version** + initialisations du démarrage (droits, secteurs), révision
   vérifiée — **avant** la fusion des registres et la réapplication des effacements ;
5. échec du remplacement ou des migrations : **retour automatique** à la sauvegarde de sécurité, message
   « rien n'a changé » ; si ce retour échoue aussi : maintenance persistante et marche à suivre ;
6. outil en ligne de commande : même maintenance, même retour automatique, même levée finale.

**Tests** (vrai parcours : route d'administration et outil en ligne de commande), SQLite et PostgreSQL :
sauvegarde PR #59 restaurée (effacement et numéros émis après la sauvegarde : réappliqué / jamais
réutilisés ; caisse, rapprochement, registres, sommes à qualifier accessibles) ; sauvegarde au schéma
actuel ; migrations en échec (état précédent intact) ; retour impossible (maintenance, seule
l'administration des sauvegardes répond, récupération par la sauvegarde de sécurité) ; maintenance
après redémarrage ; outil en ligne de commande.

---

## 4. Défaut 3 — registre corrompu : un numéro déjà émis pouvait être réutilisé

**Reproduit** (42 émis, copie précédente 41, compteur SQL 40, registre principal abîmé → le 42 était
réémis).

**Correction** (`app/services/financial_sequence.py`) :

- registre **endommagé**, ou **disparu** alors que l'installation le tenait : **émission suspendue**
  (marque `runtime/numeros-emis.bloque.json`, persistante au redémarrage, vue par tous les processus sous
  le verrou existant) ; fichiers endommagés conservés (`*.illisible-*`) ;
- la copie précédente et les compteurs de la base ne donnent que des **bornes basses** (« au moins ») ;
- **rétablissement** (Contrôle → Registres, droits administrateur) : dernier numéro de chaque série
  connue, relevé sur les documents, jamais sous une borne connue ; journalisé
  (`registres.numeros_retablis`) ;
- **première installation** (aucune trace d'un registre tenu : ni témoin `numeros-emis.tenu`, ni copie
  précédente, ni ligne `tache_planifiee` « registre_numeros_tenu » en base) : pas de blocage ;
- pendant un blocage, une copie des registres fusionnée (restauration, import) relève seulement les
  bornes ; la copie jointe aux sauvegardes est marquée `numeros_incomplets`.

**Registre des effacements** : sa reconstruction (copie précédente + base) n'est plus silencieuse. Registre
endommagé ou **disparu** alors que des effacements y avaient été recopiés : reconstruction **et incident
persistant** affiché dans Contrôle → Registres jusqu'à vérification (« Vérifié », journalisé). Les
restaurations restent possibles (la base est la source de ce registre).

**Tests** : scénario exact 40/41/42 ; registre endommagé (message dans l'application) ; registre disparu ;
dossier runtime disparu mais base au courant ; première installation ; blocage vu par un autre processus
(redémarrage) ; rétablissement puis émission au-delà ; refus sous une borne ; toutes les séries exigées ;
4 processus concurrents pendant le blocage (un seul fichier mis de côté) ; fusion pendant le blocage ;
effacements : incident « endommagé » et « disparu ».

**Évolutions volontaires** (tests adaptés, expliqués dans le commit) : l'ancien test
`test_c_fichier_endommage_mis_de_cote_et_reconstitue` devient
`test_c_fichier_endommage_mis_de_cote_et_emission_suspendue` ; restauration sur une machine neuve et
import sur une nouvelle installation : l'émission est suspendue jusqu'au rétablissement (la base sait
qu'un registre était tenu, des numéros ont pu être émis après la copie). Les tests qui simulent une
installation vierge effacent la trace « registre tenu » laissée dans la base partagée par d'autres tests.

---

## 5. Migrations

| Révision | Effet sur une installation existante |
| --- | --- |
| `e5f7a9b1c235` | Ajoute `rapprochement_bulletin.controle` / `controle_note`, signale les rapprochements à corriger ou vérifier. Aucune écriture en caisse. Se défait (les signalements sont perdus, pas les écritures). |

Aucune migration pour les défauts 2 et 3 (fichiers de marque sur disque, ligne `tache_planifiee`).

---

## 6. Installations déjà mises à jour : que faire

1. Après la mise à jour : **Caisse → Rapprochement des bulletins**, encart « À corriger ou vérifier » :
   traiter chaque ligne.
2. **Contrôle → Registres** : s'il affiche « Émission suspendue » (registre abîmé ou disparu), rétablir
   les derniers numéros d'après les documents papier ; s'il affiche « Registre des effacements
   reconstitué », vérifier puis valider.
3. Si l'installation a été **déplacée ou réinstallée** (nouveau dossier des données) puis la base
   restaurée : l'émission est suspendue jusqu'au rétablissement — c'est voulu.

## 7. Récupération en cas d'échec d'une restauration

- **Message « rien n'a changé »** : l'état d'avant est remis ; la sauvegarde demandée n'a pas pu être mise
  au niveau de cette version. Conserver le journal (`logs`), essayer une autre sauvegarde ou faire
  analyser celle-ci.
- **Page « Maintenance en cours »** pour tous : la restauration n'a pas pu se terminer et le retour
  automatique non plus. Un administrateur se connecte, ouvre **Administration → Sauvegardes** (page
  accessible pendant la maintenance) et restaure la **sauvegarde de sécurité** indiquée (état d'avant la
  restauration). La maintenance est levée à la fin d'une restauration réussie.
- Arrêt brutal du serveur pendant une restauration : même situation (la maintenance persiste au
  redémarrage), même procédure.

## 8. Vérifications et limites

| Vérifié | Où |
| --- | --- |
| Tests ciblés et suite complète SQLite | local (résultats dans la PR) |
| Tests ciblés et suite complète PostgreSQL 16 | local |
| PostgreSQL 16 et 18, SQLite | CI « Tests » |
| Construction, installation, reprise PostgreSQL 18.1 (IPv6 `::1`, mot de passe avec « ! »), mise à jour, désinstallation | CI « Installateur Windows » (Windows Server) |

**Non vérifié** : servisa, la base réelle, le réseau du centre (consigne). La restauration Windows d'un
lot au schéma de la PR #59 n'est pas rejouée par la recette Windows (elle l'est sur PostgreSQL en
local et en CI Linux).

**Limites restantes** :

- une restauration faite **à la main** (`psql`, copie de fichier) ne pose pas la marque de maintenance ni
  ne lance les migrations avant le démarrage suivant — au démarrage, les migrations s'exécutent comme
  toujours ;
- pendant la maintenance, les requêtes déjà en cours au moment de la pose de la marque se terminent ;
- le rétablissement des numéros repose sur les documents papier : l'application ne peut pas savoir mieux ;
- un effacement présent **uniquement** dans un registre des effacements perdu ne peut pas être
  reconstitué ; l'incident le signale.
