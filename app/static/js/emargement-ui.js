/* Émargement : confort d'affichage de la page.
 *
 * Deux rôles, tous deux en enrichissement progressif — sans ce script la
 * page reste fonctionnelle, elle est seulement plus longue :
 *
 * 1. REPLIAGE des blocs marqués data-repli="ouvert" ou "ferme" (état par
 *    défaut), avec mémorisation par navigateur. Les blocs peuvent
 *    s'imbriquer : « Avant et après la séance » regroupe des blocs eux-mêmes
 *    repliables.
 * 2. MODALES sans Bootstrap. Le gabarit utilise le protocole Bootstrap
 *    (data-bs-toggle / data-bs-dismiss). Le script Bootstrap est servi en
 *    local ; s'il manque malgré tout, les boutons n'ouvriraient plus rien.
 *    On fournit donc un remplaçant minimal, activé seulement si Bootstrap
 *    est réellement absent. L'affichage des fenêtres (masquées, ouvertes)
 *    est réglé par la feuille de style du gabarit, dans les deux cas.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* 1. Modales : remplaçant minimal quand Bootstrap n'a pas pu charger   */
  /* ------------------------------------------------------------------ */

  function installerModales() {
    if (window.bootstrap && window.bootstrap.Modal) { return; }

    var retours = [];  // élément à refocaliser à la fermeture

    function ouvrir(modale) {
      if (!modale) { return; }
      retours.push(document.activeElement);
      modale.classList.add("modal-ouverte");
      // Mêmes attributs que Bootstrap : visible pour le lecteur d'écran.
      modale.removeAttribute("aria-hidden");
      modale.setAttribute("role", "dialog");
      modale.setAttribute("aria-modal", "true");
      document.body.style.overflow = "hidden";
      var champ = modale.querySelector("textarea, input:not([type=hidden]), select");
      if (champ) { try { champ.focus(); } catch (e) { /* champ masqué */ } }
    }
    function fermer(modale) {
      if (!modale) { return; }
      modale.classList.remove("modal-ouverte");
      modale.setAttribute("aria-hidden", "true");
      modale.removeAttribute("aria-modal");
      if (!document.querySelector(".modal-ouverte")) { document.body.style.overflow = ""; }
      var retour = retours.pop();
      if (retour && retour.focus) { try { retour.focus(); } catch (e) { /* élément retiré */ } }
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
  }

  /* ------------------------------------------------------------------ */
  /* 2. Repliage des blocs                                               */
  /* ------------------------------------------------------------------ */

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

  /* Rappel affiché à côté du titre quand le bloc est fermé : le texte de
     data-repli-resume s'il existe, sinon ses deux premiers indicateurs
     (pas ceux d'un bloc imbriqué). */
  function resume(bloc) {
    var fixe = bloc.getAttribute("data-repli-resume");
    if (fixe) { return fixe; }
    var valeurs = bloc.querySelectorAll(".finance-kpi__value");
    var bouts = [];
    for (var i = 0; i < valeurs.length && bouts.length < 2; i++) {
      if (valeurs[i].closest("[data-repli]") !== bloc) { continue; }
      bouts.push(normaliser(valeurs[i].textContent).replace("kwh", "kWh"));
    }
    return bouts.join(" · ");
  }

  var replis = [];

  function replier(bloc, numero) {
    var titre = bloc.querySelector("h2, h3");
    if (!titre) { return; }

    var entete = titre;
    while (entete.parentElement && entete.parentElement !== bloc) {
      entete = entete.parentElement;
    }
    if (!entete || entete.parentElement !== bloc) { return; }

    var corps = document.createElement("div");
    corps.className = "repli-corps";
    corps.id = "repli-corps-" + numero;
    var suivant = entete.nextSibling;
    while (suivant) {
      var apres = suivant.nextSibling;
      corps.appendChild(suivant);
      suivant = apres;
    }
    if (!corps.childNodes.length) { return; }  // rien à replier
    bloc.appendChild(corps);

    var libelleTitre = (titre.textContent || "").replace(/\s+/g, " ").trim();
    var bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "repli-bouton";
    bouton.setAttribute("aria-controls", corps.id);
    var chevron = document.createElement("span");
    chevron.className = "repli-chevron";
    chevron.setAttribute("aria-hidden", "true");
    var action = document.createElement("span");
    action.className = "sr-only";
    var etiquette = document.createElement("span");
    etiquette.className = "repli-resume";
    bouton.appendChild(chevron);
    bouton.appendChild(action);
    bouton.appendChild(etiquette);

    entete.classList.add("repli-entete");
    entete.appendChild(bouton);

    var cle = normaliser(libelleTitre).slice(0, 60);

    function appliquer(ouvert) {
      corps.hidden = !ouvert;
      bloc.classList.toggle("repli-ferme", !ouvert);
      bouton.setAttribute("aria-expanded", ouvert ? "true" : "false");
      chevron.textContent = ouvert ? "▾" : "▸";
      bouton.title = ouvert ? "Replier ce bloc" : "Déplier ce bloc";
      action.textContent = (ouvert ? "Replier « " : "Déplier « ") + libelleTitre + " » ";
      etiquette.textContent = ouvert ? "" : resume(bloc);
    }

    var memoire = etats();
    var ouvert = (cle in memoire) ? !!memoire[cle] : bloc.getAttribute("data-repli") !== "ferme";
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

    replis.push({ appliquer: appliquer, cle: cle, bloc: bloc, corps: corps });
  }

  /* Ouvre les blocs qui contiennent la cible d'une ancre (#hart-dues,
     #bilan-seance…), y compris le regroupement qui les contient. */
  function deplierVers(cible) {
    if (!cible) { return false; }
    var trouve = false;
    for (var i = 0; i < replis.length; i++) {
      var r = replis[i];
      if ((r.bloc === cible || r.bloc.contains(cible)) && r.corps.hidden) {
        r.appliquer(true);
        trouve = true;
      }
    }
    return trouve;
  }
  function cibleDuLien(ancre) {
    var id = (ancre || "").replace(/^#/, "");
    if (!id) { return null; }
    try { id = decodeURIComponent(id); } catch (e) { /* ancre brute */ }
    return document.getElementById(id);
  }

  /* Barre « tout replier / tout déplier », posée au-dessus du premier bloc. */
  function barreGlobale() {
    if (!replis.length) { return; }
    var hote = replis[0].bloc;
    if (!hote.parentElement) { return; }

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

    // Ordre du document : un regroupement est traité avant les blocs qu'il
    // contient, qui restent repliables une fois déplacés dans son corps.
    var blocs = document.querySelectorAll("[data-repli]");
    for (var i = 0; i < blocs.length; i++) {
      if (blocs[i].closest(".modal")) { continue; }  // jamais dans une fenêtre
      replier(blocs[i], i);
    }
    barreGlobale();

    // Arrivée sur une ancre (retour d'un formulaire, lien depuis une autre
    // page) : on ouvre ce qu'il faut, sans le mémoriser.
    if (location.hash) {
      var cible = cibleDuLien(location.hash);
      if (deplierVers(cible)) { cible.scrollIntoView(); }
    }
    window.addEventListener("hashchange", function () {
      var c = cibleDuLien(location.hash);
      if (deplierVers(c)) { c.scrollIntoView(); }
    });
    // Lien interne vers l'ancre déjà active : pas de hashchange.
    document.addEventListener("click", function (evenement) {
      var lien = evenement.target.closest && evenement.target.closest('a[href^="#"]');
      if (lien) { deplierVers(cibleDuLien(lien.getAttribute("href"))); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
