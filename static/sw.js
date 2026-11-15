// GDZ Парсер — умный Service Worker (офлайн-режим)
const STATIC_CACHE = 'gdz-static-v2';
const IMAGES_CACHE = 'gdz-images-v2';
const STATIC_URLS = [
    '/',
    '/manifest.json',
    '/static/style.css',
    '/static/animations.css',
    '/static/icon-192.png',
    '/static/icon-512.png',
];

// INSTALL — прекешируем статику
self.addEventListener('install', (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(STATIC_CACHE).then(cache => {
            return Promise.all(
                STATIC_URLS.map(url =>
                    cache.add(url).catch(err => console.log('precache fail', url, err))
                )
            );
        })
    );
});

// ACTIVATE — удаляем старые кэши
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(k => k !== STATIC_CACHE && k !== IMAGES_CACHE)
                .map(k => caches.delete(k))
        )).then(() => self.clients.claim())
    );
});

// FETCH — стратегии кэширования
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // 1. Картинки решений (/api/image?url=...) — cache-first
    if (url.pathname === '/api/image') {
        event.respondWith(
            caches.open(IMAGES_CACHE).then(async cache => {
                const cached = await cache.match(event.request);
                if (cached) return cached;
                try {
                    const resp = await fetch(event.request);
                    if (resp.ok) cache.put(event.request, resp.clone());
                    return resp;
                } catch (e) {
                    // нет сети и нет кэша — вернём пустую заглушку
                    return new Response('', { status: 503, statusText: 'Offline' });
                }
            })
        );
        return;
    }

    // 2. Статика (/static/..., /manifest.json, шрифты) — cache-first
    if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json'
        || url.hostname.includes('fonts.googleapis.com') || url.hostname.includes('fonts.gstatic.com')
        || url.hostname.includes('cdn.jsdelivr.net')) {
        event.respondWith(
            caches.match(event.request).then(cached => {
                if (cached) return cached;
                return fetch(event.request).then(resp => {
                    if (resp.ok) {
                        const clone = resp.clone();
                        caches.open(STATIC_CACHE).then(c => c.put(event.request, clone));
                    }
                    return resp;
                }).catch(() => cached);
            })
        );
        return;
    }

    // 3. Главная и админка — network-first, fallback на кэш
    if (url.pathname === '/' || url.pathname === '/admin') {
        event.respondWith(
            fetch(event.request).then(resp => {
                if (resp.ok) {
                    const clone = resp.clone();
                    caches.open(STATIC_CACHE).then(c => c.put(event.request, clone));
                }
                return resp;
            }).catch(() => caches.match(event.request).then(c => c || new Response(
                '<h1>Офлайн</h1><p>Страница не закэширована. Зайди онлайн сначала.</p>',
                { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
            )))
        );
        return;
    }

    // 4. Всё остальное — network-first с fallback
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});