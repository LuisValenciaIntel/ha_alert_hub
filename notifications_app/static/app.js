function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
}

function renderNotificationCard(item) {
    const message = item.message ? `<p>${escapeHtml(item.message)}</p>` : "";
    const image = item.image
        ? `<img src="${escapeHtml(item.image)}" alt="Notification image for ${escapeHtml(item.title)}" loading="lazy">`
        : "";

    return `
        <article class="panel notification-card" data-notification-id="${item.id}">
            <div class="notification-meta">
                <span class="badge">${escapeHtml(item.source || "alert")}</span>
                <time datetime="${escapeHtml(item.created_at)}">${escapeHtml(item.created_at)}</time>
            </div>
            <h2>${escapeHtml(item.title)}</h2>
            ${message}
            ${image}
        </article>
    `;
}

async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) {
        return null;
    }
    return navigator.serviceWorker.register("/service-worker.js");
}

async function fetchNotifications() {
    const response = await fetch("/api/notifications", { headers: { Accept: "application/json" } });
    if (!response.ok) {
        throw new Error("Could not refresh notifications");
    }
    return response.json();
}

async function syncSubscription(registration) {
    const statusNode = document.getElementById("notification-status");
    const subscribeButton = document.getElementById("enable-notifications");

    if (!("PushManager" in window) || !("Notification" in window)) {
        statusNode.textContent = "Push notifications are not supported on this device/browser.";
        subscribeButton.disabled = true;
        return;
    }

    if (Notification.permission === "denied") {
        statusNode.textContent = "Push notifications were blocked in the browser settings.";
        subscribeButton.disabled = true;
        return;
    }

    const existingSubscription = await registration.pushManager.getSubscription();
    if (existingSubscription) {
        statusNode.textContent = "Push notification status: enabled.";
        subscribeButton.textContent = "Notifications enabled";
        subscribeButton.disabled = true;
    }
}

async function enableNotifications() {
    const statusNode = document.getElementById("notification-status");
    const subscribeButton = document.getElementById("enable-notifications");

    try {
        const registration = await registerServiceWorker();
        if (!registration) {
            statusNode.textContent = "Push notification status: service workers are not supported here.";
            return;
        }

        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
            statusNode.textContent = "Push notification status: permission was not granted.";
            return;
        }

        const keyResponse = await fetch("/api/push/public-key", { headers: { Accept: "application/json" } });
        if (!keyResponse.ok) {
            statusNode.textContent = "Push notification status: could not fetch the push key.";
            return;
        }
        const { publicKey } = await keyResponse.json();
        const applicationServerKey = urlBase64ToUint8Array(publicKey);

        let subscription = await registration.pushManager.getSubscription();
        if (!subscription) {
            subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey,
            });
        }

        const response = await fetch("/api/push/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(subscription),
        });
        if (!response.ok) {
            statusNode.textContent = "Push notification status: could not save the push subscription.";
            return;
        }

        statusNode.textContent = "Push notification status: enabled.";
        subscribeButton.textContent = "Notifications enabled";
        subscribeButton.disabled = true;
    } catch (error) {
        statusNode.textContent = `Push notification status: ${error.message}`;
    }
}

function attachInstallPrompt() {
    const installButton = document.getElementById("install-app");
    if (!installButton) {
        return;
    }

    let deferredPrompt = null;
    window.addEventListener("beforeinstallprompt", (event) => {
        event.preventDefault();
        deferredPrompt = event;
        installButton.hidden = false;
    });

    installButton.addEventListener("click", async () => {
        if (!deferredPrompt) {
            return;
        }
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
        installButton.hidden = true;
    });
}

async function startNotificationsPage() {
    const listNode = document.getElementById("notifications-list");
    if (!listNode) {
        return;
    }

    attachInstallPrompt();
    const subscribeButton = document.getElementById("enable-notifications");
    if (subscribeButton) {
        subscribeButton.addEventListener("click", enableNotifications);
    }

    const registration = await registerServiceWorker();
    if (registration) {
        await syncSubscription(registration);
    }

    const shellNode = document.querySelector(".app-shell");
    const pollInterval = Number(shellNode?.dataset.pollInterval || "20000");
    let latestNotificationId = Number(listNode.querySelector("[data-notification-id]")?.dataset.notificationId || "0");

    const refresh = async () => {
        try {
            const payload = await fetchNotifications();
            const items = payload.notifications || [];
            const newestId = Number(items[0]?.id || "0");
            if (newestId && newestId !== latestNotificationId) {
                latestNotificationId = newestId;
                listNode.innerHTML = items.length
                    ? items.map(renderNotificationCard).join("")
                    : `
                        <article class="panel empty-state">
                            <h2>No alerts yet</h2>
                            <p>Use the Home Assistant automation endpoint to start sending notifications.</p>
                        </article>
                    `;
            }
        } catch (error) {
            const statusNode = document.getElementById("notification-status");
            if (statusNode) {
                statusNode.textContent = `Live refresh warning: ${error.message}`;
            }
        }
    };

    window.setInterval(refresh, pollInterval);
}

window.addEventListener("load", () => {
    void startNotificationsPage();
});


