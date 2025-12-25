#!/usr/bin/env python3

import re
import json
import time
import datetime
import logging
import urllib.parse
import base64

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


class SiriusException(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


class Sirius():
    USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
    REST_FORMAT = 'https://player.siriusxm.com/rest/v2/experience/modules/{}'
    LIVE_PRIMARY_HLS = 'https://siriusxm-priprodlive.akamaized.net'
    HLS_AES_KEY = base64.b64decode('0Nsco7MAgxowGvkUT8aYag==')


    def __init__(self):
        """
        Creates a new instance of the Sirius player
        """
        self.backend = default_backend()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.USER_AGENT})
        self.playlists = {}
        self.channels = None
        self.lineup = {}


    def _log(self, message):
        logging.info('<SiriusXM>: {}'.format(message))


    def is_logged_in(self):
        return 'SXMAUTH' in self.session.cookies or 'SXMAUTHNEW' in self.session.cookies


    def is_session_authenticated(self):
        # Check for any of the possible authentication cookies
        return self.is_logged_in()


    def _get(self, method, params, authenticate=True):
        if authenticate and not self.is_logged_in() and not self._authenticate():
            self._log('Unable to authenticate')
            return None

        res = self.session.get(self.REST_FORMAT.format(method), params=params)
        if res.status_code != 200:
            self._log('Received status code {} for method \'{}\''.format(res.status_code, method))
            return None

        try:
            return res.json()
        except ValueError:
            self._log('Error decoding json for method \'{}\''.format(method))
            return None


    def _post(self, method, postdata, authenticate=True):
        if authenticate and not self.is_logged_in() and not self._authenticate():
            self._log('Unable to authenticate')
            return None

        res = self.session.post(
            self.REST_FORMAT.format(method), 
            data=json.dumps(postdata),
            headers={'Content-Type': 'application/json'}
        )
        if res.status_code != 200:
            self._log('Received status code {} for method \'{}\''.format(res.status_code, method))
            return None

        try:
            return res.json()
        except ValueError:
            self._log('Error decoding json for method \'{}\''.format(method))
            return None


    def login(self, username, password):
        """
        Login to SiriusXM with username and password
        """
        self.username = username
        self.password = password
        
        postdata = {
            'moduleList': {
                'modules': [{
                    'moduleRequest': {
                        'resultTemplate': 'web',
                        'deviceInfo': {
                            'osVersion': 'Mac',
                            'platform': 'Web',
                            'sxmAppVersion': '3.1802.10011.0',
                            'browser': 'Safari',
                            'browserVersion': '17.0',
                            'appRegion': 'US',
                            'deviceModel': 'K2WebClient',
                            'clientDeviceId': 'null',
                            'player': 'html5',
                            'clientDeviceType': 'web',
                        },
                        'standardAuth': {
                            'username': username,
                            'password': password,
                        },
                    },
                }],
            },
        }
        
        data = self._post('modify/authentication', postdata, authenticate=False)
        if not data:
            raise SiriusException('Login failed - no response')

        try:
            status = data.get('ModuleListResponse', {}).get('status', 0)
            messages = data.get('ModuleListResponse', {}).get('messages', [])
            
            if status == 1:
                self._log('Login successful')
                # After login, authenticate the session (resume)
                self._do_session_authenticate()
                # Get channels - skip authentication since we just logged in
                self._get_channels(authenticate=False)
                self._parse_lineup()
                return True
            else:
                if messages:
                    raise SiriusException('Login failed: {}'.format(messages[0].get('message', 'Unknown error')))
                raise SiriusException('Login failed')
        except KeyError:
            raise SiriusException('Error decoding json response for login')


    def _do_session_authenticate(self):
        """Send the resume request to authenticate the session (no recursion)"""
        postdata = {
            'moduleList': {
                'modules': [{
                    'moduleRequest': {
                        'resultTemplate': 'web',
                        'deviceInfo': {
                            'osVersion': 'Mac',
                            'platform': 'Web',
                            'clientDeviceType': 'web',
                            'sxmAppVersion': '3.1802.10011.0',
                            'browser': 'Safari',
                            'browserVersion': '17.0',
                            'appRegion': 'US',
                            'deviceModel': 'K2WebClient',
                            'player': 'html5',
                            'clientDeviceId': 'null'
                        }
                    }
                }]
            }
        }
        
        data = self._post('resume?OAtrial=false', postdata, authenticate=False)
        if not data:
            return False

        try:
            return data['ModuleListResponse']['status'] == 1 and self.is_session_authenticated()
        except KeyError:
            self._log('Error parsing json response for authentication')
            return False


    def _authenticate(self):
        """Authenticate the session - re-login if needed"""
        if not self.is_logged_in() and hasattr(self, 'username'):
            self.login(self.username, self.password)
            return self.is_session_authenticated()
        
        if not self.is_session_authenticated():
            return self._do_session_authenticate()
            
        return True


    def _get_sxmak_token(self):
        try:
            return self.session.cookies['SXMAKTOKEN'].split('=', 1)[1].split(',', 1)[0]
        except (KeyError, IndexError):
            return None


    def _get_gup_id(self):
        try:
            return json.loads(urllib.parse.unquote(self.session.cookies['SXMDATA']))['gupId']
        except (KeyError, ValueError):
            return None


    def _get_channels(self, authenticate=True):
        """Download channel list from API"""
        if self.channels:
            return self.channels
            
        postdata = {
            'moduleList': {
                'modules': [{
                    'moduleArea': 'Discovery',
                    'moduleType': 'ChannelListing',
                    'moduleRequest': {
                        'consumeRequests': [],
                        'resultTemplate': 'responsive',
                        'alerts': [],
                        'profileInfos': []
                    }
                }]
            }
        }
        
        data = self._post('get', postdata, authenticate=authenticate)
        if not data:
            self._log('Unable to get channel list')
            return []

        try:
            self.channels = data['ModuleListResponse']['moduleList']['modules'][0]['moduleResponse']['contentData']['channelListing']['channels']
            return self.channels
        except (KeyError, IndexError):
            self._log('Error parsing json response for channels')
            return []


    def _parse_lineup(self):
        """Parse channels into lineup dictionary indexed by channel number"""
        channels = self._get_channels()
        
        for channel in channels:
            try:
                channel_num = int(channel.get('siriusChannelNumber', 0))
                if channel_num > 0:
                    self.lineup[channel_num] = {
                        'siriusChannelNo': channel_num,
                        'channelKey': channel.get('channelId', ''),
                        'channelGuid': channel.get('channelGuid', ''),
                        'name': channel.get('name', ''),
                        'genre': channel.get('genre', {}).get('name', '') if isinstance(channel.get('genre'), dict) else channel.get('genre', 'Unknown'),
                    }
            except (ValueError, TypeError):
                continue


    def _get_channel(self, name):
        """Get channel guid and id by name, id, or number"""
        name = str(name).lower()
        for channel in self._get_channels():
            if (channel.get('name', '').lower() == name or 
                channel.get('channelId', '').lower() == name or 
                str(channel.get('siriusChannelNumber', '')) == name):
                return (channel['channelGuid'], channel['channelId'])
        return (None, None)


    def _get_playlist_url(self, guid, channel_id, use_cache=True, max_attempts=5):
        """Get the HLS playlist URL for a channel"""
        if use_cache and channel_id in self.playlists:
            return self.playlists[channel_id]

        params = {
            'assetGUID': guid,
            'ccRequestType': 'AUDIO_VIDEO',
            'channelId': channel_id,
            'hls_output_mode': 'custom',
            'marker_mode': 'all_separate_cue_points',
            'result-template': 'web',
            'time': int(round(time.time() * 1000.0)),
            'timestamp': datetime.datetime.utcnow().isoformat('T') + 'Z'
        }
        
        data = self._get('tune/now-playing-live', params)
        if not data:
            return None

        try:
            status = data['ModuleListResponse']['status']
            message = data['ModuleListResponse']['messages'][0]['message']
            message_code = data['ModuleListResponse']['messages'][0]['code']
        except (KeyError, IndexError):
            self._log('Error parsing json response for playlist')
            return None

        # Re-login if session expired
        if message_code == 201 or message_code == 208:
            if max_attempts > 0:
                self._log('Session expired, logging in and authenticating')
                if self._authenticate():
                    self._log('Successfully authenticated')
                    return self._get_playlist_url(guid, channel_id, use_cache, max_attempts - 1)
                else:
                    self._log('Failed to authenticate')
                    return None
            else:
                self._log('Reached max attempts for playlist')
                return None
        elif message_code != 100:
            self._log('Received error {} {}'.format(message_code, message))
            return None

        # Get m3u8 url
        try:
            playlists = data['ModuleListResponse']['moduleList']['modules'][0]['moduleResponse']['liveChannelData']['hlsAudioInfos']
        except (KeyError, IndexError):
            self._log('Error parsing json response for playlist')
            return None
            
        for playlist_info in playlists:
            if playlist_info['size'] == 'LARGE':
                playlist_url = playlist_info['url'].replace('%Live_Primary_HLS%', self.LIVE_PRIMARY_HLS)
                self.playlists[channel_id] = self._get_playlist_variant_url(playlist_url)
                return self.playlists[channel_id]

        return None


    def _get_playlist_variant_url(self, url):
        """Get the variant playlist URL (256k quality)"""
        params = {
            'token': self._get_sxmak_token(),
            'consumer': 'k2',
            'gupId': self._get_gup_id(),
        }
        res = self.session.get(url, params=params)

        if res.status_code != 200:
            self._log('Received status code {} on playlist variant retrieval'.format(res.status_code))
            return None
        
        for x in res.text.split('\n'):
            if x.rstrip().endswith('.m3u8'):
                return '{}/{}'.format(url.rsplit('/', 1)[0], x.rstrip())
        
        return None


    def get_playlist(self, channel_key, use_cache=True, max_attempts=3):
        """Retrieve m3u8 playlist for a given channel"""
        guid, channel_id = self._get_channel(channel_key)
        if not guid or not channel_id:
            self._log('No channel for {}'.format(channel_key))
            return None

        url = self._get_playlist_url(guid, channel_id, use_cache)
        if not url:
            # Try refreshing the token
            if max_attempts > 0:
                self._log('No playlist URL, refreshing session')
                self.login(self.username, self.password)
                # Clear the cached playlist URL
                if channel_id in self.playlists:
                    del self.playlists[channel_id]
                return self.get_playlist(channel_key, False, max_attempts - 1)
            return None
            
        params = {
            'token': self._get_sxmak_token(),
            'consumer': 'k2',
            'gupId': self._get_gup_id(),
        }
        res = self.session.get(url, params=params)

        if res.status_code == 403:
            if max_attempts > 0:
                self._log('Received status code 403 on playlist, refreshing session')
                # Re-login to get fresh tokens
                self.login(self.username, self.password)
                # Clear cached playlist URL
                if channel_id in self.playlists:
                    del self.playlists[channel_id]
                return self.get_playlist(channel_key, False, max_attempts - 1)
            else:
                self._log('Max attempts reached for playlist')
                return None

        if res.status_code != 200:
            self._log('Received status code {} on playlist variant'.format(res.status_code))
            return None

        # Add base path to segments
        base_url = url.rsplit('/', 1)[0]
        base_path = base_url[8:].split('/', 1)[1]
        lines = res.text.split('\n')
        for x in range(len(lines)):
            if lines[x].rstrip().endswith('.aac'):
                lines[x] = '{}/{}'.format(base_path, lines[x])
        return '\n'.join(lines)


    def get_segment(self, channel_key, segment, max_attempts=3):
        """Get a media segment from a channel"""
        # Handle both old-style segment names and full paths
        if segment.startswith('/') or '/' in segment:
            path = segment.lstrip('/')
        else:
            # Old style - need to build the path
            guid, channel_id = self._get_channel(channel_key)
            if not guid or not channel_id:
                return None
            url = self._get_playlist_url(guid, channel_id)
            if not url:
                return None
            base_path = url.rsplit('/', 1)[0][8:].split('/', 1)[1]
            path = '{}/{}'.format(base_path, segment)

        url = '{}/{}'.format(self.LIVE_PRIMARY_HLS, path)
        params = {
            'token': self._get_sxmak_token(),
            'consumer': 'k2',
            'gupId': self._get_gup_id(),
        }
        res = self.session.get(url, params=params)

        if res.status_code == 403:
            if max_attempts > 0:
                # Refresh auth and playlist token before retrying
                self._log('Received status code 403 on segment, refreshing session and playlist token')
                self.login(self.username, self.password)
                self.playlists = {}
                # Pull a fresh playlist to prime new tokens
                self.get_playlist(channel_key, use_cache=False)
                return self.get_segment(channel_key, segment, max_attempts - 1)
            else:
                self._log('Received status code 403 on segment, max attempts exceeded')
                return None

        if res.status_code != 200:
            self._log('Received status code {} on segment'.format(res.status_code))
            return None

        return res.content


    def _filter_playlist(self, playlist, last=None, rewind=0):
        """
        Gets new items from a playlist, optionally given a resume point
        Rewind specifies a number of minutes to go back in history
        """
        lines = [x.strip() for x in playlist.splitlines() if x.strip() and not x.startswith('#')]
        if last and last in lines:
            return lines[lines.index(last) + 1:]
        return lines[-(10 + 3 * rewind):]


    def get_hls_url(self, channel_key):
        """Get the authenticated HLS URL for direct playback in VLC or similar"""
        guid, channel_id = self._get_channel(channel_key)
        if not guid or not channel_id:
            return None
            
        url = self._get_playlist_url(guid, channel_id)
        if not url:
            return None
            
        # Add authentication params
        params = {
            'token': self._get_sxmak_token(),
            'consumer': 'k2',
            'gupId': self._get_gup_id(),
        }
        return url + '?' + urllib.parse.urlencode(params)


    def get_now_playing(self, channel_key, attempts=2):
        """Fetch now-playing metadata (artist/title/album) for a channel"""
        guid, channel_id = self._get_channel(channel_key)
        if not guid or not channel_id:
            return None

        params = {
            'assetGUID': guid,
            'ccRequestType': 'AUDIO_VIDEO',
            'channelId': channel_id,
            'hls_output_mode': 'custom',
            'marker_mode': 'all_separate_cue_points',
            'result-template': 'web',
            'time': int(round(time.time() * 1000.0)),
            'timestamp': datetime.datetime.utcnow().isoformat('T') + 'Z'
        }

        data = self._get('tune/now-playing-live', params)
        if not data:
            return None

        try:
            message_code = data['ModuleListResponse']['messages'][0]['code']
        except (KeyError, IndexError):
            message_code = None

        if message_code in (201, 208) and attempts > 0:
            # session expired, re-auth and retry
            self.login(self.username, self.password)
            return self.get_now_playing(channel_key, attempts - 1)

        try:
            lcd = data['ModuleListResponse']['moduleList']['modules'][0]['moduleResponse']['liveChannelData']
        except (KeyError, IndexError, TypeError):
            return None

        current_event = lcd.get('currentEvent') or (lcd.get('liveChannelEvents') or [{}])[0]
        song = current_event.get('song', {}) if isinstance(current_event, dict) else {}
        artists = current_event.get('artists') or [] if isinstance(current_event, dict) else []

        artist = artists[0].get('name') if artists else 'Unknown'
        title = song.get('name') or song.get('title') or current_event.get('name') or 'Unknown'
        album = song.get('album') or ''

        return {
            'channel': lcd.get('name', ''),
            'artist': artist or 'Unknown',
            'title': title or 'Unknown',
            'album': album or ''
        }


    def packet_generator(self, channel_key, rewind=0):
        """Generator that produces AAC audio segments
        Rewind specifies a number of minutes to go back in history
        """
        playlist = []
        entry = None
        
        while True:
            if len(playlist) < 3:
                resp = self.get_playlist(channel_key)
                if resp:
                    new_entries = self._filter_playlist(resp, entry, rewind)
                    playlist += [x for x in new_entries if x not in playlist]
                    
            if len(playlist):
                entry = playlist.pop(0)
                logging.debug('Got audio chunk ' + entry)
                segment = self.get_segment(channel_key, entry)
                if segment:
                    yield segment
            else:
                time.sleep(10)
