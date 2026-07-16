"""Contenu de l'aide intégrée.

Deux structures alimentent toute l'aide de l'application :

- ``AIDE_PAGES`` : aide contextuelle par page. La clé est le nom de
  l'endpoint Flask (ex: "main.dashboard"). Le gabarit ``layout.html``
  affiche automatiquement un bandeau « Comprendre cette page » sur
  chaque page référencée ici — aucun template à modifier.
- ``NOTICE`` : la notice complète (centre d'aide /aide), organisée en
  chapitres. C'est le mode d'emploi de référence, imprimable.

Un test (tests/test_aide.py) garantit que chaque clé de AIDE_PAGES
correspond à une vraie page de l'application : impossible de laisser
l'aide diverger du logiciel sans casser la CI.

Règles d'écriture (public : débutant complet) :
- vouvoiement, phrases courtes, pas de jargon technique ;
- dire d'abord À QUOI SERT la page, puis COMMENT s'en servir ;
- une astuce concrète quand c'est utile.
"""

AIDE_PAGES: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Accueil et navigation
    # ------------------------------------------------------------------
    "main.dashboard": {
        "titre": "Le tableau de bord",
        "resume": "C'est votre page d'accueil : elle rassemble vos actions du jour, les points à traiter et les chiffres clés.",
        "etapes": [
            "Consultez les indicateurs pour voir l'activité de la structure d'un coup d'œil.",
            "Utilisez les boutons d'action rapide pour vos tâches courantes.",
            "La barre de recherche en haut retrouve une personne, un projet ou une subvention en quelques lettres.",
        ],
        "astuce": "Le bouton « Personnaliser » vous permet de choisir les indicateurs et raccourcis affichés. Le mode Simple/Expert (en haut à droite) allège ou enrichit l'affichage.",
    },
    "main.guides_liste": {
        "titre": "Les guides pas à pas",
        "resume": "Un guide vous emmène sur les bonnes pages, dans le bon ordre, avec une explication à chaque étape.",
        "etapes": [
            "Choisissez un guide et cliquez sur « Démarrer ».",
            "Suivez le bandeau en haut de page : il vous dit quoi faire, étape par étape.",
            "Vous pouvez quitter un guide à tout moment et le reprendre plus tard.",
        ],
        "astuce": "Le glossaire (bouton en haut de la page) explique tous les mots du métier : parfait pour un nouvel arrivant.",
    },
    "main.glossaire": {
        "titre": "Le glossaire",
        "resume": "Tous les mots du métier et de l'application, expliqués simplement — le dico du social.",
        "etapes": [
            "Tapez un mot dans la barre de recherche pour le retrouver instantanément.",
            "Chaque terme indique aussi où le retrouver dans l'application.",
        ],
        "astuce": "Le bouton Imprimer permet d'en faire un livret d'accueil pour un nouveau salarié ou bénévole.",
    },
    "activite.saisie_grille": {
        "titre": "La saisie en grille",
        "resume": "Pour rattraper les feuilles d'émargement papier : un mois entier de présences en cases à cocher.",
        "etapes": [
            "Choisissez l'atelier puis le mois de la feuille papier.",
            "Cochez les présents (participants en lignes, dates en colonnes), puis Enregistrer.",
            "Si une séance ou une personne manque, ajoutez-la depuis les formulaires sous la grille.",
        ],
        "astuce": "Les cases 🔒 sont des présences signées au kiosque ou à statut particulier : elles ne se modifient que depuis la feuille d'émargement détaillée.",
    },
    "activite.emargements_attente": {
        "titre": "Les émargements en attente",
        "resume": "Toutes les séances passées sans présence saisie : la feuille n'est pas revenue, ou pas encore saisie.",
        "etapes": [
            "Les ateliers les plus en retard sont en haut de la liste.",
            "« Saisir (grille) » ouvre directement le bon atelier et le bon mois.",
            "« Relancer » pose un rappel dans « À traiter » pour courir après la feuille.",
        ],
        "astuce": "Une séance qui n'a réellement accueilli personne peut être annulée depuis sa page : elle sortira de cette liste.",
    },
    "main.mon_agenda": {
        "titre": "Synchroniser mon agenda",
        "resume": "Abonnez votre agenda (Google, Apple, Outlook) à vos séances : ajouté une fois, il se met à jour tout seul.",
        "etapes": [
            "Copiez votre lien personnel.",
            "Dans Google Agenda, « Autres agendas » → + → « À partir de l'URL » → collez le lien.",
            "Vos séances apparaissent ; Google les rafraîchit ensuite automatiquement (comptez quelques heures, ce n'est pas instantané).",
        ],
        "astuce": "Le lien est secret et ne contient aucun nom de participant. En cas de doute, régénérez-le : l'ancien cesse aussitôt de fonctionner.",
    },
    "main.impayes": {
        "titre": "Les impayés",
        "resume": "La liste de toutes les cotisations non soldées de l'année scolaire, la plus grosse ardoise en premier.",
        "etapes": [
            "Choisissez l'année scolaire (et le secteur si vous avez la vue globale).",
            "Le total à recouvrer est affiché en haut.",
            "Cliquez sur une personne pour ouvrir sa fiche : c'est là qu'on enregistre un règlement ou une relance.",
        ],
        "astuce": "Sur la fiche, le bouton « Relancer » pose un rappel dans « À traiter » ; le bouton « 🧾 Reçu » imprime une preuve de règlement pour la famille.",
    },
    "main.caisse": {
        "titre": "La caisse",
        "resume": "La boîte physique des espèces et chèques : les encaissements y entrent tout seuls, ici on compte et on dépose.",
        "etapes": [
            "Réglez d'abord le fond de caisse (la somme gardée en permanence pour rendre la monnaie).",
            "Comptez régulièrement : saisissez le total, l'application calcule l'écart et le trace.",
            "Pour un dépôt en banque : espèces au-dessus du fond + chèques, puis imprimez le bordereau.",
        ],
        "astuce": "Le guide « Je compte la caisse et je fais le dépôt » (page Guides) accompagne toute l'opération, pensé pour une première fois.",
    },
    "main.parcours_metier": {
        "titre": "Les parcours par métier",
        "resume": "Cette page part de votre situation réelle — accueil, animation, coordination, direction, finance ou administration — et propose les bons raccourcis.",
        "etapes": [
            "Repérez la carte qui correspond à votre rôle du moment.",
            "Cliquez sur l’action principale en vert pour démarrer rapidement.",
            "Utilisez les autres boutons pour accéder aux tâches liées.",
        ],
        "astuce": "Une même personne peut utiliser plusieurs cartes selon sa journée : accueil le matin, bilan l’après-midi.",
    },
    "main.assistant_bilan_financeur": {
        "titre": "L’assistant bilan financeur",
        "resume": "Une checklist pour préparer un dossier financeur sans oublier les pièces et données principales.",
        "etapes": [
            "Choisissez l’année puis la subvention à préparer.",
            "Vérifiez chaque point de la checklist : financeur, budget réel, dépenses et échéance.",
            "Ouvrez les documents prêts ou la qualité bilan pour finaliser.",
        ],
        "astuce": "Utilisez cette page quelques semaines avant l’échéance : elle sert à anticiper, pas seulement à exporter au dernier moment.",
    },
    "main.qualite_bilans": {
        "titre": "La qualité des données pour les bilans",
        "resume": "Cette page trie les anomalies selon leur impact sur les bilans : bloquant, important ou confort.",
        "etapes": [
            "Commencez toujours par les points bloquants.",
            "Traitez ensuite les points importants pour fiabiliser les chiffres.",
            "Gardez les points de confort pour les périodes plus calmes.",
        ],
        "astuce": "Cette vue est faite pour une réunion de préparation bilan : elle aide à répartir les corrections entre collègues.",
    },
    "main.direction_pilotage": {
        "titre": "Le pilotage direction",
        "resume": "Cette page rassemble les chiffres que la direction suit régulièrement : publics, présences, budget, bénévolat, alertes et échéances.",
        "etapes": [
            "Choisissez l’année et, si besoin, un secteur.",
            "Lisez d’abord les points de vigilance : ce sont les sujets à traiter ou à arbitrer.",
            "Utilisez les raccourcis pour ouvrir les documents, la qualité des données ou le support comité/CA.",
        ],
        "astuce": "Cette page ne remplace pas les bilans détaillés : elle sert de tableau de bord de décision rapide.",
    },
    "main.comite_pilotage": {
        "titre": "Le support comité / CA",
        "resume": "Une synthèse imprimable, sans données nominatives, pour préparer un comité de pilotage ou un conseil d’administration.",
        "etapes": [
            "Vérifiez les chiffres clés et les points de vigilance.",
            "Ajoutez vos commentaires oralement ou dans le compte rendu de réunion.",
            "Utilisez le bouton Imprimer / PDF pour partager le support.",
        ],
        "astuce": "Gardez cette vue courte : elle doit aider à décider, pas remplacer tous les tableaux détaillés.",
    },
    "main.journal_metier": {
        "titre": "Le journal métier",
        "resume": "Cette page retrace les actions importantes de l’application dans un langage plus lisible que le journal technique.",
        "etapes": [
            "Filtrez par type d’action ou par cible si vous cherchez un dossier précis.",
            "Regardez qui a fait l’action et à quelle date.",
            "Passez à la vue technique seulement si vous devez diagnostiquer finement un événement.",
        ],
        "astuce": "C’est un outil de confiance : il sert à comprendre ce qui s’est passé, pas à surveiller le travail quotidien.",
    },
    "admin.matrice_droits": {
        "titre": "La matrice des droits",
        "resume": "Une vue simple des permissions par rôle pour décider qui peut voir, modifier ou administrer les données sensibles.",
        "etapes": [
            "Lisez les permissions par grande famille : publics, activités, finances, bilans, ressources sensibles.",
            "Une coche indique que le rôle possède ce droit.",
            "Cliquez sur Modifier les droits pour ajuster les rôles si nécessaire.",
        ],
        "astuce": "Validez cette matrice en équipe de direction avant d’ouvrir de nouveaux accès.",
    },
    "main.dashboard_customize": {
        "titre": "Personnaliser votre tableau de bord",
        "resume": "Choisissez ici ce que VOUS voulez voir en arrivant dans l'application.",
        "etapes": [
            "Cochez les indicateurs et raccourcis qui vous servent au quotidien.",
            "Enregistrez : ce réglage n'affecte que votre compte, pas celui de vos collègues.",
        ],
        "astuce": "Le bouton « Réinitialiser » revient à l'affichage standard si vous êtes perdu.",
    },
    "main.global_search_results": {
        "titre": "La recherche globale",
        "resume": "Une seule barre de recherche pour tout retrouver : personnes, projets, subventions, ateliers, partenaires, quartiers.",
        "etapes": [
            "Tapez un nom (même partiel) : les résultats sont classés par type.",
            "Cliquez sur un résultat pour ouvrir sa fiche.",
        ],
        "astuce": "Vous pouvez préciser le type : « participants dupont » ne cherchera que dans les personnes.",
    },
    "main.suivi_rappels": {
        "titre": "À traiter — vos rappels et alertes",
        "resume": "Cette page rassemble tout ce qui demande votre attention : rappels, orientations en attente, échéances de projets et de budgets.",
        "etapes": [
            "Les éléments sont classés du plus urgent (rouge) au moins urgent.",
            "Cliquez sur un élément pour ouvrir la fiche concernée et agir.",
            "Créez vos propres rappels avec le bouton « Nouveau rappel » : ils apparaîtront ici à l'échéance.",
        ],
        "astuce": "Consultez cette page chaque matin : c'est votre liste de choses à faire.",
    },
    "main.hub_publics": {
        "titre": "L'espace Publics",
        "resume": "Le point d'entrée vers tout ce qui concerne les personnes : participants, orientations, quartiers.",
        "etapes": ["Choisissez une carte pour accéder au module correspondant."],
    },
    "main.hub_activites": {
        "titre": "L'espace Activités",
        "resume": "Le point d'entrée vers les ateliers, les séances, l'émargement et les projets.",
        "etapes": ["Choisissez une carte pour accéder au module correspondant."],
    },
    "main.hub_bilans": {
        "titre": "L'espace Bilans",
        "resume": "Le point d'entrée vers les statistiques, les bilans financiers et le bilan SENACS.",
        "etapes": ["Choisissez une carte pour accéder au module correspondant."],
    },
    "main.hub_ressources": {
        "titre": "L'espace Ressources",
        "resume": "Le point d'entrée vers les partenaires, questionnaires, inventaires et le matériel.",
        "etapes": ["Choisissez une carte pour accéder au module correspondant."],
    },
    "main.documents_exports": {
        "titre": "Documents prêts à transmettre",
        "resume": "Retrouvez ici, par année, les exports et documents imprimables : listes, bilans, fiches.",
        "etapes": [
            "Choisissez l'année en haut de la page.",
            "Cliquez sur un document pour le télécharger ou l'imprimer.",
        ],
    },

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------
    "participants.list_participants": {
        "titre": "L'annuaire des participants",
        "resume": "Toutes les personnes accueillies par la structure, tous secteurs confondus. Une personne = une seule fiche, même si elle fréquente plusieurs secteurs.",
        "etapes": [
            "Recherchez par nom dans la barre de recherche.",
            "Cliquez sur une personne pour ouvrir sa fiche complète.",
            "« Nouveau participant » crée une fiche : vérifiez d'abord que la personne n'existe pas déjà !",
        ],
        "astuce": "Une fiche en double fausse toutes les statistiques. En cas de doute, cherchez d'abord, créez ensuite. La page « Doublons » aide à fusionner les fiches créées en double.",
    },
    "participants.new_participant": {
        "titre": "Créer une fiche participant",
        "resume": "Enregistrez une nouvelle personne. Seuls le nom et le prénom sont obligatoires, mais chaque information compte pour les bilans.",
        "etapes": [
            "Renseignez au minimum nom et prénom.",
            "La date de naissance et le quartier servent aux statistiques officielles (CAF/SENACS) : renseignez-les dès que possible.",
            "Posez la question du droit à l'image et enregistrez la réponse : votre nom et la date seront tracés comme preuve.",
        ],
        "astuce": "Une fiche complète aujourd'hui = un bilan SENACS juste en mars. Les fiches incomplètes sont signalées dans « Qualité des données ».",
    },
    "participants.edit_participant": {
        "titre": "La fiche participant",
        "resume": "Toutes les informations d'une personne : identité, coordonnées, quartier, droit à l'image, parcours.",
        "etapes": [
            "Modifiez les champs puis « Enregistrer ».",
            "La section « Droit à l'image » trace qui a recueilli la réponse et quand : c'est votre preuve RGPD.",
            "« Exporter toutes les données » génère le dossier complet à remettre à la personne si elle le demande (obligation légale, délai d'un mois).",
        ],
        "astuce": "L'anonymisation est définitive : elle efface l'identité mais conserve les chiffres pour les statistiques. À utiliser si la personne demande l'effacement de ses données.",
    },
    "participants.duplicates": {
        "titre": "Fusionner les fiches en double",
        "resume": "Quand une personne a été créée deux fois, cette page permet de fusionner les fiches sans perdre l'historique.",
        "etapes": [
            "Vérifiez que les fiches proposées concernent bien la même personne.",
            "Choisissez la fiche à conserver : les présences et données de l'autre y seront rattachées.",
        ],
        "astuce": "En cas de doute (homonymes !), comparez les dates de naissance avant de fusionner.",
    },

    # ------------------------------------------------------------------
    # Activités / ateliers / émargement
    # ------------------------------------------------------------------
    "activite.index": {
        "titre": "Les ateliers et activités",
        "resume": "La liste des ateliers de votre secteur : ateliers réguliers, accompagnements individuels, événements.",
        "etapes": [
            "Cliquez sur un atelier pour voir ses séances.",
            "« Nouvelle activité » crée un atelier ; vous ajouterez ensuite ses séances.",
            "La corbeille conserve les ateliers supprimés : rien n'est perdu par erreur.",
        ],
        "astuce": "Renseignez les horaires des séances : ils alimentent les « heures face public » du bilan SENACS.",
    },
    "activite.sessions": {
        "titre": "Les séances d'un atelier",
        "resume": "Chaque date d'atelier est une séance. C'est depuis ici que vous faites l'émargement (la liste de présence).",
        "etapes": [
            "« Nouvelle séance » pour ajouter une date.",
            "Cliquez sur « Émargement » pour pointer les présents.",
            "Les séances annulées peuvent être marquées comme telles plutôt que supprimées.",
        ],
    },
    "activite.emargement": {
        "titre": "L'émargement (liste de présence)",
        "resume": "Pointez les personnes présentes à la séance. C'est LA donnée qui alimente toutes vos statistiques de fréquentation.",
        "etapes": [
            "Cochez les présents parmi les inscrits, ou ajoutez une personne via la recherche.",
            "Une personne absente de l'annuaire ? Créez sa fiche d'abord (bouton dédié).",
            "La feuille peut être signée sur écran (tablette) ou imprimée puis archivée.",
        ],
        "astuce": "Un émargement fait le jour même est fiable ; un émargement reconstitué de mémoire en fin de mois ne l'est pas. Les présences comptent pour les bilans CAF.",
    },
    "activite.participants": {
        "titre": "Les participants de votre secteur",
        "resume": "La vue « secteur » de l'annuaire : les personnes qui fréquentent vos activités.",
        "etapes": [
            "Cliquez sur une personne pour modifier sa fiche.",
            "Générez une attestation de participation depuis la fiche.",
        ],
    },
    "activite.attestations": {
        "titre": "Les attestations de participation",
        "resume": "Générez des attestations officielles (présence, participation) en série pour un groupe de personnes.",
        "etapes": [
            "Sélectionnez les personnes concernées.",
            "Choisissez les options du document puis générez : un fichier Word par personne.",
        ],
    },
    "activite.emargement_models": {
        "titre": "Les modèles de feuilles d'émargement",
        "resume": "Personnalisez l'apparence des feuilles d'émargement imprimées (logo, mentions, colonnes).",
        "etapes": [
            "Créez un modèle par type de besoin (atelier régulier, événement…).",
            "Le modèle choisi s'applique au moment d'imprimer une feuille.",
        ],
    },

    # ------------------------------------------------------------------
    # Projets
    # ------------------------------------------------------------------
    "projets.projets_list": {
        "titre": "Les projets",
        "resume": "Un projet = une action structurée avec des objectifs, un budget et des indicateurs (ex : CLAS, FLE, projet jardin).",
        "etapes": [
            "Cliquez sur un projet pour ouvrir son dossier complet.",
            "« Nouveau projet » : donnez un nom et un secteur, vous compléterez ensuite.",
        ],
        "astuce": "Reliez vos ateliers aux projets : les présences alimenteront automatiquement les indicateurs du projet.",
    },
    "projets.projets_edit": {
        "titre": "Le dossier d'un projet",
        "resume": "Tout le projet en un seul endroit : description, actions, journal de bord, indicateurs, budget, bilans.",
        "etapes": [
            "Les onglets du haut naviguent entre les volets du projet.",
            "Le journal de bord garde la mémoire des faits marquants : remplissez-le au fil de l'eau, il nourrira vos bilans.",
            "« Indicateurs » mesure l'avancement ; « Budget » suit les charges et produits.",
        ],
    },
    "projets.finance_home": {
        "titre": "Le pilotage financier",
        "resume": "La vue d'ensemble financière : où en sont les budgets, secteur par secteur.",
        "etapes": [
            "Choisissez votre secteur et l'année.",
            "La vue « simple » va à l'essentiel ; la vue détaillée montre toutes les lignes.",
        ],
    },
    "projets.finance_secteur": {
        "titre": "Le pilotage financier du secteur",
        "resume": "Le tableau de bord financier annuel de votre secteur : subventions, dépenses, restes à consommer.",
        "etapes": [
            "Vérifiez les alertes en haut de page : elles signalent les incohérences à corriger.",
            "L'export Excel produit le dossier financier annuel complet.",
        ],
    },

    # ------------------------------------------------------------------
    # Subventions et dépenses
    # ------------------------------------------------------------------
    "main.subventions_list": {
        "titre": "Les subventions",
        "resume": "Chaque financement reçu (CAF, Ville, État…) est une subvention, découpée en lignes de budget.",
        "etapes": [
            "Cliquez sur une subvention pour ouvrir son pilotage détaillé.",
            "« Nouvelle subvention » : nom, secteur, année et montants.",
        ],
        "astuce": "Le « demandé » est ce que vous avez sollicité, l'« attribué » ce qui est accordé, le « reçu » ce qui est arrivé sur le compte. Les trois peuvent différer !",
    },
    "main.subvention_pilotage": {
        "titre": "Le pilotage d'une subvention",
        "resume": "Suivez ligne par ligne l'utilisation d'un financement : prévu, dépensé, reste à consommer.",
        "etapes": [
            "Créez les lignes de budget correspondant aux postes de votre convention.",
            "Rattachez les dépenses aux lignes : le « consommé » se calcule tout seul.",
            "Le bilan de la subvention (bouton dédié) est votre justificatif pour le financeur.",
        ],
        "astuce": "Une dépense peut être répartie entre plusieurs lignes ou subventions (ventilation au prorata).",
    },
    "budget.depenses_list": {
        "titre": "Les dépenses",
        "resume": "Toutes les dépenses enregistrées, avec leur affectation aux subventions.",
        "etapes": [
            "« Nouvelle dépense » : montant, libellé, justificatif, et affectation à une ou plusieurs lignes de budget.",
            "Une dépense non affectée n'est justifiée auprès d'aucun financeur : la liste les signale.",
        ],
    },
    "budget.depense_new": {
        "titre": "Enregistrer une dépense",
        "resume": "Saisissez la dépense puis affectez-la aux budgets concernés : c'est ce qui permet de justifier l'usage des subventions.",
        "etapes": [
            "Renseignez le montant, la date et le libellé (et la pièce justificative si possible).",
            "Affectez la dépense à une ou plusieurs lignes de subvention.",
        ],
        "astuce": "Saisissez les dépenses au fil de l'eau : un bilan financier se prépare toute l'année, pas la veille de l'échéance.",
    },

    # ------------------------------------------------------------------
    # Bilans et statistiques
    # ------------------------------------------------------------------
    "main.stats": {
        "titre": "Les statistiques",
        "resume": "Les chiffres de fréquentation et d'activité, filtrables par année, secteur et projet.",
        "etapes": [
            "Réglez les filtres en haut puis parcourez les indicateurs.",
            "Ces chiffres viennent des émargements : leur fiabilité dépend du pointage des présences.",
        ],
    },
    "main.stats_bilans": {
        "titre": "Stats & bilans",
        "resume": "La vue de pilotage : indicateurs clés et accès aux différents bilans.",
        "etapes": ["Choisissez l'année et le périmètre, puis ouvrez le bilan souhaité."],
    },
    "main.bilan_global": {
        "titre": "Le bilan financeurs",
        "resume": "La synthèse financière globale de la structure, prête à présenter aux financeurs.",
        "etapes": [
            "Vérifiez l'année sélectionnée.",
            "L'export Excel produit le document à transmettre.",
        ],
    },
    "bilans.dashboard": {
        "titre": "Les bilans",
        "resume": "Le centre des bilans : par secteur, par subvention, bilans narratifs (« lourds ») et SENACS.",
        "etapes": ["Choisissez le type de bilan selon votre destinataire (financeur, CA, CAF)."],
    },
    "bilans.bilan_senacs": {
        "titre": "Le bilan SENACS (CAF)",
        "resume": "L'enquête annuelle nationale des centres sociaux. Cette page pré-calcule les chiffres demandés à partir de vos données.",
        "etapes": [
            "Choisissez l'année des données (campagne SENACS de l'année suivante).",
            "Vérifiez les alertes : fiches sans date de naissance, séances sans durée — corrigez-les pour fiabiliser les chiffres.",
            "Exportez les tableaux de consolidation et reportez-les dans senacs.fr.",
        ],
        "astuce": "Les participants sont automatiquement dédoublonnés entre secteurs : une personne qui fréquente trois ateliers compte une seule fois, comme l'exige l'enquête. Bénévolat, emplois et finances restent à saisir à la main.",
    },
    "main.qualite_donnees_transverse": {
        "titre": "La qualité des données",
        "resume": "Le détecteur de fiches incomplètes ou incohérentes : tout ce qui risque de fausser vos bilans.",
        "etapes": [
            "Parcourez les anomalies par famille (participants, projets, finances…).",
            "Cliquez sur une anomalie pour ouvrir la fiche et la corriger.",
            "Créez un rappel sur une anomalie pour la traiter plus tard.",
        ],
        "astuce": "Dix minutes par semaine sur cette page = des bilans annuels sans mauvaise surprise.",
    },
    "statsimpact.dashboard": {
        "titre": "Résultats et impact des activités",
        "resume": "Analyses détaillées de la fréquentation : démographie, assiduité, comparaisons entre périodes.",
        "etapes": [
            "Réglez les filtres (période, secteur, ateliers).",
            "Les graphiques se mettent à jour automatiquement.",
        ],
    },
    "statsimpact.exports": {
        "titre": "Exports des résultats et de l'impact",
        "resume": "Les exports Excel détaillés de la fréquentation, dont le classeur « par atelier ».",
        "etapes": [
            "Choisissez la période et le périmètre.",
            "Téléchargez l'export adapté à votre besoin.",
        ],
    },

    # ------------------------------------------------------------------
    # Administration et contrôle
    # ------------------------------------------------------------------
    "admin.users": {
        "titre": "Les comptes utilisateurs",
        "resume": "Créez les comptes de vos collègues et choisissez leur rôle : c'est le rôle qui détermine ce que chacun peut voir et faire.",
        "etapes": [
            "« Créer un compte » : email, mot de passe provisoire, rôle et secteur.",
            "Rôle « responsable_secteur » : accès limité à son secteur. Rôle « direction » : accès global complet. Rôle « admin_tech » : gestion technique sans les données métier.",
            "Un collègue qui part ? Supprimez ou désactivez son compte le jour même.",
        ],
        "astuce": "Donnez à chacun le rôle MINIMUM nécessaire : c'est la meilleure protection des données de vos publics.",
    },
    "main.controle": {
        "titre": "La page Contrôle",
        "resume": "Les outils de vérification et de configuration avancée de l'application.",
        "etapes": [
            "« Purge RGPD » gère l'anonymisation des fiches inactives.",
            "Le référentiel de consommation sert aux estimations d'impact énergétique.",
        ],
    },
    "main.purge_rgpd": {
        "titre": "La purge RGPD",
        "resume": "La loi interdit de garder indéfiniment les données d'une personne qui ne vient plus. Cette page anonymise automatiquement les fiches sans activité depuis 3 ans.",
        "etapes": [
            "Consultez la liste des fiches en attente d'anonymisation.",
            "Une personne listée fréquente encore la structure ? Ouvrez sa fiche et enregistrez-la : elle sort de la liste.",
            "La purge tourne toute seule chaque jour ; le bouton permet de la lancer immédiatement.",
        ],
        "astuce": "L'anonymisation conserve les chiffres (les présences passées comptent toujours dans les statistiques) mais efface définitivement l'identité.",
    },

    # ==================================================================
    # PHASE 2 — modules secondaires
    # ==================================================================

    # ---------------------- Projets (compléments) ----------------------
    "projets.projets_new": {
        "titre": "Créer un projet",
        "resume": "Donnez un nom et un secteur à votre projet ; vous compléterez ensuite ses actions, son budget et ses indicateurs.",
        "etapes": [
            "Renseignez le nom et le secteur porteur.",
            "Validez : vous arrivez sur le dossier du projet, à enrichir au fil de l'eau.",
        ],
    },
    "projets.finance_simple": {
        "titre": "Finance — vue simple",
        "resume": "Une vue allégée des finances de votre secteur, à l'essentiel : ce qui rentre, ce qui sort, ce qui reste.",
        "etapes": ["Choisissez l'année ; passez à la vue détaillée si vous avez besoin du détail ligne par ligne."],
    },

    # ---------------------- Pédagogie -----------------------------------
    "pedagogie.index": {
        "titre": "Suivi des apprentissages",
        "resume": "Le module pédagogique part d'une séance réelle : qu'est-ce qu'on travaille aujourd'hui, qui est là, et est-ce que les personnes progressent ?",
        "etapes": [
            "« Observer une séance » pour pointer les savoir-faire travaillés et la progression de chacun.",
            "Le passeport de compétences de chaque participant garde la trace de son parcours.",
        ],
        "astuce": "Inutile de tout remplir : choisissez 2 ou 3 savoir-faire par séance, c'est suffisant pour suivre une progression.",
    },
    "pedagogie.parcours_pedagogique": {
        "titre": "Observer une séance",
        "resume": "Pour une séance donnée, choisissez les savoir-faire travaillés puis notez où en est chaque participant.",
        "etapes": [
            "Sélectionnez la séance.",
            "Cochez les compétences travaillées ce jour-là.",
            "Pour chaque présent, indiquez le résultat (acquis, en cours, à revoir).",
        ],
    },
    "pedagogie.modules_pedagogiques": {
        "titre": "Les modules pédagogiques",
        "resume": "Un module regroupe des compétences qui vont ensemble (ex : « bases du numérique », « FLE niveau A1 »), pour les réutiliser facilement.",
        "etapes": ["Créez un module, puis rattachez-y les compétences des référentiels."],
    },
    "pedagogie.objectifs": {
        "titre": "Compétences et objectifs",
        "resume": "Définissez et suivez les objectifs de compétences, en lien avec les référentiels.",
        "etapes": ["Parcourez les compétences ; suivez leur progression à partir des observations de séances."],
    },
    "pedagogie.suivi_pedagogique": {
        "titre": "Le suivi pédagogique",
        "resume": "La vue d'ensemble de la progression des participants sur les compétences travaillées.",
        "etapes": ["Repérez qui progresse et qui a besoin d'un appui particulier."],
    },
    "pedagogie.pilotage_objectifs": {
        "titre": "Pilotage des objectifs (RA / PAG)",
        "resume": "La synthèse des objectifs pour vos documents officiels : Rapport d'Activité et Projet d'Animation Globale.",
        "etapes": ["Consultez les indicateurs agrégés ; exportez pour vos bilans."],
    },
    "pedagogie.plan_projet": {
        "titre": "Le plan de projet pédagogique",
        "resume": "Structurez les objectifs pédagogiques d'un projet et reliez-les aux compétences travaillées.",
        "etapes": ["Décrivez les objectifs ; rattachez-les aux modules et compétences."],
    },
    "pedagogie.referentiels_list": {
        "titre": "Les référentiels de compétences",
        "resume": "Les bibliothèques de compétences (numérique, FLE…) dans lesquelles vous piochez pour vos séances.",
        "etapes": [
            "Ouvrez un référentiel pour voir ses compétences.",
            "Vous pouvez importer des compétences en masse via un fichier CSV.",
        ],
    },
    "pedagogie.kiosk_pedagogique": {
        "titre": "Observation en mode kiosque",
        "resume": "Une saisie simplifiée des observations de séance, pensée pour une tablette en atelier.",
        "etapes": ["Sélectionnez la séance et notez les progrès au fil de l'atelier."],
    },

    # ---------------------- Partenaires & orientations ------------------
    "partenaires.index": {
        "titre": "L'annuaire des partenaires",
        "resume": "Tous vos partenaires (institutions, associations, écoles, bailleurs…) avec leurs domaines d'intervention.",
        "etapes": [
            "Cliquez sur un partenaire pour voir ou modifier sa fiche.",
            "« Nouveau partenaire » pour en ajouter un.",
            "Les domaines d'orientation servent à proposer le bon partenaire lors d'une orientation.",
        ],
    },
    "partenaires.create": {
        "titre": "Ajouter un partenaire",
        "resume": "Enregistrez un nouveau partenaire et ses domaines d'intervention.",
        "etapes": [
            "Renseignez le nom et les coordonnées.",
            "Cochez les domaines pour lesquels ce partenaire peut accueillir vos orientations.",
        ],
    },
    "partenaires.orientations": {
        "titre": "Les orientations (accès aux droits)",
        "resume": "Le suivi des personnes que vous orientez vers un partenaire ou un dispositif (logement, santé, emploi, administratif…).",
        "etapes": [
            "Filtrez par année, domaine ou statut.",
            "Mettez à jour le statut d'une orientation au fil de son avancement.",
            "Le tableau de bord en haut résume l'activité d'accès aux droits — utile pour le bilan SENACS.",
        ],
        "astuce": "L'accès aux droits est un indicateur valorisé par la CAF : tracez vos orientations, même informelles.",
    },

    # ---------------------- Quartiers -----------------------------------
    "quartiers.index": {
        "titre": "Les quartiers",
        "resume": "La liste des quartiers utilisés pour rattacher les participants et produire les statistiques de territoire.",
        "etapes": [
            "Ajoutez vos quartiers d'intervention.",
            "Marquez ceux classés en politique de la ville (QPV) : c'est un indicateur clé pour la CAF et la Ville.",
        ],
        "astuce": "Un quartier bien renseigné (avec son statut QPV) alimente automatiquement le bilan SENACS.",
    },
    "quartiers.stats": {
        "titre": "Statistiques par quartier",
        "resume": "La répartition de vos publics par quartier, avec le focus politique de la ville (QPV).",
        "etapes": ["Consultez les chiffres ; ils dépendent du quartier renseigné sur les fiches participants."],
    },

    # ---------------------- Questionnaires ------------------------------
    "questionnaires.index": {
        "titre": "Les questionnaires",
        "resume": "Créez des questionnaires (satisfaction, besoins, évaluation) à faire remplir lors des séances.",
        "etapes": [
            "Cliquez sur un questionnaire pour le modifier ou voir ses réponses.",
            "« Nouveau questionnaire » pour en créer un.",
        ],
    },
    "questionnaires.create": {
        "titre": "Créer un questionnaire",
        "resume": "Donnez un titre à votre questionnaire ; vous ajouterez ensuite ses questions.",
        "etapes": ["Renseignez le titre et le secteur, puis ajoutez les questions une à une."],
    },

    # ---------------------- Insertion -----------------------------------
    "insertion.index": {
        "titre": "Le module Insertion",
        "resume": "Le suivi du parcours d'insertion socio-professionnelle : titres de séjour, parcours, positionnements, certifications. Ces données sont sensibles et réservées aux agents habilités.",
        "etapes": [
            "Recherchez une personne pour ouvrir son dossier d'insertion.",
            "Renseignez son parcours au fil des étapes.",
        ],
        "astuce": "Ces informations sont protégées : seuls les comptes autorisés y accèdent. Ne les renseignez que si elles sont utiles à l'accompagnement.",
    },
    "insertion.referentiels_overview": {
        "titre": "Les référentiels Insertion",
        "resume": "Les listes de choix du module insertion (titres de séjour, niveaux, dispositifs, prescripteurs…), à tenir à jour.",
        "etapes": ["Ouvrez un référentiel pour ajouter, modifier ou retirer ses valeurs."],
    },

    # ---------------------- Budgets prévisionnels -----------------------
    "previsionnel.index": {
        "titre": "Les budgets demandés",
        "resume": "Préparez les budgets prévisionnels par secteur et par année, avant de solliciter vos financeurs.",
        "etapes": [
            "Créez un budget : secteur, année, et postes de dépenses/recettes (à partir du référentiel).",
            "Comparez ensuite le prévu au réalisé pour piloter en cours d'année.",
        ],
        "astuce": "Un budget prévisionnel sert de base à vos demandes de subvention : il se transforme en lignes réelles une fois le financement obtenu.",
    },
    "previsionnel.referentiel": {
        "titre": "Le référentiel budgétaire",
        "resume": "Les comptes, catégories et modèles de budget réutilisables pour construire vos prévisionnels rapidement.",
        "etapes": ["Maintenez vos comptes et catégories ; créez des modèles pour les budgets récurrents."],
    },

    # ---------------------- Factures & inventaire -----------------------
    "inventaire.factures_list": {
        "titre": "Les factures d'achat",
        "resume": "Enregistrez vos factures d'achat ; elles alimentent l'inventaire et le suivi des dépenses.",
        "etapes": [
            "« Nouvelle facture » pour en saisir une.",
            "Rattachez les articles achetés pour suivre votre matériel.",
        ],
    },
    "inventaire.facture_new": {
        "titre": "Saisir une facture d'achat",
        "resume": "Enregistrez une facture (fournisseur, montant, date) et le matériel acquis.",
        "etapes": ["Renseignez l'en-tête de la facture, puis ajoutez les lignes d'articles."],
    },
    "inventaire_materiel.list_items": {
        "titre": "L'inventaire du matériel",
        "resume": "Le parc de matériel de la structure : équipements, état, secteur de rattachement.",
        "etapes": [
            "Filtrez par secteur ou par état.",
            "« Ajouter » pour enregistrer un nouvel équipement.",
        ],
    },
    "inventaire_materiel.new_item": {
        "titre": "Ajouter du matériel",
        "resume": "Enregistrez un équipement dans l'inventaire.",
        "etapes": ["Renseignez le nom, l'état, le secteur et les informations utiles (valeur, date d'achat)."],
    },

    # ---------------------- Bilans (détail) -----------------------------
    "bilans.bilan_secteur": {
        "titre": "Le bilan par secteur",
        "resume": "La synthèse de l'activité et des finances d'un secteur sur une année.",
        "etapes": ["Choisissez l'année et le secteur ; exportez pour vos comptes-rendus."],
    },
    "bilans.bilan_subvention": {
        "titre": "Le bilan par subvention",
        "resume": "Le bilan détaillé d'un financement, prêt à justifier auprès du financeur.",
        "etapes": ["Sélectionnez la subvention et l'année ; vérifiez la cohérence avant export."],
    },
    "bilans.bilans_lourds": {
        "titre": "Les bilans narratifs",
        "resume": "Les bilans « lourds » : le récit de l'année par secteur (faits marquants, photos, frise), au-delà des chiffres.",
        "etapes": [
            "Rédigez le récit de l'année ; ajoutez photos et temps forts.",
            "Exportez en Word pour vos rapports d'activité.",
        ],
        "astuce": "Alimentez-les au fil de l'eau depuis le journal de bord des projets : vous gagnerez un temps précieux en fin d'année.",
    },
    "bilans.inventaire": {
        "titre": "Le bilan d'inventaire",
        "resume": "La synthèse du matériel et des achats sur l'année.",
        "etapes": ["Choisissez l'année pour voir l'état du parc et les acquisitions."],
    },
    "bilans.qualite": {
        "titre": "La qualité des données (bilans)",
        "resume": "Le contrôle de cohérence des données avant de produire vos bilans officiels.",
        "etapes": ["Corrigez les anomalies signalées pour fiabiliser vos chiffres."],
    },

    # ---------------------- Stats-impact (détail) -----------------------
    "statsimpact.stats_pedagogie": {
        "titre": "Stats-impact : pédagogie",
        "resume": "Les statistiques de progression pédagogique : compétences travaillées, acquis, par public.",
        "etapes": ["Réglez les filtres ; analysez les progressions."],
    },
    "statsimpact.qualite_donnees": {
        "titre": "Stats-impact : qualité des données",
        "resume": "Le détecteur d'anomalies propre au module stats-impact (séances, présences, durées).",
        "etapes": ["Corrigez les points signalés pour des statistiques fiables."],
    },

    # ---------------------- Administration (détail) ---------------------
    "admin.droits": {
        "titre": "Les droits d'accès",
        "resume": "L'écran avancé des droits : quels rôles existent, ce que chacun permet, et qui possède quel rôle.",
        "etapes": [
            "Attribuez un ou plusieurs rôles à un utilisateur.",
            "Modifiez finement les permissions d'un rôle si besoin.",
        ],
        "astuce": "Pour la gestion courante, l'écran « Utilisateurs » suffit. Ne touchez aux permissions des rôles que si vous savez ce que vous faites.",
    },
    "admin.secteurs": {
        "titre": "Les secteurs",
        "resume": "La liste des secteurs/pôles de la structure (Famille, Numérique, Santé…) utilisés partout dans l'application.",
        "etapes": ["Activez, renommez ou ajoutez vos secteurs selon votre organisation."],
        "astuce": "Modifier un secteur impacte toute l'application : faites-le avec précaution et de préférence en début d'année.",
    },
    "admin.instance_settings": {
        "titre": "Identité de la structure",
        "resume": "Le nom, les logos, l'adresse publique et la messagerie (SMTP) de votre structure.",
        "etapes": [
            "Renseignez le nom et les logos : ils apparaissent dans l'application et sur les documents.",
            "Configurez la messagerie (SMTP) pour activer l'envoi des emails (réinitialisation de mot de passe).",
        ],
    },
    "admin.referentiels": {
        "titre": "Listes de compétences",
        "resume": "Les listes de compétences utilisées par le suivi pédagogique.",
        "etapes": ["Consultez les listes et le nombre de compétences ; importez-en de nouvelles si besoin."],
    },
    "admin.import_excel": {
        "titre": "Import Excel des présences",
        "resume": "Importez en masse des présences depuis un fichier Excel, utile pour reprendre un historique existant.",
        "etapes": [
            "Choisissez le secteur et le fichier.",
            "Lancez d'abord un test (« simulation ») pour vérifier avant d'importer pour de bon.",
        ],
        "astuce": "Faites toujours une simulation d'abord : elle montre ce qui sera importé sans rien modifier.",
    },
    "admin.sauvegardes": {
        "titre": "Sauvegardes",
        "resume": "Créez une sauvegarde de la base de données et des pièces jointes, et consultez les sauvegardes existantes.",
        "etapes": [
            "Cliquez sur « Sauvegarder maintenant » : la sauvegarde est créée sur le serveur, dans le dossier backups/.",
            "Copiez régulièrement ce dossier sur un support externe (disque, clé, espace réseau) pour être protégé en cas de panne.",
        ],
        "astuce": "La sauvegarde reste sur le serveur : aucune donnée personnelle n'est téléchargée par le navigateur.",
    },

    # ---------------------- Pages de pilotage prioritaires -------------
    "quartiers.carte": {
        "titre": "La carte des habitants",
        "resume": "Visualisez la répartition des participants par quartier, sans afficher les adresses individuelles.",
        "etapes": [
            "Choisissez un secteur, un type de public ou une période.",
            "Cliquez sur une bulle pour consulter l'effectif du quartier.",
            "Si des habitants restent non localisés, vérifiez leur quartier ou leur adresse.",
        ],
        "astuce": "La taille d'une bulle représente un effectif, jamais un domicile précis.",
    },
    "partenaires.carte": {
        "titre": "La carte des partenaires",
        "resume": "Repérez les structures partenaires et leurs domaines d'intervention sur une carte.",
        "etapes": [
            "Filtrez la carte par secteur d'intervention.",
            "Cliquez sur un marqueur pour consulter le partenaire.",
            "Ouvrez sa fiche pour retrouver ses coordonnées et les orientations possibles.",
        ],
        "astuce": "Un partenaire absent de la carte a généralement une adresse manquante ou non reconnue.",
    },
    "admin.sante_systeme": {
        "titre": "L'état technique de l'application",
        "resume": "Vérifiez le fonctionnement de la base de données, des sauvegardes, de la messagerie et du portail.",
        "etapes": [
            "Repérez les éléments signalés en rouge ou comme non configurés.",
            "Créez une sauvegarde si la dernière est absente ou trop ancienne.",
            "Utilisez les boutons de test pour la messagerie et le portail.",
        ],
        "astuce": "Un voyant rouge ne bloque pas toujours l'application, mais il doit être vérifié rapidement.",
    },
    "admin.journal_audit": {
        "titre": "L'historique des actions sensibles",
        "resume": "Retrouvez les modifications importantes : droits, comptes, restaurations et exports de données personnelles.",
        "etapes": [
            "Filtrez par type d'action ou par élément concerné.",
            "Vérifiez la date, l'utilisateur et le détail de l'opération.",
            "Retirez les filtres pour revenir aux dernières actions.",
        ],
        "astuce": "Cette page aide à comprendre qui a effectué une action sensible et à quel moment.",
    },
    "main.controle_navigation": {
        "titre": "Vérifier les liens de l'application",
        "resume": "Repérez les liens ou pages qui risquent de ne pas fonctionner correctement.",
        "etapes": [
            "Cliquez sur « Relancer la vérification ».",
            "Traitez d'abord les éléments indiqués comme bloquants.",
            "Transmettez le nom du fichier et le message au responsable technique.",
        ],
        "astuce": "Cette vérification ne modifie aucune donnée métier.",
    },
    "previsionnel.generateur": {
        "titre": "Créer un budget demandé",
        "resume": "Préparez un budget complet en charges et produits, puis exportez-le ou rattachez-le à un dossier.",
        "etapes": [
            "Choisissez l'organisme, l'exercice et le secteur.",
            "Ajoutez ou adaptez les lignes jusqu'à équilibrer charges et produits.",
            "Exportez vers Excel ou envoyez le budget vers un projet ou une subvention.",
        ],
        "astuce": "Vos modifications sont conservées dans ce navigateur ; « Réinitialiser » revient au modèle de départ.",
    },
    "main.hart_collectif": {
        "titre": "La participation des habitants",
        "resume": "Visualisez le niveau de participation attribué aux habitants, de l'information à la décision partagée.",
        "etapes": [
            "Choisissez le secteur et la période à étudier.",
            "Activez la comparaison si vous souhaitez observer une évolution.",
            "Cliquez sur une marche pour voir les habitants concernés.",
        ],
        "astuce": "Le niveau résulte d'une évaluation humaine : il n'est pas calculé automatiquement.",
    },
    "main.benevolat": {
        "titre": "Le suivi du bénévolat",
        "resume": "Enregistrez les heures données par les habitants et calculez leur valorisation.",
        "etapes": [
            "Choisissez l'année et, si nécessaire, le secteur.",
            "Recherchez la personne puis saisissez la date, les heures et l'activité.",
            "Vérifiez les totaux avant d'imprimer ou d'exporter.",
        ],
        "astuce": "Ces heures alimentent le bilan SENACS et la valorisation comptable du bénévolat.",
    },
    "main.rh": {
        "titre": "Le suivi de l'équipe salariée",
        "resume": "Suivez les postes, contrats, temps de travail et coûts de l'équipe pour les bilans et les finances.",
        "etapes": [
            "Choisissez l'exercice à consulter.",
            "Ajoutez ou importez les salariés avec leur poste, leur secteur et leur contrat.",
            "Vérifiez les personnes sans secteur et mettez à jour les départs.",
        ],
        "astuce": "Mettez cette page à jour avant le bilan SENACS afin de fiabiliser les ETP.",
    },
    "main.dons_registre": {
        "titre": "Les dons et reçus fiscaux",
        "resume": "Enregistrez les dons et générez un reçu fiscal numéroté.",
        "etapes": [
            "Choisissez l'exercice puis renseignez le donateur et le don.",
            "Vérifiez les coordonnées de l'organisme avant de créer le reçu.",
            "Ouvrez le reçu pour l'imprimer ; en cas d'erreur, annulez-le.",
        ],
        "astuce": "Un reçu émis ne se supprime pas : son annulation reste visible dans le registre.",
    },
    "main.tarifs_cotisations": {
        "titre": "Les tarifs d'adhésion et de participation",
        "resume": "Définissez les montants appliqués aux nouvelles cotisations d'une année scolaire.",
        "etapes": [
            "Choisissez l'année scolaire.",
            "Ajoutez un montant et sa date de début pour chaque type de tarif.",
            "Vérifiez quel tarif est actuellement en vigueur.",
        ],
        "astuce": "Modifier un tarif ne change jamais les cotisations déjà créées.",
    },
    "main.cout_unitaire": {
        "titre": "Calculer le coût d'une action",
        "resume": "Rapprochez un financement de l'activité réalisée pour obtenir un coût par participant, présence, heure ou séance.",
        "etapes": [
            "Choisissez la période et les ateliers concernés.",
            "Saisissez un montant ou sélectionnez une subvention.",
            "Calculez puis exportez le résultat si vous devez le transmettre.",
        ],
        "astuce": "Le résultat dépend directement de la qualité des séances et des présences enregistrées.",
    },
    "participants.synthese_participant": {
        "titre": "La synthèse d'une personne",
        "resume": "Retrouvez au même endroit l'identité, les présences, les orientations et le parcours d'une personne.",
        "etapes": [
            "Consultez d'abord les indicateurs et les points d'attention.",
            "Parcourez les sections pour comprendre son activité.",
            "Utilisez « Modifier », « Insertion » ou « Passeport » pour compléter la fiche.",
        ],
        "astuce": "Seules les informations autorisées par votre rôle et votre secteur sont affichées.",
    },
    "main.tresorerie": {
        "titre": "Les subventions à recevoir",
        "resume": "Anticipez les versements attendus et repérez les encaissements en retard ou sans date prévue.",
        "etapes": [
            "Choisissez l'exercice à analyser.",
            "Consultez les versements attendus par mois et les alertes.",
            "Ouvrez les subventions concernées pour compléter les dates ou montants reçus.",
        ],
        "astuce": "Un versement sans date prévue ne peut pas apparaître dans le calendrier mensuel.",
    },
    "main.comparaison": {
        "titre": "Comparer deux années",
        "resume": "Comparez l'activité et les financements de l'année choisie avec l'année précédente.",
        "etapes": [
            "Choisissez l'année la plus récente à comparer.",
            "Lisez d'abord l'évolution globale, puis le détail par secteur.",
            "Exportez ou imprimez la comparaison pour la partager.",
        ],
        "astuce": "Avant de conclure, vérifiez que les deux années ont un niveau de saisie comparable.",
    },

    # ---------------------- Accès tablette / kiosque --------------------
    "launcher.index": {
        "titre": "Accès tablette & QR codes",
        "resume": "Les liens et QR codes pour ouvrir l'application ou le pointage depuis une tablette d'atelier.",
        "etapes": [
            "Scannez le QR code « Pointage tablette » pour ouvrir l'émargement public.",
            "Le pointage tablette permet aux participants de confirmer leur présence eux-mêmes, sans accéder au reste de l'application.",
        ],
    },
}


# ----------------------------------------------------------------------
# LA NOTICE COMPLÈTE (centre d'aide /aide)
# ----------------------------------------------------------------------
# Chaque chapitre : id (ancre), icone, titre, intro, sections [(titre, [paragraphes])]

NOTICE: list[dict] = [
    {
        "id": "demarrer",
        "icone": "🚀",
        "titre": "Démarrer avec l'application",
        "intro": "Ce qu'il faut savoir pour vos premiers pas.",
        "sections": [
            ("Se connecter", [
                "Ouvrez l'adresse de l'application dans votre navigateur (demandez-la à votre responsable). Saisissez votre email et votre mot de passe.",
                "Mot de passe oublié ? Cliquez sur le lien sous le formulaire : vous recevrez un email de réinitialisation (si la messagerie est configurée). Sinon, demandez à un administrateur.",
                "Après 5 mots de passe erronés, la connexion est bloquée 15 minutes par sécurité : c'est normal, attendez puis réessayez.",
            ]),
            ("Comprendre l'écran", [
                "En haut : le menu de navigation, organisé en espaces (Publics, Activités, Bilans, Ressources) ; la barre de recherche globale ; vos réglages d'affichage.",
                "Le bouton Simple/Expert change la densité de l'information : commencez en Simple, passez en Expert quand vous serez à l'aise.",
                "Le bouton « ? » (en haut à droite) ouvre cette notice à tout moment. Sur la plupart des pages, un bandeau « Comprendre cette page » explique le contexte.",
            ]),
            ("Les trois règles d'or", [
                "1. UNE personne = UNE fiche. Cherchez toujours avant de créer : les doublons faussent tous les bilans.",
                "2. Pointez les présences le jour même : l'émargement est la matière première de toutes vos statistiques.",
                "3. Complétez les fiches (date de naissance, quartier) : le bilan CAF de mars se prépare toute l'année.",
            ]),
        ],
    },
    {
        "id": "participants",
        "icone": "👥",
        "titre": "Les participants",
        "intro": "L'annuaire unique des personnes accueillies, partagé entre tous les secteurs.",
        "sections": [
            ("Créer et modifier une fiche", [
                "Espace Publics → Participants → Nouveau participant. Seuls nom et prénom sont obligatoires, mais chaque champ compte : la date de naissance alimente les tranches d'âge SENACS, le quartier alimente les statistiques de territoire (QPV).",
                "La fiche est partagée entre secteurs : si la personne fréquente déjà un autre secteur, sa fiche existe déjà — utilisez la recherche.",
            ]),
            ("Le droit à l'image (RGPD)", [
                "Demandez à la personne si elle accepte d'apparaître en photo/vidéo, et enregistrez sa réponse dans la section dédiée de sa fiche. La date et votre nom sont tracés automatiquement : c'est la preuve du recueil, exigée en cas de contrôle.",
                "Une personne peut changer d'avis à tout moment : mettez simplement à jour le champ.",
            ]),
            ("Le droit d'accès et l'effacement (RGPD)", [
                "Toute personne peut demander une copie de ses données : bouton « Exporter toutes les données » sur sa fiche. Vous avez un mois pour la remettre.",
                "Toute personne peut demander l'effacement : utilisez « Anonymiser » sur sa fiche. Son identité est effacée définitivement, mais les chiffres de fréquentation restent comptés.",
                "Les fiches sans activité depuis 3 ans sont anonymisées automatiquement (voir Administration → Purge RGPD).",
            ]),
            ("Les doublons", [
                "Si une personne a été créée deux fois, la page Doublons propose la fusion : l'historique des deux fiches est réuni sur celle que vous conservez.",
            ]),
        ],
    },
    {
        "id": "activites",
        "icone": "📅",
        "titre": "Ateliers, séances et émargement",
        "intro": "Le cœur du suivi d'activité : qui participe à quoi, et quand.",
        "sections": [
            ("Organiser un atelier", [
                "Un atelier est une activité (FLE, CLAS, atelier numérique, café couture…). Chaque date de cet atelier est une séance.",
                "Espace Activités → Nouvelle activité : nom, secteur, type (collectif ou individuel). Puis ajoutez les séances avec dates et horaires.",
                "Renseignez les horaires des séances : ils calculent les « heures face public » demandées par la CAF.",
            ]),
            ("Faire l'émargement", [
                "Ouvrez la séance → Émargement. Cochez les présents, ajoutez les nouveaux venus par la recherche.",
                "La feuille peut être signée à l'écran (tablette en mode kiosque) ou imprimée. Les feuilles archivées sont conservées et téléchargeables.",
                "C'est l'émargement qui crée les « participations » comptées dans tous les bilans : sa régularité fait la fiabilité de vos chiffres.",
            ]),
            ("Attestations", [
                "Espace Activités → Attestations : sélectionnez des personnes et générez leurs attestations de participation (documents Word personnalisables).",
            ]),
        ],
    },
    {
        "id": "projets",
        "icone": "🎯",
        "titre": "Les projets",
        "intro": "Structurer une action : objectifs, journal de bord, indicateurs, budget.",
        "sections": [
            ("Créer un projet", [
                "Espace Activités → Projets → Nouveau projet. Un projet rassemble des actions, un budget (charges/produits), des indicateurs de réussite et un journal de bord.",
                "Reliez vos ateliers au projet : les présences alimenteront automatiquement ses indicateurs.",
            ]),
            ("Suivre et rendre compte", [
                "Le journal de bord conserve les faits marquants, réussites et difficultés : remplissez-le au fil de l'eau, c'est la matière de vos bilans narratifs.",
                "La fiche projet imprimable et le bilan d'action servent de support aux comités de pilotage et aux financeurs.",
            ]),
        ],
    },
    {
        "id": "finances",
        "icone": "💶",
        "titre": "Subventions, dépenses et pilotage financier",
        "intro": "Suivre chaque financement et justifier son utilisation.",
        "sections": [
            ("Les subventions", [
                "Une subvention = un financement reçu (CAF, Ville, Département…), pour un secteur et une année. Trois montants la décrivent : demandé (sollicité), attribué (accordé), reçu (versé).",
                "Le pilotage d'une subvention se fait ligne par ligne : créez les lignes de budget correspondant à votre convention (salaires, activités, fonctionnement…).",
            ]),
            ("Les dépenses", [
                "Enregistrez chaque dépense puis affectez-la aux lignes de budget concernées. Une dépense peut être répartie entre plusieurs subventions (ventilation).",
                "Le « consommé » et le « reste à consommer » de chaque ligne se calculent automatiquement.",
            ]),
            ("Les bilans financiers", [
                "Le bilan d'une subvention (depuis sa page de pilotage) justifie l'utilisation du financement auprès du financeur.",
                "Le pilotage financier du secteur donne la vue annuelle complète, avec alertes d'incohérence et export Excel.",
                "Le bilan financeurs global (espace Bilans) présente la synthèse de toute la structure.",
            ]),
        ],
    },
    {
        "id": "bilans",
        "icone": "📊",
        "titre": "Statistiques et bilans",
        "intro": "Des émargements aux bilans officiels.",
        "sections": [
            ("D'où viennent les chiffres ?", [
                "Toutes les statistiques découlent des fiches participants et des émargements. Une fiche incomplète ou une présence non pointée = un chiffre faux.",
                "La page « Qualité des données » liste tout ce qui mérite correction : consultez-la régulièrement.",
            ]),
            ("Le bilan SENACS (CAF)", [
                "Espace Bilans → Bilan SENACS : l'application pré-calcule les publics (participants uniques dédoublonnés entre secteurs, tranches d'âge officielles, quartiers), le tableau actions/séances et la liste des partenaires.",
                "L'export Excel reprend vos tableaux de consolidation : il reste à saisir les chiffres dans senacs.fr, et à compléter à la main ce que l'application ne gère pas (bénévolat, emplois, finances, événementiel).",
            ]),
            ("Stats-impact", [
                "Pour aller plus loin : analyses démographiques, assiduité, comparaisons de périodes, exports détaillés par atelier.",
            ]),
        ],
    },
    {
        "id": "rappels",
        "icone": "🔔",
        "titre": "Rappels et suivi quotidien",
        "intro": "Ne rien laisser passer.",
        "sections": [
            ("La page « À traiter »", [
                "Elle rassemble vos rappels, les orientations en attente, les échéances de projets et de budgets. Consultez-la chaque matin.",
                "Créez un rappel sur n'importe quel sujet avec une échéance : il remontera au bon moment, classé par urgence.",
            ]),
        ],
    },
    {
        "id": "pedagogie",
        "icone": "🎓",
        "titre": "Pédagogie et passeport de compétences",
        "intro": "Suivre ce que les personnes apprennent, séance après séance.",
        "sections": [
            ("La logique du module", [
                "Le module pédagogique part d'une séance réelle : on choisit les quelques savoir-faire travaillés ce jour-là, puis on note où en est chaque participant (acquis, en cours, à revoir).",
                "Pas besoin de tout remplir : deux ou trois compétences par séance suffisent pour suivre une progression dans le temps.",
            ]),
            ("Référentiels, modules et objectifs", [
                "Les référentiels sont des bibliothèques de compétences (numérique, FLE…). Vous pouvez en importer par fichier CSV.",
                "Un module regroupe des compétences qui vont ensemble, pour les réutiliser. Les objectifs et le plan de projet relient ces compétences à vos projets.",
            ]),
            ("Le passeport et le pilotage", [
                "Chaque participant a un passeport de compétences qui garde la trace de son parcours, avec notes et pièces jointes.",
                "Le pilotage des objectifs (RA / PAG) agrège tout cela pour vos documents officiels : Rapport d'Activité et Projet d'Animation Globale.",
            ]),
        ],
    },
    {
        "id": "partenaires",
        "icone": "🤝",
        "titre": "Partenaires et orientations",
        "intro": "Votre réseau et le suivi de l'accès aux droits.",
        "sections": [
            ("L'annuaire des partenaires", [
                "Espace Ressources → Partenaires : institutions, associations, écoles, bailleurs… Renseignez pour chacun ses domaines d'intervention (logement, santé, emploi…).",
                "Ces domaines servent à proposer le bon partenaire au moment d'orienter une personne.",
            ]),
            ("Les orientations (accès aux droits)", [
                "Quand vous orientez une personne vers un partenaire ou un dispositif, enregistrez-le : domaine, demande, urgence, et suivez le statut jusqu'à la résolution.",
                "Le tableau de bord des orientations résume cette activité d'accès aux droits — un indicateur valorisé par la CAF dans le bilan SENACS. Tracez vos orientations, même informelles.",
            ]),
        ],
    },
    {
        "id": "configuration",
        "icone": "⚙️",
        "titre": "Configuration de l'application",
        "intro": "Les réglages de structure, à faire surtout en début d'année (réservé aux administrateurs).",
        "sections": [
            ("Identité de la structure", [
                "Administration → Instance : nom, logos, adresse publique et messagerie (SMTP). La messagerie est nécessaire pour l'envoi des emails de réinitialisation de mot de passe.",
            ]),
            ("Secteurs et référentiels", [
                "Les secteurs (Famille, Numérique, Santé…) structurent toute l'application : modifiez-les avec précaution, de préférence en début d'année.",
                "Les référentiels (compétences pédagogiques, listes de choix insertion, comptes budgétaires) sont les bibliothèques de valeurs réutilisées dans les formulaires.",
            ]),
            ("Reprise de données", [
                "Administration → Import Excel : pour reprendre un historique de présences existant. Lancez toujours une simulation d'abord : elle montre ce qui sera importé sans rien modifier.",
            ]),
        ],
    },
    {
        "id": "administration",
        "icone": "🔧",
        "titre": "Administration (direction / référent)",
        "intro": "Gérer les comptes, les droits et les obligations légales.",
        "sections": [
            ("Les comptes et les rôles", [
                "Administration → Utilisateurs : créez un compte par collègue, avec le rôle adapté. Le rôle détermine TOUT ce que la personne peut voir et faire.",
                "« responsable_secteur » : son secteur uniquement. « direction » : tout. « admin_tech » : la technique (comptes, réglages) sans les données métier. D'autres rôles affinés existent dans l'écran RBAC.",
                "Règle d'or : le minimum nécessaire à chacun. Un départ ? Supprimez le compte le jour même.",
            ]),
            ("La purge RGPD", [
                "Contrôle → Purge RGPD : les fiches sans activité depuis 3 ans sont anonymisées automatiquement, une fois par jour. La page montre la liste en attente et permet de lancer manuellement.",
                "Chaque anonymisation est tracée dans le journal de l'application.",
            ]),
            ("La sécurité des connexions", [
                "Chaque tentative de connexion est journalisée (conservée 1 an). Après 5 échecs en 15 minutes, le compte est temporairement bloqué : les tentatives de piratage par essais successifs sont neutralisées.",
            ]),
            ("Identité de la structure", [
                "Administration → Instance : nom de la structure, logos, adresse publique, messagerie (SMTP) pour les emails de réinitialisation de mot de passe.",
            ]),
        ],
    },
    {
        "id": "installation",
        "icone": "💾",
        "titre": "Installation, sauvegardes et dépannage",
        "intro": "Pour la personne qui gère le serveur (aucune compétence technique requise).",
        "sections": [
            ("Installer l'application", [
                "Téléchargez le projet (ZIP), ouvrez le dossier « installation », clic droit sur Installer.ps1 → Exécuter avec PowerShell, et répondez aux questions. Le script installe tout : Python, la base de données PostgreSQL, l'application, le démarrage automatique et la sauvegarde quotidienne.",
                "Le guide détaillé pas-à-pas est dans installation/LISEZMOI-INSTALLATION.md.",
            ]),
            ("Mettre à jour", [
                "Téléchargez la nouvelle version et relancez Installer.ps1 : il remplace le programme en conservant vos données, et applique tout seul les évolutions de la base.",
            ]),
            ("Les sauvegardes", [
                "Une sauvegarde tourne chaque nuit à 2h00. Testez une restauration de temps en temps : une sauvegarde jamais testée n'est qu'une promesse.",
            ]),
            ("Quand quelque chose ne va pas", [
                "Une page d'erreur ? Le détail technique est enregistré dans instance/logs/erreurs.log sur le serveur : transmettez ce fichier à votre support, il contient la date, la page et la cause exacte.",
                "L'application ne répond plus ? Redémarrez le service Windows « AppGestion » (ou l'ordinateur). Vos données sont dans PostgreSQL : un redémarrage ne perd rien.",
            ]),
        ],
    },
]
