/* Émargement : confort d'affichage de la page.
 *
 * Deux rôles, tous deux en enrichissement progressif — sans ce script la
 * page reste fonctionnelle, elle est seulement plus longue :
 *
 * 1. REPLIAGE des blocs secondaires, avec mémorisation par navigateur.
 * 2. MODALES sans Bootstrap. Le gabarit utilise le protocole Bootstrap
 *    (data-bs-toggle / data-bs-dismiss) et charge la bibliothèque depuis un
 *    CDN : sur une installation en réseau local sans Internet, elle n'arrive
 *    jamais et TOUTES les fenêtres (une note rapide par participant) restent
 *    affichées en pleine page. On fournit donc un remplaçant minimal, activé
 *    seulement si Bootstrap est réellement absent.
 *
 * Le repliage travaille sur le DOM construit par le navigateur : le gabarit
 * contient des <section> refermées par </div>, qu'il aurait été risqué de
 * restructurer sur un écran aussi central.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* 1. Modales : remplaçant minimal quand Bootstrap n'a pas pu charger   */
  /* ------------------------------------------------------------------ */

  function installerModales() {
    if (window.bootstrap && window.bootstrap.Modal) { return; }

    function ouvrir(modale) {
      if (!modale) { return; }
      modale.classList.add("modal-ouverte");
      document.body.style.overflow = "hidden";
      var champ = modale.querySelector("textarea, input:not([type=hidden]), select");
      if (champ) { try { champ.focus(); } catch (e) { /* champ masqué */ } }
    }
    function fermer(modale) {
      if (!modale) { return; }
      modale.classList.remove("modal-ouverte");
      if (!document.querySelector(".modal-ouverte")) { document.body.style.overflow = ""; }
    }

    // API minimale compatible avec le code déjà présent dans la page
    // (`new bootstrap.Modal(el).show()`), pour ne rien avoir à y changer.
    function Modal(element) { this._element = element; }
    Modal.prototype.show = function () { ouvrir(this._element); };
    Modal.prototype.hide = function () { fermer(this._element); };
    Modal.getInstance = function (element) { return new Modal(element); };
    Modal.getOrCreateInstance = function (element) { return new Modal(element); };
    window.bootstrap = window.bootstrap || {};
    window.bootstrap.Modal = Modal;

    document.addEventListener("click", function (evenement) {
      var declencheur = evenement.target.closest('[data-bs-toggle="modal"]');
      if (declencheur) {
        var cible = declencheur.getAttribute("data-bs-target");
        if (cible) {
          evenement.preventDefault();
          ouvrir(document.querySelector(cible));
          return;
        }
      }
      var fermeture = evenement.target.closest('[data-bs-dismiss="modal"], .btn-close');
      if (fermeture) {
        evenement.preventDefault();
        fermer(fermeture.closest(".modal"));
        return;
      }
      // Clic sur le fond sombre (et non dans la boîte) : on ferme.
      if (evenement.target.classList && evenement.target.classList.contains("modal-ouverte")) {
        fermer(evenement.target);
      }
    });

    document.addEventListener("keydown", function (evenement) {
      if (evenement.key === "Escape") {
        var ouverte = document.querySelector(".modal-ouverte");
        if (ouverte) { fermer(ouverte); }
      }
    });

    document.documentElement.classList.add("sans-bootstrap");
  }

  /* ------------------------------------------------------------------ */
  /* 2. Repliage des blocs                                               */
  /* ------------------------------------------------------------------ */

  // [libellé du titre, ouvert par défaut]
  var BLOCS = [
    ["Flux conseillé", false],
    ["Échelle de Hart", false],
    ["Consommation estimée", false],
    ["Documents de session", false],
    ["Pointage tablette", false],
    ["Signatures à distance", false],
    ["Traçabilité des corrections", false],
    ["Modules pédagogiques", false],
    ["Bilan de la séance", false],
    ["Créer un participant", false],
    ["inscrit", true],
    ["Enregistrer une présence", true],
    ["Présences déjà enregistrées", true]
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

  /* Rappel des deux premiers indicateurs quand le bloc est fermé. */
  function resume(bloc) {
    var valeurs = bloc.querySelectorAll(".finance-kpi__value");
    var bouts = [];
    for (var i = 0; i < valeurs.length && i < 2; i++) {
      bouts.push(normaliser(valeurs[i].textContent).toUpperCase());
    }
    return bouts.join(" · ");
  }

  var replis = [];

  function replier(bloc, titre, ouvertParDefaut) {
    if (bloc.dataset.repliPose === "1") { return; }
    bloc.dataset.repliPose = "1";

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
    if (!corps.childNodes.length) { return; }  // rien à replier
    bloc.appendChild(corps);

    var bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "repli-bouton";
    var chevron = document.createElement("span");
    chevron.className = "repli-chevron";
    chevron.setAttribute("aria-hidden", "true");
    var etiquette = document.createElement("span");
    etiquette.className = "repli-resume";
    bouton.appendChild(chevron);
    bouton.appendChild(etiquette);

    entete.classList.add("repli-entete");
    entete.appendChild(bouton);

    var cle = normaliser(titre.textContent).slice(0, 60);

    function appliquer(ouvert) {
      corps.hidden = !ouvert;
      bloc.classList.toggle("repli-ferme", !ouvert);
      bouton.setAttribute("aria-expanded", ouvert ? "true" : "false");
      chevron.textContent = ouvert ? "▾" : "▸";
      bouton.title = ouvert ? "Replier ce bloc" : "Déplier ce bloc";
      etiquette.textContent = ouvert ? "" : resume(bloc);
    }

    var memoire = etats();
    var ouvert = (cle in memoire) ? !!memoire[cle] : !!ouvertParDefaut;
    if (location.hash) {
      var ancre = document.getElementById(location.hash.slice(1));
      if (ancre && (ancre === bloc || bloc.contains(ancre))) { ouvert = true; }
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
    titre.style.cursor = "pointer";
    titre.addEventListener("click", basculer);

    replis.push({ appliquer: appliquer, cle: cle, corps: corps });
  }

  /* Barre « tout replier / tout déplier », posée au-dessus du premier bloc. */
  function barreGlobale() {
    if (!replis.length) { return; }
    var premier = document.querySelector(".repli-entete");
    if (!premier) { return; }
    var hote = premier.closest("section, .card");
    if (!hote || !hote.parentElement) { return; }

    var barre = document.createElement("div");
    barre.className = "repli-barre";
    var bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "btn";
    var tousOuverts = false;
    function etiqueter() {
      bouton.textContent = tousOuverts ? "⌃ Tout replier" : "⌄ Tout déplier";
    }
    etiqueter();
    bouton.addEventListener("click", function () {
      tousOuverts = !tousOuverts;
      for (var i = 0; i < replis.length; i++) {
        replis[i].appliquer(tousOuverts);
        memoriser(replis[i].cle, tousOuverts);
      }
      etiqueter();
    });
    barre.appendChild(bouton);
    hote.parentElement.insertBefore(barre, hote);
  }

  function demarrer() {
    installerModales();

    // Volontairement sur tout le document : le gabarit referme certains
    // conteneurs plus tôt que prévu, donc se limiter à un ancêtre commun
    // ne trouverait qu'une partie des blocs.
    var titres = document.querySelectorAll("h2");
    for (var i = 0; i < titres.length; i++) {
      var titre = titres[i];
      if (titre.closest(".modal")) { continue; }  // jamais dans une fenêtre
      var texte = normaliser(titre.textContent);
      for (var j = 0; j < BLOCS.length; j++) {
        if (texte.indexOf(normaliser(BLOCS[j][0])) !== -1) {
          var bloc = titre.closest("section.section-card, .card, section");
          if (bloc) { replier(bloc, titre, BLOCS[j][1]); }
          break;
        }
      }
    }
    barreGlobale();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
