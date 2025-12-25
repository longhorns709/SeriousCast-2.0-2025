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
            # if a better mime type than octet-stream is available, use it
            content_type = 'appllication/octet-stream'
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
        url = 'http://{}:{}/'.format(self.sbe.config('hostname'), self.sbe.config('port'))

        logging.info('Streaming: Channel #{} "{}" with rewind {}'.format(
            channel_number,
            channel['name'],
            rewind))

        # Use HTTP/1.0 for infinite streaming without Content-Length
        self.protocol_version = 'HTTP/1.0'
        self.send_response_only(200)
        self.send_header('Content-Type', 'audio/aac')
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.send_header('icy-br', '256')
        self.send_header('icy-name', channel['name'])
        self.send_header('icy-genre', channel.get('genre', 'Unknown'))
        self.end_headers()

        channel_id = str(channel['channelKey'])

        # Stream AAC segments directly
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

        lines = []
        for line in playlist.split('\n'):
            line = line.strip()
            if line.endswith('.aac'):
                encoded = urllib.parse.quote(line, safe='')
                # No trailing slash before query so routing matches
                lines.append('/segment/{}?path={}'.format(channel_number, encoded))
            elif line.startswith('#EXT-X-KEY'):
                # Force absolute key URL so players request /key/1
                lines.append('#EXT-X-KEY:METHOD=AES-128,URI="/key/1"')
            else:
                lines.append(line)
        
        response = '\n'.join(lines).encode('utf-8')
        
        self.send_standard_headers(len(response), {
            'Content-Type': 'application/vnd.apple.mpegurl',
            'Cache-Control': 'no-cache',
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
            },
        }, sort_keys=True, indent=4).encode('utf-8')

        self.send_standard_headers(len(response), {
            'Content-type': 'application/json',
        })

        self.wfile.write(response)


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
