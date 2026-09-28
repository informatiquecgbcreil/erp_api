// Mon Centre Social — service worker (version {{ version }}).
// Rien de ce qui concerne les personnes accueillies n'est gardé sur
// l'appareil : seules la page « hors connexion » et l'icône sont en cache.
// Toutes les autres requêtes vont directement au serveur du centre.
const CACHE = "mcs-hors-ligne-{{ version }}";
const HORS_LIGNE = "{{ hors_ligne }}";

self.addEventListener("install", (evenement) => {
  evenement.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([HORS_LIGNE, "{{ icone }}"])));
  self.skipWaiting();
});

self.addEventListener("activate", (evenement) => {
  evenement.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((cle) => cle !== CACHE).map((cle) => caches.delete(cle))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evenement) => {
  // Seules les ouvertures de page sont concernées, et seulement quand le
  // serveur ne répond pas (hors du wifi du centre, serveur arrêté).
  if (evenement.request.mode !== "navigate") return;
  evenement.respondWith(fetch(evenement.request).catch(() => caches.match(HORS_LIGNE)));
});
