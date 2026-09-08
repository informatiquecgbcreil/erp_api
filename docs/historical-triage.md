# Triage assisté du classeur STATS 2026

Analyse du rapport d'analyse `historical-import-1` produit sur le vrai classeur
`STATS_2026_par_activite.xlsx` (empreinte `64486d62e5d0b967…`), rejoué contre une
copie de base contenant déjà des participants (empreinte `88a6143ae4b6dda2…`).

Procédure et règles d'import : [historical-import.md](historical-import.md).
Constats du dry-run initial : [historical-validation.md](historical-validation.md).

Les chiffres de ce document ne recoupent pas ceux de la validation initiale, et
c'est normal : celle-ci portait sur une base vide (970 REVIEW, 494 créations,
aucune fiche existante candidate). L'analyse reprise ici tourne contre une base
peuplée — **150 fiches ERP apparaissent comme candidates** — et intègre la
correction des activités proches. D'où 1 014 REVIEW et 450 créations.

## Pourquoi 1 184 blocages ne sont pas 1 184 décisions

Le rapport compte des **lignes source**, pas des personnes. Le classeur contient
1 464 lignes de participants réparties sur 66 feuilles d'activité, mais seulement
**834 identités distinctes** : 600 personnes n'apparaissent qu'une fois, une
apparaît vingt-deux fois. Le recoupement concerne 234 personnes et 864 lignes.

La cause de fond du volume de REVIEW est structurelle, pas accidentelle. Les
règles de `historical_matching` exigent, pour un rapprochement automatique, une
date de naissance complète ou un contact concordant. Le classeur ne contient que
`NOM`, `PRENOM`, `ANNEE`, `SEXE`, `QUARTIER` : **aucun téléphone, aucun courriel,
aucune date complète**. Aucune ligne ne peut donc atteindre EXACT ou
HIGH_CONFIDENCE, et 1 014 lignes tombent en REVIEW avec le motif
`names_without_sufficient_identity_evidence` (990 occurrences). Ce n'est pas un
défaut du moteur : c'est le refus documenté de fusionner sur les seuls noms.

Le triage ne relâche aucune de ces règles. Il change l'unité de travail : au lieu
de relire 1 014 lignes, on relit **834 dossiers**, et on ne tranche à la main que
ceux où le classeur laisse une vraie ambiguïté.

| Objet | Blocages du rapport | Dossiers après triage | Restant à trancher |
|---|---:|---:|---:|
| Participants | 1 014 lignes | 834 dossiers | **221 dossiers** (492 lignes) |
| Secteurs d'activité | 66 | 66 | **2** |
| Séances | 46 | 46 | **5** |
| Anomalies bloquantes | 58 | 58 | **58 acquittements** |
| **Total** | **1 184** | | **286** |

## Qualité de la source, telle qu'elle est

- **Années de naissance** : 409 lignes sans année, 26 `ADULTE`, 6 `ENFANT`,
  3 `ADOS`. `ADULTE` et `ENFANT` sont traités comme une absence ; `ADOS` non, il
  produit `invalid_birth_year`. Deux valeurs `19853`, une `10`, une `12` :
  probablement 1985 et des âges saisis à la place d'années.
- **124 années postérieures à 2020**, presque toutes sur les feuilles petite
  enfance ; cohérent avec des activités parents-enfants, pas une anomalie.
- **8 doublons dans une même feuille** : la même personne occupe deux lignes de
  la même activité. Une fois regroupées, leurs présences fusionnent sur une
  relation unique — le rapport le signalera comme présences redondantes.
- **Présences** : 7 031 cellules, toutes égales au nombre `1`, total conforme aux
  totaux Excel enregistrés. 880 colonnes de séances, du 5 janvier au 29 juin 2026.
- **Territoire** : une seule valeur inconnue, `MOULIN` (CTAI LUCIA, ligne 53).
- **Deux feuilles au nom voisin** : `ANNIVERSAIRE EPE` et `LES ANNIVERSAIRES EPE`.
  Le moteur ne les rapproche pas (ressemblance 0,87, sous le seuil de 0,9) : si
  c'est la même activité, il faut le dire explicitement, sinon deux activités
  seront créées.

## Ce que le triage propose, et sur quelle preuve

### Personnes — 652 dossiers sur 834

| Proposition | Dossiers | Lignes | Preuve retenue |
|---|---:|---:|---|
| Rattacher à une fiche ERP | 90 | 236 | nom, prénom **et** année identiques à une seule fiche, sans contradiction |
| Regrouper les lignes du classeur | 73 | 286 | mêmes nom, prénom et année sur plusieurs feuilles, genre et territoire compatibles |
| Créer la personne | 450 | 450 | identité unique dans le classeur, aucun candidat proche |

**Aucun rapprochement n'est proposé sur le seul nom.** Un dossier sans millésime
— ni dans le classeur, ni sur la fiche ERP homonyme — part à l'arbitrage, même
quand rien ne le contredit : c'est exactement le cas que `historical_matching`
refuse de trancher seul, et le triage ne le contourne pas. Cela représente 39
dossiers du vrai classeur.

La clé d'identité ignore les espaces et les tirets : `EL BAYAD` et `ELBAYAD` sont
le même dossier. Quand un dossier porte plusieurs orthographes, la décision fixe
explicitement l'orthographe majoritaire au lieu de laisser le moteur trancher par
ordre de tri.

Le risque assumé de ces regroupements est la fusion de deux personnes réellement
distinctes portant les mêmes nom, prénom et année. Le classeur ne contient que
**trois** cas d'homonymie interne — et ils portent justement des années
différentes, donc ils ne sont pas regroupés.

### Personnes — 182 dossiers à trancher

| Motif | Dossiers | Ce qu'il faut décider |
|---|---:|---|
| Orthographe proche | 147 | `SGIR` / `SGHIR` / `SHGIR fatima`, `KHALOUKY` / `KALOUKHY` / `KHALOUKI achraf`, `LAMARRE nnie` / `annie` : la même personne ou non. Attention aux fratries : `FELLOUS aylan` / `ayman`, `CHERRAD amir` / `amira`. |
| Aucune année pour étayer le rapprochement | 39 | homonymes sans millésime : 30 groupes internes au classeur et 9 dossiers face à une fiche ERP. |
| Identité incomplète ou année illisible | 17 | prénom seul, nom seul, `ADOS` en année : compléter, rattacher ou ignorer. |
| Année du classeur ≠ fiche ERP | 9 | sept écarts de un à trois ans (1982/1983, 2018/2019, 1996/1997…). La fiche ERP n'est jamais écrasée : rattacher ne modifie pas sa date. |
| Genre du classeur ≠ fiche ERP | 4 | dont `TOURE Sadou` (`F` au classeur, `Homme` en fiche). |
| Homonymes dans le classeur | 3 | `BIATRANE naima` 1983/1993, `OTMANI cilia` 2003/2023, `HAMMOUD fidaa` 1988/2001. |
| Genre contradictoire entre feuilles | 2 | `BASSAMA jeannelle`, `ALIMY zarlasht`. |

Ces 221 dossiers ne sont pas des cas marginaux : ils portent une part
substantielle des présences du classeur.

### Séances — 41 dates sur 46

Les 46 colonnes litigieuses suivent trois motifs répétitifs :

- **`31` sous un en-tête d'avril** (8 colonnes) : le 31 avril n'existe pas, et
  l'initiale `M` du jour de semaine confirme **mardi 31 mars 2026**.
- **`30/30`** (7 colonnes) : quantième saisi deux fois. Encadré par les colonnes
  voisines et confirmé par `L`, c'est **lundi 30 mars 2026**.
- **`66`** (3 colonnes) : même faute sur un chiffre. Entre les 4 et 11 mai, avec
  `ME`, c'est **mercredi 6 mai 2026**.
- **Mois d'en-tête périmé** (les 11 colonnes de `VACANCES`, entre autres) :
  l'en-tête annonce janvier, les quantièmes et jours de semaine désignent les
  vacances d'hiver, du 16 au 27 février 2026.

Restent cinq colonnes : deux samedis de janvier sans quantième
(`CONCERT DU NOUVEL AN`, `THEATRE EN JEU`, cinq dates possibles chacune) et trois
contradictions entre quantième et jour de semaine (`CTAI LUCIA!CH`,
`LAB EXPRESSION!U` et `!W`, deux dates possibles chacune). Aucune n'est tranchée
automatiquement : deux lectures possibles ne valent pas une décision.

### Anomalies bloquantes — 58 acquittements, jamais automatiques

Les 57 anomalies du parseur et l'anomalie territoriale forment une famille de
blocages **distincte** des décisions ci-dessus : une séance datée reste bloquée
par l'anomalie qui a signalé sa cellule tant que celle-ci n'est pas acquittée.
Acquitter n'est pas corriger, c'est déclarer avoir vérifié la cellule d'origine.
Le triage ne le fait donc jamais tout seul, même quand il propose une lecture.

Il fait en revanche le travail de préparation : chaque anomalie est exportée avec
sa feuille, sa cellule, la valeur réellement saisie et, pour 41 d'entre elles, la
date que le triage propose pour cette colonne. On acquitte en connaissance de
cause au lieu de cocher 58 cases dans une page web.

| Code | Anomalies | Lecture proposée en regard |
|---|---:|---|
| `weekday_mismatch` | 25 | oui, quand la colonne est datée |
| `invalid_day` | 13 | idem |
| `missing_identity` | 11 | non, elles renvoient à un dossier de personne |
| `invalid_calendar_date` | 8 | oui |
| `unknown_territory` | 1 | non, c'est `MOULIN` |

### Activités — 64 secteurs sur 66

Le secteur est déduit du nom métier par mots-clés, avec une confiance affichée.
Six propositions sont marquées `moyenne` et méritent une relecture attentive :
`CTAI LUCIA`, `FORS - MEDIA`, `ATELIER CONVERSATION FRANCELINE`, `MARCHE`,
`NAWELL MADANI`, `ZUMBA`. **Un nom de feuille ne prouve pas un secteur** : la
table complète est dans le compte rendu de triage, à relire ligne par ligne.

Deux activités restent à trancher parce qu'elles ressemblent à des ateliers
existants : `NUMERIQUE AUTREMENT` (candidat « Le Numérique Autrement », id 8) et
`NUMERIQUE PAR TOUS` (candidats « Numérique Par Tous » id 6 et « Numérique Par
Tous pro » id 29).

## Utilisation depuis l'application

Sur l'écran de prévisualisation, le bouton **« Proposer un triage et refaire le
dry-run »** applique tout ce qui précède sans quitter le navigateur : il
recalcule le triage à partir de l'aperçu en cours, complète les décisions et
relance l'analyse. Une décision déjà enregistrée n'est jamais remplacée, et
aucune anomalie n'est acquittée. Les points restants se traitent ensuite dans le
formulaire habituel de l'écran.

C'est la voie normale. Les commandes ci-dessous servent au travail hors ligne,
sur une copie, ou quand on préfère trancher les arbitrages au tableur.

## Utilisation en ligne de commande

L'outil ne lit que le rapport JSON. Il ne touche ni la base, ni le classeur, et
n'applique rien.

Le rapport d'entrée est celui de l'analyse : soit le `--output` de
`python -m tools.historical_import --database <base> analyze`, soit le fichier
téléchargé depuis Administration → Migration historique. Le triage ne relit pas
le classeur : sans ce rapport il ne peut rien faire, et il le dit clairement au
lieu de lever une exception. Le chemin passé à `--report` est relatif au dossier
courant ; les dossiers de sortie manquants, eux, sont créés.

```powershell
python -m tools.historical_triage trier `
  --report instance/historical_validation/rapport.json `
  --decisions instance/historical_validation/decisions.json `
  --markdown instance/historical_validation/triage.md `
  --csv instance/historical_validation/arbitrages.csv
```

1. Lire `triage.md` : ce qui est proposé, et pourquoi.
2. Ouvrir `arbitrages.csv` — point-virgule et UTF-8 avec BOM, s'ouvre directement
   dans Excel — et remplir la colonne `decision` :

   | Type | Valeurs acceptées |
   |---|---|
   | `personne` | `nouvelle`, `fiche:<id ERP>`, `groupe:<clé d'un autre dossier>`, `ignorer` |
   | `seance` | `AAAA-MM-JJ`, `ignorer` |
   | `activite` | un libellé de secteur, `atelier:<id ERP>`, `nouvelle`, `ignorer` |
   | `anomalie` | `acquitter` — et rien d'autre : refuser une anomalie, c'est la laisser vide |

   `groupe:` accepte une cible située n'importe où dans le fichier, y compris plus
   bas ; les renvois sont résolus après coup. Les lignes vides restent bloquantes,
   ce qui est le comportement voulu : rien n'est décidé par défaut.
3. Reprendre les arbitrages :

```powershell
python -m tools.historical_triage fusionner `
  --report instance/historical_validation/rapport.json `
  --csv instance/historical_validation/arbitrages.csv `
  --decisions instance/historical_validation/decisions.json
```

   Toute saisie non reconnue est refusée ligne par ligne et signalée ; elle ne
   modifie aucune décision et le code de retour passe à 1.

4. Rejouer l'analyse avec ces décisions, relire l'aperçu, recommencer tant qu'il
   reste des blocages, puis seulement `apply` :

```powershell
python -m tools.historical_import --database instance/historical_validation/staging.db analyze `
  --file "C:\CHEMIN\STATS_2026_par_activite.xlsx" `
  --decisions instance/historical_validation/decisions.json `
  --output instance/historical_validation/rapport-valide.json
```

`--sans-secteurs` désactive les propositions de secteur et laisse les 66
affectations manuelles. `--secteurs "A,B,C"` impose un référentiel ; sinon celui
du rapport, sinon `config.SECTEURS`.

## Limites

- Le triage **propose** ; il ne remplace pas la relecture. Un dossier « rattacher »
  reste une affirmation d'identité fondée sur nom, prénom et année seuls.
- Les anomalies ne sont jamais acquittées par l'outil, quelle que soit la
  confiance de la lecture qu'il propose pour la colonne concernée.
- Les propositions de secteur sont lexicales. Elles n'ont aucune connaissance du
  projet social ni de l'organigramme réel.
- Les dates déduites reposent sur l'ordre chronologique des colonnes et sur les
  initiales de jour du classeur. Une feuille dont les colonnes ne seraient pas
  chronologiques produirait des propositions fausses : le compte rendu affiche la
  saisie d'origine de chaque colonne pour permettre le contrôle.
- Les chiffres de ce document valent pour l'analyse citée en tête. Un autre
  classeur ou une autre base changent les empreintes, et le triage doit être
  refait.
- L'outil n'écrit jamais dans la base. Le refus d'un aperçu périmé, la
  transaction unique et l'immutabilité des lots restent assurés par
  `historical_import`.
