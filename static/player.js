$(function() {
    var url_base = window.location.origin;
    var offset = 0;
    var current_channel;
    var favorites = $.cookie('favorites');
    var now_playing_last;
    var default_art = '/static/channel-art/404.webp';
    var metadata_request = false;
    var audio = $('#player')[0];
    var hls;
    var channelNames = {};

    // build channel name map
    $('table#channels tbody tr').each(function() {
        var ch = $(this).data('channel');
        var name = $('.name', this).text().trim();
        channelNames[ch] = name || ('Channel ' + ch);
    });

    // Theme handling
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        $('#theme-toggle').text(theme === 'dark' ? '☀️' : '🌙');
    }

    var savedTheme = $.cookie('theme') || 'light';
    applyTheme(savedTheme);

    $('#theme-toggle').on('click', function() {
        var next = (document.documentElement.getAttribute('data-theme') === 'dark') ? 'light' : 'dark';
        $.cookie('theme', next, { expires: 9999 });
        applyTheme(next);
    });

    if (favorites !== undefined) {
        favorites = unescape(favorites).split(',');
    } else {
        favorites = Array();
    }
    rebuild_favorites();

    if ($.cookie('volume') !== undefined) {
        audio.volume = parseInt($.cookie('volume'), 10) / 100;
        $('#player-volume').val(parseInt($.cookie('volume'), 10));
    } else {
        audio.volume = 1;
    }

    // Make per-channel downloads use the same M3U format as the favorites download (works in VLC)
    $('.channel-download').each(function() {
        var ch = $(this).data('channel');
        var name = channelNames[ch] || ('Channel ' + ch);
        var lines = ['#EXTM3U', '#EXTINF:-1,' + name, url_base + '/hls/' + ch + '.m3u8'];
        var content = btoa(lines.join('\n'));
        $(this).attr('href', 'data:audio/x-mpegurl;base64,' + content);
        $(this).attr('download', 'channel_' + ch + '.m3u8');
    });

    // Fallback art if missing
    $('img.channel-art').on('error', function() {
        var fallback = $(this).data('fallback') || '/static/channel-art/404.webp';
        if (this.src !== fallback) {
            this.src = fallback;
        }
    });

    function start_stream(stream_url) {
        set_metadata('Retrieving info...', '');
        metadata_request = false;

        if (hls) {
            hls.destroy();
            hls = null;
        }

        if (audio.canPlayType('application/vnd.apple.mpegurl')) {
            audio.src = stream_url;
            audio.play();
        } else if (window.Hls && window.Hls.isSupported()) {
            hls = new Hls({
                // Live stream settings - refresh playlist frequently
                liveSyncDurationCount: 3,
                liveMaxLatencyDurationCount: 10,
                liveDurationInfinity: true,
                // Disable playlist caching for live streams
                manifestLoadingMaxRetry: 6,
                manifestLoadingTimeOut: 10000,
                levelLoadingMaxRetry: 6,
                levelLoadingTimeOut: 10000,
                // Lower latency settings
                lowLatencyMode: true,
                backBufferLength: 30
            });
            hls.loadSource(stream_url);
            hls.attachMedia(audio);
            hls.on(Hls.Events.MANIFEST_PARSED, function() {
                audio.play();
            });
            hls.on(Hls.Events.ERROR, function(event, data) {
                console.error('HLS error', data);
                // Auto-recover from non-fatal errors
                if (data.fatal) {
                    switch(data.type) {
                        case Hls.ErrorTypes.NETWORK_ERROR:
                            console.log('Network error, trying to recover...');
                            hls.startLoad();
                            break;
                        case Hls.ErrorTypes.MEDIA_ERROR:
                            console.log('Media error, trying to recover...');
                            hls.recoverMediaError();
                            break;
                        default:
                            console.log('Fatal error, cannot recover');
                            hls.destroy();
                            break;
                    }
                }
            });
        } else {
            alert('HLS not supported in this browser. Try a modern browser or open the playlist in VLC.');
        }
    }

    function update_art(url) {
        var artUrl = (url && url.length) ? url : default_art;
        // Update blurred background
        $('.art').css('background-image', "url('" + artUrl + "')");
        // Update visible album thumbnail
        $('#album-art-img').attr('src', artUrl);
        // Update favicon
        $('link[rel="shortcut icon"]').attr('href', artUrl);
        // Hide buy link by default; backend does not provide it
        $('#buylink').hide();
    }

    function set_metadata(channel, now_playing, artwork) {
        $('.currentinfo h3').text(channel);
        $('.currentinfo h4').text(now_playing);
        $('.controls').css('bottom', '0');
        $('#channels').css('margin-bottom', '98px');
        $('title').text(now_playing);

        if (now_playing !== now_playing_last) {
            update_art(artwork);
            now_playing_last = now_playing;
        }
    }

    function add_favorite(channel) {
        if (favorites.indexOf(String(channel)) === -1) {
            favorites.push(String(channel));
            put_favorites();
        }
    }

    function remove_favorite(channel) {
        if (favorites.indexOf(String(channel)) !== -1) {
            favorites.splice(favorites.indexOf(String(channel)),1);
            put_favorites();
        }
    }

    function put_favorites() {
        $.cookie('favorites', escape(favorites.join(',')), { expires: 9999 });
        rebuild_favorites();
    }

    function rebuild_favorites() {
        if (favorites.indexOf('') !== -1) {
            favorites.splice(favorites.indexOf(''),1);
        }
        if (favorites.length > 0) {
            $('#favhead').show();
            $('#favchannels').show();
            $('#favchannels tr').remove();
            $.each(favorites, function(data, key) {
                var element = $('tr[data-channel='+key+']').clone();
                $('#listing').append(element);
            });
            $('#favchannels .channel-add').attr('class','channel-remove');
            $('#favchannels .channel-remove img').attr('src','/static/img/minus.svg');
        } else {
            $('#favhead').hide();
            $('#favchannels').hide();
        }
        updateColumnVisibility();
        refreshFavoritesNowPlaying();
        updateFavoritesPlaylistLink();
    }

    $('.playpause img').click(function() {
        if (audio.paused) {
            audio.play();
            $(this).attr('src','/static/img/pause.svg');
        } else {
            audio.pause();
            $(this).attr('src','/static/img/play.svg');
        }
    });

    $('.channel-add').click(function() {
        add_favorite($(this).data('channel'));
    });

    $('table').on("click",".player-stream",function() {
        current_channel = $(this).data('channel');
        // Add timestamp to bust any browser caching
        var stream_url = url_base + '/hls/' + current_channel + '.m3u8?_t=' + Date.now();
        start_stream(stream_url);
        return false;
    });

    $('#favchannels tbody').on("click",".channel-remove",function() {
        remove_favorite($(this).data('channel'));
    });
    
    $('.volume img').click(function() {
        audio.muted = !audio.muted;
        if (audio.muted) {
            $(this).attr('src','/static/img/volume-mute.svg');
        } else {
            $(this).attr('src','/static/img/volume-high.svg');
        }
    });
    
    $('#player-volume').change(function() {
        var volume = parseInt($('#player-volume').val(), 10);
        audio.volume = volume / 100;
        $.cookie('volume', volume,{ expires: 9999 });
    });
    
    $('#player-rewind').change(function() {
        offset = 300 - $('#player-rewind').val();
        
        if (offset === 0) {
            $('#time').text('Live');
        } else {
            $('#time').text(offset + ' min ago');
        }
        if (current_channel !== undefined) {
            var stream_url = url_base + '/hls/' + current_channel + '.m3u8?_t=' + Date.now();
            start_stream(stream_url);
        }
    });
    
    $('#time').click(function() {
        $('#player-rewind').val(300);
        $('#player-rewind').change();
        $('#player-rewind').mouseup();
    });

    function updateColumnVisibility() {
        var hasGenre = $('.genre').filter(function(){ return $(this).text().trim().length > 0; }).length > 0;
        var hasDesc = $('.desc').filter(function(){ return $(this).text().trim().length > 0; }).length > 0;

        if (!hasGenre) {
            $('.genre, th:contains("Genre")').hide();
        }
        if (!hasDesc) {
            $('.desc, th:contains("Description")').hide();
        }
    }

    updateColumnVisibility();

    // Only favorites get live now-playing metadata; others remain static
    function refreshFavoritesNowPlaying() {
        if (!favorites.length) return;
        favorites.forEach(function(ch) {
            $.getJSON('/metadata/' + ch + '/0', function(data) {
                var np = data['nowplaying'] || {};
                var artist = np['artist'] || '';
                var title = np['title'] || '';
                var artwork = np['artwork'] || '';
                var channel = data['channel'] || {};
                var genre = channel['genre'] || '';
                var row = $('#favchannels tr[data-channel="' + ch + '"]');
                // Update genre for favorites
                if (genre) row.find('.genre').text(genre);
                
                // Add or update now-playing info under the channel name
                var nameCell = row.find('.name');
                var nowPlayingText = '';
                if (artist && title) {
                    nowPlayingText = artist + ' - ' + title;
                } else if (artist) {
                    nowPlayingText = artist;
                } else if (title) {
                    nowPlayingText = title;
                }
                
                // Check if now-playing span exists, if not create it
                var npSpan = nameCell.find('.fav-now-playing');
                if (npSpan.length === 0) {
                    nameCell.append('<span class="fav-now-playing"></span>');
                    npSpan = nameCell.find('.fav-now-playing');
                }
                npSpan.text(nowPlayingText);
                
                // Update the mini artwork on the favorite card
                if (artwork) {
                    var artImg = row.find('.channel-art');
                    // Store original channel art as fallback
                    if (!artImg.data('original-src')) {
                        artImg.data('original-src', artImg.attr('src'));
                    }
                    artImg.attr('src', artwork);
                }
            });
        });
    }

    function updateFavoritesPlaylistLink() {
        var link = $('#fav-download');
        if (!favorites.length) {
            link.hide();
            return;
        }
        var lines = ['#EXTM3U'];
        favorites.forEach(function(ch) {
            var name = channelNames[ch] || ('Channel ' + ch);
            lines.push('#EXTINF:-1,' + name);
            lines.push(url_base + '/hls/' + ch + '.m3u8');
        });
        var content = lines.join('\n');
        link.attr('href', 'data:audio/x-mpegurl;base64,' + btoa(content));
        link.show();
    }

    // Update current playing channel metadata every 2 seconds
    setInterval(function() {
        try {
            if (current_channel !== undefined && !metadata_request) {
                metadata_request = true;
                $.getJSON('/metadata/' + current_channel + '/' + offset, function (data) {
                    var channel = data['channel']['name'];
                    var np = data['nowplaying'] || {};
                    var artist = np['artist'] || 'Unknown';
                    var title = np['title'] || 'Unknown';
                    var now_playing = artist + ' - ' + title;
                    var art = np['artwork'] || '';
                    set_metadata(channel, now_playing, art);
                    metadata_request = false;
                });
            }
        } catch (ex) {
        }
    }, 2000);

    // Update favorites now-playing info every 15 seconds
    setInterval(function() {
        refreshFavoritesNowPlaying();
    }, 15000);
});