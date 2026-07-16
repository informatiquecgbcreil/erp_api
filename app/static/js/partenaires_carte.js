/* Carte des partenaires — un marqueur cliquable par structure (Leaflet).
 * Les partenaires sont des structures (info publique) : on affiche des points
 * individuels, pas une agrégation. Données : /partenaires/carte/data.
 */
(function () {
  "use strict";

  var el = document.getElementById("carte-partenaires");
  if (!el || typeof L === "undefined") return;

  var cfg = {
    dataUrl: el.getAttribute("data-data-url"),
    tileUrl: el.getAttribute("data-tile-url"),
    tileAttr: el.getAttribute("data-tile-attr"),
    markerBase: el.getAttribute("data-marker-base"),
    lat: parseFloat(el.getAttribute("data-centre-lat")) || 49.2583,
    lon: parseFloat(el.getAttribute("data-centre-lon")) || 2.475,
  };

  var icon = L.icon({
    iconUrl: cfg.markerBase + "marker-icon.png",
    iconRetinaUrl: cfg.markerBase + "marker-icon-2x.png",
    shadowUrl: cfg.markerBase + "marker-shadow.png",
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowSize: [41, 41],
  });

  var map = L.map(el, { scrollWheelZoom: true }).setView([cfg.lat, cfg.lon], 12);
  L.tileLayer(cfg.tileUrl, { maxZoom: 19, attribution: cfg.tileAttr }).addTo(map);
  var layer = L.layerGroup().addTo(map);
  var infoEl = document.getElementById("carte-info");

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function tags(items) {
    if (!items || !items.length) return "";
    return (
      "<div class='carte-pop-tags'>" +
      items.map(function (t) { return "<span class='chip'>" + escapeHtml(t) + "</span>"; }).join(" ") +
      "</div>"
    );
  }

  function dessiner(data) {
    layer.clearLayers();
    var pts = (data && data.partenaires) || [];
    var bounds = [];
    pts.forEach(function (p) {
      if (p.lat == null || p.lon == null) return;
      var html =
        "<div class='carte-pop'><div class='carte-pop-titre'>" + escapeHtml(p.nom) + "</div>" +
        (p.adresse ? "<div class='muted'>" + escapeHtml(p.adresse) + "</div>" : "") +
        tags(p.secteurs) +
        tags(p.competences) +
        (p.tel ? "<div>📞 " + escapeHtml(p.tel) + "</div>" : "") +
        (p.email ? "<div>✉️ " + escapeHtml(p.email) + "</div>" : "") +
        (p.fiche_url ? "<div style='margin-top:6px;'><a href='" + p.fiche_url + "'>Ouvrir la fiche →</a></div>" : "") +
        "</div>";
      var m = L.marker([p.lat, p.lon], { icon: icon }).bindPopup(html);
      m.bindTooltip(p.nom, { direction: "top" });
      m.addTo(layer);
      bounds.push([p.lat, p.lon]);
    });

    if (bounds.length) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
    }

    if (infoEl) {
      infoEl.innerHTML =
        "<strong>" + (data.localises || 0) + "</strong> localisé(s) sur " + (data.total || 0) +
        " · <strong>" + (data.non_localises || 0) + "</strong> sans coordonnées (adresse manquante ou non géocodée)";
    }
  }

  function charger() {
    var sel = document.getElementById("carte-secteur");
    var url = cfg.dataUrl + (sel && sel.value ? "?secteur=" + encodeURIComponent(sel.value) : "");
    if (infoEl) infoEl.textContent = "Chargement…";
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(dessiner)
      .catch(function (e) {
        if (infoEl) infoEl.textContent = "Erreur de chargement (" + e.message + ").";
      });
  }

  var sel = document.getElementById("carte-secteur");
  if (sel) sel.addEventListener("change", charger);
  charger();
})();
