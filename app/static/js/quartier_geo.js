/* Placement manuel d'un quartier sur une mini-carte (fiche quartier).
 * Clic ou glisser du repère => écrit latitude/longitude dans le formulaire.
 */
(function () {
  "use strict";

  var el = document.getElementById("quartier-map");
  if (!el || typeof L === "undefined") return;

  var latInput = document.getElementById("quartier-lat");
  var lonInput = document.getElementById("quartier-lon");
  var effacer = document.getElementById("quartier-effacer");
  var label = document.getElementById("quartier-coords-label");

  var markerBase = el.getAttribute("data-marker-base");
  var icon = L.icon({
    iconUrl: markerBase + "marker-icon.png",
    iconRetinaUrl: markerBase + "marker-icon-2x.png",
    shadowUrl: markerBase + "marker-shadow.png",
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    shadowSize: [41, 41],
  });

  var hasCoord = !!(latInput.value && lonInput.value);
  var lat = parseFloat(latInput.value) || 49.2583;
  var lon = parseFloat(lonInput.value) || 2.475;

  var map = L.map(el).setView([lat, lon], hasCoord ? 15 : 13);
  L.tileLayer(el.getAttribute("data-tile-url"), {
    maxZoom: 19,
    attribution: el.getAttribute("data-tile-attr"),
  }).addTo(map);

  var marker = null;

  function set(la, lo) {
    latInput.value = la.toFixed(6);
    lonInput.value = lo.toFixed(6);
    if (effacer) effacer.value = "0";
    if (label) label.textContent = "Position définie (manuelle)";
  }

  function place(la, lo) {
    if (marker) {
      marker.setLatLng([la, lo]);
    } else {
      marker = L.marker([la, lo], { icon: icon, draggable: true }).addTo(map);
      marker.on("dragend", function () {
        var p = marker.getLatLng();
        set(p.lat, p.lng);
      });
    }
    set(la, lo);
  }

  if (hasCoord) place(lat, lon);

  map.on("click", function (e) {
    place(e.latlng.lat, e.latlng.lng);
  });

  var clearBtn = document.getElementById("quartier-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      if (marker) {
        map.removeLayer(marker);
        marker = null;
      }
      latInput.value = "";
      lonInput.value = "";
      if (effacer) effacer.value = "1";
      if (label) label.textContent = "Aucune position (auto)";
    });
  }
})();
