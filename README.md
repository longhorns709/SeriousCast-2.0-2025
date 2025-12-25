# SeriousCast 2.0 (2025)

Modernized SiriusXM web player and HLS proxy. Handles authentication and decryption of SiriusXM streams, serves clean HLS playlists for VLC/mpv, and ships a plugin-free HTML5 web UI with dark/light themes, favorites, and downloadable M3U playlists.

## Requirements

SeriousCast is written in Python 3.9+.

It has dependencies on:
* [cryptography](https://cryptography.io/en/latest/)
* [Requests](http://docs.python-requests.org/en/latest/)
* [Jinja2](http://jinja.pocoo.org/docs/)
* [bitstring](http://pythonhosted.org//bitstring/)
* [hls.js](https://github.com/video-dev/hls.js) (browser playback)

You can use `pip install -r requirements.txt` to install these packages. Windows users will need to
get an [OpenSSL binary](https://www.openssl.org/related/binaries.html). Linux users will need the
relevant packages installed to [build cryptography](https://cryptography.io/en/latest/installation/#building-cryptography-on-linux).

The legacy VLC browser plugin is removed; playback is pure HTML5 with HLS.

## Setup

1) Copy `settings-example.cfg` to `settings.cfg` and fill in SiriusXM `username`/`password`, plus `hostname`/`port` (default 30000).
2) Install dependencies: `pip install -r requirements.txt`.
3) Run `python server.py` and open `http://<host>:<port>/`.

Web UI features (no plugins required):
- Play channels via HLS in-browser (HTML5 audio + hls.js fallback).
- Light/dark toggle.
- Favorites list with downloadable `favorites.m3u8` (per your selected channels).
- Channel art support: place 190x190 WebP files in `static/channel-art/` named `<channel>.webp`; missing art falls back to `404.webp`.
- Downloads: each channel exposes its `.m3u8` for VLC/mpv.

HLS endpoints (useful for external players):
- `/hls/<channel>.m3u8` – playlist with local segment/key rewrite.
- `/segment/<channel>?path=...` – proxied AAC segments.
- `/key/1` – AES-128 key.

Metadata:
- `/metadata/<channel>` returns JSON with channel info and now-playing artist/title/album pulled from SiriusXM.

## License

SeriousCast is licensed under the MIT (Expat) License.
