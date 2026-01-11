#!/usr/bin/env python3

import http.server
import socketserver
import re
import configparser
import os
import mimetypes
import json
import sys
import logging
import collections
import time
import math
import urllib.parse

import jinja2

import sirius
import mpegutils

# Register additional MIME types for PWA
mimetypes.add_type('application/manifest+json', '.json')
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('image/webp', '.webp')


class Singleton(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class SeriousBackend(metaclass=Singleton):
    def __init__(self):
        if not os.path.isfile('settings.cfg'):
            logging.critical('settings.cfg not found')
            sys.exit(1)

        self._cfg = configparser.ConfigParser()
        self._cfg.read('settings.cfg')

        self.sxm = sirius.Sirius()
        self.templates = jinja2.Environment(loader=jinja2.FileSystemLoader('templates'), autoescape=True)

        username = self.config('username')
        password = self.config('password')
        logging.info('Signing in with username "{}"'.format(username))
        self.sxm.login(username, password)


    def config(self, key):
        return self._cfg.get('SeriousCast', key)


class SeriousHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class SeriousRequestHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.sbe = SeriousBackend()
        super().__init__(*args, **kwargs)


    def send_standard_headers(self, content_length, headers=None, response_code=200):
        logging.debug('HTTP {} [{}] ({} b)'.format(response_code, self.path, content_length))

        self.protocol_version = 'HTTP/1.1'
        self.send_response_only(response_code)
        self.send_header('Connection', 'close')
        self.send_header('Content-length', content_length)

        if headers != None:
            for field_name, field_value in headers.items():
                self.send_header(field_name, field_value)

        self.end_headers()


    def index(self):
        template = self.sbe.templates.get_template('list.html')
        channels = sorted(self.sbe.sxm.lineup.values(), key=lambda k: k['siriusChannelNo'])
        for channel in channels:
            filename = '{} - {}.pls'.format(channel['siriusChannelNo'], channel['name'])
            filename = filename.encode('ascii', 'ignore').decode().replace(' ', '_')
            channel['playlistName'] = filename
        html = template.render({'channels': channels})
        response = html.encode('utf-8')

        self.send_standard_headers(len(response), {
            'Content-type': 'text/html; charset=utf-8',
        })

        self.wfile.write(response)


    def file_not_found(self):
        template = self.sbe.templates.get_template('404.html')
        html = template.render()
        response = html.encode('utf-8')

        self.send_standard_headers(len(response), {
            'Content-type': 'text/html; charset=utf-8',
        }, response_code=404)

        self.wfile.write(response)


    def static_file(self, path):
        # we'll collapse .. and such and follow symlinks to make sure
        # we're staying inside of ./static/
        full_path = os.path.realpath(os.path.join("./static/", path))

        if full_path.startswith(os.path.realpath("./static/")):
            # Check if file exists
            if not os.path.isfile(full_path):
                # For channel-art, serve the 404.webp fallback
                if 'channel-art' in path:
                    fallback_path = os.path.realpath("./static/channel-art/404.webp")
                    if os.path.isfile(fallback_path):
                        with open(fallback_path, 'rb') as f:
                            content = f.read()
                            self.send_standard_headers(len(content), {
                                'Content-type': 'image/webp',
                            })
                            self.wfile.write(content)
                            return
                return self.file_not_found()
            
            # if a better mime type than octet-stream is available, use it
            content_type = 'application/octet-stream'
            extension = os.path.splitext(full_path)[1]
            if extension in mimetypes.types_map:
                content_type = mimetypes.types_map[extension]

            with open(full_path, 'rb') as f:
                content = f.read()
                self.send_standard_headers(len(content), {
                    'Content-type': content_type,
                })
                self.wfile.write(content)
        else:
            self.file_not_found()


    def channel_stream(self, channel_number, rewind=0):
        channel_number = int(channel_number)
        rewind = int(rewind)

        if channel_number not in self.sbe.sxm.lineup:
            return self.file_not_found()

        channel = self.sbe.sxm.lineup[channel_number]
        channel_id = str(channel['channelKey'])

        logging.info('Streaming: Channel #{} "{}" with rewind {}'.format(
            channel_number,
            channel['name'],
            rewind))

        # Use HTTP/1.0 for infinite streaming without Content-Length
        self.protocol_version = 'HTTP/1.0'
        self.send_response_only(200)
        self.send_header('Content-Type', 'audio/aacp')
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.send_header('Connection', 'close')
        self.end_headers()

        # Stream AAC segments directly (metadata comes from XSPF playlist)
        for aac_segment in self.sbe.sxm.packet_generator(channel_id, rewind):
            if aac_segment:
                try:
                    self.wfile.write(aac_segment)
                    self.wfile.flush()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError) as e:
                    logging.info('Connection dropped: ' + str(e))
                    return


    def channel_hls(self, channel_number):
        """Serve HLS playlist for VLC playback"""
        channel_number = int(channel_number)

        if channel_number not in self.sbe.sxm.lineup:
            return self.file_not_found()

        channel = self.sbe.sxm.lineup[channel_number]
        channel_id = str(channel['channelKey'])
        
        playlist = self.sbe.sxm.get_playlist(channel_id)
        if not playlist:
            return self.file_not_found()

        logging.info('Serving HLS playlist: Channel #{} "{}"'.format(
            channel_number,
            channel['name']))
        logging.debug('Playlist content for channel %s:\n%s', channel_number, playlist)

        # Parse the playlist and keep only the last few segments for live playback
        # Each segment is ~10 seconds, keep last 6 segments (~60 seconds buffer)
        raw_lines = playlist.split('\n')
        
        # Separate header lines from segment entries
        header_lines = []
        segment_entries = []  # Each entry is (metadata_lines, segment_url)
        current_metadata = []
        
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            if line.endswith('.aac'):
                # This is a segment URL - pair it with accumulated metadata
                segment_entries.append((current_metadata, line))
                current_metadata = []
            elif line.startswith('#EXTM3U') or line.startswith('#EXT-X-VERSION') or \
                 line.startswith('#EXT-X-TARGETDURATION') or line.startswith('#EXT-X-MEDIA-SEQUENCE') or \
                 line.startswith('#EXT-X-KEY'):
                header_lines.append(line)
            elif line.startswith('#'):
                # Metadata for next segment (like #EXTINF, #EXT-X-PROGRAM-DATE-TIME)
                current_metadata.append(line)
        
        # Keep only the last 6 segments for near-live playback
        live_segments = segment_entries[-6:] if len(segment_entries) > 6 else segment_entries
        
        # Update media sequence to match the trimmed playlist
        # Original sequence + (total - kept) = new starting sequence
        new_sequence = len(segment_entries) - len(live_segments)
        
        # Build the output playlist
        lines = []
        for hdr in header_lines:
            if hdr.startswith('#EXT-X-MEDIA-SEQUENCE'):
                # Update the media sequence number
                try:
                    orig_seq = int(hdr.split(':')[1])
                    lines.append('#EXT-X-MEDIA-SEQUENCE:{}'.format(orig_seq + new_sequence))
                except (IndexError, ValueError):
                    lines.append(hdr)
            elif hdr.startswith('#EXT-X-KEY'):
                # Force absolute key URL so players request /key/1
                lines.append('#EXT-X-KEY:METHOD=AES-128,URI="/key/1"')
            else:
                lines.append(hdr)
        
        # Add the segment entries
        for metadata, segment_url in live_segments:
            for meta_line in metadata:
                lines.append(meta_line)
            encoded = urllib.parse.quote(segment_url, safe='')
            lines.append('/segment/{}?path={}'.format(channel_number, encoded))
        
        response = '\n'.join(lines).encode('utf-8')
        
        self.send_standard_headers(len(response), {
            'Content-Type': 'application/vnd.apple.mpegurl',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        })
        self.wfile.write(response)


    def channel_segment(self, channel_number):
        """Proxy HLS segment requests"""
        channel_number = int(channel_number)
        if channel_number not in self.sbe.sxm.lineup:
            return self.file_not_found()

        query = urllib.parse.urlparse(self.path).query
        path_qs = urllib.parse.parse_qs(query).get('path')
        if not path_qs or not path_qs[0]:
            return self.file_not_found()

        real_path = urllib.parse.unquote(path_qs[0]).lstrip('/')

        logging.debug('Segment request channel %s path %s', channel_number, real_path)

        channel = self.sbe.sxm.lineup[channel_number]
        channel_id = str(channel['channelKey'])

        segment = self.sbe.sxm.get_segment(channel_id, real_path)
        if not segment:
            return self.file_not_found()
            
        self.send_standard_headers(len(segment), {
            'Content-Type': 'audio/aac',
            'Cache-Control': 'no-cache',
        })
        self.wfile.write(segment)


    def hls_key(self):
        """Serve the static HLS AES key used by SiriusXM streams"""
        key = sirius.Sirius.HLS_AES_KEY
        self.send_standard_headers(len(key), {
            'Content-Type': 'text/plain',
            'Cache-Control': 'no-cache',
        })
        self.wfile.write(key)


    def channel_metadata(self, channel_number, rewind=0):
        channel_number = int(channel_number)
        rewind = int(rewind)

        if channel_number not in self.sbe.sxm.lineup:
            return self.file_not_found()

        channel = self.sbe.sxm.lineup[channel_number]
        now = self.sbe.sxm.get_now_playing(channel['channelKey']) or {}

        response = json.dumps({
            'channel': channel,
            'nowplaying': {
                'artist': now.get('artist', 'Unknown'),
                'title': now.get('title', channel['name']),
                'album': now.get('album', ''),
                'artwork': now.get('artwork', ''),
            },
        }, sort_keys=True, indent=4).encode('utf-8')

        self.send_standard_headers(len(response), {
            'Content-type': 'application/json',
        })

        self.wfile.write(response)


    def channel_vlc_playlist(self, channel_number):
        """Generate XSPF playlist for VLC with artwork and metadata"""
        channel_number = int(channel_number)

        if channel_number not in self.sbe.sxm.lineup:
            return self.file_not_found()

        channel = self.sbe.sxm.lineup[channel_number]
        channel_id = str(channel['channelKey'])
        
        # Get current now playing info
        try:
            np = self.sbe.sxm.get_now_playing(channel_id)
            artist = np.get('artist', '')
            title = np.get('title', '')
            artwork = np.get('artwork', '')
        except:
            artist = ''
            title = ''
            artwork = ''
        
        if not artwork:
            artwork = 'http://{}:{}/static/channel-art/{}.webp'.format(
                self.sbe.config('hostname'), self.sbe.config('port'), channel_number)
        
        base_url = 'http://{}:{}'.format(self.sbe.config('hostname'), self.sbe.config('port'))
        # Use HLS stream which VLC handles natively
        stream_url = '{}/hls/{}.m3u8'.format(base_url, channel_number)
        
        # Build XSPF playlist that VLC understands
        track_title = '{} - {}'.format(artist, title) if artist and title else channel['name']
        
        xspf = '''<?xml version="1.0" encoding="UTF-8"?>
<playlist xmlns="http://xspf.org/ns/0/" xmlns:vlc="http://www.videolan.org/vlc/playlist/ns/0/" version="1">
    <title>{channel_name}</title>
    <trackList>
        <track>
            <location>{stream_url}</location>
            <title>{track_title}</title>
            <creator>{artist}</creator>
            <album>{channel_name}</album>
            <image>{artwork}</image>
            <info>{base_url}</info>
        </track>
    </trackList>
</playlist>'''.format(
            channel_name=channel['name'].replace('&', '&amp;').replace('<', '&lt;'),
            stream_url=stream_url,
            track_title=track_title.replace('&', '&amp;').replace('<', '&lt;'),
            artist=artist.replace('&', '&amp;').replace('<', '&lt;') if artist else channel['name'],
            artwork=artwork,
            base_url=base_url
        )
        
        response = xspf.encode('utf-8')
        self.send_standard_headers(len(response), {
            'Content-Type': 'application/xspf+xml',
            'Content-Disposition': 'inline; filename="{}.xspf"'.format(channel['name']),
        })
        self.wfile.write(response)


    def channel_artwork(self, channel_number):
        channel_number = int(channel_number)

        if channel_number not in self.sbe.sxm.lineup:
            return self.file_not_found()

        channel = self.sbe.sxm.lineup[channel_number]
        art_url = self.sbe.sxm.get_channel_art(channel['channelKey'])
        if not art_url:
            return self.file_not_found()

        logging.info('Redirecting artwork for channel #%s to %s', channel_number, art_url)
        self.protocol_version = 'HTTP/1.1'
        self.send_response_only(302)
        self.send_header('Location', art_url)
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.send_header('Content-length', 0)
        self.end_headers()


    def do_GET(self):
        routes = (
            (r'^/$', self.index),
            (r'^/static/(?P<path>.+)$', self.static_file),
            (r'^/hls/(?P<channel_number>[0-9]+)\.m3u8$', self.channel_hls),
            (r'^/hls/(?P<channel_number>[0-9]+)$', self.channel_hls),
            (r'^/key/1$', self.hls_key),
            (r'^/hls/key/1$', self.hls_key),
            (r'^/segment/(?P<channel_number>[0-9]+)/?$', self.channel_segment),
            (r'^/channel/(?P<channel_number>[0-9]+)$', self.channel_stream),
            (r'^/channel/(?P<channel_number>[0-9]+)/(?P<rewind>[0-9]+)$', self.channel_stream),
            (r'^/vlc/(?P<channel_number>[0-9]+)\.xspf$', self.channel_vlc_playlist),
            (r'^/vlc/(?P<channel_number>[0-9]+)$', self.channel_vlc_playlist),
            (r'^/art/(?P<channel_number>[0-9]+)$', self.channel_artwork),
            (r'^/metadata/(?P<channel_number>[0-9]+)$', self.channel_metadata),
            (r'^/metadata/(?P<channel_number>[0-9]+)/(?P<rewind>[0-9]+)$', self.channel_metadata),
        )

        path_only = urllib.parse.urlparse(self.path).path

        for route_path, route_handler in routes:
            match = re.search(route_path, path_only)
            if match:
                return route_handler(**match.groupdict())

        self.file_not_found()


if __name__ == '__main__':
    # Basic logging to file
    logging.basicConfig(level=logging.DEBUG,
        format='%(asctime)s :: %(levelname)s :: %(thread)d :: %(message)s',
        datefmt='%m/%d %H:%M',
        filename='seriouscast.log',
        filemode='w')

    # Set up console logging output
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console_format = logging.Formatter('%(levelname)s :: %(thread)-5d :: %(message)s')
    console.setFormatter(console_format)
    logging.getLogger('').addHandler(console)

    # Disable (most) logging from requests
    requests_log = logging.getLogger("requests")
    requests_log.setLevel(logging.WARNING)

    logging.info('Setting up server, please wait')
    sbe = SeriousBackend()
    port = int(sbe.config('port'))
    logging.info('Starting server on port {}'.format(port))
    server = SeriousHTTPServer(('0.0.0.0', port), SeriousRequestHandler)
    server.serve_forever()
