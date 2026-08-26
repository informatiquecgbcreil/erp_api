/* Émargement : replie les blocs secondaires pour dégager l'essentiel.
 *
 * Le repliage se fait sur le DOM déjà construit par le navigateur, et non
 * dans le gabarit : la page reste donc strictement identique si ce script
 * ne s'exécute pas, et aucune balise du gabarit n'a eu à être déplacée.
 * L'état ouvert/fermé de chaque bloc est mémorisé par navigateur.
 */
(function () {
  "use strict";

  // [titre du bloc, ouvert par défaut]. Les blocs absents sont ignorés.
  var BLOCS = [
    ["Flux conseillé", false],
    ["Échelle de Hart", false],
    ["Consommation estimée", false],
    ["Documents de session", false],
    ["Pointage tablette", false],
    ["Signatures à distance", false],
    ["Traçabilité des corrections de planning", false],
    ["Modules pédagogiques de la séance", false],
    ["Bilan de la séance", false],
    ["Créer un participant", false]
  ];

  var CLE = "erp.emargement.replis";

  function etats() {
    try { return JSON.parse(localStorage.getItem(CLE) || "{}"); } catch (e) { return {}; }
  }
  function memoriser(cle, ouvert) {
    try {
      var tout = etats();
      tout[cle] = ouvert;
      localStorage.setItem(CLE, JSON.stringify(tout));
    } catch (e) { /* navigation privée : on se passe de mémoire */ }
  }

  function normaliser(texte) {
    return (texte || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  /* Résumé affiché à droite du titre quand le bloc est replié : les deux
     premiers indicateurs du bloc, pour ne pas avoir à l'ouvrir. */
  function resume(bloc) {
    var valeurs = bloc.querySelectorAll(".finance-kpi__value");
    var bouts = [];
    for (var i = 0; i < valeurs.length && i < 2; i++) {
      bouts.push(normaliser(valeurs[i].textContent).toUpperCase());
    }
    return bouts.join(" · ");
  }

  function replier(bloc, titre, ouvertParDefaut) {
    if (bloc.dataset.repliPose === "1") { return; }
    bloc.dataset.repliPose = "1";

    // L'en-tête est l'enfant direct du bloc qui contient le titre.
    var entete = titre;
    while (entete.parentElement && entete.parentElement !== bloc) {
      entete = entete.parentElement;
    }
    if (!entete || entete.parentElement !== bloc) { return; }

    var corps = document.createElement("div");
    corps.className = "repli-corps";
    var suivant = entete.nextSibling;
    while (suivant) {
      var apres = suivant.nextSibling;
      corps.appendChild(suivant);
      suivant = apres;
    }
    bloc.appendChild(corps);

    var bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "repli-bouton";
    bouton.setAttribute("aria-expanded", "false");
    bouton.innerHTML = '<span class="repli-chevron" aria-hidden="true">▸</span>';
    var etiquette = document.createElement("span");
    etiquette.className = "repli-resume";
    bouton.appendChild(etiquette);

    entete.classList.add("repli-entete");
    entete.appendChild(bouton);

    var cle = normaliser(titre.textContent).slice(0, 60);

    function appliquer(ouvert) {
      corps.hidden = !ouvert;
      bloc.classList.toggle("repli-ferme", !ouvert);
      bouton.setAttribute("aria-expanded", ouvert ? "true" : "false");
      bouton.querySelector(".repli-chevron").textContent = ouvert ? "▾" : "▸";
      bouton.title = ouvert ? "Replier ce bloc" : "Déplier ce bloc";
      etiquette.textContent = ouvert ? "" : resume(bloc);
    }

    var memoire = etats();
    var ouvert = (cle in memoire) ? !!memoire[cle] : !!ouvertParDefaut;
    // Une ancre pointant dans ce bloc l'ouvre : un lien profond doit marcher.
    if (location.hash) {
      var cible = document.getElementById(location.hash.slice(1));
      if (cible && (cible === bloc || bloc.contains(cible))) { ouvert = true; }
    }
    appliquer(ouvert);

    function basculer() {
      var nouvel = corps.hidden;
      appliquer(nouvel);
      memoriser(cle, nouvel);
    }

    bouton.addEventListener("click", function (evenement) {
      evenement.preventDefault();
      evenement.stopPropagation();
      basculer();
    });
    // Le titre lui-même est cliquable : cible plus large, plus confortable.
    titre.style.cursor = "pointer";
    titre.addEventListener("click", basculer);
  }

  function demarrer() {
    var titres = document.querySelectorAll(".emargement-shell h2");
    for (var i = 0; i < titres.length; i++) {
      var titre = titres[i];
      var texte = normaliser(titre.textContent);
      for (var j = 0; j < BLOCS.length; j++) {
        if (texte.indexOf(normaliser(BLOCS[j][0])) !== -1) {
          var bloc = titre.closest("section.section-card, .card");
          if (bloc) { replier(bloc, titre, BLOCS[j][1]); }
          break;
        }
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
