"""Glossaire métier : le dico du social pour les nouveaux salariés.

Chaque terme est défini en français courant, avec si besoin une note
« Dans l'application » qui fait le lien entre le mot du métier et
l'endroit où on le retrouve dans le logiciel. C'est aussi le document
de référence du vocabulaire : quand un écran hésite entre deux mots
(séance/session…), c'est le terme du glossaire qui fait foi.

Règles d'écriture :
- public : quelqu'un qui débute dans un centre social ET dans le logiciel ;
- une définition = 1 à 3 phrases, zéro jargon non expliqué ;
- les sigles sont toujours développés.
"""

GLOSSAIRE: list[dict] = [
    {
        "categorie": "Les personnes",
        "icone": "👥",
        "termes": [
            {
                "terme": "Participant",
                "definition": "Toute personne qui prend part à une activité du centre, quel que soit son âge. Une fiche participant = une personne réelle, qu'on retrouve d'année en année.",
                "dans_app": "Menu Participants. C'est la fiche centrale : présences, passeport, parcours y sont rattachés.",
            },
            {
                "terme": "Usager / bénéficiaire",
                "definition": "Autres mots pour « participant », employés par certains financeurs ou administrations. C'est la même chose.",
                "dans_app": "L'application dit toujours « participant ».",
            },
            {
                "terme": "Habitant",
                "definition": "Personne qui vit dans un quartier du territoire, qu'elle fréquente le centre ou non. Le centre social travaille pour les habitants, pas seulement pour ses inscrits.",
                "dans_app": "Module Quartiers et carte des habitants.",
            },
            {
                "terme": "Bénévole",
                "definition": "Personne qui donne du temps gratuitement pour faire vivre le centre (animation, accompagnement, conseil d'administration…). Leur temps a une valeur reconnue par les financeurs.",
                "dans_app": "Case « bénévole » sur la fiche participant, page Bénévolat pour les heures et leur valorisation.",
            },
            {
                "terme": "Salarié",
                "definition": "Membre de l'équipe payé par la structure. On le compte en personnes et en ETP (voir ce mot).",
                "dans_app": "Module RH, réservé à la direction.",
            },
        ],
    },
    {
        "categorie": "L'activité au quotidien",
        "icone": "📅",
        "termes": [
            {
                "terme": "Secteur",
                "definition": "Grande famille d'activités du centre : Jeunesse, Familles, Insertion, Numérique… Chaque atelier, projet ou dépense appartient à un secteur, et beaucoup de personnes de l'équipe ne voient que le leur.",
                "dans_app": "Le secteur est demandé à la création de presque tout. Votre « portée » (en haut de page) indique le vôtre.",
            },
            {
                "terme": "Atelier",
                "definition": "Activité régulière ou ponctuelle proposée aux habitants : accompagnement scolaire, cours de français, café des parents… Un atelier contient des séances.",
                "dans_app": "Menu Présences : la liste des ateliers est le point de départ pour faire l'appel.",
            },
            {
                "terme": "Séance",
                "definition": "Un rendez-vous daté d'un atelier : l'accompagnement scolaire du mardi 14 mars, c'est une séance. C'est à la séance qu'on note les présents. (On disait parfois « session » : c'est le même mot.)",
                "dans_app": "Dans la page d'un atelier. Une séance peut être « événementielle » (voir Événement).",
            },
            {
                "terme": "Présence",
                "definition": "Le fait qu'une personne était à une séance. C'est LA donnée de base : les statistiques, les bilans et les financements reposent dessus. Pas de présence notée = activité invisible.",
                "dans_app": "Se coche sur la feuille d'émargement d'une séance, ou via le kiosque.",
            },
            {
                "terme": "Émargement / faire l'appel",
                "definition": "Noter qui est présent à une séance. « Émarger », c'est signer ou cocher sa présence.",
                "dans_app": "Bouton principal de chaque séance. Le guide « Je fais l'appel d'une séance » vous accompagne.",
            },
            {
                "terme": "Kiosque",
                "definition": "Écran (souvent une tablette à l'entrée) où les gens pointent eux-mêmes leur arrivée avec un code affiché par l'animateur. Évite de faire l'appel à la main.",
                "dans_app": "Bouton « Kiosque » sur la page d'émargement d'une séance ; il génère un code et un lien.",
            },
            {
                "terme": "Événement (séance événementielle)",
                "definition": "Temps fort ponctuel : fête de quartier, sortie familiale, repas partagé. Compté à part dans les bilans nationaux (SENACS), car différent d'un atelier régulier.",
                "dans_app": "Case « événement » sur la séance.",
            },
            {
                "terme": "Capacité",
                "definition": "Nombre de places d'une séance ou d'un atelier. Sert à mesurer le taux de remplissage.",
            },
        ],
    },
    {
        "categorie": "Le suivi des parcours",
        "icone": "🎓",
        "termes": [
            {
                "terme": "Passeport (de compétences)",
                "definition": "La page qui rassemble tout le parcours d'apprentissage d'un participant : compétences acquises, évaluations, notes des animateurs, participation.",
                "dans_app": "Bouton « Passeport » sur la fiche d'un participant.",
            },
            {
                "terme": "Compétence",
                "definition": "Savoir-faire concret qu'une personne développe au centre : « écrire un mail », « prendre la parole en groupe »… On évalue sa progression, pas une note scolaire.",
                "dans_app": "Module Apprentissages ; les compétences s'évaluent depuis l'émargement ou le passeport.",
            },
            {
                "terme": "Référentiel",
                "definition": "La liste organisée des compétences qu'on suit. C'est le « catalogue » commun à toute la structure.",
                "dans_app": "Apprentissages → Référentiels (modifiable par les personnes habilitées).",
            },
            {
                "terme": "Module pédagogique",
                "definition": "Paquet de compétences travaillées ensemble pendant un cycle de séances (ex. module « premiers pas ordinateur »).",
                "dans_app": "Se rattache aux séances pour proposer les bonnes compétences à évaluer.",
            },
            {
                "terme": "Insertion",
                "definition": "Accompagnement individuel vers l'emploi, la formation, les droits ou les papiers. Suit des informations sensibles (titre de séjour, diplômes) réservées aux personnes habilitées.",
                "dans_app": "Module Insertion, visible uniquement avec les droits correspondants.",
            },
            {
                "terme": "CIR (Contrat d'intégration républicaine)",
                "definition": "Contrat signé entre l'État et une personne étrangère nouvellement arrivée : formation civique et cours de français. Une info utile au suivi insertion.",
            },
            {
                "terme": "Orientation",
                "definition": "Le fait d'envoyer une personne vers un partenaire (assistante sociale, mission locale, association…) et de suivre ce qu'il en advient.",
                "dans_app": "Depuis la fiche participant ou le module Partenaires ; les orientations à relancer remontent dans « À traiter ».",
            },
            {
                "terme": "Droit à l'image / consentement",
                "definition": "Autorisation écrite qu'une personne donne (ou refuse) pour être photographiée ou filmée. Obligatoire avant toute diffusion. Se demande dès l'accueil.",
                "dans_app": "Sur la fiche participant. L'export RGPD de la fiche est possible à la demande de la personne.",
            },
            {
                "terme": "RGPD",
                "definition": "Règlement général sur la protection des données : la loi européenne qui encadre ce qu'on enregistre sur les gens. En pratique : ne saisir que l'utile, et pouvoir montrer/effacer les données d'une personne si elle le demande.",
            },
        ],
    },
    {
        "categorie": "Éducation populaire & pouvoir d'agir",
        "icone": "✊",
        "termes": [
            {
                "terme": "Éducation populaire",
                "definition": "L'idée fondatrice des centres sociaux : chacun peut apprendre, transmettre et agir toute sa vie, en dehors de l'école, à partir de ce qu'il vit. On apprend ensemble, en faisant, et les savoirs de chacun comptent — pas seulement les diplômes.",
                "dans_app": "C'est l'esprit du module Apprentissages : on y évalue des progrès réels, pas des notes scolaires.",
            },
            {
                "terme": "Développement du pouvoir d'agir (DPA)",
                "definition": "Accompagner les personnes pour qu'elles reprennent prise sur leur vie et leur territoire : passer de « on fait pour eux » à « ils font eux-mêmes, avec nous ». On parle aussi d'« empowerment ». Concrètement : partir des envies des habitants, pas de nos programmes.",
                "dans_app": "L'échelle de Hart mesure exactement ça : la place réelle laissée aux habitants dans les décisions.",
            },
            {
                "terme": "Participation des habitants",
                "definition": "Le principe selon lequel les habitants ne sont pas des « publics » à qui on offre des services, mais des acteurs qui construisent le centre avec l'équipe : donner son avis, animer, décider, siéger au conseil d'administration.",
                "dans_app": "Onglet Participation du passeport (parcours individuel) et page Échelle de Hart (vue collective).",
            },
            {
                "terme": "Échelle de Hart",
                "definition": "Outil (imaginé par Roger Hart) qui décrit 8 marches de participation, de la simple présence décorative jusqu'à la co-décision. Monter l'escalier = laisser de plus en plus de vraie prise aux habitants. Ce n'est pas une note : c'est un repère pour se questionner.",
                "dans_app": "Représentée en escalier dans le passeport et en vue collective. C'est une évaluation humaine, jamais un calcul automatique.",
            },
            {
                "terme": "Aller-vers",
                "definition": "Ne pas attendre que les gens poussent la porte du centre : sortir, être présent en pied d'immeuble, sur le marché, à la sortie de l'école, pour toucher ceux qui ne viendraient jamais d'eux-mêmes.",
            },
            {
                "terme": "Co-construction",
                "definition": "Monter un projet AVEC les personnes concernées, du diagnostic à l'évaluation — pas seulement leur demander leur avis à la fin. Plus exigeant, plus lent, mais les actions co-construites tiennent dans le temps.",
            },
            {
                "terme": "Diagnostic partagé",
                "definition": "Photographie des besoins et des ressources d'un territoire, faite AVEC les habitants et les partenaires (et pas seulement à partir de statistiques). C'est la première étape du projet social.",
                "dans_app": "Les données quartiers, présences et questionnaires fournissent la matière chiffrée du diagnostic.",
            },
            {
                "terme": "Pair-aidance",
                "definition": "L'entraide entre personnes qui vivent ou ont vécu la même situation : un parent qui en accompagne un autre, un ancien apprenant qui aide un débutant. Le centre la favorise car elle crée de la confiance qu'aucun professionnel ne peut décréter.",
            },
            {
                "terme": "Lien social",
                "definition": "Ce qui relie les gens entre eux : se connaître, se rendre service, se sentir appartenir à un endroit. C'est la matière première du centre social — beaucoup d'actions n'ont pas d'autre but, et c'est déjà énorme.",
            },
            {
                "terme": "Mixité sociale",
                "definition": "Faire se rencontrer des personnes qui ne se croiseraient pas autrement : générations, quartiers, milieux, origines. Un repas partagé qui mélange vraiment, c'est de la mixité réussie.",
            },
            {
                "terme": "Médiation sociale",
                "definition": "Faciliter le dialogue là où il s'est rompu : entre voisins, entre familles et institutions (école, bailleur, CAF…). Le médiateur ne juge pas, il rétablit le lien.",
            },
            {
                "terme": "Animation globale",
                "definition": "Le socle du centre social financé par la CAF : une fonction d'accueil, d'écoute et de coordination qui bénéficie à tout le territoire, au-delà des activités. C'est ce qui distingue un centre social d'une simple structure d'activités.",
                "dans_app": "La prestation CAF correspondante (AGC) apparaît dans les subventions.",
            },
            {
                "terme": "Projet social",
                "definition": "LE document fondateur du centre : tous les 4 ans environ, il décrit le territoire (diagnostic), les priorités et les moyens. C'est sur lui que la CAF accorde l'agrément. Idéalement écrit avec les habitants.",
                "dans_app": "Les bilans et statistiques de l'application servent à l'écrire et à l'évaluer.",
            },
            {
                "terme": "Référent familles / ACF",
                "definition": "Le ou la professionnelle qui coordonne tout ce qui touche aux familles et à la parentalité, dans le cadre de l'Animation collective familles (ACF), financée par la CAF en complément de l'animation globale.",
            },
            {
                "terme": "Comité d'habitants / comité d'usagers",
                "definition": "Groupe d'habitants qui participe aux décisions du centre : programmation, aménagement, budget d'une action… Une des formes concrètes de la participation, entre le simple avis et le conseil d'administration.",
            },
            {
                "terme": "FPH (Fonds de participation des habitants)",
                "definition": "Petite enveloppe (souvent liée à la politique de la ville) que des habitants attribuent eux-mêmes à des micro-projets d'autres habitants : fête des voisins, sortie collective… Les habitants décident, la structure sécurise.",
            },
            {
                "terme": "FCSF & charte fédérale",
                "definition": "La Fédération des centres sociaux et socioculturels de France, qui relie les centres entre eux. Sa charte fonde l'action sur trois valeurs : dignité humaine, solidarité, démocratie.",
            },
            {
                "terme": "Laïcité",
                "definition": "Principe qui garantit à chacun la liberté de croire ou non, et la neutralité de la structure : le centre accueille tout le monde, sans prosélytisme d'aucune sorte. Au quotidien : on accueille les personnes telles qu'elles sont, et les activités restent ouvertes à tous.",
            },
        ],
    },
    {
        "categorie": "Mesurer et évaluer",
        "icone": "📏",
        "termes": [
            {
                "terme": "Évaluation",
                "definition": "Se demander honnêtement : qu'est-ce que notre action a changé, pour qui, et comment le sait-on ? Ce n'est ni une punition ni de la paperasse : c'est ce qui permet d'améliorer l'action et de la raconter aux financeurs.",
                "dans_app": "Les présences, questionnaires et statistiques fournissent la matière ; l'analyse reste humaine.",
            },
            {
                "terme": "Indicateur",
                "definition": "Une information choisie À L'AVANCE pour suivre une action : nombre de participants, taux de présence, satisfaction… Un bon indicateur répond à une question qu'on se pose vraiment — sinon c'est juste un chiffre de plus.",
                "dans_app": "Les indicateurs des projets se suivent dans la fiche action ; ceux à renseigner remontent dans « À traiter ».",
            },
            {
                "terme": "Indicateur quantitatif",
                "definition": "Ce qui se COMPTE : 45 personnes, 12 séances, 80 % de présence. Indispensable mais jamais suffisant : « 45 personnes sont venues » ne dit pas ce que ça leur a apporté.",
            },
            {
                "terme": "Indicateur qualitatif",
                "definition": "Ce qui se CONSTATE sans se compter : la confiance qui revient, une prise de parole, un parent qui ose entrer dans l'école. Se recueille par l'observation, les témoignages, les questionnaires. Aussi précieux que les chiffres — les financeurs le savent.",
            },
            {
                "terme": "Indicateur de processus (ou de réalisation)",
                "definition": "Mesure ce qu'on a FAIT : nombre de séances tenues, d'heures d'atelier, de personnes touchées. Répond à « l'action a-t-elle eu lieu comme prévu ? » — pas encore à « a-t-elle servi ? ».",
            },
            {
                "terme": "Indicateur de résultat",
                "definition": "Mesure ce que l'action a PRODUIT directement à la fin : 8 personnes sur 12 ont obtenu leur code de la route, 15 parents sont venus aux ateliers. C'est le premier niveau du « ça a servi ».",
            },
            {
                "terme": "Effet",
                "definition": "Le changement observable chez les personnes APRÈS l'action, au-delà du résultat immédiat : reprendre confiance, sortir de chez soi, refaire des démarches seul. Les effets se voient à quelques mois.",
            },
            {
                "terme": "Impact",
                "definition": "Le changement durable et large, à l'échelle d'une vie ou d'un territoire : un quartier où les gens se parlent, des habitants qui s'organisent seuls. L'impact se mesure sur des années et n'est jamais dû à une seule action — rester modeste dans les bilans.",
            },
            {
                "terme": "Objectif général / objectif opérationnel",
                "definition": "L'objectif général dit la direction (« rompre l'isolement des personnes âgées ») ; les objectifs opérationnels disent les pas concrets (« ouvrir un atelier hebdomadaire », « toucher 20 personnes la première année »). On évalue les opérationnels pour progresser vers le général.",
            },
            {
                "terme": "Objectif SMART",
                "definition": "Aide-mémoire pour écrire un objectif évaluable : Spécifique, Mesurable, Atteignable, Réaliste, défini dans le Temps. « Faire venir 15 parents aux ateliers d'ici juin » est SMART ; « améliorer la parentalité » ne l'est pas.",
            },
            {
                "terme": "Critère d'évaluation",
                "definition": "L'angle sous lequel on juge une action : efficacité (a-t-on atteint l'objectif ?), pertinence (répondait-elle à un vrai besoin ?), efficience (à un coût raisonnable ?). Les indicateurs viennent ensuite chiffrer chaque critère.",
            },
            {
                "terme": "PAG",
                "definition": "Sigle à double sens selon les maisons : le plus souvent « Pouvoir d'AGir » (voir Développement du pouvoir d'agir), parfois « Projet d'Animation Globale » (le projet social du centre). En réunion, ne pas hésiter à demander lequel — tout le monde gagne du temps.",
            },
            {
                "terme": "Participant unique",
                "definition": "Une personne comptée UNE seule fois, même si elle est venue 40 fois. « 300 présences » et « 45 participants uniques » racontent deux choses différentes : le volume d'activité et le nombre de personnes réellement touchées.",
                "dans_app": "Les statistiques distinguent systématiquement présences et participants uniques.",
            },
            {
                "terme": "File active",
                "definition": "Le nombre de personnes différentes suivies ou accueillies sur une période donnée (souvent l'année). Très utilisé dans les dossiers de financement — c'est en général le nombre de participants uniques.",
            },
            {
                "terme": "Assiduité / fidélisation",
                "definition": "L'assiduité : une personne inscrite vient-elle régulièrement ? La fidélisation : revient-elle d'une période sur l'autre ? Deux signes qu'une action répond à un vrai besoin.",
                "dans_app": "Le tableau de bord des ateliers (stats) calcule la fidélisation à partir des présences.",
            },
            {
                "terme": "Taux de remplissage",
                "definition": "Présences réelles rapportées à la capacité : un atelier de 12 places avec 6 présents est à 50 %. Un taux durablement bas questionne l'horaire, le lieu ou le besoin — pas forcément l'animateur.",
            },
            {
                "terme": "Verbatim / témoignage",
                "definition": "Les mots exacts d'une personne, cités tels quels dans un bilan : « avant je n'osais pas sortir, maintenant j'accompagne les sorties ». Un bon verbatim vaut souvent mieux qu'un tableau — avec l'accord de la personne, toujours.",
            },
            {
                "terme": "Auto-évaluation",
                "definition": "Quand la personne évalue elle-même son propre chemin (« où j'en suis, d'où je pars »). Cohérente avec l'éducation populaire : la personne est actrice de son parcours, pas objet de mesure.",
                "dans_app": "Le passeport de compétences peut recueillir le ressenti de la personne, pas seulement l'avis de l'animateur.",
            },
        ],
    },
    {
        "categorie": "L'argent",
        "icone": "💰",
        "termes": [
            {
                "terme": "Subvention",
                "definition": "Argent versé par un financeur (CAF, ville, département, État…) pour mener une action. Se demande, s'obtient (ou pas), se justifie ensuite par un bilan.",
                "dans_app": "Menu Finances → Enveloppes : chaque subvention avec son demandé / attribué / reçu.",
            },
            {
                "terme": "Financeur",
                "definition": "Celui qui donne l'argent : CAF, commune, agglomération, État, fondation… Chacun a ses dossiers et ses bilans à rendre.",
            },
            {
                "terme": "Enveloppe",
                "definition": "Dans l'application, le suivi d'une subvention : combien demandé, combien attribué, combien déjà dépensé, combien il reste.",
                "dans_app": "Finances → Enveloppes.",
            },
            {
                "terme": "Dépense",
                "definition": "Achat ou facture imputé sur une enveloppe ou un projet : matériel, intervenant, transport… Bien imputer chaque dépense, c'est ce qui rend les bilans financiers justes.",
                "dans_app": "Finances → Dépenses.",
            },
            {
                "terme": "Projet",
                "definition": "Action structurée avec un objectif, un budget (charges et produits) et souvent plusieurs financeurs : « vacances familles », « atelier sociolinguistique »…",
                "dans_app": "Finances → Projets financés. Le guide « Je monte un projet » arrive bientôt.",
            },
            {
                "terme": "AAP (Appel à projets)",
                "definition": "Quand un financeur annonce : « je finance des projets sur tel thème, déposez vos dossiers ». On y répond avec un projet et un budget prévisionnel.",
            },
            {
                "terme": "Prévisionnel",
                "definition": "Budget estimé AVANT de faire : ce qu'on pense dépenser et recevoir. S'oppose au « réalisé », ce qui s'est vraiment passé.",
                "dans_app": "Finances → Prévisionnels & demandes, avec un générateur de budget.",
            },
            {
                "terme": "Charges / produits",
                "definition": "Les deux colonnes d'un budget : les charges = ce que ça coûte, les produits = ce qui finance (subventions, participations des familles…). Un budget équilibré : charges = produits.",
            },
            {
                "terme": "Coût unitaire",
                "definition": "Ce que coûte réellement une unité d'activité : une heure d'atelier, une présence. Utile pour objectiver les demandes de financement.",
                "dans_app": "Page Coût unitaire (Ressources).",
            },
            {
                "terme": "Valorisation du bénévolat",
                "definition": "Traduire les heures données par les bénévoles en euros (heures × taux horaire) pour les faire apparaître dans les budgets et bilans. Les financeurs y sont attentifs : c'est la richesse propre du centre.",
                "dans_app": "Page Bénévolat ; le taux horaire se règle dans les paramètres (direction/finance).",
            },
            {
                "terme": "Don & reçu fiscal (CERFA 11580)",
                "definition": "Don d'argent d'un particulier ou d'une entreprise. En échange, la structure délivre un reçu fiscal officiel (formulaire CERFA n° 11580) qui ouvre droit à une réduction d'impôt.",
                "dans_app": "Page Dons & reçus fiscaux (droits dédiés).",
            },
            {
                "terme": "ETP (Équivalent temps plein)",
                "definition": "Unité pour compter le personnel : une personne à mi-temps = 0,5 ETP. Permet de comparer des équipes aux contrats différents.",
                "dans_app": "Module RH ; repris automatiquement dans le SENACS.",
            },
            {
                "terme": "Masse salariale",
                "definition": "Le total des salaires et charges payés par la structure sur l'année. Première dépense d'un centre social.",
            },
            {
                "terme": "Trésorerie",
                "definition": "L'argent réellement disponible en caisse à un instant donné — à ne pas confondre avec le budget : on peut avoir un budget équilibré et une trésorerie vide si les subventions arrivent en retard.",
            },
            {
                "terme": "Fond de caisse",
                "definition": "La somme permanente laissée dans la caisse pour rendre la monnaie (souvent 30 à 50 €). Elle ne se dépose jamais à la banque : quand on fait un dépôt, on emporte la recette et on laisse le fond.",
                "dans_app": "Page Caisse (Ressources) : à régler une fois au départ.",
            },
            {
                "terme": "Arrêté de caisse (comptage)",
                "definition": "Compter physiquement tout ce qu'il y a dans la caisse et comparer au montant théorique. S'il y a un écart, on le note noir sur blanc — l'important n'est pas d'être parfait, c'est de tracer. À faire régulièrement (chaque semaine, ou avant chaque dépôt).",
                "dans_app": "Page Caisse : le comptage trace l'écart et réaligne le théorique automatiquement.",
            },
            {
                "terme": "Dépôt en banque (remise)",
                "definition": "Apporter la recette (espèces et chèques encaissés) à la banque, accompagnée d'un bordereau qui liste ce qu'on dépose. Pour les chèques, on parle de « remise de chèques ».",
                "dans_app": "Page Caisse : l'application enregistre le dépôt et imprime le bordereau.",
            },
            {
                "terme": "Bordereau",
                "definition": "Le papier qui accompagne un dépôt en banque : il détaille les montants (espèces, nombre de chèques) et sert de preuve des deux côtés. La banque y appose son cachet.",
            },
            {
                "terme": "Cofinancement",
                "definition": "Quand plusieurs financeurs paient ensemble la même action : la CAF 40 %, la ville 30 %, le reste en fonds propres. Quasiment toutes les actions d'un centre social sont cofinancées — d'où l'importance de bien ventiler les dépenses.",
            },
            {
                "terme": "Fonds propres",
                "definition": "L'argent qui appartient en propre à la structure (adhésions, participations des familles, réserves…), par opposition aux subventions. Les financeurs demandent presque toujours que la structure en mette une part.",
            },
            {
                "terme": "Adhésion / cotisation",
                "definition": "L'adhésion fait de la personne un membre de l'association (avec voix à l'assemblée générale) ; la cotisation est la somme, souvent symbolique, versée pour adhérer. Adhérer, c'est un acte d'appartenance, pas un simple ticket d'entrée.",
            },
            {
                "terme": "Convention (de financement)",
                "definition": "Le contrat signé avec un financeur : ce qu'on s'engage à faire, combien il verse, quand, et quel bilan rendre. À lire AVANT d'agir : les obligations (logos, bilans, délais) s'y cachent.",
            },
            {
                "terme": "Acompte / solde",
                "definition": "Les subventions arrivent souvent en deux fois : un acompte au démarrage (par exemple 70 %) puis le solde APRÈS remise du bilan. Un bilan en retard, c'est du solde qui n'arrive pas — et la trésorerie qui souffre.",
            },
            {
                "terme": "Exercice (comptable)",
                "definition": "L'année de référence des comptes, en général l'année civile (du 1er janvier au 31 décembre). « L'exercice 2026 » = tout ce qui a été dépensé et reçu en 2026.",
                "dans_app": "Le sélecteur « Exercice » des pages finances filtre sur cette année-là.",
            },
            {
                "terme": "Bilan comptable (≠ bilan d'activité)",
                "definition": "Attention au piège du mot « bilan » : le bilan COMPTABLE est la photographie financière annuelle (ce qu'on possède, ce qu'on doit), établie par le comptable. Le bilan D'ACTIVITÉ raconte ce qu'on a fait et pour qui. Les financeurs demandent souvent les deux.",
            },
        ],
    },
    {
        "categorie": "Les institutions et les bilans",
        "icone": "🏛️",
        "termes": [
            {
                "terme": "CAF (Caisse d'allocations familiales)",
                "definition": "Le financeur principal des centres sociaux. C'est elle qui délivre l'« agrément centre social », renouvelé sur la base d'un projet social et de bilans.",
            },
            {
                "terme": "Agrément / contrat de projet",
                "definition": "L'agrément est la reconnaissance officielle « centre social » par la CAF, accordée pour plusieurs années sur la base du contrat de projet : le document qui fixe les objectifs du centre pour la période.",
            },
            {
                "terme": "Bilan",
                "definition": "Le compte-rendu (chiffres + récit) qu'on rend à un financeur ou à ses instances : combien de personnes, quelles activités, quel argent utilisé, quels effets. Les bilans se préparent toute l'année : ils reposent sur les présences et dépenses saisies au quotidien.",
                "dans_app": "Menu Bilans. Le guide « Je prépare un bilan pour un financeur » vous accompagne.",
            },
            {
                "terme": "SENACS",
                "definition": "Système national d'échanges des centres sociaux : le questionnaire annuel que remplissent tous les centres (publics, activités, équipe, budget). Alimente l'observatoire national.",
                "dans_app": "L'application pré-remplit le SENACS à partir de vos données (Bilans).",
            },
            {
                "terme": "QPV (Quartier prioritaire de la politique de la ville)",
                "definition": "Quartier officiellement reconnu comme prioritaire par l'État. Beaucoup de financements ciblent les habitants des QPV : d'où l'importance de bien renseigner le quartier sur les fiches.",
                "dans_app": "Module Quartiers ; le quartier se choisit sur la fiche participant.",
            },
            {
                "terme": "CA / AG",
                "definition": "Conseil d'administration (les habitants et partenaires qui pilotent l'association) et assemblée générale (la réunion annuelle de tous les adhérents). On leur présente chiffres et bilans.",
                "dans_app": "Le bouton Imprimer / PDF du tableau de bord sort une synthèse présentable en CA.",
            },
            {
                "terme": "Questionnaire d'impact",
                "definition": "Questionnaire posé aux participants (avant/après une action, ou satisfaction) pour mesurer les effets réels au-delà des chiffres de fréquentation.",
                "dans_app": "Module Questionnaires.",
            },
            {
                "terme": "Partenaire",
                "definition": "Structure avec qui le centre travaille : école, mission locale, CCAS, association… On oriente des personnes vers eux et on monte des actions ensemble.",
                "dans_app": "Module Partenaires, avec annuaire et carte.",
            },
            {
                "terme": "Politique de la ville / contrat de ville",
                "definition": "L'ensemble des moyens que l'État et les collectivités consacrent aux quartiers prioritaires (QPV). Le contrat de ville est le document local qui organise ces moyens — beaucoup d'appels à projets en découlent.",
            },
            {
                "terme": "CLAS (Contrat local d'accompagnement à la scolarité)",
                "definition": "Le dispositif qui finance l'accompagnement scolaire : aide aux devoirs, ouverture culturelle ET accompagnement des parents dans leur rôle. Un bilan spécifique est à rendre chaque année.",
                "dans_app": "Les présences des ateliers d'accompagnement scolaire alimentent directement ce bilan.",
            },
            {
                "terme": "REAAP (Réseau d'écoute, d'appui et d'accompagnement des parents)",
                "definition": "Le dispositif qui finance les actions de soutien à la parentalité : cafés des parents, groupes de parole, sorties familles. Porté par la CAF.",
            },
            {
                "terme": "ASL (Atelier sociolinguistique)",
                "definition": "Apprentissage du français à partir des situations de la vie quotidienne (école, santé, démarches) — on apprend la langue ET les codes pour être autonome. Différent d'un cours de français classique.",
            },
            {
                "terme": "Adulte-relais",
                "definition": "Poste de médiation sociale financé par l'État, réservé aux habitants des quartiers prioritaires. Beaucoup de médiateurs des centres sociaux sont sur ce type de contrat.",
            },
            {
                "terme": "EVS (Espace de vie sociale)",
                "definition": "Le « petit frère » du centre social : même esprit (participation des habitants, lien social) mais agrément CAF plus léger, souvent sans salarié permanent. Beaucoup de centres accompagnent des EVS sur leur territoire.",
            },
            {
                "terme": "ALSH (Accueil de loisirs sans hébergement)",
                "definition": "Le « centre de loisirs » : accueil déclaré des enfants et ados hors temps scolaire (mercredis, vacances), avec des règles strictes d'encadrement et une déclaration à la SDJES (services de l'État). Financé notamment par une prestation de service CAF.",
            },
            {
                "terme": "LAEP (Lieu d'accueil enfants-parents)",
                "definition": "Espace où parents et jeunes enfants (0-6 ans) viennent librement, ensemble, sans inscription : on joue, on se pose, on parle avec des accueillants formés. Ni halte-garderie, ni consultation — un lieu pour être parent tranquillement.",
            },
            {
                "terme": "Prestation de service (PS)",
                "definition": "Le mode de financement type de la CAF : un tarif national versé pour une fonction reconnue (PS « animation globale », PS « ACF », PS « ALSH », PS « LAEP »…). Chaque PS a ses conditions et son bilan.",
            },
            {
                "terme": "CTG (Convention territoriale globale)",
                "definition": "Le contrat-cadre entre la CAF et les collectivités d'un territoire, qui organise tous les financements famille/jeunesse/social. Elle a remplacé les anciens « contrats enfance jeunesse » (CEJ). Le centre social y est presque toujours cité.",
            },
            {
                "terme": "VACAF",
                "definition": "L'aide aux vacances de la CAF : les familles à petit budget paient une partie du séjour, VACAF complète directement auprès de l'organisateur. Les « premiers départs en vacances » accompagnés par le centre s'appuient souvent dessus.",
            },
            {
                "terme": "CCAS (Centre communal d'action sociale)",
                "definition": "Le service social de la mairie : aides d'urgence, domiciliation, accompagnement des personnes âgées… Partenaire quotidien du centre social — on s'oriente mutuellement des personnes.",
            },
            {
                "terme": "Mission locale",
                "definition": "La structure qui accompagne les 16-25 ans sortis de l'école : emploi, formation, santé, logement, permis. LE partenaire jeunesse pour l'insertion des jeunes adultes.",
            },
            {
                "terme": "PMI (Protection maternelle et infantile)",
                "definition": "Le service du département pour la santé des futurs parents et des enfants de 0 à 6 ans : consultations gratuites, puéricultrices, bilans en école maternelle. Partenaire naturel des actions petite enfance et parentalité.",
            },
            {
                "terme": "Bureau (de l'association)",
                "definition": "Le noyau du conseil d'administration : président·e, trésorier·ère, secrétaire. Ce sont des habitants bénévoles élus — c'est l'employeur légal de l'équipe salariée.",
            },
        ],
    },
    {
        "categorie": "Les mots de l'application",
        "icone": "🖥️",
        "termes": [
            {
                "terme": "Rôle & permissions",
                "definition": "Votre rôle (animateur, responsable de secteur, direction…) détermine ce que vous voyez et pouvez modifier. Si une page vous est refusée, c'est une question de droits, pas une panne.",
                "dans_app": "Votre rôle et votre portée s'affichent en haut de chaque page. La direction règle les droits dans Admin.",
            },
            {
                "terme": "Mode simple / expert",
                "definition": "Deux niveaux d'affichage : le mode simple montre l'essentiel du quotidien, le mode expert montre tout (graphiques, pilotage, réglages).",
                "dans_app": "Boutons Simple / Expert en haut à droite. Chacun choisit pour soi.",
            },
            {
                "terme": "Poste de travail (« Ma journée »)",
                "definition": "Le bloc d'accueil qui propose vos 3 à 5 actions du jour selon votre rôle, avec les compteurs du jour (séances, rappels).",
                "dans_app": "Page d'accueil, juste sous le titre.",
            },
            {
                "terme": "Guide pas à pas",
                "definition": "Un fil conducteur qui vous emmène sur les bonnes pages dans le bon ordre, avec une explication à chaque étape. On peut le quitter et le reprendre librement.",
                "dans_app": "Page Guides (lien sur l'accueil).",
            },
            {
                "terme": "À traiter (centre de suivi)",
                "definition": "La page qui rassemble tout ce qui attend une action : rappels, orientations à relancer, indicateurs à renseigner.",
                "dans_app": "Menu « À traiter ».",
            },
            {
                "terme": "Corbeille",
                "definition": "Rien ne se supprime directement : ateliers et séances passent d'abord à la corbeille, d'où on peut les restaurer. La suppression définitive est réservée aux rôles autorisés.",
            },
            {
                "terme": "Sauvegarde",
                "definition": "Copie de sécurité de toutes les données, pour pouvoir tout retrouver en cas de panne. À vérifier régulièrement — c'est le filet de sécurité de la structure.",
                "dans_app": "Admin → Sauvegardes (direction / admin technique).",
            },
        ],
    },
]


def glossaire_termes_plats() -> list[dict]:
    """Tous les termes de base à plat (pour tests et recherche)."""
    plats = []
    for cat in GLOSSAIRE:
        for t in cat["termes"]:
            plats.append({**t, "categorie": cat["categorie"]})
    return plats


# ---------------------------------------------------------------------------
# Fusion avec les personnalisations de la structure (table glossaire_terme)
# ---------------------------------------------------------------------------

ICONE_CATEGORIE_PERSO = "📁"
CATEGORIE_PERSO_DEFAUT = "Les mots de la structure"


def _cle(terme: str) -> str:
    return (terme or "").strip().lower()


def termes_de_base() -> set[str]:
    """Clés (minuscules) des termes du glossaire intégré."""
    return {_cle(t["terme"]) for t in glossaire_termes_plats()}


def glossaire_fusionne() -> list[dict]:
    """Glossaire de base + ajustements locaux (ajouts, modifs, masquages).

    Chaque terme rendu porte :
    - ``id`` : id de la ligne locale (None si terme de base intact) ;
    - ``origine`` : 'base', 'modifie' (base remplacé) ou 'perso' (ajouté).
    """
    try:
        from app.models import GlossaireTerme
        rows = GlossaireTerme.query.order_by(GlossaireTerme.terme.asc()).all()
    except Exception:
        rows = []
    locaux = {_cle(r.terme): r for r in rows}
    utilises: set[str] = set()

    cats: list[dict] = []
    for cat in GLOSSAIRE:
        termes = []
        for t in cat["termes"]:
            k = _cle(t["terme"])
            r = locaux.get(k)
            if r is not None:
                utilises.add(k)
                if r.masque:
                    continue
                termes.append({
                    "terme": r.terme, "definition": r.definition,
                    "dans_app": (r.dans_app or "").strip() or None,
                    "id": r.id, "origine": "modifie",
                })
            else:
                termes.append({**t, "id": None, "origine": "base"})
        cats.append({"categorie": cat["categorie"], "icone": cat["icone"], "termes": termes})

    # Mots ajoutés par la structure : rejoignent leur catégorie si elle
    # existe, sinon une catégorie dédiée (ou celle qu'ils déclarent).
    icones = {c["categorie"]: c["icone"] for c in cats}
    for k, r in locaux.items():
        if k in utilises or r.masque:
            continue
        item = {
            "terme": r.terme, "definition": r.definition,
            "dans_app": (r.dans_app or "").strip() or None,
            "id": r.id, "origine": "perso",
        }
        cat_nom = (r.categorie or "").strip() or CATEGORIE_PERSO_DEFAUT
        cible = next((c for c in cats if c["categorie"] == cat_nom), None)
        if cible is None:
            cible = {"categorie": cat_nom, "icone": icones.get(cat_nom, ICONE_CATEGORIE_PERSO), "termes": []}
            cats.append(cible)
        cible["termes"].append(item)

    return [c for c in cats if c["termes"]]
