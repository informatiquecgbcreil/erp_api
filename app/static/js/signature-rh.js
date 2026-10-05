/* Cadres de signature de l'espace salarié.
 *
 * Chaque bloc [data-signature] contient un <canvas> et un champ caché
 * [data-signature-input]. À l'envoi du formulaire, le dessin est copié dans
 * le champ (PNG base64) ; un cadre vierge laisse le champ vide, et le
 * serveur refuse (sauf case « je signe » prévue pour les appareils dont le
 * cadre ne transmet rien). Même principe que le kiosque d'émargement.
 */
(function () {
  "use strict";

  function brancher(bloc) {
    var canvas = bloc.querySelector("canvas");
    var champ = bloc.querySelector("[data-signature-input]");
    if (!canvas || !champ || bloc.dataset.pret) return;
    bloc.dataset.pret = "1";
    var ctx = canvas.getContext("2d");
    var dessine = false, dernier = null, aTrace = false;

    function peindre() {
      ctx.save();
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.restore();
      aTrace = false;
      champ.value = "";
    }
    function ajuster() {
      var largeur = Math.round(canvas.clientWidth);
      if (largeur > 0 && canvas.width !== largeur) canvas.width = largeur;
      peindre();
    }
    function position(e) {
      var r = canvas.getBoundingClientRect();
      return { x: (e.clientX - r.left) * (canvas.width / r.width), y: (e.clientY - r.top) * (canvas.height / r.height) };
    }
    function tracer(p) {
      ctx.lineWidth = 2.5; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#111827";
      ctx.beginPath(); ctx.moveTo(dernier.x, dernier.y); ctx.lineTo(p.x, p.y); ctx.stroke();
      dernier = p; aTrace = true;
    }
    canvas.addEventListener("pointerdown", function (e) {
      dessine = true; dernier = position(e);
      try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    canvas.addEventListener("pointermove", function (e) { if (dessine) { tracer(position(e)); e.preventDefault(); } });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (nom) {
      canvas.addEventListener(nom, function () {
        if (!dessine) return;
        dessine = false; dernier = null;
        if (aTrace) champ.value = canvas.toDataURL("image/png");
      });
    });
    var effacer = bloc.querySelector("[data-signature-clear]");
    if (effacer) effacer.addEventListener("click", function (e) { e.preventDefault(); peindre(); });
    var formulaire = bloc.closest("form");
    if (formulaire) formulaire.addEventListener("submit", function () {
      if (aTrace) champ.value = canvas.toDataURL("image/png");
    });
    canvas.style.touchAction = "none";
    ajuster();
  }

  function tout() { document.querySelectorAll("[data-signature]").forEach(brancher); }

  // Les cadres placés dans un <details> fermé n'ont pas de largeur : on les
  // (re)dimensionne à l'ouverture.
  document.addEventListener("toggle", function (e) {
    if (e.target && e.target.open) e.target.querySelectorAll("[data-signature]").forEach(function (b) {
      delete b.dataset.pret; brancher(b);
    });
  }, true);

  /* Suggestions d'agenda : quand la date change, la liste « C'était pour… »
   * se recharge avec les séances et réunions de ce jour-là. */
  function suggestions() {
    document.querySelectorAll("[data-agenda-jour]").forEach(function (champDate) {
      var cible = document.getElementById(champDate.dataset.agendaJour);
      if (!cible) return;
      champDate.addEventListener("change", function () {
        fetch(champDate.dataset.agendaUrl + "?jour=" + encodeURIComponent(champDate.value), { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : []; })
          .then(function (liste) {
            var vide = cible.querySelector("option[value='']");
            cible.innerHTML = "";
            cible.appendChild(vide || new Option("— Rien de particulier —", ""));
            liste.forEach(function (e) { cible.appendChild(new Option(e.libelle, e.cle)); });
          })
          .catch(function () {});
      });
    });
  }

  function demarrer() { tout(); suggestions(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer);
  else demarrer();
})();
