function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

const LAST_NOTIFICATION_ID_KEY = "home-alert-hub:last-notification-id";

function supportsBrowserNotifications() {
    return "Notification" in window;
}

function getStoredNotificationId() {
    return Number(window.localStorage.getItem(LAST_NOTIFICATION_ID_KEY) || "0");
}

function setStoredNotificationId(notificationId) {
    if (notificationId > 0) {
        window.localStorage.setItem(LAST_NOTIFICATION_ID_KEY, String(notificationId));
    }
}

function setNotificationStatus(message, buttonText = null, disabled = false) {
    const statusNode = document.getElementById("notification-status");
    const buttonNode = document.getElementById("enable-notifications");
    if (statusNode) {
        statusNode.textContent = message;
    }
    if (buttonNode && buttonText !== null) {
        buttonNode.textContent = buttonText;
        buttonNode.disabled = disabled;
    }
}

function setHomeAssistantStatus(message, isError = false) {
    const statusNode = document.getElementById("home-assistant-status");
    if (statusNode) {
        statusNode.textContent = message;
        statusNode.classList.toggle("error", isError);
    }
}

function showBrowserNotification(item) {
    if (!supportsBrowserNotifications() || Notification.permission !== "granted") {
        return;
    }

    const options = {
        body: item.message || "A new Home Assistant notification is available.",
        icon: "/static/icons/icon-192.svg",
        badge: "/static/icons/badge.svg",
        image: item.image || undefined,
        tag: `alert-${item.id}`,
        renotify: true,
        data: {
            url: "/notifications",
            notificationId: item.id,
        },
    };

    try {
        const notification = new Notification(item.title || "New alert", options);
        notification.onclick = () => {
            window.focus();
            window.location.href = "/notifications";
            notification.close();
        };
    } catch (error) {
        console.warn("Could not display browser notification", error);
    }
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

async function triggerHomeAssistantAutomation() {
    const buttonNode = document.getElementById("trigger-home-assistant");
    if (!buttonNode) {
        return;
    }

    const entityId = buttonNode.dataset.entityId || "";
    if (!entityId) {
        setHomeAssistantStatus("Home Assistant automation entity_id is not configured.", true);
        return;
    }

    buttonNode.disabled = true;
    setHomeAssistantStatus("Sending the automation trigger to Home Assistant...");

    try {
        const response = await fetch("/api/home-assistant/trigger", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ entity_id: entityId }),
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
            const errorMessage = payload.error || "Could not trigger Home Assistant automation";
            console.error("Home Assistant trigger error", errorMessage);
            setHomeAssistantStatus(`Home Assistant trigger error: ${errorMessage}`, true);
            return;
        }

        setHomeAssistantStatus("Home Assistant automation triggered successfully.");
    } catch (error) {
        console.error("Home Assistant trigger error", error);
        setHomeAssistantStatus(`Home Assistant trigger error: ${error.message}`, true);
    } finally {
        buttonNode.disabled = false;
    }
}

async function enableNotifications() {
    if (!supportsBrowserNotifications()) {
        setNotificationStatus("Browser notifications are not supported on this device/browser.", "Notifications unavailable", true);
        return;
    }

    try {
        const permission = await Notification.requestPermission();
        if (permission === "granted") {
            setNotificationStatus("Browser notifications are enabled.", "Notifications enabled", true);
            return;
        }

        if (permission === "denied") {
            setNotificationStatus("Browser notifications were blocked in the browser settings. Please allow notifications for this site, then tap the button again.", "Notifications blocked", true);
            return;
        }

        setNotificationStatus("Browser notification permission was not granted. No push subscription is required anymore.", "Enable notifications", false);
    } catch (error) {
        setNotificationStatus(`Browser notification status: ${error.message}`, "Enable notifications", false);
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
    const haButton = document.getElementById("trigger-home-assistant");
    if (haButton) {
        haButton.addEventListener("click", triggerHomeAssistantAutomation);
    }
    const subscribeButton = document.getElementById("enable-notifications");
    if (subscribeButton) {
        subscribeButton.addEventListener("click", enableNotifications);
    }

    await registerServiceWorker();

    if (supportsBrowserNotifications()) {
        if (Notification.permission === "granted") {
            setNotificationStatus("Browser notifications are enabled.", "Notifications enabled", true);
        } else if (Notification.permission === "denied") {
            setNotificationStatus("Browser notifications were blocked in the browser settings.", "Notifications blocked", true);
        } else {
            setNotificationStatus("Browser notifications are off. Tap the button to enable them.", "Enable notifications", false);
        }
    } else {
        setNotificationStatus("Browser notifications are not supported on this device/browser.", "Notifications unavailable", true);
    }

    const shellNode = document.querySelector(".app-shell");
    const pollInterval = Number(shellNode?.dataset.pollInterval || "20000");
    const initialDomLatest = Number(listNode.querySelector("[data-notification-id]")?.dataset.notificationId || "0");
    let latestNotificationId = Math.max(getStoredNotificationId(), initialDomLatest);
    if (latestNotificationId > 0) {
        setStoredNotificationId(latestNotificationId);
    }

    const initialNotifications = Array.from(listNode.querySelectorAll("[data-notification-id]"));
    if (!initialNotifications.length) {
        try {
            const payload = await fetchNotifications();
            const items = payload.notifications || [];
            if (items.length) {
                latestNotificationId = Math.max(latestNotificationId, Number(items[0]?.id || "0"));
                setStoredNotificationId(latestNotificationId);
                listNode.innerHTML = items.map(renderNotificationCard).join("");
            }
        } catch (error) {
            const statusNode = document.getElementById("notification-status");
            if (statusNode) {
                statusNode.textContent = `Live refresh warning: ${error.message}`;
            }
        }
    }

    const refresh = async () => {
        try {
            const payload = await fetchNotifications();
            const items = payload.notifications || [];
            const newestId = Number(items[0]?.id || "0");
            if (newestId && newestId !== latestNotificationId) {
                const unseenItems = items
                    .filter((item) => Number(item.id || "0") > latestNotificationId)
                    .sort((left, right) => Number(left.id || "0") - Number(right.id || "0"));

                latestNotificationId = newestId;
                setStoredNotificationId(latestNotificationId);
                listNode.innerHTML = items.length
                    ? items.map(renderNotificationCard).join("")
                    : `
                        <article class="panel empty-state">
                            <h2>No alerts yet</h2>
                            <p>Use the Home Assistant automation endpoint to start sending notifications.</p>
                        </article>
                    `;

                for (const item of unseenItems) {
                    showBrowserNotification(item);
                }
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


