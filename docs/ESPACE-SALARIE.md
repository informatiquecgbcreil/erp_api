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
| Migrations | `migrations/versions/a9d4e6f8b2c1_espace_salarie.py`, `b3f5a7c9d1e2_decision_par_interesse.py` |
| Gabarits | `app/templates/salaries/`, `_espace_salarie.html`, `_dashboard_onglets.html` |
| Cadres de signature | `app/static/js/signature-rh.js` |
| Tests | `tests/test_espace_salarie.py` |

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

`brouillon` → (salarié signe) `soumise` → (assistant·e signe) `transmise` →
(direction signe) `acceptee` / `refusee` → (assistant·e signe) notification
(`notifiee_le`). La direction peut décider depuis `soumise` (assistant·e
absent·e). Refus motivé obligatoire. Le salarié peut annuler tant que rien
n'est décidé. Solde = heures sup (ajustements négatifs compris) −
récupérations **acceptées** ; il peut être négatif (récupération par avance).

**Décider pour soi-même.** Tout le monde a un espace salarié, direction
comprise : la direction peut donc décider de SA propre demande (et retirer
des heures de SON solde). C'est permis — une petite structure n'a pas
toujours d'autre décideur — mais jamais discret : la demande porte
`decision_par_interesse` (badge « décidée par l'intéressé·e », filtre dédié
dans la boîte de l'équipe, compteur annuel sur l'accueil de la direction) et
le journal enregistre une action distincte (`rh.recup_auto_decision`,
`rh.heures_retirees_par_interesse`). De quoi permettre un contrôle par le
bureau ou le CA. L'assistant·e peut transmettre et notifier ses propres
demandes (simple relais, sans pouvoir de décision).

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

## Droits

| Permission | Rôle(s) par défaut | Ouvre |
|---|---|---|
| `salarie:espace` | toute l'équipe (y compris admin technique) | son propre espace |
| `recup:transmettre` | assistant·e de direction, direction | transmettre, notifier |
| `recup:decider` | direction | décider, retirer des heures sup |
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
