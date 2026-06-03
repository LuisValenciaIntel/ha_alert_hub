const CACHE_NAME = "home-alert-hub-v1";
const CORE_ASSETS = [
    "/",
    "/login",
    "/manifest.webmanifest",
    "/static/styles.css",
    "/static/app.js",
    "/static/icons/icon-192.svg",
    "/static/icons/icon-512.svg",
    "/static/icons/badge.svg",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS)).then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") {
        return;
    }

    const requestUrl = new URL(event.request.url);
    if (requestUrl.origin !== self.location.origin) {
        return;
    }

    if (requestUrl.pathname.startsWith("/static/") || requestUrl.pathname === "/" || requestUrl.pathname === "/login") {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                const networkFetch = fetch(event.request)
                    .then((response) => {
                        const cloned = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, cloned));
                        return response;
                    })
                    .catch(() => cached);
                return cached || networkFetch;
            })
        );
    }
});


