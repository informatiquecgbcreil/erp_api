"""Triage assisté d'un rapport d'analyse historique : regroupe, propose, n'écrit rien.

Le rapport produit par ``historical_import.analyze_import`` classe des LIGNES
source. Sur le vrai classeur, une même personne occupe jusqu'à vingt-deux
lignes réparties dans autant de feuilles d'activité : la relecture ligne à
ligne est donc impraticable et surtout redondante.

Ce module regroupe ces lignes en DOSSIERS — une personne, une séance, une
activité — et propose une décision par dossier lorsque les preuves présentes
dans le classeur la rendent évidente. Il ne relâche aucune des règles de
``historical_matching`` : il produit un fichier de décisions explicites,
exactement celles qu'un humain aurait saisies, à relire avant tout import.

Aucun SQL, aucune écriture, aucune lecture du classeur : seul le rapport JSON
est nécessaire.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher

from app.ateliers.historical_matching import normalize_person

VERSION = "historical-triage-1"

# Ligne 4 du classeur : initiale du jour de semaine. ME distingue mercredi de
# mardi ; les autres initiales sont sans ambiguïté dans ce classeur.
JOURS_SEMAINE = {"L": 0, "M": 1, "MA": 1, "ME": 2, "J": 3, "V": 4, "S": 5, "D": 6}

# Mots-clés d'affectation des activités à un secteur : (secteur, mots sûrs,
# mots seulement probables). L'ordre compte — une feuille « PARENTS EPE »
# relève de l'EPE, pas de la parentalité générale — et un mot n'est reconnu
# qu'en début de mot du nom métier, pluriel compris.
REGLES_SECTEUR = (
    ("EPE",
     ("EPE", "ANNIVERSAIRE", "COMPTINE", "PETITE ENFANCE", "PASSERELLE", "ASSISTANTE MATERNELLE"),
     ("RECRE",)),
    ("Numérique",
     ("NUMERIQUE", "GAMING", "DIGITAL", "INFORMATIQUE", "CAFE MAINTENANCE", "CAFE REPAIR"),
     ("CTAI", "MEDIA")),
    ("Familles", ("PARENTALITE", "PARENT", "FAMILLE"), ()),
    ("Santé Transition", ("SANTE", "DIETETICIENNE", "BIEN ETRE"), ("ZUMBA",)),
    ("Insertion Sociale et Professionnelle",
     ("ALPHA", "FLE", "CAP", "EMPLOI", "PARCOURS", "PRISE DE PAROLE", "CAUSERIE",
      "INTEGRACTION", "FFP", "PCB", "INSERTION"),
     ("CONVERSATION", "FORS")),
    ("Animation Globale",
     ("BARBECUE", "CONCERT", "SORTIE", "VACANCES", "COLLECTIF D HABITANTS", "CAFE MOUV",
      "MOTS EN ART", "LAB EXPRESSION", "THEATRE", "TRAC", "MUSIQUE", "COUTURE",
      "CONFECTION", "PARTENAIRE", "FAPE"),
     ("MARCHE", "MADANI", "SPECTACLE")),
)


def _normaliser(valeur):
    valeur = unicodedata.normalize("NFKD", str(valeur or "")).casefold()
    valeur = "".join(c for c in valeur if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", valeur).strip()


def _compact(valeur):
    """Clé insensible aux espaces : EL BAYAD et ELBAYAD sont la même personne."""
    return re.sub(r"\s+", "", valeur) if valeur else None


def _slug(valeur):
    return re.sub(r"[^a-z0-9]+", "-", _normaliser(valeur)).strip("-") or "dossier"


def _genre(valeur):
    valeur = _normaliser(valeur)
    return valeur or None


def _arete_directe(candidat):
    """Un candidat relié seulement par la chaîne du composant n'est pas une preuve."""
    return candidat.get("reasons") != ["non_transitive_identity_chain"]


def _normalise_fiche(brut):
    """Fiche existante lue avec les règles du moteur, jamais avec les siennes.

    Une fiche peut porter « F » là où le classeur porte « Femme », ou une date
    complète là où le classeur ne connaît qu'une année.
    """
    return normalize_person(brut)[0]


def _cle_candidat(candidat):
    normalise = _normalise_fiche(candidat["raw"])
    return _compact(normalise["nom"]), _compact(normalise["prenom"])


def _affichage(cle):
    nom, prenom = cle
    return f"{(nom or '?').upper()} {prenom or '?'}"


def trier_personnes(rapport):
    """Regroupe les lignes source par identité et propose une décision par dossier."""
    lignes = rapport["matching"]["rows"]
    presences = Counter(cellule["person_key"] for cellule in rapport["parser"]["attendance"])

    membres = defaultdict(list)
    for ligne in lignes:
        normalise = ligne["normalized"]
        membres[(_compact(normalise.get("nom")), _compact(normalise.get("prenom")))].append(ligne)

    dossiers = []
    for cle, groupe in sorted(membres.items(), key=lambda item: (item[0][0] or "", item[0][1] or "")):
        dossiers.append(_dossier_personne(cle, groupe, presences))
    _attribuer_groupes(dossiers)
    return dossiers


def _dossier_personne(cle, groupe, presences):
    nom, prenom = cle
    annees = {l["normalized"].get("birth_year") for l in groupe if l["normalized"].get("birth_year")}
    genres = {_genre(l["normalized"].get("genre")) for l in groupe if l["normalized"].get("genre")}
    incompletes = [l["key"] for l in groupe
                   if {"incomplete_identity", "invalid_birth_year"} & set(l["reasons"])]

    fiches, voisins = {}, Counter()
    for ligne in groupe:
        for candidat in ligne["candidates"]:
            if not _arete_directe(candidat):
                continue
            if _cle_candidat(candidat) == cle:
                if candidat["kind"] == "participant":
                    fiches[candidat["id"]] = candidat["raw"]
            else:
                voisins[_affichage(_cle_candidat(candidat))] += 1

    orthographes = Counter(
        (str(l["raw"].get("nom") or "").strip(), str(l["raw"].get("prenom") or "").strip())
        for l in groupe)
    majoritaire = max(orthographes, key=orthographes.get)
    normalisees = {i: _normalise_fiche(f) for i, f in fiches.items()}
    annees_fiches = {n["birth_year"] for n in normalisees.values() if n["birth_year"]}
    genres_fiches = {_genre(n["genre"]) for n in normalisees.values() if n["genre"]}

    dossier = {
        "cle": f"{nom or ''}|{prenom or ''}",
        "libelle": _affichage(cle),
        "nom": majoritaire[0] or None,
        "prenom": majoritaire[1] or None,
        "annee": next(iter(annees)) if len(annees) == 1 else None,
        "genre": next(iter(genres)) if len(genres) == 1 else None,
        "lignes": sorted(l["key"] for l in groupe),
        "feuilles": sorted({l["key"].rsplit("!", 1)[0] for l in groupe}),
        "presences": sum(presences.get(l["key"], 0) for l in groupe),
        "orthographes": sorted(f"{n} {p}".strip() for n, p in orthographes),
        "fiches_erp": [{"id": i, "nom": fiches[i].get("nom"), "prenom": fiches[i].get("prenom"),
                        "naissance": normalisees[i]["birth_date"] or normalisees[i]["birth_year"],
                        "genre": normalisees[i]["genre"]} for i in sorted(fiches)],
        "voisins": [{"libelle": v, "lignes": n} for v, n in voisins.most_common()],
        "alertes": [],
        "statut": None,
        "categorie": None,
        "motif": "",
        "decision": None,
    }

    if not nom or not prenom or incompletes:
        dossier.update(statut="arbitrer", categorie="identité incomplète ou année illisible",
                       motif="nom, prénom ou année inexploitable : aucune personne créée sans décision")
        dossier["alertes"] = sorted(incompletes)
    elif len(annees) > 1:
        dossier.update(statut="arbitrer", categorie="homonymes dans le classeur",
                       motif=f"mêmes nom et prénom, années {sorted(annees)}")
    elif len(genres) > 1:
        dossier.update(statut="arbitrer", categorie="genre contradictoire entre les feuilles",
                       motif=f"genres {sorted(genres)} pour la même identité")
    elif len(fiches) > 1:
        dossier.update(statut="arbitrer", categorie="plusieurs fiches ERP homonymes",
                       motif=f"fiches {sorted(fiches)} : choisir laquelle")
    elif fiches and annees and annees_fiches and annees != annees_fiches:
        dossier.update(statut="arbitrer", categorie="année du classeur ≠ fiche ERP",
                       motif=f"classeur {sorted(annees)} contre fiche ERP {sorted(annees_fiches)}")
    elif fiches and genres and genres_fiches and genres != genres_fiches:
        dossier.update(statut="arbitrer", categorie="genre du classeur ≠ fiche ERP",
                       motif=f"classeur {sorted(genres)} contre fiche ERP {sorted(genres_fiches)}")
    elif voisins:
        dossier.update(statut="arbitrer", categorie="orthographe proche",
                       motif="variante possible de " + ", ".join(sorted(voisins)))
    elif fiches:
        identifiant = next(iter(fiches))
        dossier.update(statut="rattacher", categorie="rattachement à une fiche ERP",
                       motif=f"fiche ERP {identifiant} : nom, prénom et année concordants",
                       decision={"action": "participant", "participant_id": identifiant})
    elif len(groupe) > 1:
        dossier.update(statut="regrouper", categorie="regroupement interne au classeur",
                       motif=f"{len(groupe)} lignes de même nom, prénom et année")
    else:
        dossier.update(statut="creer", categorie="création sans candidat",
                       motif="identité unique dans le classeur, aucune fiche ERP proche")
    return dossier


def _attribuer_groupes(dossiers):
    """Identifiant de regroupement stable et unique pour les fusions internes."""
    utilises = set()
    for dossier in dossiers:
        if dossier["statut"] != "regrouper":
            continue
        base = _slug(f"{dossier['nom']} {dossier['prenom']} {dossier['annee'] or 'sans-annee'}")
        groupe, suffixe = base, 2
        while groupe in utilises:
            groupe, suffixe = f"{base}-{suffixe}", suffixe + 1
        utilises.add(groupe)
        decision = {"action": "new", "group": groupe}
        if len(set(dossier["orthographes"])) > 1:
            # Le moteur choisirait une orthographe par tri ; on fixe la majoritaire.
            decision["values"] = {"nom": dossier["nom"], "prenom": dossier["prenom"]}
        dossier["decision"] = decision


def _colonne(seance):
    return re.match(r"[A-Z]+", seance["source_cell"]).group(0)


def _jour_semaine(seance):
    entete = seance["raw_headers"].get(_colonne(seance) + "4")
    return JOURS_SEMAINE.get(str(entete or "").strip().upper())


def _numero_jour(seance):
    """Premier entier lisible comme quantième dans la cellule du jour.

    Le classeur contient des saisies doublées — « 30/30 » pour le 30, « 66 »
    pour le 6 — que l'analyse refuse à juste titre d'interpréter seule.
    """
    brut = seance["raw_headers"].get(_colonne(seance) + "6")
    for nombre in re.findall(r"\d+", str(brut or "")):
        if 1 <= int(nombre) <= 31:
            return int(nombre)
    return None


def _bornes(seance, datees, ancre):
    """Dates encadrantes : les colonnes d'une feuille sont chronologiques.

    À défaut de colonne datée d'un côté, le mois inscrit en ligne 2 ferme la
    fenêtre. Il ne sert qu'à rétrécir un intervalle sinon large d'une année.
    """
    avant = [d for colonne, d in datees if colonne < seance["source_column"]]
    apres = [d for colonne, d in datees if colonne > seance["source_column"]]
    debut = max(avant) if avant else (ancre if ancre else None)
    fin = min(apres) if apres else (_fin_de_mois(ancre) if ancre else None)
    return debut, fin


def _fin_de_mois(jour):
    suivant = jour.replace(day=28) + timedelta(days=4)
    return suivant - timedelta(days=suivant.day)


def _ancre_mois(seance, seances_feuille):
    """Dernier mois explicite inscrit en ligne 2, à hauteur ou à gauche de la colonne."""
    ancres = []
    for autre in seances_feuille:
        valeur = autre["raw_headers"].get(_colonne(autre) + "2")
        if not valeur:
            continue
        try:
            ancres.append((autre["source_column"], date.fromisoformat(str(valeur)[:10]).replace(day=1)))
        except ValueError:
            continue
    retenues = [mois for colonne, mois in sorted(ancres) if colonne <= seance["source_column"]]
    return retenues[-1] if retenues else None


def _dates_possibles(seance, datees, annee, ancre=None, strict=True):
    debut, fin = _bornes(seance, datees, ancre)
    debut = debut or date(annee, 1, 1)
    fin = fin or date(annee, 12, 31)
    if fin < debut or (fin - debut).days > 400:
        return []
    jour, semaine = _numero_jour(seance), _jour_semaine(seance)
    if jour is None and semaine is None:
        return []
    if not strict:
        # Quantième et jour de semaine se contredisent : les deux lectures sont
        # proposées, jamais choisies.
        gauche = _filtrer(debut, fin, jour, None) if jour else []
        droite = _filtrer(debut, fin, None, semaine) if semaine is not None else []
        return sorted(set(gauche) | set(droite))
    return _filtrer(debut, fin, jour, semaine)


def _filtrer(debut, fin, jour, semaine):
    possibles, courant = [], debut
    while courant <= fin:
        if (jour is None or courant.day == jour) and (semaine is None or courant.weekday() == semaine):
            possibles.append(courant)
        courant += timedelta(days=1)
    return possibles


def trier_seances(rapport):
    """Propose une date pour les séances REVIEW dont le classeur ne laisse qu'une lecture."""
    annee = rapport["source"].get("year") or date.today().year
    par_feuille = defaultdict(list)
    for seance in rapport["sessions"]:
        par_feuille[seance["source_sheet"]].append(seance)

    dossiers, resolues = [], {}
    a_traiter = sorted((s for s in rapport["sessions"] if s["status"] == "REVIEW"),
                       key=lambda s: (s["source_sheet"], s["source_column"]))
    # Deux passes : les colonnes tranchées par l'analyse servent de bornes aux autres.
    for passe in (1, 2):
        for seance in list(a_traiter):
            datees = sorted(
                (autre["source_column"], date.fromisoformat(resolues.get(autre["key"], autre["date_session"])))
                for autre in par_feuille[seance["source_sheet"]]
                if resolues.get(autre["key"]) or autre["date_session"])
            dossier = _dossier_seance(seance, datees, annee, passe,
                                      _ancre_mois(seance, par_feuille[seance["source_sheet"]]))
            if dossier is None:
                continue
            a_traiter.remove(seance)
            dossiers.append(dossier)
            if dossier["statut"] == "dater":
                resolues[seance["key"]] = dossier["decision"]["date_session"]
    dossiers.sort(key=lambda d: d["cle"])
    return dossiers


def _dossier_seance(seance, datees, annee, passe, ancre=None):
    proposees = [date.fromisoformat(d) for d in (seance.get("candidate_dates") or [])]
    semaine, jour = _jour_semaine(seance), _numero_jour(seance)
    if proposees:
        retenues = [d for d in proposees if semaine is None or d.weekday() == semaine]
        motif = "date déduite du mois et confirmée par le jour de semaine"
    elif passe == 1:
        return None  # Attendre que les colonnes voisines soient tranchées.
    else:
        retenues = _dates_possibles(seance, datees, annee, ancre)
        motif = "seule date compatible avec les colonnes voisines, le quantième et le jour de semaine"
        if not retenues:
            retenues = _dates_possibles(seance, datees, annee, ancre, strict=False)
            motif = "quantième et jour de semaine contradictoires : les deux lectures possibles"
            if len(retenues) == 1:
                retenues = []  # Une seule lecture relâchée ne vaut pas décision.

    dossier = {
        "cle": seance["key"],
        "feuille": seance["source_sheet"],
        "cellule": seance["source_cell"],
        "creneau": seance.get("source_slot"),
        "jour_saisi": seance["raw_headers"].get(_colonne(seance) + "6"),
        "jour_semaine": seance["raw_headers"].get(_colonne(seance) + "4"),
        "anomalies": sorted({a["code"] for a in seance.get("anomalies", [])}),
        "options": [d.isoformat() for d in retenues[:8]],
        "statut": None, "motif": "", "decision": None,
    }
    if len(retenues) == 1:
        dossier.update(statut="dater", motif=motif,
                       decision={"action": "date", "date_session": retenues[0].isoformat()})
    else:
        dossier.update(statut="arbitrer",
                       motif=f"{len(retenues)} dates compatibles" if retenues else "aucune date compatible")
    return dossier


def trier_activites(rapport, *, proposer_secteurs=True, secteurs_connus=()):
    """Propose un secteur par activité et signale les feuilles au nom voisin."""
    labels = list(secteurs_connus) or [label for label, _ in REGLES_SECTEUR]
    dossiers = []
    for activite in rapport["activities"]:
        propose, confiance = _secteur_propose(activite["name"], labels)
        dossier = {
            "cle": activite["key"],
            "nom": activite["name"],
            "feuille": activite["source_sheet"],
            "secteur_propose": propose,
            "confiance": confiance,
            "candidats_erp": [{"id": c["id"], "nom": c["name"], "secteur": c.get("secteur")}
                              for c in activite.get("candidates", [])],
            "voisines": [], "statut": None, "motif": "", "decision": None,
        }
        if activite["match_status"] == "REVIEW":
            dossier.update(statut="arbitrer",
                           motif="ressemble à une activité existante : confirmer la reprise ou la création")
        elif propose:
            dossier.update(statut="secteur", motif=f"secteur déduit du nom métier ({confiance})",
                           decision={"secteur": propose})
        else:
            dossier.update(statut="arbitrer", motif="aucun secteur déductible du nom : à choisir")
        dossiers.append(dossier)
    _signaler_voisines(dossiers)
    if not proposer_secteurs:
        for dossier in dossiers:
            if dossier["statut"] == "secteur":
                dossier.update(statut="arbitrer", decision=None,
                               motif="proposition de secteur désactivée")
    return dossiers


def _secteur_propose(nom, labels):
    """Secteur déduit du nom métier ; « haute » n'engage que la règle, pas la vérité."""
    normalise = _normaliser(nom)
    for confiance, rang in (("haute", 1), ("moyenne", 2)):
        for regle in REGLES_SECTEUR:
            label, mots = regle[0], regle[rang]
            if label not in labels:
                continue
            for mot in mots:
                if re.search(rf"(?:^| ){re.escape(_normaliser(mot))}", normalise):
                    return label, confiance
    return None, "aucune"


def _signaler_voisines(dossiers):
    """Deux feuilles au nom proche dans le même secteur restent bloquées à l'import."""
    for gauche in dossiers:
        for droite in dossiers:
            if gauche is droite:
                continue
            ratio = SequenceMatcher(None, _normaliser(gauche["nom"]), _normaliser(droite["nom"])).ratio()
            if ratio < 0.8:
                continue
            gauche["voisines"].append({"nom": droite["nom"], "ratio": round(ratio, 3),
                                       "meme_secteur": gauche["secteur_propose"] == droite["secteur_propose"]})
            if ratio >= 0.9 and gauche["secteur_propose"] == droite["secteur_propose"] and gauche["statut"] == "secteur":
                gauche.update(statut="arbitrer", decision=None,
                              motif=f"nom très proche de « {droite['nom']} » dans le même secteur : "
                                    "confirmer deux activités distinctes ou un nom commun")


def trier(rapport, *, proposer_secteurs=True, secteurs_connus=()):
    """Triage complet : dossiers, décisions proposées et compteurs de relecture."""
    personnes = trier_personnes(rapport)
    seances = trier_seances(rapport)
    activites = trier_activites(rapport, proposer_secteurs=proposer_secteurs,
                                secteurs_connus=secteurs_connus)
    decisions = {"participants": {}, "activities": {}, "sessions": {}, "acknowledged_anomalies": []}
    for dossier in personnes:
        if dossier["decision"]:
            for ligne in dossier["lignes"]:
                decisions["participants"][ligne] = dict(dossier["decision"])
    for dossier in seances:
        if dossier["decision"]:
            decisions["sessions"][dossier["cle"]] = dict(dossier["decision"])
    for dossier in activites:
        if dossier["decision"]:
            decisions["activities"][dossier["cle"]] = dict(dossier["decision"])
    return {
        "version": VERSION,
        "source": rapport.get("source"),
        "rapport_digest": rapport.get("digest"),
        "base_digest": rapport.get("database_digest"),
        "personnes": personnes,
        "seances": seances,
        "activites": activites,
        "decisions": decisions,
        "resume": _resume(rapport, personnes, seances, activites, decisions),
    }


def _resume(rapport, personnes, seances, activites, decisions):
    compte = lambda dossiers, statut: sum(d["statut"] == statut for d in dossiers)
    lignes = lambda statut: sum(len(d["lignes"]) for d in personnes if d["statut"] == statut)
    return {
        "lignes_source": len(rapport["matching"]["rows"]),
        "dossiers_personnes": len(personnes),
        "personnes_rattachees": compte(personnes, "rattacher"),
        "personnes_regroupees": compte(personnes, "regrouper"),
        "personnes_creees": compte(personnes, "creer"),
        "personnes_a_arbitrer": compte(personnes, "arbitrer"),
        "lignes_rattachees": lignes("rattacher"),
        "lignes_regroupees": lignes("regrouper"),
        "lignes_a_arbitrer": lignes("arbitrer"),
        "presences_a_arbitrer": sum(d["presences"] for d in personnes if d["statut"] == "arbitrer"),
        "seances_a_valider": len(seances),
        "seances_datees": compte(seances, "dater"),
        "seances_a_arbitrer": compte(seances, "arbitrer"),
        "activites": len(activites),
        "activites_avec_secteur": compte(activites, "secteur"),
        "activites_a_arbitrer": compte(activites, "arbitrer"),
        "decisions_participants": len(decisions["participants"]),
        "decisions_seances": len(decisions["sessions"]),
        "decisions_activites": len(decisions["activities"]),
        "blocages_restants": (compte(personnes, "arbitrer") + compte(seances, "arbitrer")
                              + compte(activites, "arbitrer")),
    }


# --- Export et reprise des arbitrages -------------------------------------

COLONNES_CSV = ("type", "cle", "libelle", "details", "proposition", "decision", "commentaire")

AIDE_DECISION = {
    "personne": "nouvelle | fiche:<id ERP> | groupe:<clé d'un autre dossier> | ignorer",
    "seance": "AAAA-MM-JJ | ignorer",
    "activite": "<libellé de secteur> | atelier:<id ERP> | nouvelle | ignorer",
}


def exporter_arbitrages(triage):
    """Lignes prêtes pour un tableur : une par dossier restant à trancher."""
    lignes = []
    for dossier in triage["personnes"]:
        if dossier["statut"] != "arbitrer":
            continue
        details = (f"{len(dossier['lignes'])} ligne(s), {dossier['presences']} présence(s), "
                   f"année {dossier['annee'] or '?'}, {dossier['genre'] or 'genre ?'}, "
                   f"feuilles : {', '.join(dossier['feuilles'])}")
        if dossier["fiches_erp"]:
            details += " | fiches ERP : " + ", ".join(
                f"{f['id']} {f['nom']} {f['prenom']} ({f['naissance'] or '?'}, {f['genre'] or '?'})"
                for f in dossier["fiches_erp"])
        if len(dossier["orthographes"]) > 1:
            details += " | orthographes : " + " / ".join(dossier["orthographes"])
        lignes.append({"type": "personne", "cle": dossier["cle"], "libelle": dossier["libelle"],
                       "details": details, "proposition": f"{dossier['categorie']} — {dossier['motif']}",
                       "decision": "", "commentaire": ""})
    for dossier in triage["seances"]:
        if dossier["statut"] != "arbitrer":
            continue
        lignes.append({"type": "seance", "cle": dossier["cle"],
                       "libelle": f"{dossier['feuille']} colonne {dossier['cellule']}",
                       "details": (f"jour saisi {dossier['jour_saisi']!r}, "
                                   f"jour de semaine {dossier['jour_semaine']!r}, "
                                   f"créneau {dossier['creneau']}, {', '.join(dossier['anomalies'])}"),
                       "proposition": " ou ".join(dossier["options"]) or dossier["motif"],
                       "decision": "", "commentaire": ""})
    for dossier in triage["activites"]:
        if dossier["statut"] != "arbitrer":
            continue
        lignes.append({"type": "activite", "cle": dossier["cle"], "libelle": dossier["nom"],
                       "details": "candidats ERP : " + (", ".join(
                           f"{c['id']} {c['nom']} ({c['secteur']})" for c in dossier["candidats_erp"]) or "aucun"),
                       "proposition": dossier["motif"], "decision": "", "commentaire": ""})
    return lignes


def fusionner_arbitrages(triage, lignes):
    """Complète les décisions automatiques avec les arbitrages saisis au tableur."""
    personnes = {d["cle"]: d for d in triage["personnes"]}
    seances = {d["cle"]: d for d in triage["seances"]}
    activites = {d["cle"]: d for d in triage["activites"]}
    decisions = {section: dict(valeurs) if isinstance(valeurs, dict) else list(valeurs)
                 for section, valeurs in triage["decisions"].items()}
    groupes = {d["decision"]["group"] for d in triage["personnes"]
               if (d["decision"] or {}).get("group")}
    refusees, reportees = [], []

    def appliquer(numero, cle, type_, choix):
        try:
            if type_ == "personne":
                _arbitrer_personne(decisions, personnes, groupes, cle, choix)
            elif type_ == "seance":
                _arbitrer_seance(decisions, seances, cle, choix)
            elif type_ == "activite":
                _arbitrer_activite(decisions, activites, cle, choix)
            else:
                raise ValueError(f"type inconnu « {type_} »")
        except ValueError as erreur:
            return {"ligne": numero, "cle": cle, "decision": choix, "erreur": str(erreur)}
        return None

    for numero, ligne in enumerate(lignes, start=2):
        choix = (ligne.get("decision") or "").strip()
        if not choix:
            continue
        entree = (numero, (ligne.get("cle") or "").strip(), (ligne.get("type") or "").strip(), choix)
        # Un renvoi vers un autre dossier attend que sa cible soit tranchée ;
        # l'ordre des lignes du tableur ne décide de rien.
        if choix.startswith("groupe:"):
            reportees.append(entree)
            continue
        refus = appliquer(*entree)
        if refus:
            refusees.append(refus)

    while reportees:
        echecs = [entree for entree in reportees if appliquer(*entree)]
        if len(echecs) == len(reportees):
            refusees.extend(appliquer(*entree) for entree in echecs)
            break
        reportees = echecs
    return decisions, sorted(refusees, key=lambda r: r["ligne"])


def _dossier(index, cle, type_):
    if cle not in index:
        raise ValueError(f"{type_} « {cle} » absente du triage")
    return index[cle]


def _arbitrer_personne(decisions, personnes, groupes, cle, choix):
    dossier = _dossier(personnes, cle, "personne")
    if choix == "ignorer":
        decision = {"action": "ignore"}
    elif choix == "nouvelle":
        groupe = _slug(dossier["libelle"] + " " + str(dossier["annee"] or "sans-annee"))
        while groupe in groupes:
            groupe += "-bis"
        groupes.add(groupe)
        decision = {"action": "new", "group": groupe}
    elif choix.startswith("fiche:"):
        identifiant = choix.split(":", 1)[1].strip()
        if not identifiant.isdigit():
            raise ValueError("identifiant de fiche ERP non numérique")
        decision = {"action": "participant", "participant_id": int(identifiant)}
    elif choix.startswith("groupe:"):
        cible = _dossier(personnes, choix.split(":", 1)[1].strip(), "personne")
        modele = cible["decision"] or decisions["participants"].get((cible["lignes"] or [None])[0])
        if not modele:
            raise ValueError("le dossier cible n'a pas encore de décision : le trancher d'abord")
        decision = dict(modele)
        decision.pop("values", None)
        for source in cible["lignes"]:
            decisions["participants"][source] = dict(modele)
    else:
        raise ValueError("décision personne non reconnue : " + AIDE_DECISION["personne"])
    for source in dossier["lignes"]:
        decisions["participants"][source] = dict(decision)


def _arbitrer_seance(decisions, seances, cle, choix):
    _dossier(seances, cle, "séance")
    if choix == "ignorer":
        decisions["sessions"][cle] = {"action": "ignore"}
        return
    try:
        date.fromisoformat(choix)
    except ValueError:
        raise ValueError("décision séance non reconnue : " + AIDE_DECISION["seance"])
    decisions["sessions"][cle] = {"action": "date", "date_session": choix}


def _arbitrer_activite(decisions, activites, cle, choix):
    dossier = _dossier(activites, cle, "activité")
    if choix == "ignorer":
        decisions["activities"][cle] = {"action": "ignore"}
    elif choix == "nouvelle":
        decision = {"action": "new"}
        if dossier["secteur_propose"]:
            decision["secteur"] = dossier["secteur_propose"]
        decisions["activities"][cle] = decision
    elif choix.startswith("atelier:"):
        identifiant = choix.split(":", 1)[1].strip()
        if not identifiant.isdigit():
            raise ValueError("identifiant d'atelier non numérique")
        decisions["activities"][cle] = {"action": "existing", "atelier_id": int(identifiant)}
    else:
        decisions["activities"][cle] = {"secteur": choix}


# --- Rapport lisible ------------------------------------------------------

def rapport_markdown(triage):
    """Compte rendu privé : ce qui est proposé, ce qui reste à trancher."""
    resume, lignes = triage["resume"], []
    source = triage.get("source") or {}
    lignes += [
        "# Triage de la migration historique",
        "",
        f"Classeur : `{source.get('filename', '?')}` — empreinte `{str(source.get('sha256'))[:16]}…`",
        f"Rapport d'analyse : `{str(triage.get('rapport_digest'))[:16]}…` — "
        f"base : `{str(triage.get('base_digest'))[:16]}…`",
        "",
        "Ce document ne décide rien : il regroupe les lignes du classeur en dossiers et "
        "propose les décisions que les preuves du fichier rendent évidentes. "
        "Tout reste à relire avant l'import.",
        "",
        "## Ce que le triage absorbe",
        "",
        "| Objet | Total | Proposé | À trancher |",
        "|---|---:|---:|---:|",
        f"| Lignes de personnes | {resume['lignes_source']} | "
        f"{resume['lignes_source'] - resume['lignes_a_arbitrer']} | {resume['lignes_a_arbitrer']} |",
        f"| Dossiers de personnes | {resume['dossiers_personnes']} | "
        f"{resume['dossiers_personnes'] - resume['personnes_a_arbitrer']} | {resume['personnes_a_arbitrer']} |",
        f"| Séances à valider | {resume['seances_a_valider']} | {resume['seances_datees']} | "
        f"{resume['seances_a_arbitrer']} |",
        f"| Activités | {resume['activites']} | {resume['activites_avec_secteur']} | "
        f"{resume['activites_a_arbitrer']} |",
        "",
        f"Détail des personnes : {resume['personnes_rattachees']} dossiers rattachés à une fiche ERP "
        f"({resume['lignes_rattachees']} lignes), {resume['personnes_regroupees']} regroupements internes "
        f"({resume['lignes_regroupees']} lignes), {resume['personnes_creees']} créations sans candidat, "
        f"{resume['personnes_a_arbitrer']} dossiers à trancher "
        f"({resume['presences_a_arbitrer']} présences concernées).",
        "",
    ]

    arbitrages = [d for d in triage["personnes"] if d["statut"] == "arbitrer"]
    motifs = Counter(d["categorie"] for d in arbitrages)
    lignes += ["## Personnes à trancher", "", "| Motif | Dossiers |", "|---|---:|"]
    lignes += [f"| {motif} | {nombre} |" for motif, nombre in motifs.most_common()]
    lignes += ["", "| Dossier | Lignes | Présences | Année | Motif |", "|---|---:|---:|---|---|"]
    for dossier in sorted(arbitrages, key=lambda d: (-d["presences"], d["libelle"])):
        lignes.append(f"| {dossier['libelle']} | {len(dossier['lignes'])} | {dossier['presences']} | "
                      f"{dossier['annee'] or '?'} | {dossier['categorie']} — {dossier['motif']} |")

    lignes += ["", "## Séances", ""]
    for dossier in triage["seances"]:
        if dossier["statut"] == "dater":
            lignes.append(f"- `{dossier['cle']}` → **{dossier['decision']['date_session']}** "
                          f"(saisie {dossier['jour_saisi']!r}, {dossier['jour_semaine']!r}) — {dossier['motif']}")
    lignes += ["", "Restent à trancher :", ""]
    for dossier in triage["seances"]:
        if dossier["statut"] == "arbitrer":
            lignes.append(f"- `{dossier['cle']}` — saisie {dossier['jour_saisi']!r}, "
                          f"jour {dossier['jour_semaine']!r} → {' ou '.join(dossier['options']) or 'aucune piste'}")

    lignes += ["", "## Activités et secteurs", "",
               "Propositions déduites du nom métier : à relire une par une, "
               "le nom d'une feuille ne prouve pas son secteur.", "",
               "| Activité | Secteur proposé | Confiance | Remarque |", "|---|---|---|---|"]
    for dossier in sorted(triage["activites"], key=lambda d: d["nom"]):
        voisines = ", ".join(f"proche de « {v['nom']} »" for v in dossier["voisines"])
        remarque = dossier["motif"] if dossier["statut"] == "arbitrer" else voisines
        lignes.append(f"| {dossier['nom']} | {dossier['secteur_propose'] or '—'} | "
                      f"{dossier['confiance']} | {remarque} |")
    lignes += ["", f"Décisions écrites : {resume['decisions_participants']} lignes de personnes, "
               f"{resume['decisions_seances']} séances, {resume['decisions_activites']} activités.", ""]
    return "\n".join(lignes) + "\n"
