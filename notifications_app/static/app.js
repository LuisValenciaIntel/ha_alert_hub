function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

const LAST_NOTIFICATION_ID_KEY = "home-alert-hub:last-notification-id";
const ALL_CATEGORIES_VALUE = "__all__";

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const normalized = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(normalized);
    return Uint8Array.from(rawData, (character) => character.charCodeAt(0));
}

function normalizeCategory(value) {
    return String(value ?? "").trim();
}

function getCategoryName(category) {
    if (typeof category === "string") {
        return normalizeCategory(category);
    }
    return normalizeCategory(category?.name);
}

function getCategoryIcon(category) {
    if (typeof category === "string") {
        return "";
    }
    return String(category?.icon ?? "").trim();
}

function getCategoryColor(category) {
    if (typeof category === "string") {
        return "";
    }
    const color = String(category?.color ?? "").trim();
    return /^#[0-9A-F]{6}$/i.test(color) ? color.toUpperCase() : "";
}

function buildCategoryBadgeStyle(color) {
    return color
        ? ` style="--category-color: ${escapeHtml(color)}; --category-color-soft: ${escapeHtml(color)}22; --category-color-border: ${escapeHtml(color)}55;"`
        : "";
}

function applyCategoryBadgeStyles(root = document) {
    for (const badge of root.querySelectorAll(".badge-category[data-category-color]")) {
        const color = getCategoryColor({ color: badge.dataset.categoryColor });
        if (!color) {
            continue;
        }
        badge.style.setProperty("--category-color", color);
        badge.style.setProperty("--category-color-soft", `${color}22`);
        badge.style.setProperty("--category-color-border", `${color}55`);
    }
}

function supportsBrowserNotifications() {
    return "Notification" in window;
}

function supportsWebPush() {
    return supportsBrowserNotifications()
        && "serviceWorker" in navigator
        && "PushManager" in window;
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

async function showBrowserNotification(item) {
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
        vibrate: [200, 100, 200],
        data: {
            url: "/notifications",
            notificationId: item.id,
        },
    };

    // Prefer service-worker showNotification: works reliably in PWA standalone
    // mode and on browsers that disallow new Notification() from page context.
    if ("serviceWorker" in navigator) {
        try {
            const registration = await navigator.serviceWorker.getRegistration("/service-worker.js");
            if (registration) {
                await registration.showNotification(item.title || "New alert", options);
                return;
            }
        } catch (_err) {
            // fall through to direct Notification API
        }
    }

    // Fallback: direct Notification constructor (PC without service worker).
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

async function fetchPushConfig() {
    const response = await fetch("/api/push/config", { headers: { Accept: "application/json" } });
    if (!response.ok) {
        throw new Error("Could not load push configuration");
    }
    return response.json();
}

async function subscribeServiceWorkerPush(registration, applicationServerKey) {
    const existingSubscription = await registration.pushManager.getSubscription();
    if (existingSubscription) {
        return existingSubscription;
    }
    return registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(applicationServerKey),
    });
}

async function savePushSubscription(subscription) {
    const response = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "Could not save push subscription");
    }
}

async function removePushSubscription(subscription) {
    const response = await fetch("/api/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: subscription?.endpoint || "" }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "Could not remove push subscription");
    }
}

async function syncPushSubscription(registration, pushConfig) {
    if (!pushConfig?.enabled || !pushConfig?.public_key) {
        throw new Error("Web push is not enabled on the server. Configure PUBLIC_BASE_URL with your HTTPS URL.");
    }

    const subscription = await subscribeServiceWorkerPush(registration, pushConfig.public_key);
    await savePushSubscription(subscription.toJSON());
    return subscription;
}

function renderNotificationTags(item) {
    const badges = [`<span class="badge">${escapeHtml(item.source || "alert")}</span>`];
    const category = normalizeCategory(item.category);
    if (category) {
        const categoryIcon = String(item.category_icon ?? "").trim();
        const categoryColor = getCategoryColor({ color: item.category_color });
        const iconMarkup = categoryIcon ? `<span class="badge-icon">${escapeHtml(categoryIcon)}</span>` : "";
        badges.push(
            `<span class="badge badge-category"${buildCategoryBadgeStyle(categoryColor)}>${iconMarkup}${escapeHtml(category)}</span>`
        );
    }
    return badges.join("");
}

function renderNotificationCard(item) {
    const message = item.message ? `<p>${escapeHtml(item.message)}</p>` : "";
    const image = item.image
        ? `
            <button
                type="button"
                class="notification-image-button"
                data-image-src="${escapeHtml(item.image)}"
                data-image-alt="Notification image for ${escapeHtml(item.title)}"
                data-image-caption="${escapeHtml(item.title)}"
                aria-label="Open image fullscreen"
            >
                <img src="${escapeHtml(item.image)}" alt="Notification image for ${escapeHtml(item.title)}" loading="lazy">
                <span class="sr-only">Open image fullscreen</span>
            </button>
        `
        : "";
    const category = normalizeCategory(item.category);

    return `
        <article class="panel notification-card" data-notification-id="${item.id}" data-category="${escapeHtml(category)}">
            <div class="notification-meta">
                <div class="notification-tags">
                    ${renderNotificationTags(item)}
                </div>
                <time datetime="${escapeHtml(item.created_at)}">${escapeHtml(item.created_at)}</time>
            </div>
            <h2>${escapeHtml(item.title)}</h2>
            ${message}
            ${image}
        </article>
    `;
}

function renderEmptyState(selectedCategory) {
    const category = normalizeCategory(selectedCategory);
    const description = category && category !== ALL_CATEGORIES_VALUE
        ? `No alerts found for the \"${escapeHtml(category)}\" category yet.`
        : "Use the Home Assistant automation endpoint to start sending alerts.";

    return `
        <article class="panel empty-state">
            <h2>No alerts yet</h2>
            <p>${description}</p>
        </article>
    `;
}

function updateCategoryFilter(categories, selectedCategory) {
    const filterNode = document.getElementById("category-filter");
    if (!filterNode) {
        return ALL_CATEGORIES_VALUE;
    }

    const seen = new Set();
    const uniqueCategories = [];
    for (const category of categories || []) {
        const name = getCategoryName(category);
        if (!name || seen.has(name)) {
            continue;
        }
        seen.add(name);
        uniqueCategories.push({
            name,
            icon: getCategoryIcon(category),
            color: getCategoryColor(category),
        });
    }

    const nextValue = uniqueCategories.some((category) => category.name === selectedCategory)
        ? selectedCategory
        : ALL_CATEGORIES_VALUE;
    const fragment = document.createDocumentFragment();

    const allOption = document.createElement("option");
    allOption.value = ALL_CATEGORIES_VALUE;
    allOption.textContent = "Show all notifications available";
    fragment.appendChild(allOption);

    for (const category of uniqueCategories) {
        const option = document.createElement("option");
        option.value = category.name;
        option.textContent = `${category.icon ? `${category.icon} ` : ""}${category.name}`;
        fragment.appendChild(option);
    }

    filterNode.replaceChildren(fragment);
    filterNode.value = nextValue;
    return nextValue;
}

function renderNotificationList(listNode, items, selectedCategory) {
    const activeCategory = normalizeCategory(selectedCategory);
    const filteredItems = activeCategory && activeCategory !== ALL_CATEGORIES_VALUE
        ? items.filter((item) => normalizeCategory(item.category) === activeCategory)
        : items;

    listNode.innerHTML = filteredItems.length
        ? filteredItems.map(renderNotificationCard).join("")
        : renderEmptyState(activeCategory);
}

function buildNotificationsApiUrl(selectedCategory) {
    const url = new URL("/api/notifications", window.location.origin);
    const category = normalizeCategory(selectedCategory);
    if (category && category !== ALL_CATEGORIES_VALUE) {
        url.searchParams.set("category", category);
    }
    return url;
}

function syncNotificationsPageUrl(selectedCategory) {
    const url = new URL(window.location.href);
    const category = normalizeCategory(selectedCategory);
    if (category && category !== ALL_CATEGORIES_VALUE) {
        url.searchParams.set("category", category);
    } else {
        url.searchParams.delete("category");
    }
    window.history.replaceState({}, "", url);
}

function closeImageViewer() {
    const viewerNode = document.getElementById("image-viewer");
    const imageNode = document.getElementById("image-viewer-image");
    const captionNode = document.getElementById("image-viewer-caption");
    if (!viewerNode || !imageNode || !captionNode) {
        return;
    }

    viewerNode.hidden = true;
    viewerNode.setAttribute("aria-hidden", "true");
    imageNode.src = "";
    imageNode.alt = "";
    captionNode.textContent = "";
    document.body.classList.remove("image-viewer-open");
}

function openImageViewer({ src, alt, caption }) {
    const viewerNode = document.getElementById("image-viewer");
    const imageNode = document.getElementById("image-viewer-image");
    const captionNode = document.getElementById("image-viewer-caption");
    const closeButtonNode = document.getElementById("image-viewer-close");
    if (!viewerNode || !imageNode || !captionNode || !closeButtonNode || !src) {
        return;
    }

    imageNode.src = src;
    imageNode.alt = alt || "Notification image";
    captionNode.textContent = caption || "";
    viewerNode.hidden = false;
    viewerNode.setAttribute("aria-hidden", "false");
    document.body.classList.add("image-viewer-open");
    closeButtonNode.focus();
}

function bindNotificationImageViewer(root = document) {
    for (const triggerNode of root.querySelectorAll(".notification-image-button")) {
        if (triggerNode.dataset.viewerBound === "true") {
            continue;
        }
        triggerNode.dataset.viewerBound = "true";
        triggerNode.addEventListener("click", () => {
            openImageViewer({
                src: triggerNode.dataset.imageSrc || "",
                alt: triggerNode.dataset.imageAlt || "",
                caption: triggerNode.dataset.imageCaption || "",
            });
        });
    }
}

function attachImageViewer() {
    const viewerNode = document.getElementById("image-viewer");
    if (!viewerNode || viewerNode.dataset.viewerReady === "true") {
        bindNotificationImageViewer(document);
        return;
    }

    viewerNode.dataset.viewerReady = "true";
    bindNotificationImageViewer(document);

    for (const closeNode of viewerNode.querySelectorAll("[data-image-viewer-close]")) {
        closeNode.addEventListener("click", closeImageViewer);
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeImageViewer();
        }
    });
}

async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) {
        return null;
    }
    return navigator.serviceWorker.register("/service-worker.js");
}

async function fetchNotifications(selectedCategory) {
    const response = await fetch(buildNotificationsApiUrl(selectedCategory), { headers: { Accept: "application/json" } });
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
        setNotificationStatus(
            "Notifications are not supported on this device/browser.",
            "Notifications unavailable", true
        );
        return;
    }

    try {
        const permission = await Notification.requestPermission();

        if (permission === "denied") {
            setNotificationStatus(
                "Notifications are blocked. Allow notifications for this site in browser settings, then try again.",
                "Notifications blocked", true
            );
            return;
        }

        if (permission !== "granted") {
            setNotificationStatus("Notification permission was not granted.", "Enable notifications", false);
            return;
        }

        // Permission granted — attempt server-side web push (works in background / PWA).
        if (supportsWebPush()) {
            try {
                const registration = await navigator.serviceWorker.ready;
                const pushConfig = await fetchPushConfig();
                if (pushConfig?.enabled && pushConfig?.public_key) {
                    await syncPushSubscription(registration, pushConfig);
                    setNotificationStatus(
                        "Push notifications are enabled. You'll be notified even when this tab is closed.",
                        "Push enabled ✓", true
                    );
                    return;
                }
            } catch (err) {
                // Server push not available or subscription failed — fall through to polling mode.
                console.warn("Web push subscription failed, using polling mode:", err.message);
            }
        }

        // Polling mode: notifications fire while the page is open (works on PC, no server push required).
        setNotificationStatus(
            "Notifications are enabled. Keep this tab open to receive alerts.",
            "Notifications enabled", true
        );
    } catch (error) {
        setNotificationStatus(`Notification setup error: ${error.message}`, "Enable notifications", false);
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
    const categoryFilterNode = document.getElementById("category-filter");
    const categoryFilterForm = document.getElementById("category-filter-form");
    const shellNode = document.querySelector(".app-shell");
    if (!listNode) {
        return;
    }

    attachInstallPrompt();
    attachImageViewer();
    const haButton = document.getElementById("trigger-home-assistant");
    if (haButton) {
        haButton.addEventListener("click", triggerHomeAssistantAutomation);
    }
    const subscribeButton = document.getElementById("enable-notifications");
    if (subscribeButton) {
        subscribeButton.addEventListener("click", enableNotifications);
    }

    await registerServiceWorker();

    // Track whether a push subscription is active so polling notifications
    // can defer to the service worker when server push is in use.
    let hasPushSubscription = false;

    // Initialise notification status and auto-sync push subscription on page load.
    if (supportsWebPush()) {
        try {
            const registration = await navigator.serviceWorker.ready;
            const existingSubscription = await registration.pushManager.getSubscription();
            hasPushSubscription = existingSubscription !== null;
            const shellPushEnabled = shellNode?.dataset.webPushEnabled === "true";

            if (Notification.permission === "granted" && shellPushEnabled) {
                const pushConfig = await fetchPushConfig();
                await syncPushSubscription(registration, pushConfig);
                hasPushSubscription = true;
            }

            if (Notification.permission === "granted" && hasPushSubscription) {
                setNotificationStatus(
                    "Push notifications are enabled. You'll be notified even when this tab is closed.",
                    "Push enabled ✓", true
                );
            } else if (Notification.permission === "granted") {
                setNotificationStatus(
                    "Notifications are enabled (polling). Keep this tab open to receive alerts.",
                    "Notifications enabled", true
                );
            } else if (Notification.permission === "denied") {
                setNotificationStatus(
                    "Notifications are blocked. Allow notifications in browser settings.",
                    "Notifications blocked", true
                );
            } else {
                setNotificationStatus(
                    "Enable notifications to get alerts on this device.",
                    "Enable notifications", false
                );
            }
        } catch (error) {
            if (Notification.permission === "granted") {
                setNotificationStatus(
                    "Notifications are enabled (polling). Keep this tab open to receive alerts.",
                    "Notifications enabled", true
                );
            } else {
                setNotificationStatus(`Notification setup: ${error.message}`, "Enable notifications", false);
            }
        }
    } else if (supportsBrowserNotifications()) {
        // Browser has Notification API but no service worker / PushManager (old browser or http).
        if (Notification.permission === "granted") {
            setNotificationStatus(
                "Notifications are enabled (polling). Keep this tab open to receive alerts.",
                "Notifications enabled", true
            );
        } else if (Notification.permission === "denied") {
            setNotificationStatus("Notifications are blocked in browser settings.", "Notifications blocked", true);
        } else {
            setNotificationStatus("Enable notifications to get alerts on this device.", "Enable notifications", false);
        }
    } else {
        setNotificationStatus("Notifications are not supported on this device/browser.", "Notifications unavailable", true);
    }

    // Re-subscribe automatically when the browser invalidates the push subscription
    // (e.g. after a browser update or OS-level permission revoke + re-grant).
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.addEventListener("message", async (event) => {
            if (event.data?.type === "pushsubscriptionchange") {
                try {
                    const registration = await navigator.serviceWorker.ready;
                    const pushConfig = await fetchPushConfig();
                    if (pushConfig?.enabled && pushConfig?.public_key) {
                        await syncPushSubscription(registration, pushConfig);
                        hasPushSubscription = true;
                        setNotificationStatus(
                            "Push notifications are enabled. You'll be notified even when this tab is closed.",
                            "Push enabled ✓", true
                        );
                    }
                } catch (err) {
                    console.warn("Failed to re-subscribe after pushsubscriptionchange:", err.message);
                }
            }
        });
    }

    const pollInterval = Number(shellNode?.dataset.pollInterval || "20000");
    const initialDomLatest = Number(listNode.querySelector("[data-notification-id]")?.dataset.notificationId || "0");
    let latestNotificationId = Math.max(getStoredNotificationId(), initialDomLatest);
    let selectedCategory = normalizeCategory(categoryFilterNode?.value) || ALL_CATEGORIES_VALUE;
    let notificationItems = [];
    if (latestNotificationId > 0) {
        setStoredNotificationId(latestNotificationId);
    }

    const syncNotifications = (payload, notifyNewItems = false) => {
        const items = Array.isArray(payload?.notifications) ? payload.notifications : [];
        const categories = Array.isArray(payload?.categories) ? payload.categories : [];
        const newestId = Number(items[0]?.id || "0");
        const unseenItems = notifyNewItems
            ? items
                .filter((item) => Number(item.id || "0") > latestNotificationId)
                .sort((left, right) => Number(left.id || "0") - Number(right.id || "0"))
            : [];

        notificationItems = items;
        selectedCategory = updateCategoryFilter(categories, selectedCategory);
        renderNotificationList(listNode, notificationItems, selectedCategory);
        applyCategoryBadgeStyles(listNode);
        bindNotificationImageViewer(listNode);

        if (newestId > 0) {
            latestNotificationId = Math.max(latestNotificationId, newestId);
            setStoredNotificationId(latestNotificationId);
        }

        for (const item of unseenItems) {
            if (Notification.permission === "granted") {
                showBrowserNotification(item);
            }
        }
    };

    if (categoryFilterNode) {
        categoryFilterNode.addEventListener("change", () => {
            selectedCategory = normalizeCategory(categoryFilterNode.value) || ALL_CATEGORIES_VALUE;
            syncNotificationsPageUrl(selectedCategory);
            if (!window.fetch && categoryFilterForm) {
                categoryFilterForm.submit();
                return;
            }
            void refresh(false);
        });
    }

    try {
        const initialPayload = await fetchNotifications(selectedCategory);
        syncNotifications(initialPayload, false);
    } catch (error) {
        const statusNode = document.getElementById("notification-status");
        if (statusNode) {
            statusNode.textContent = `Live refresh warning: ${error.message}`;
        }
    }

    async function refresh(notifyNewItems = true) {
        try {
            const payload = await fetchNotifications(selectedCategory);
            syncNotificationsPageUrl(selectedCategory);
            syncNotifications(payload, notifyNewItems);
        } catch (error) {
            const statusNode = document.getElementById("notification-status");
            if (statusNode) {
                statusNode.textContent = `Live refresh warning: ${error.message}`;
            }
        }
    }

    window.setInterval(() => {
        void refresh(true);
    }, pollInterval);
}

window.addEventListener("load", () => {
    applyCategoryBadgeStyles(document);
    void startNotificationsPage();
});


