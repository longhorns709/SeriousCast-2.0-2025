const CACHE_NAME = 'seriouscast-v1';
const STATIC_ASSETS = [
    '/',
    '/static/styles.css',
    '/static/mobile.css',
    '/static/reset.css',
    '/static/player.js',
    '/static/jquery.cookie.js',
    '/static/img/plays.svg',
    '/static/img/pause.svg',
    '/static/img/play.svg',
    '/static/img/plus.svg',
    '/static/img/minus.svg',
    '/static/img/arrow-down.svg',
    '/static/img/volume-high.svg',
    '/static/img/volume-mute.svg',
    '/static/img/cart.svg',
    '/static/channel-art/404.webp'
];

// Install - cache static assets
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

// Activate - clean old caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

// Fetch - network first for API, cache first for static
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    
    // Skip caching for streams, metadata, and dynamic content
    if (url.pathname.startsWith('/hls/') || 
        url.pathname.startsWith('/segment/') ||
        url.pathname.startsWith('/key/') ||
        url.pathname.startsWith('/metadata/') ||
        url.pathname.startsWith('/channel/') ||
        url.pathname.startsWith('/vlc/')) {
        return;
    }
    
    // For static assets, try cache first
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then(cached => {
                return cached || fetch(event.request).then(response => {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(event.request, clone);
                    });
                    return response;
                });
            })
        );
        return;
    }
    
    // Network first for main page
    event.respondWith(
        fetch(event.request).catch(() => {
            return caches.match(event.request);
        })
    );
});
