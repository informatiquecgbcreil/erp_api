# Espace salarié (intégration de l'application « Récup »)

L'application **Récup** (heures supplémentaires, récupérations, frais
kilométriques, profils salariaux, coffre-fort de documents) est intégrée à
l'ERP. Elle n'avait jamais tourné en production : aucune donnée n'est à
reprendre. L'ancienne synchronisation par API (`RECUP_BASE_URL`,
`RECUP_TOKEN`, table `recup_rh_snapshot`) est retirée.

Le tout appartient au module **« Ressources humaines »**
(`app/services/modules.py`) : désactiver ce module masque tout l'espace.

## Où c'est dans le code

| Élément | Emplacement |
|---|---|
| Pages | `app/salaries/` (blueprint `salaries`, préfixe `/salarie`) |
| Logique partagée (soldes, liens, signatures, compteurs) | `app/services/espace_salarie.py` |
| Calcul au barème kilométrique | `app/services/frais_km.py` |
| Modèles | `app/models.py`, section « ESPACE SALARIÉ » |
| Migrations | `migrations/versions/a9d4e6f8b2c1_espace_salarie.py`, `b3f5a7c9d1e2_decision_par_interesse.py`, `c4a6b8d0e2f3_courriels_et_secteur_rh.py`, `d5b7c9e1f3a4_refus_par_relais.py` |
| E-mails | `app/services/courriels_rh.py` (+ types « rh_a_traiter », « frais_km » dans `app/services/notifications.py`) |
| Exports | `app/services/exports_rh.py`, `app/salaries/exports.py` |
| Gabarits | `app/templates/salaries/`, `_espace_salarie.html`, `_dashboard_onglets.html` |
| Cadres de signature | `app/static/js/signature-rh.js` |
| Tests | `tests/test_espace_salarie.py`, `tests/test_rh_courriels_exports.py` |

## Ce qui est rattaché à quoi

- Tout l'historique (heures, demandes, frais, profil, documents) est rattaché
  à la **fiche salarié** (`salarie`), pas au compte : il survit à un
  changement de compte. `salarie.user_id` relie la fiche au compte.
- **Lien automatique** : une fiche libre et un compte actif qui portent le
  même nom (« prénom nom » ou « nom prénom », accents et majuscules ignorés),
  chacun de façon unique, sont reliés sans intervention : à l'ouverture de
  l'espace, à la création du compte, à la création/import de fiches et à
  l'ouverture de la page RH. Sinon : colonne « Compte » de la page RH.
- **Heures sup et trajets ↔ agenda** : le formulaire propose les séances et
  créneaux de l'agenda de la personne ce jour-là (même périmètre que son
  flux iCal) ; le lien est enregistré (`session_id`, `creneau_id`).
- **Frais km → dépense** : la finance crée la dépense sur une ligne de
  charge ; la ligne proposée est celle du même exercice, du secteur du
  salarié, au compte 625. La note garde `depense_id`.
- **Profil salarial → masse salariale** : à l'enregistrement, la direction
  peut reporter le coût annuel estimé (coût chargé × 35 h × ETP × semaines)
  dans `salarie.salaire_brut_charge`, qui alimente la page RH et le SENACS.
  Le report est explicite (case à cocher), jamais silencieux.

## Circuit des récupérations

1. Le salarié déclare ses heures sup : créditées tout de suite, visibles
   **en direct** par l'assistant·e et la direction (boîte « Demandes de
   l'équipe », 30 derniers jours), qui peuvent les **corriger dans un sens
   ou dans l'autre** (0 = retrait), justification obligatoire. La
   déclaration d'origine n'est jamais modifiée : la correction est une ligne
   d'ajustement (`est_ajustement`, `origine_id`) qui porte la différence avec
   la durée en vigueur. Le salarié reçoit un e-mail avec la justification.
2. Il demande une récupération : `brouillon` → (il signe) `soumise`.
   L'assistant·e reçoit un e-mail.
3. L'assistant·e signe et **transmet** (`transmise` : la direction reçoit un
   e-mail, le salarié aussi) ou **refuse** avec une justification
   (`refusee` + `refusee_par_relais` : le salarié reçoit un e-mail avec la
   justification ; circuit terminé).
4. La direction signe et **accepte**, ou **refuse** avec une justification.
   Le salarié (avec la justification en cas de refus) et l'assistant·e
   reçoivent un e-mail. La direction peut décider depuis `soumise`
   (assistant·e absent·e).
5. L'assistant·e marque la décision **« Pris connaissance »** (`notifiee_le`,
   `notifiee_par_user_id`), d'un clic, sans signature : le salarié est déjà
   prévenu par l'e-mail. (Les demandes plus anciennes peuvent porter une
   signature de notification : elle reste affichée.)

La justification n'est obligatoire qu'en cas de refus ou de correction. Le
salarié peut annuler tant que rien n'est décidé. Solde = heures sup
(corrections comprises) − récupérations **acceptées** ; il peut être négatif
(récupération par avance).

**Décider pour soi-même.** Tout le monde a un espace salarié, direction
comprise : la direction peut donc décider de SA propre demande (et corriger
SES propres heures). C'est permis — une petite structure n'a pas
toujours d'autre décideur — mais jamais discret : la demande porte
`decision_par_interesse` (badge « décidée par l'intéressé·e », filtre dédié
dans la boîte de l'équipe, compteur annuel sur l'accueil de la direction) et
le journal enregistre une action distincte (`rh.recup_auto_decision`,
`rh.heures_corrigees_par_interesse`). De quoi permettre un contrôle par le
bureau ou le CA. L'assistant·e peut transmettre ou refuser ses propres
demandes et corriger ses propres heures : même marquage.

Les compteurs « à traiter » sont calculés à partir des statuts : il n'y a pas
de liste de tâches parallèle à maintenir (l'ancienne table `tasks` de Récup
n'est pas reprise).

## Frais kilométriques : le calcul

Le barème officiel donne un montant **annuel** par tranche de distance
annuelle (`d × taux + forfait`). Chaque trajet reçoit
`F(cumul + km) − F(cumul)`, où le cumul est la distance déjà déclarée dans
l'année pour ce véhicule (type + puissance). La somme de l'année redonne
exactement `F(total)`, tranches et forfaits compris. Récup choisissait la
tranche d'après la distance du seul trajet : juste sous 5 000 km par an,
faux au-delà. Si le barème de l'année n'est pas saisi, le plus récent
antérieur s'applique et la note l'indique (`annee_bareme`).

## E-mails

Deux niveaux, qui ne se remplacent pas :

1. **Au fil de l'eau, à la personne concernée** (`courriels_rh.py`) :

   | Événement | Destinataire |
   |---|---|
   | demande de récupération signée | l'assistant·e (droit `recup:transmettre` sans `recup:decider`) ; à défaut la direction |
   | demande transmise | la direction (`recup:decider`) et le salarié |
   | demande refusée par l'assistant·e | le salarié, avec la justification |
   | décision de la direction | le salarié (avec la justification si refus) et l'assistant·e |
   | heures sup corrigées ou retirées | le salarié, avec la justification |
   | document partagé | chaque personne de la liste d'accès |

   - L'e-mail est écrit dans la table `courriel_rh` **dans la transaction de
     l'action**, puis envoyé juste après : action annulée = personne
     prévenu ; serveur de mail absent ou en panne = action réussie, e-mail
     en file.
   - Réessais : à chaque action RH et une fois par jour (première requête),
     au plus 5 fois ; un e-mail non parti sous 7 jours expire (il n'aurait
     plus de sens). État de la file et bouton « Envoyer la file maintenant » :
     Administration → Notifications.
   - Jamais à l'auteur de l'action. Un fait, la justification quand il y en
     a une (refus, correction : le salarié doit savoir pourquoi), et un
     lien ; jamais de document ni de montant de salaire. Le lien pointe vers
     l'adresse publique configurée (`ERP_PUBLIC_BASE_URL`), donc vers le
     réseau du centre.
   - Chacun peut couper ces e-mails : Accueil → Espace salarié → « Mes
     e-mails » (table `preference_courriel_rh`) ; les badges restent.
   - L'ancienne étape « notifier le salarié » signée par l'assistant·e est
     devenue un simple **« Pris connaissance »** : c'est l'e-mail qui
     prévient le salarié.

2. **Récapitulatifs** dans Administration → Notifications (même mécanique
   que les autres types, rien d'actif par défaut) : « Récupérations en
   souffrance » (demandes bloquées au-delà du délai, décisions dont
   l'assistant·e n'a pas pris connaissance, décisions par l'intéressé·e) et « Frais kilométriques »
   (notes du mois, notes non passées en dépense).

## Exports (paie mensuelle)

Page « Exports RH » (menu de l'espace salarié, page RH). Période = un mois
par défaut (ou du/au), filtres secteurs et salariés.

- **Classeur Excel** : Synthèse (une ligne par salarié : solde début, heures
  sup, corrections signées (+ ajout, − retrait), récup prises, en attente, solde fin, frais km, non
  imputés), Par secteur, Heures sup, Récupérations, Frais km, et en option
  un onglet par salarié. Heures en décimal (3,5 h), montants en euros.
- **Relevé individuel** (`/salarie/releve/<id>?mois=AAAA-MM`) : solde au
  début, chaque mouvement avec le solde courant, solde à la fin, frais km,
  cadres de visa. Imprimable en PDF depuis le navigateur.
- **État de frais km mensuel** (`/salarie/frais-km/etat/<id>?mois=…`) :
  trajets, barème, total, visas salarié / direction / comptabilité.

Le **secteur** d'une ligne est figé à sa saisie (colonne `secteur` sur les
heures, demandes et trajets) : un changement de secteur ne réimpute pas le
passé. Un solde « au jour J » = heures sup datées ≤ J − récupérations
acceptées datées ≤ J.

Droits : classeur complet `rh:view` ; frais km seuls `frais_km:suivi` ;
relevé : la personne, `rh:view` ou `recup:decider` ; état de frais : la
personne, `frais_km:suivi` ou `rh:view`. **Aucun export ne contient de coût
horaire ni de salaire.**

## Droits

| Permission | Rôle(s) par défaut | Ouvre |
|---|---|---|
| `salarie:espace` | toute l'équipe (y compris admin technique) | son propre espace |
| `recup:transmettre` | assistant·e de direction, direction | transmettre ou refuser, prendre connaissance, corriger des heures sup |
| `recup:decider` | direction | décider, corriger des heures sup |
| `frais_km:suivi` | direction, finance | notes de l'équipe, passage en dépense (+ `depenses:create`) |
| `frais_km:baremes` | direction, finance, admin technique | barèmes |
| `salaires:gerer` | direction | profils salariaux — **ne se donne que par qui l'a** |
| `coffre:types` | direction, admin technique | types de documents |

Un nouveau rôle **« Assistant(e) de direction »** est créé au démarrage.
Le coffre-fort ne connaît pas de passe-droit : un document n'est lisible que
par son déposant et sa liste d'accès, la direction comprise.

## Fichiers

Signatures, justificatifs et documents sont rangés sous
`APP_UPLOAD_DIR/rh/` : compris dans les sauvegardes, jamais servis par
`/static` ni `/media`, uniquement par les routes qui vérifient les droits.
