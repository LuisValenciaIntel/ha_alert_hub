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

self.addEventListener("push", (event) => {
    let payload = {};
    if (event.data) {
        try {
            payload = event.data.json();
        } catch (error) {
            payload = { title: "New alert", body: event.data.text() };
        }
    }

    const title = payload.title || "New alert";
    const options = {
        body: payload.body || "A new Home Assistant notification is available.",
        icon: payload.icon || "/static/icons/icon-192.svg",
        badge: payload.badge || "/static/icons/badge.svg",
        image: payload.image || undefined,
        data: {
            url: payload.url || "/notifications",
            notificationId: payload.notificationId || null,
        },
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const targetUrl = new URL(event.notification.data?.url || "/notifications", self.location.origin).href;

    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if (client.url === targetUrl && "focus" in client) {
                    return client.focus();
                }
            }

            if (self.clients.openWindow) {
                return self.clients.openWindow(targetUrl);
            }
            return null;
        })
    );
});

