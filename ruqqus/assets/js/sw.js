// The site's service worker, served at /sw.js (routes/static.py) so it may show notifications for the whole site. It only shows
// push notifications (helpers/push_send.py) and opens the chat a tap is for: it has no fetch handler, so it never sees or
// keeps a page, and every request goes to the network as before.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function localPath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : "/chat";
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = {};
  }
  const title = typeof data.title === "string" ? data.title.slice(0, 80) : "";
  const options = {
    body: typeof data.body === "string" ? data.body.slice(0, 200) : "",
    tag: typeof data.tag === "string" ? data.tag.slice(0, 120) : "chat",
    renotify: true,
    requireInteraction: !!data.call,
    icon: "/assets/images/favicons/android-chrome-192x192.png",
    data: { url: localPath(data.url) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(localPath(event.notification.data && event.notification.data.url), self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
    for (const w of windows) {
      if (w.url === target && "focus" in w) return w.focus();
    }
    return self.clients.openWindow(target);
  }));
});
