const CACHE_NAME = "home-alert-hub-v3";
const CORE_ASSETS = [
    "/",
    "/login",
    "/notifications",
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

    if (requestUrl.pathname.startsWith("/static/") || requestUrl.pathname === "/" || requestUrl.pathname === "/login" || requestUrl.pathname === "/notifications") {
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

self.addEventListener("push", (event) => {
    const payload = event.data ? event.data.json() : {};
    const title = payload.title || "New alert";
    const options = {
        body: payload.body || "A new Home Assistant notification is available.",
        icon: payload.icon || "/static/icons/icon-192.svg",
        badge: payload.badge || "/static/icons/badge.svg",
        image: payload.image || undefined,
        tag: payload.tag || undefined,
        renotify: true,
        vibrate: [200, 100, 200],
        data: {
            url: payload.url || "/notifications",
            notificationId: payload.notificationId || null,
            category: payload.category || "",
        },
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const targetUrl = event.notification.data?.url || "/notifications";
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if (client.url === targetUrl && "focus" in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
            return undefined;
        })
    );
});

// When the browser expires or rotates the push subscription, tell any open
// window so it can fetch a fresh subscription and re-register with the server.
self.addEventListener("pushsubscriptionchange", (event) => {
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                client.postMessage({ type: "pushsubscriptionchange" });
            }
        })
    );
});


