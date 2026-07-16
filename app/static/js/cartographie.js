/* Carte des habitants — agrégation par quartier (Leaflet auto-hébergé).
 * Une bulle par quartier (taille = nombre d'habitants), aucun domicile
 * individuel. Filtres : secteur, type de public, et PÉRIODE DE PRÉSENCE
 * (année ou plage). Clic sur une bulle => lien vers les stats du quartier.
 */
(function () {
  "use strict";

  var el = document.getElementById("carte-habitants");
  if (!el || typeof L === "undefined") return;

  var cfg = {
    dataUrl: el.getAttribute("data-data-url"),
    statsUrl: el.getAttribute("data-stats-url"),
    tileUrl: el.getAttribute("data-tile-url"),
    tileAttr: el.getAttribute("data-tile-attr"),
    lat: parseFloat(el.getAttribute("data-centre-lat")) || 49.2583,
    lon: parseFloat(el.getAttribute("data-centre-lon")) || 2.475,
  };

  var map = L.map(el, { scrollWheelZoom: true }).setView([cfg.lat, cfg.lon], 13);
  L.tileLayer(cfg.tileUrl, { maxZoom: 19, attribution: cfg.tileAttr }).addTo(map);

  var layer = L.layerGroup().addTo(map);
  var infoEl = document.getElementById("carte-info");

  function rayon(total) {
    var r = 8 + Math.sqrt(total) * 4;
    return Math.max(10, Math.min(46, r));
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Période choisie => bornes pour /carte/data + querystring pour la page stats.
  function periode() {
    var sel = document.getElementById("carte-periode");
    var v = sel ? sel.value : "";
    if (v === "custom") {
      var f = document.getElementById("carte-date-from");
      var t = document.getElementById("carte-date-to");
      var from = f && f.value ? f.value : "";
      var to = t && t.value ? t.value : "";
      var qs = "period=custom" + (from ? "&date_from=" + from : "") + (to ? "&date_to=" + to : "");
      return { from: from, to: to, statsQS: qs };
    }
    if (/^\d{4}$/.test(v)) {
      return { from: v + "-01-01", to: v + "-12-31", statsQS: "period=" + v };
    }
    return { from: "", to: "", statsQS: "period=tout" };
  }

  function ventilation(titre, obj) {
    var keys = Object.keys(obj || {});
    if (!keys.length) return "";
    var lignes = keys.slice(0, 6).map(function (k) {
      return "<li>" + escapeHtml(k) + " : <strong>" + obj[k] + "</strong></li>";
    }).join("");
    return "<div class='carte-pop-sec'><div class='muted'>" + titre + "</div><ul>" + lignes + "</ul></div>";
  }

  function dessiner(data) {
    layer.clearLayers();
    var quartiers = (data && data.quartiers) || [];
    var per = periode();

    var bounds = [];
    quartiers.forEach(function (q) {
      if (q.lat == null || q.lon == null) return;
      var couleur = q.is_qpv ? "#d9534f" : "#2a6fb0";
      var marker = L.circleMarker([q.lat, q.lon], {
        radius: rayon(q.total),
        color: couleur,
        weight: 2,
        fillColor: couleur,
        fillOpacity: 0.45,
      });
      var titre = escapeHtml(q.nom) + (q.ville ? " <span class='muted'>(" + escapeHtml(q.ville) + ")</span>" : "");
      if (q.is_qpv) titre += " <span class='carte-qpv'>QPV</span>";
      // Lien vers les stats : seulement pour un vrai quartier (id non nul).
      var lien = "";
      if (q.id != null && cfg.statsUrl) {
        lien = "<div style='margin-top:6px;'><a href='" + cfg.statsUrl +
          "?quartier_id=" + q.id + "&" + per.statsQS + "'>📊 Voir les statistiques →</a></div>";
      }
      var html =
        "<div class='carte-pop'><div class='carte-pop-titre'>" + titre + "</div>" +
        "<div class='carte-pop-total'>" + q.total + " habitant(s)</div>" +
        ventilation("Par secteur", q.par_secteur) +
        ventilation("Par public", q.par_type_public) +
        lien +
        "</div>";
      marker.bindPopup(html);
      marker.bindTooltip(String(q.total), { permanent: true, direction: "center", className: "carte-count" });
      marker.addTo(layer);
      bounds.push([q.lat, q.lon]);
    });

    if (bounds.length) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
    }

    if (infoEl) {
      infoEl.innerHTML =
        "<strong>" + (data.localises || 0) + "</strong> sur la période · " +
        "<strong>" + (data.non_localises || 0) + "</strong> non localisé(s) (sans quartier positionné ni adresse géocodée)";
    }
  }

  function query() {
    var p = new URLSearchParams();
    var sec = document.getElementById("carte-secteur");
    var typ = document.getElementById("carte-type_public");
    if (sec && sec.value) p.set("secteur", sec.value);
    if (typ && typ.value) p.set("type_public", typ.value);
    var per = periode();
    if (per.from) p.set("date_from", per.from);
    if (per.to) p.set("date_to", per.to);
    return p.toString();
  }

  function charger() {
    var qs = query();
    if (infoEl) infoEl.textContent = "Chargement…";
    fetch(cfg.dataUrl + (qs ? "?" + qs : ""), { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(dessiner)
      .catch(function (e) {
        if (infoEl) infoEl.textContent = "Erreur de chargement des données (" + e.message + ").";
      });
  }

  function toggleCustom() {
    var sel = document.getElementById("carte-periode");
    var show = sel && sel.value === "custom";
    var fields = document.querySelectorAll(".carte-periode-custom");
    for (var i = 0; i < fields.length; i++) {
      fields[i].style.display = show ? "" : "none";
    }
  }

  ["carte-secteur", "carte-type_public", "carte-periode", "carte-date-from", "carte-date-to"].forEach(function (id) {
    var node = document.getElementById(id);
    if (!node) return;
    node.addEventListener("change", function () {
      if (id === "carte-periode") toggleCustom();
      charger();
    });
  });

  toggleCustom();
  charger();
})();
