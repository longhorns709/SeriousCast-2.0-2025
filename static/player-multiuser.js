$(function() {
    var url_base = window.location.origin;
    var offset = 0;
    var current_channel;
    var currentUser = null;
    var users = [];
    var favorites = [];
    var now_playing_last;
    var default_art = '/static/channel-art/404.webp';
    var metadata_request = false;
    var audio = $('#player')[0];
    var hls;
    var channelNames = {};

    // User avatar colors
    var avatarColors = [
        ['#0f9bd7', '#0b7eaf'],  // Blue
        ['#e74c3c', '#c0392b'],  // Red
        ['#2ecc71', '#27ae60'],  // Green
        ['#9b59b6', '#8e44ad'],  // Purple
        ['#f39c12', '#d68910'],  // Orange
        ['#1abc9c', '#16a085'],  // Teal
        ['#e91e63', '#c2185b'],  // Pink
        ['#00bcd4', '#0097a7']   // Cyan
    ];

    // Load users from localStorage
    function loadUsers() {
        try {
            var stored = localStorage.getItem('seriouscast_users');
            if (stored) {
                users = JSON.parse(stored);
            }
        } catch(e) {
            users = [];
        }
        if (!users || users.length === 0) {
            users = [];
        }
    }

    function saveUsers() {
        localStorage.setItem('seriouscast_users', JSON.stringify(users));
    }

    function getCurrentUser() {
        var userId = localStorage.getItem('seriouscast_current_user');
        if (userId) {
            return users.find(u => u.id === userId) || null;
        }
        return null;
    }

    function setCurrentUser(user) {
        currentUser = user;
        localStorage.setItem('seriouscast_current_user', user ? user.id : '');
        if (user) {
            favorites = user.favorites || [];
        } else {
            favorites = [];
        }
    }

    function getUserFavorites(user) {
        return user ? (user.favorites || []) : [];
    }

    function saveUserFavorites() {
        if (currentUser) {
            currentUser.favorites = favorites;
            // Update user in array
            var idx = users.findIndex(u => u.id === currentUser.id);
            if (idx >= 0) {
                users[idx] = currentUser;
            }
            saveUsers();
        }
    }

    function generateUserId() {
        return 'user_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    function getInitials(name) {
        return name.split(' ').map(w => w[0]).join('').toUpperCase().substr(0, 2);
    }

    function getAvatarColor(index) {
        return avatarColors[index % avatarColors.length];
    }

    // Show user selection modal
    function showUserModal(editMode) {
        editMode = editMode || false;
        
        var html = '<div class="user-modal-overlay" id="user-modal">';
        html += '<div class="user-modal">';
        html += '<h2>Who\'s Listening?</h2>';
        html += '<p>Select your profile to load your favorites</p>';
        html += '<div class="user-grid' + (editMode ? ' edit-mode' : '') + '">';
        
        users.forEach(function(user, index) {
            var colors = getAvatarColor(index);
            var initials = getInitials(user.name);
            html += '<div class="user-btn" data-user-id="' + user.id + '">';
            html += '<button class="delete-user" data-user-id="' + user.id + '">&times;</button>';
            html += '<div class="user-avatar" style="background: linear-gradient(135deg, ' + colors[0] + ', ' + colors[1] + ')">' + initials + '</div>';
            html += '<span>' + escapeHtml(user.name) + '</span>';
            html += '</div>';
        });
        
        html += '<div class="user-btn add-user">';
        html += '<div class="user-avatar">+</div>';
        html += '<span>Add User</span>';
        html += '</div>';
        html += '</div>';
        
        html += '<div class="add-user-form" id="add-user-form">';
        html += '<input type="text" id="new-user-name" placeholder="Enter name..." maxlength="20" autocomplete="off">';
        html += '<button id="create-user-btn">Create Profile</button>';
        html += '</div>';
        
        if (users.length > 0) {
            html += '<button class="edit-users-btn" id="edit-users-btn">' + (editMode ? 'Done' : 'Edit Profiles') + '</button>';
        }
        
        html += '</div></div>';
        
        $('body').append(html);
        
        // Bind events
        $('#user-modal .user-btn:not(.add-user)').on('click', function(e) {
            if ($(e.target).hasClass('delete-user')) return;
            if ($('.user-grid').hasClass('edit-mode')) return;
            
            var userId = $(this).data('user-id');
            var user = users.find(u => u.id === userId);
            if (user) {
                setCurrentUser(user);
                $('#user-modal').remove();
                onUserSelected();
            }
        });
        
        $('#user-modal .add-user').on('click', function() {
            $('#add-user-form').addClass('visible');
            $('#new-user-name').focus();
        });
        
        $('#create-user-btn').on('click', function() {
            createNewUser();
        });
        
        $('#new-user-name').on('keypress', function(e) {
            if (e.which === 13) {
                createNewUser();
            }
        });
        
        $('#edit-users-btn').on('click', function() {
            $('#user-modal').remove();
            showUserModal(!editMode);
        });
        
        $('.delete-user').on('click', function(e) {
            e.stopPropagation();
            var userId = $(this).data('user-id');
            if (confirm('Delete this profile?')) {
                users = users.filter(u => u.id !== userId);
                saveUsers();
                $('#user-modal').remove();
                if (users.length === 0) {
                    showUserModal(false);
                } else {
                    showUserModal(true);
                }
            }
        });
    }

    function createNewUser() {
        var name = $('#new-user-name').val().trim();
        if (!name) {
            $('#new-user-name').focus();
            return;
        }
        
        var user = {
            id: generateUserId(),
            name: name,
            favorites: [],
            createdAt: Date.now()
        };
        
        users.push(user);
        saveUsers();
        setCurrentUser(user);
        $('#user-modal').remove();
        onUserSelected();
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function updateUserBadge() {
        $('.current-user-badge').remove();
        
        if (currentUser) {
            var idx = users.findIndex(u => u.id === currentUser.id);
            var colors = getAvatarColor(idx >= 0 ? idx : 0);
            var initials = getInitials(currentUser.name);
            
            var badge = '<div class="current-user-badge" id="switch-user">';
            badge += '<div class="mini-avatar" style="background: linear-gradient(135deg, ' + colors[0] + ', ' + colors[1] + ')">' + initials + '</div>';
            badge += '<span>' + escapeHtml(currentUser.name) + '</span>';
            badge += '</div>';
            
            $('header nav').prepend(badge);
            
            $('#switch-user').on('click', function() {
                showUserModal();
            });
        }
    }

    function onUserSelected() {
        updateUserBadge();
        rebuild_favorites();
        refreshFavoritesNowPlaying();
        updateFavoritesPlaylistLink();
    }

    // Build channel name map
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

    var savedTheme = localStorage.getItem('seriouscast_theme') || 'light';
    applyTheme(savedTheme);

    $('#theme-toggle').on('click', function() {
        var next = (document.documentElement.getAttribute('data-theme') === 'dark') ? 'light' : 'dark';
        localStorage.setItem('seriouscast_theme', next);
        applyTheme(next);
    });

    // Jiggle mode for reordering favorites (iOS-style)
    var jiggleMode = false;
    var holdTimer = null;
    var draggedItem = null;
    var draggedIndex = -1;

    function startJiggleMode() {
        if (jiggleMode) return;
        jiggleMode = true;
        $('#favchannels').addClass('jiggle-mode');
        $('#favchannels tr').addClass('jiggle');
        if ($('#jiggle-done').length === 0) {
            $('.section-header').append('<button id="jiggle-done" class="pill-btn">Done</button>');
        }
        $('#jiggle-done').show();
    }

    function stopJiggleMode() {
        jiggleMode = false;
        $('#favchannels').removeClass('jiggle-mode');
        $('#favchannels tr').removeClass('jiggle');
        $('#jiggle-done').hide();
    }

    // Long press to enter jiggle mode
    $(document).on('mousedown touchstart', '#favchannels tr', function(e) {
        if (jiggleMode) return;
        var row = $(this);
        holdTimer = setTimeout(function() {
            startJiggleMode();
        }, 500);
    });

    $(document).on('mouseup touchend mouseleave', '#favchannels tr', function() {
        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
    });

    $(document).on('click', '#jiggle-done', function() {
        stopJiggleMode();
    });

    // Drag and drop reordering in jiggle mode
    $(document).on('mousedown touchstart', '#favchannels.jiggle-mode tr', function(e) {
        if (!jiggleMode) return;
        e.preventDefault();
        draggedItem = $(this);
        draggedIndex = draggedItem.index();
        draggedItem.addClass('dragging');
    });

    $(document).on('mousemove touchmove', function(e) {
        if (!draggedItem) return;
        e.preventDefault();
        
        var clientY = e.type === 'touchmove' ? e.originalEvent.touches[0].clientY : e.clientY;
        var clientX = e.type === 'touchmove' ? e.originalEvent.touches[0].clientX : e.clientX;
        
        $('#favchannels tr').not('.dragging').each(function() {
            var rect = this.getBoundingClientRect();
            if (clientX >= rect.left && clientX <= rect.right &&
                clientY >= rect.top && clientY <= rect.bottom) {
                var hoverIndex = $(this).index();
                if (hoverIndex !== draggedIndex) {
                    if (hoverIndex < draggedIndex) {
                        $(this).before(draggedItem);
                    } else {
                        $(this).after(draggedItem);
                    }
                    draggedIndex = draggedItem.index();
                }
            }
        });
    });

    $(document).on('mouseup touchend', function() {
        if (!draggedItem) return;
        draggedItem.removeClass('dragging');
        draggedItem = null;
        
        if (jiggleMode) {
            var newOrder = [];
            $('#favchannels tr').each(function() {
                newOrder.push(String($(this).data('channel')));
            });
            favorites = newOrder;
            saveUserFavorites();
            refreshFavoritesNowPlaying();
            updateFavoritesPlaylistLink();
        }
    });

    // Initialize
    loadUsers();
    currentUser = getCurrentUser();
    
    if (!currentUser) {
        // Show user selection modal on first load
        showUserModal();
    } else {
        favorites = currentUser.favorites || [];
        onUserSelected();
    }

    // Volume from localStorage
    var savedVolume = localStorage.getItem('seriouscast_volume');
    if (savedVolume !== null) {
        audio.volume = parseInt(savedVolume, 10) / 100;
        $('#player-volume').val(parseInt(savedVolume, 10));
    } else {
        audio.volume = 1;
    }

    // Make per-channel downloads use XSPF format
    $('.channel-download').each(function() {
        var ch = $(this).data('channel');
        $(this).attr('href', url_base + '/vlc/' + ch + '.xspf');
        $(this).attr('download', 'channel_' + ch + '.xspf');
    });

    // Fallback art chain: local file -> SiriusXM art URL -> 404.webp
    $('img.channel-art').on('error', function() {
        var $img = $(this);
        var sxmArt = $img.data('sxm-art');
        var fallback = $img.data('fallback') || '/static/channel-art/404.webp';
        var currentSrc = this.src;
        
        // If we haven't tried SiriusXM art yet and it exists, try it
        if (sxmArt && !$img.data('tried-sxm')) {
            $img.data('tried-sxm', true);
            this.src = sxmArt;
        } else if (currentSrc !== fallback) {
            // Final fallback to 404.webp
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
                liveSyncDurationCount: 3,
                liveMaxLatencyDurationCount: 10,
                liveDurationInfinity: true,
                manifestLoadingMaxRetry: 6,
                manifestLoadingTimeOut: 10000,
                levelLoadingMaxRetry: 6,
                levelLoadingTimeOut: 10000,
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
        $('.art').css('background-image', "url('" + artUrl + "')");
        $('#album-art-img').attr('src', artUrl);
        $('link[rel="shortcut icon"]').attr('href', artUrl);
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
            favorites.splice(favorites.indexOf(String(channel)), 1);
            put_favorites();
        }
    }

    function put_favorites() {
        saveUserFavorites();
        rebuild_favorites();
    }

    function rebuild_favorites() {
        if (favorites.indexOf('') !== -1) {
            favorites.splice(favorites.indexOf(''), 1);
        }
        
        $('#channels tbody tr').show();
        favorites.forEach(function(ch) {
            $('#channels tbody tr[data-channel="' + ch + '"]').hide();
        });
        
        if (favorites.length > 0) {
            $('#favhead').show();
            $('#favchannels').show();
            $('#favchannels tr').remove();
            $.each(favorites, function(data, key) {
                var element = $('#channels tr[data-channel='+key+']').clone();
                element.show();
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
        localStorage.setItem('seriouscast_volume', volume);
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
                
                if (genre) row.find('.genre').text(genre);
                
                var nameCell = row.find('.name');
                var nowPlayingText = '';
                if (artist && title) {
                    nowPlayingText = artist + ' - ' + title;
                } else if (artist) {
                    nowPlayingText = artist;
                } else if (title) {
                    nowPlayingText = title;
                }
                
                var npSpan = nameCell.find('.fav-now-playing');
                if (npSpan.length === 0) {
                    nameCell.append('<span class="fav-now-playing"></span>');
                    npSpan = nameCell.find('.fav-now-playing');
                }
                npSpan.text(nowPlayingText);
                
                if (artwork) {
                    var artImg = row.find('.channel-art');
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

    // Register service worker for PWA
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js').then(function(reg) {
            console.log('Service worker registered');
        }).catch(function(err) {
            console.log('Service worker registration failed:', err);
        });
    }
});
