/* Générateur de budget prévisionnel — éditeur de structure.
   Rend et pilote une structure éditable (ajouter/retirer des comptes,
   ajouter/supprimer/renommer des lignes), calcule les totaux en direct,
   sauvegarde localement (localStorage) et sérialise la structure à l'envoi. */
(function () {
  "use strict";

  var root = document.getElementById("genRoot");
  if (!root) return;

  var LABELS = window.CERFA_SECTION_LABELS || {
    charges: "Charges", produits: "Produits",
    contrib_emplois: "Contributions volontaires — emplois",
    contrib_ressources: "Contributions volontaires — ressources"
  };
  var OPERATING = ["charges", "produits"];
  var CONTRIB = ["contrib_emplois", "contrib_ressources"];
  var STORAGE_KEY = "budget_generateur_structure_v1";

  var form = document.getElementById("cerfaForm");
  var hiddenStructure = document.getElementById("structureJson");

  function parseAmount(raw) {
    raw = (raw || "").toString().replace(/\s/g, "").replace(/,/g, ".");
    if (!raw) return 0;
    if (/^[0-9]*\.?[0-9]+$/.test(raw)) return parseFloat(raw);
    if (/^[0-9.+]+$/.test(raw)) {
      var parts = raw.split("+"), total = 0, any = false;
      for (var i = 0; i < parts.length; i++) {
        if (parts[i] === "") continue;
        var n = Number(parts[i]);
        if (isNaN(n)) return 0;
        total += n; any = true;
      }
      return any ? total : 0;
    }
    return 0;
  }
  function fmt(n) {
    return (Math.round(n * 100) / 100).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function el(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  function buildLine(line) {
    var row = el("div", "gen-line");
    var lib = el("input", "in gen-llib", { placeholder: "Libellé de la ligne", "aria-label": "Libellé de la ligne" });
    lib.value = (line && line.libelle) || "";
    var amt = el("input", "in gen-amt", { inputmode: "decimal", placeholder: "0", "aria-label": "Montant" });
    amt.value = (line && line.montant) ? line.montant : "";
    var del = el("button", "btn danger gen-del-line", { type: "button", title: "Supprimer la ligne" });
    del.textContent = "🗑";
    row.appendChild(lib); row.appendChild(amt); row.appendChild(del);
    return row;
  }

  function buildCompte(sec, compte) {
    var box = el("div", "gen-compte");
    box.setAttribute("data-sec", sec);
    var head = el("div", "gen-compte-head");
    var code = el("input", "in gen-code", { placeholder: "Code", "aria-label": "Code du compte" });
    code.value = (compte && compte.code) || "";
    var clib = el("input", "in gen-clib", { placeholder: "Libellé du compte", "aria-label": "Libellé du compte" });
    clib.value = (compte && compte.libelle) || "";
    var sub = el("span", "gen-csub"); sub.textContent = "0,00 €";
    var addLine = el("button", "btn gen-add-line", { type: "button", title: "Ajouter une ligne" });
    addLine.textContent = "＋ ligne";
    var delC = el("button", "btn danger gen-del-compte", { type: "button", title: "Supprimer le compte" });
    delC.textContent = "🗑";
    head.appendChild(code); head.appendChild(clib); head.appendChild(sub); head.appendChild(addLine); head.appendChild(delC);
    var lines = el("div", "gen-lines");
    ((compte && compte.lines) || []).forEach(function (l) { lines.appendChild(buildLine(l)); });
    box.appendChild(head); box.appendChild(lines);
    return box;
  }

  function buildSection(sec, comptes) {
    var s = el("section", "gen-sec section-card"); s.setAttribute("data-sec", sec);
    var head = el("div", "gen-sec-head");
    var h = el("h3"); h.textContent = LABELS[sec] || sec;
    var tot = el("span", "gen-sec-total"); tot.textContent = "0,00 €";
    head.appendChild(h); head.appendChild(tot);
    var wrap = el("div", "gen-comptes");
    (comptes || []).forEach(function (c) { wrap.appendChild(buildCompte(sec, c)); });
    var add = el("button", "btn ok gen-add-compte", { type: "button" });
    add.textContent = "＋ Ajouter un compte";
    s.appendChild(head); s.appendChild(wrap); s.appendChild(add);
    return s;
  }

  function render(structure) {
    root.innerHTML = "";
    var g1 = el("div", "gen-grid");
    OPERATING.forEach(function (k) { g1.appendChild(buildSection(k, structure[k])); });
    root.appendChild(g1);
    var h = el("h3", "section-title"); h.style.marginTop = "18px";
    h.textContent = "Contributions volontaires en nature";
    root.appendChild(h);
    var g2 = el("div", "gen-grid");
    CONTRIB.forEach(function (k) { g2.appendChild(buildSection(k, structure[k])); });
    root.appendChild(g2);
  }

  function collect() {
    var out = { charges: [], produits: [], contrib_emplois: [], contrib_ressources: [] };
    root.querySelectorAll(".gen-sec").forEach(function (secEl) {
      var sec = secEl.getAttribute("data-sec");
      if (!out[sec]) return;
      secEl.querySelectorAll(".gen-compte").forEach(function (cEl) {
        var lines = [];
        cEl.querySelectorAll(".gen-line").forEach(function (lEl) {
          var lib = lEl.querySelector(".gen-llib").value.trim();
          var amt = parseAmount(lEl.querySelector(".gen-amt").value);
          if (lib || amt) lines.push({ libelle: lib, montant: amt });
        });
        var code = cEl.querySelector(".gen-code").value.trim();
        var clib = cEl.querySelector(".gen-clib").value.trim();
        if (code || clib || lines.length) out[sec].push({ code: code, libelle: clib, lines: lines });
      });
    });
    return out;
  }

  function recompute() {
    var sectionTotals = { charges: 0, produits: 0, contrib_emplois: 0, contrib_ressources: 0 };
    root.querySelectorAll(".gen-sec").forEach(function (secEl) {
      var sec = secEl.getAttribute("data-sec");
      var secTotal = 0;
      secEl.querySelectorAll(".gen-compte").forEach(function (cEl) {
        var sub = 0;
        cEl.querySelectorAll(".gen-amt").forEach(function (a) { sub += parseAmount(a.value); });
        var subEl = cEl.querySelector(".gen-csub");
        if (subEl) subEl.textContent = fmt(sub) + " €";
        secTotal += sub;
      });
      var t = secEl.querySelector(".gen-sec-total");
      if (t) t.textContent = fmt(secTotal) + " €";
      if (sec in sectionTotals) sectionTotals[sec] = secTotal;
    });
    var tgc = sectionTotals.charges + sectionTotals.contrib_emplois;
    var tgp = sectionTotals.produits + sectionTotals.contrib_ressources;
    var eq = tgp - tgc;
    set("tCharges", fmt(sectionTotals.charges));
    set("tProduits", fmt(sectionTotals.produits));
    var eqEl = document.getElementById("tEquilibre");
    if (eqEl) { eqEl.textContent = fmt(eq); eqEl.className = Math.abs(eq) < 0.005 ? "eq-ok" : "eq-bad"; }
  }
  function set(id, txt) { var e = document.getElementById(id); if (e) e.textContent = txt; }

  var saveTimer = null;
  function autosave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(collect())); } catch (e) {}
    }, 400);
  }

  // Délégation d'événements
  root.addEventListener("input", function () { recompute(); autosave(); });
  root.addEventListener("click", function (e) {
    var t = e.target;
    if (t.classList.contains("gen-del-line")) {
      t.closest(".gen-line").remove(); recompute(); autosave();
    } else if (t.classList.contains("gen-del-compte")) {
      t.closest(".gen-compte").remove(); recompute(); autosave();
    } else if (t.classList.contains("gen-add-line")) {
      t.closest(".gen-compte").querySelector(".gen-lines").appendChild(buildLine(null));
      autosave();
    } else if (t.classList.contains("gen-add-compte")) {
      var secEl = t.closest(".gen-sec");
      secEl.querySelector(".gen-comptes").appendChild(buildCompte(secEl.getAttribute("data-sec"), { lines: [{}] }));
      autosave();
    }
  });

  // Initialisation : localStorage sinon structure par défaut du serveur.
  function defaultStructure() {
    try { return JSON.parse(document.getElementById("cerfaDefault").textContent); }
    catch (e) { return { charges: [], produits: [], contrib_emplois: [], contrib_ressources: [] }; }
  }
  var initial = null;
  try {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved) initial = JSON.parse(saved);
  } catch (e) {}
  if (!initial || typeof initial !== "object") initial = defaultStructure();
  ["charges", "produits", "contrib_emplois", "contrib_ressources"].forEach(function (k) {
    if (!Array.isArray(initial[k])) initial[k] = [];
  });
  render(initial);
  recompute();

  // Réinitialiser au modèle CERFA
  var resetBtn = document.getElementById("genReset");
  if (resetBtn) resetBtn.addEventListener("click", function () {
    if (!confirm("Réinitialiser au modèle CERFA ? Vos modifications locales seront perdues.")) return;
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    render(defaultStructure()); recompute();
  });

  // Sérialisation à l'envoi + miroir du nom
  if (form && hiddenStructure) {
    form.addEventListener("submit", function () {
      hiddenStructure.value = JSON.stringify(collect());
      var subvNom = form.querySelector('input[name="subvention_nom"]');
      var budgetMirror = document.getElementById("budgetNomMirror");
      if (subvNom && budgetMirror) budgetMirror.value = subvNom.value;
      // L'export télécharge sans recharger : on réactive les boutons.
      setTimeout(function () {
        form.querySelectorAll('button[type="submit"]').forEach(function (b) {
          b.disabled = false; b.classList.remove("is-pending");
          if (b.dataset.originalText) b.innerHTML = b.dataset.originalText;
          delete b.dataset.pendingApplied;
        });
      }, 2500);
    });
  }
})();
