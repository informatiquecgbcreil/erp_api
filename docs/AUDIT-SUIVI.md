# Suivi de l'audit (main `ce39a1c`) — consolidation

État au 28/09/2026. La PR #59 a été fusionnée dans main (`dff7ff2`).

**Suite : trois défauts relevés après la fusion** (règlements de bulletins ignorés à la reprise,
effacement annulé réappliqué après restauration, écritures concurrentes du registre des numéros)
et leur correction, avec preuves et limites : **`docs/CONSOLIDATION-APRES-PR59.md`** (branche
`claude/audit-mon-centre-social-gdy74x`, non fusionnée).

**Trois états à ne pas confondre :**

| Où | Ce que ça veut dire |
| --- | --- |
| **main** | Fusionné : PR #56 (chantier 1), #57 (chantier 2), #58 (chantier 3), #59 (consolidation). |
| **Suite (après PR #59)** | Branche `claude/audit-mon-centre-social-gdy74x`, **non fusionnée**. |
| **servisa** | **Aucune intervention.** Rien de ce document n'est déployé sur le serveur de production. |

L'installateur Windows de cette validation est l'artefact CI `Mon-Centre-Social-Windows-x64`
(workflow « Windows installer » de la PR #59). Il est **construit et recetté** sur une machine
Windows Server éphémère (installation, service, HTTPS, reprise PostgreSQL 18.1, mise à jour,
désinstallation). Il n'est pas installé chez vous.

Légende preuves : `fichier::test` = test automatisé qui rejoue le constat (SQLite et PostgreSQL
sauf mention) ; *CI Windows* = `desktop/SystemSmoke.cs` exécuté par le workflow Windows ;
*Caddy réel* = vérifié avec le binaire Caddy 2.11.4.

---

## Critiques

| Réf | Statut | Où | Preuve | Limites |
| --- | --- | --- | --- | --- |
| C1 — nan/inf empoisonnent la caisse | Corrigé | main #56 (`a5a0c27`), complété PR #59 (`220ccc1`) | `test_montants.py` (toutes les saisies), garde `before_flush` NaN, écran « Anomalies de montants » + contraintes CHECK PostgreSQL posées quand les données sont saines (`test_encaissements.py`) | Lignes déjà empoisonnées dans une base existante : détectées et corrigées depuis l'écran (motif obligatoire, journal), pas en silence. |
| C2 — espèces sur bulletin comptées deux fois / perdues | Corrigé | PR #59 (`220ccc1`) | `test_encaissements.py` (encaissement unique, ventilation, reprise des anciens bulletins, concurrence PostgreSQL) | Encaissements anciens non ventilables : écran « À qualifier » (mode « à préciser »), à traiter par l'équipe. |
| C3 — kiosque : n'importe qui émarge n'importe qui ; présence = droits | Corrigé | main #57 (`cc84086`), complété PR #59 (`6fbbda4`) | `test_kiosque_droits.py` (27 tests : proposition par navigateur, vraie signature, lecture seule, validation, origine indéterminée, anciennes routes, inscription, statistiques) | Règle métier conservée : l'annuaire reste commun au kiosque (recherche de tout le monde, nom et prénom seuls). |
| C4 — copie périmée ouverte après redémarrage | Corrigé | PR #59 (`4363c90`, `e24e935`) | `test_reprise_bascule.py` (source en lecture seule, empreintes, copie périmée refusée, reprise après échec, bout en bout PostgreSQL) ; *CI Windows* (reprise PG 18.1, `SOURCE_EN_LECTURE_SEULE_APRES_BASCULE_OK`) | Retour arrière après ouverture aux utilisateurs : manuel (procédure plus bas). |

## Majeurs

| Réf | Statut | Où | Preuve | Limites |
| --- | --- | --- | --- | --- |
| 1.1 `/participants/new` sans droit | Corrigé | main #57 | `test_kiosque_droits.py::test_creation_de_fiche_exige_le_droit` | — |
| 1.2 ancienne anonymisation superficielle | Corrigé | main #57 | `test_kiosque_droits.py::test_ancienne_anonymisation_est_complete_et_journalisee` | — |
| 2.1 facture modifiable après émission, pas d'avoir | Corrigé | PR #59 (`034d27a`) | `test_encaissements.py` (facture figée, réimpression identique, avoir numéroté à l'annulation) | Factures émises avant la mise à jour : figées à leur prochaine impression, avec la mention « reconstituée ». |
| 2.2 pas de contre-passation ni d'ajustement | Corrigé | PR #59 (`220ccc1`) | `test_encaissements.py`, `test_caisse.py` (contre-passation motivée et journalisée, ajustement de caisse) | — |
| 2.3 dépôt de caisse non atomique | Corrigé | main #56 | `test_montants.py` (verrou, double envoi) | — |
| 2.4 locations absentes de la caisse | Corrigé | PR #59 (`220ccc1`) | `test_salles_documents.py`, `test_encaissements.py` | — |
| 2.5 répartition perd l'argent des fiches supprimées | Corrigé | PR #59 (`220ccc1`) | `test_encaissements.py` (ligne « fiches supprimées ») | — |
| 3.1 copies de signatures et bilans survivent | Corrigé | PR #59 (`aa3bea2`) | `test_rgpd_consolidation.py::test_copies_de_signatures_et_bilans_effaces`, `::test_bilan_pedagogique_ne_reste_pas_sur_le_disque` | La feuille d'émargement archivée (pièce justificative) garde les signatures qu'elle contient. |
| 3.2 journal d'audit garde les identités | Corrigé | PR #59 (`aa3bea2`) | `test_rgpd_consolidation.py::test_journal_relie_par_identifiant_et_efface_sans_approximation`, `::test_migration_rattache_seulement_les_cibles_exactes` | Lignes anciennes qui ne citent qu'un nom (sans identifiant) : pas de réécriture approximative (choix demandé), elles partent avec la durée de conservation du journal (3 ans par défaut, réglable). |
| 3.3 suppression laisse des traces | Corrigé | PR #59 (`aa3bea2`) | `test_rgpd_consolidation.py::test_suppression_definitive_sans_trace_identifiante` | — |
| 3.4 purge anonymise des personnes actives | Corrigé | PR #59 (`aa3bea2`) | `test_rgpd_consolidation.py` (inscription à venir, atelier en cours, Hart, encaissement, bulletin d'un proche, portail, positionnement) | Purge automatique **désactivée par défaut** (PR #59 `3a44e2b`). |
| 3.5 purge dans la requête, tout ou rien | Corrigé | PR #59 (`aa3bea2`) | `::test_purge_par_lots_bornee_et_reprise`, `::test_une_fiche_en_erreur_ne_bloque_pas_les_autres`, `::test_la_purge_ne_tourne_plus_dans_la_requete` | Fil d'exécution dans le processus web (pas de planificateur externe) ; `flask maintenance` pour un planificateur. |
| 3.6 file d'effacement bloquée | Corrigé | PR #59 (`aa3bea2`) | `::test_file_d_effacement_ne_se_bloque_plus` | Fichiers hors dossiers de l'application : jamais effacés automatiquement (contrôle manuel, signalé à l'écran). |
| 3.7 export article 15 incomplet | Corrigé | PR #59 (`aa3bea2`) | `::test_export_article_15_complet_et_archive` (feuilles + ZIP avec fichiers) | — |
| 3.8 pas de conservation hors participants | Corrigé | PR #59 (`aa3bea2`) | `::test_durees_de_conservation`, `::test_duree_zero_conserve_sans_limite`, `::test_locataire_anonymisable_seulement_sans_reservation_en_cours` | Contacts des partenaires : pas de durée automatique (contacts professionnels, suppression à la main). Factures émises : copies figées conservées (obligation comptable). |
| 3.9 fusion et purge non journalisées | Corrigé | PR #59 (`6fbbda4`, `aa3bea2`) | `test_kiosque_droits.py::test_fusion_conserve_la_seule_signature` (journal), purge : `::test_une_fiche_en_erreur…` | — |
| 4.1 dérive de schéma user/role | Corrigé | PR #59 (`4363c90`) | `test_reprise_bascule.py` (réconciliation avant bootstrap, message juste) | — |
| 4.2 copie de fichiers externes en administrateur | Corrigé | PR #59 (`4363c90`) | `test_reprise_bascule.py`, `test_consolidation_migration.py` (dossiers autorisés, UNC refusés, rapport des refus) | — |
| 4.3 mot de passe spécial dans le champ connexion | Corrigé | PR #59 (`4363c90`) | `test_reprise_bascule.py::test_champs_separes_mot_de_passe_special` (champs séparés dans l'assistant) | — |
| 5.1 exports Excel à formules actives | Corrigé | main #57 | `test_kiosque_droits.py::test_exports_excel_sans_formule_injectee` | — |
| 5.2 pointé à l'avance ne peut pas signer | Corrigé | main #57 | `test_kiosque_droits.py::test_signature_kiosque_completee_sur_un_pointage_existant` | — |
| 5.3 SENACS compte absences, annulées, prévues | Corrigé partout | main #58 (SENACS, bilans, stats, coûts) + PR #59 (`e585fc5` : tableau de bord, direction, comparaison, remplissage, quartier, export CSV) | `test_senacs_chiffres.py`, `test_indicateurs_coherence.py` | La page « qualité des données » reste exhaustive (voulu). |
| 5.4 fusion perd la seule signature | Corrigé | PR #59 (`6fbbda4`) | `test_kiosque_droits.py::test_fusion_conserve_la_seule_signature` | — |
| 5.5 signature vide acceptée | Corrigé | main #57 | `test_kiosque_droits.py::test_kiosque_exige_une_vraie_signature` | — |
| 5.6 recherche sensible aux accents, pas nom + prénom | Corrigé | PR #59 (`6fbbda4`) | `test_recherche_doublons.py` (kiosque et équipe, apostrophes, œ, désordre) | — |
| 5.7 ville sans quartier = hors QPV | Corrigé partout | main #58 (SENACS) + PR #59 (`e585fc5` : taux QPV des projets, orientations) | `test_senacs_chiffres.py`, `test_indicateurs_coherence.py::test_taux_qpv_sans_les_quartiers_inconnus` | Libellés QPV propres à Creil (Rouher, Hauts de Creil) dans SENACS/magatomatique : antérieurs, hors périmètre (voir points ouverts). |
| 5.8 `/bilans/senacs` lent | Corrigé | main #58 | `test_senacs_chiffres.py` (nombre de requêtes borné) | — |
| 5.9 doublons manqués | Corrigé | PR #59 (`6fbbda4`) | `test_recherche_doublons.py::test_doublons_cas_courants` (8 cas), `::test_doublons_ne_rapprochent_pas…` | — |
| 6.1 réglages kiosque de l'ancien .env | Corrigé | PR #59 (`4363c90`, `2909007`) | `test_consolidation_migration.py` ; message Funnel et `reglages_importes` dans `Reprise.json` | La redirection du tunnel Funnel reste à faire par vous (commande fournie). |
| 6.2 ports testés sur 127.0.0.1 | Corrigé | PR #59 (`2909007`) | *CI Windows* `PORTS_OCCUPES_DETECTES_OK` | — |
| 6.3 IP du VPN retenue | Corrigé | PR #59 (`2909007`) | Code C# (carte avec passerelle, exclusions, champ de correction) ; hôtes supplémentaires : `test_sauvegarde_consolidation.py` | **Non testable en CI** (une seule carte réseau) : à vérifier sur servisa à l'installation (l'assistant affiche l'adresse retenue). |
| 6.4 nouvelle tentative : tout refait, copies accumulées | Corrigé | PR #59 (`4363c90`) | `test_reprise_bascule.py` (nettoyage des tentatives de CETTE installation) ; `--reset` | — |
| 6.5 sauvegarde coupée à 5 min, jamais hors serveur | Corrigé | PR #59 (`2909007`) | `test_sauvegarde_consolidation.py` ; *CI Windows* `SAUVEGARDE_VERIFIEE_ET_RESTAURATION_COMPLETE_OK` | Copie hors serveur : à régler par vous (dossier + test d'écriture dans l'administration). |
| 6.6 migration longue : boucle, succès annoncé | Corrigé | PR #59 (`2909007`) | `test_sauvegarde_consolidation.py::test_temoin_de_migration_longue` ; code 8 de l'installateur | Durée réelle d'une grosse migration sur servisa : non mesurée. |
| 6.7 autorité racine sans restriction | Corrigé (installations neuves) | PR #59 (`2909007`) | `test_sauvegarde_consolidation.py` (NameConstraints) ; *Caddy réel* (site Internet rejeté) ; *CI Windows* (autorité créée) | Installation existante : garde son autorité (la remplacer couperait les postes). |

## Mineurs

| Axe | Constat | Statut | Où / preuve |
| --- | --- | --- | --- |
| Sécurité | Compteur PIN partagé Funnel, plafond global jamais vérifié | Corrigé | PR #59 `6fbbda4` : adresse réelle derrière Caddy (*Caddy réel*), plafond pour Internet seulement — `test_recherche_doublons.py::test_plafond_global…` |
| Sécurité | Session 31 j, pas d'inactivité, cookie valide après déconnexion | Corrigé | PR #59 `e585fc5` — `test_sessions_securite.py` |
| Sécurité | Liens GET qui modifient | Corrigé | PR #59 `6fbbda4` (POST) — `test_emargement_avant_impression.py::test_generer_refuse_en_get` |
| Sécurité | PBKDF2 : temps de réponse, pas de plafond par adresse ni sur la réinitialisation | Corrigé | PR #59 `e585fc5` — `test_sessions_securite.py` |
| Sécurité | `admin:users` sans `admin:rbac` crée un compte direction | Corrigé | PR #59 `e585fc5` — `test_securite_mineurs.py` |
| Sécurité | Veille : réseau interne, jeton après redirection | Corrigé | PR #59 `e585fc5` — `test_securite_mineurs.py` (limite : rebinding DNS) |
| Sécurité | Défis et titres de questionnaires visibles entre secteurs | Corrigé | PR #59 `e585fc5` — `test_securite_mineurs.py` |
| Sécurité | Accès par adresse Tailscale/VPN refusé (400) | Corrigé | PR #59 `2909007` (hôtes supplémentaires) — `test_sauvegarde_consolidation.py` |
| Caisse | Montant en lettres | Corrigé | PR #59 `034d27a` — `test_dons_couts.py` |
| Caisse | Annulation de don en espèces déposé | Corrigé | PR #59 `034d27a` (motif, correction datée) — `test_encaissements.py` |
| Caisse | Montant dû sous le déjà payé | Corrigé | PR #59 `034d27a` — `test_encaissements.py` |
| Caisse | Numéros réattribués après restauration | Corrigé | PR #59 `034d27a` (registre hors sauvegardes) — `test_encaissements.py` |
| Caisse | Anciennes routes de suppression | Corrigé | PR #59 `220ccc1`/`6fbbda4` — `test_kiosque_droits.py::test_ancienne_suppression_rapide_ne_supprime_plus` |
| Caisse | Montants en virgule flottante | **Ouvert (dette documentée)** | Aucune erreur observable (audit : 200 000 paiements simulés) ; passage en décimal = réécriture hors périmètre. |
| RGPD | Paiement.commentaire et bilans individuels gardent le nom | Corrigé | PR #59 `220ccc1`/`aa3bea2` — `test_rgpd_consolidation.py::test_bilan_de_rendez_vous_individuel_efface` |
| RGPD | Portail recrée le lien vers une fiche anonymisée | Corrigé | PR #59 `aa3bea2` — `::test_portail_ne_recree_pas…` |
| RGPD | Restauration ressuscite les anonymisés | Corrigé | PR #59 `aa3bea2` — `::test_restauration_reanonymise…` (limite : registre tenu seulement avec APP_DATA_DIR, donc Windows) |
| RGPD | `journal_connexion` garde le texte tapé | Corrigé | PR #59 `aa3bea2` — `::test_journal_de_connexion…` |
| Reprise | Chemins réécrits un par un | Corrigé | PR #59 `e585fc5` — 20 000 chemins : 18,5 s → 0,27 s, `test_sauvegarde_consolidation.py::test_reecriture_des_chemins_par_lots` |
| Reprise | Colonnes sans FK, type non détecté, obligatoires à défaut calculé | Corrigé | PR #59 `e585fc5` — `test_sauvegarde_consolidation.py` (FK PostgreSQL, écarts de type signalés) |
| Reprise | Messages source/destination, WIN1252 | Corrigé | PR #59 `4363c90` — `test_reprise_bascule.py::test_diagnostic_distingue_source_et_destination` |
| Base | Locale C : tri | Corrigé (bases neuves) | PR #59 `2909007` (ICU fr-FR si disponible) ; bases existantes inchangées |
| Base | 127 écarts modèle/schéma, retour arrière cassé | Partiellement corrigé | PR #59 `e585fc5` : 3 FK + 12 index créés ; 115 écarts restants mesurés = même index sous un autre nom, ON DELETE différent, 12 nullabilités, 1 type, 1 colonne historique (documentés, non modifiés : risque de refuser des données) ; retour arrière des 7 migrations de la consolidation vérifié (`::test_retour_arriere…`). Retour arrière des migrations antérieures : non vérifié. |
| Base | Secrets en clair dans complete.json, *_TOKEN non masqués | Corrigé | PR #59 `2909007` (dossier private, masquage) |
| Métier | « C'est moi », libellés Ville | Corrigé | main #57 + PR #59 `6fbbda4` |
| Métier | Ajout depuis l'émargement sans contrôle de doublon | Corrigé | PR #59 `6fbbda4` — `test_recherche_doublons.py::test_emargement_propose…` |
| Métier | Kiosque : apostrophes/ligatures, 429 anglais, cache jamais vidé | Corrigé | PR #59 `6fbbda4` — `test_recherche_doublons.py` |
| Métier | CSV : négatifs en texte | Corrigé | PR #59 `e585fc5` — `test_securite_mineurs.py::test_csv_montants_negatifs…` |
| Métier | Questionnaire kiosque rempli en boucle | Corrigé | PR #59 `6fbbda4` — `test_recherche_doublons.py::test_questionnaire_*` |
| Windows | HTTP/3 annoncé | Corrigé | PR #59 `2909007` — *Caddy réel*, *CI Windows* (pas d'Alt-Svc) |
| Windows | Journaux jamais purgés | Corrigé | PR #59 `2909007` (rotation 5 Mo) |
| Windows | « Fermer » arrête le service pour tous | Corrigé | PR #59 `2909007` (menu de l'icône) |
| Windows | Pare-feu : VPN/Tailscale perdus | Corrigé | PR #59 `2909007` (plages déclarées) — non testable en CI |
| Windows | Mise à jour : anciens fichiers restent | Corrigé | PR #59 `2909007` ([InstallDelete]) — *CI Windows* (mise à jour) |
| Windows | Arrêt de PostgreSQL non surveillé | Corrigé | PR #59 `2909007` — `test_sauvegarde_consolidation.py::test_arret_de_postgresql_detecte` |
| Linux | `run_waitress.py` ignore X-Forwarded-For | Corrigé | PR #59 `e585fc5` (`ERP_TRUSTED_PROXY`, documenté) |

## Revue des droits route par route (demande de la consolidation)

`test_droits_routes.py` : chaque rôle par défaut visite toutes les pages sans paramètre (aucune
erreur 500, aucune page d'administration technique ouverte à un rôle métier), et aucune route
n'est publique hors liste connue. Revue manuelle des 29 routes de fichiers et d'exports :
toutes vérifient droit et périmètre ; corrigés au passage : bilan pédagogique sans contrôle de
périmètre, modification rapide depuis les statistiques (et son erreur 500), ancienne route de
modification du module activité, inscription annuelle sur présence kiosque non validée.

## Ce qui n'a pas pu être vérifié ici

- Tout ce qui demande servisa : ordre réel des cartes réseau et choix d'adresse (6.3), profils
  de pare-feu des cartes VPN, durées réelles de sauvegarde et de migration sur vos volumes,
  collation `French_France.1252` de votre source, routage réel du Funnel.
- La machine CI Windows n'a qu'une carte réseau et pas de tunnel.
- Aucune donnée de production n'a été utilisée : uniquement des données synthétiques.

---

## Procédure : mise à jour, reprise, retour arrière

### Avant (quel que soit le cas)

1. Sauvegarde complète de l'ancienne installation (base : `pg_dump`, dossiers `instance` et
   `uploads`), copiée **hors du serveur**.
2. Couper le tunnel Funnel jusqu'à la bascule (il vise l'ancien port).
3. Noter le nom du service Windows de l'ancienne application.

### Reprise d'une ancienne installation (bascule)

1. Lancer l'installateur, choisir « Reprendre une installation existante » : dossier de
   l'ancienne application, puis **hôte, port, base, utilisateur et mot de passe dans des champs
   séparés**, et le nom de l'ancien service.
2. Étape réseau : vérifier **l'adresse IP proposée** (celle de la carte reliée à la box, pas celle
   du VPN) et ajouter les autres adresses du serveur (Tailscale, VPN).
3. Pendant la copie, l'ancienne base passe **en lecture seule** et l'ancien service est
   **désactivé** : aucune saisie ne peut se perdre. Si la source a changé entre la copie et
   l'ouverture, la copie est refaite automatiquement.
4. Après : vérifier la connexion d'un compte existant, quelques fiches et documents ; lire
   `Direction-DSI\Reprise.json` (réglages repris, documents refusés ou introuvables, types de
   colonnes à vérifier).
5. Administration > Sauvegardes : indiquer un dossier hors serveur et **tester l'écriture** ;
   puis `MonCentreSocial.exe --restore-test` (en administrateur) doit dire « se restaure
   correctement ».
6. Rediriger le tunnel : `tailscale funnel --bg http://127.0.0.1:<port kiosque>` (le port est dans
   le dossier confidentiel et dans le message de fin).
7. Caisse : traiter « Rapprochement des bulletins » (règlements d'anciens bulletins ni prouvés
   reportés ni exclus — voir `docs/CONSOLIDATION-APRES-PR59.md` §4), puis l'écran « À qualifier »
   (anciens encaissements sans mode connu) et, s'il y a lieu, « Anomalies de montants ».
8. RGPD : la purge automatique reste **désactivée** ; la direction relit Contrôle → Purge RGPD
   (liste et durées de conservation) avant de l'activer.

### Mise à jour d'une installation déjà sur l'installateur Windows

1. Sauvegarde + copie externe (Administration > Sauvegardes).
2. Lancer le nouvel installateur : il arrête le service, **remplace tout le programme**, applique
   les migrations (sans limite de temps tant qu'elles avancent) et redémarre. En mode silencieux,
   code de sortie **8** = le centre n'a pas redémarré (voir `logs\mise-a-jour-erreur.txt`).
3. Après : `MonCentreSocial.exe --restore-test`.

### Retour arrière

- **Échec pendant la reprise, avant ouverture** : automatique. L'ancienne base redevient
  modifiable, l'ancien service retrouve son mode de démarrage et redémarre s'il tournait ; la
  copie est gardée pour une nouvelle tentative d'activation.
- **Après ouverture aux utilisateurs** (manuel) :
  1. arrêter Mon Centre Social (icône « Arrêter le service pour tout le monde… ») ;
  2. sur le serveur de l'ancienne base, en superutilisateur :
     `ALTER DATABASE <base> RESET default_transaction_read_only;` ;
  3. `sc config "<ancien service>" start= auto` puis démarrer l'ancien service ;
  4. **les saisies faites dans la nouvelle application depuis la bascule ne sont pas dans
     l'ancienne** : les exporter avant l'étape 1 et les ressaisir.
- **Après une mise à jour** : réinstaller la version précédente et restaurer la sauvegarde faite
  juste avant (Administration > Sauvegardes). Ne pas faire tourner un ancien programme sur un
  schéma plus récent. Les 7 migrations de cette consolidation savent se défaire (testé sur SQLite
  et PostgreSQL), mais la voie sûre en production reste la restauration.

---

## Points restant ouverts

| Point | Impact | Pourquoi il reste ouvert |
| --- | --- | --- |
| Montants en virgule flottante | Aucun écart observé (audit : 200 000 paiements simulés) ; risque théorique d'arrondi sur de très longues sommes. | Passer en décimal touche toutes les tables d'argent et leurs calculs : réécriture hors périmètre. Les montants sont bornés et finis (C1). |
| 115 écarts modèle/schéma restants | Aucun fonctionnel identifié : index de même rôle sous un autre nom, ON DELETE plus stricts en base, 12 nullabilités, 1 type (`compte` 120 caractères en base, 20 au modèle), 1 colonne historique. | Les « corriger » pourrait refuser des données existantes ; mesuré et documenté plutôt que modifié. |
| Retour arrière des migrations antérieures à la consolidation | Un `downgrade` au-delà de `a4c7e2f9d153` n'est pas garanti. | Hors périmètre ; la procédure de retour arrière passe par la restauration d'une sauvegarde. |
| Autorité HTTPS des installations existantes | Une installation antérieure garde son autorité sans restriction (6.7). | La remplacer couperait l'accès HTTPS de tous les postes jusqu'au redéploiement : à décider par la DSI (réinstallation neuve + reprise + redéploiement du certificat). |
| Choix de l'adresse IP et pare-feu VPN sur servisa (6.3) | Si la détection se trompait, les postes ne joindraient pas le serveur. | Non testable en CI (une carte) ; l'assistant affiche l'adresse retenue et permet de la corriger : **à vérifier à l'installation**. |
| Durées réelles sur servisa (sauvegarde, migration) | Délais proportionnés, mais non mesurés sur vos volumes. | Aucun accès à la production (consigne). |
| Registre des effacements (restauration) hors Windows | Une installation manuelle (Linux) sans `APP_DATA_DIR` ne tient pas les registres hors base (numéros, effacements). | Même emplacement que le registre des numéros de reçus ; documenté. Les défauts de ces registres relevés après la fusion sont traités dans `docs/CONSOLIDATION-APRES-PR59.md`. |
| Veille : rebinding DNS | Un nom qui change d'adresse entre le contrôle et la connexion n'est pas couvert. | Limite classique ; le cas nécessite un serveur DNS hostile. |
| Libellés QPV propres à Creil (SENACS, magatomatique) | Une autre structure verrait « Rouher / Hauts de Creil ». | Antérieur à ce chantier, choix produit ; la consigne de généricité portait sur la reprise, qui est générique. |
| Contacts des partenaires | Pas de durée de conservation automatique. | Contacts professionnels ; suppression à la main si besoin. |
| GitGuardian (PR #59) | Alerte sur des mots de passe fictifs de test (`P@ss%é!w0rd`…). | Faux positif : à marquer comme tel dans le tableau de bord GitGuardian (action de votre côté). |
