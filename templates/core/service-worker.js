'use strict';

const OFFLINE_CACHE = 'construction-manager-offline-v1';
const OFFLINE_URL = '{% url "core:offline" %}';

// Only the public offline notice is cached. Authenticated pages, documents,
// project records, and financial data always remain network-only.
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(OFFLINE_CACHE)
            .then((cache) => cache.add(OFFLINE_URL))
            .then(() => self.skipWaiting()),
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys
                    .filter((key) => key.startsWith('construction-manager-offline-') && key !== OFFLINE_CACHE)
                    .map((key) => caches.delete(key)),
            ))
            .then(() => self.clients.claim()),
    );
});

self.addEventListener('fetch', (event) => {
    if (event.request.mode !== 'navigate') {
        return;
    }
    event.respondWith(
        fetch(event.request).catch(() => caches.match(OFFLINE_URL)),
    );
});
