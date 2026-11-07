// Простой Service Worker для установки PWA
self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(clients.claim());
});

// Перехватываем запросы (пока просто пропускаем их в сеть)
self.addEventListener('fetch', (event) => {
    // В будущем здесь можно настроить кэширование картинок для офлайн-режима
    // event.respondWith(fetch(event.request));
});