// Settings view renderer
GlaDOS.views.settings = {
    renderCard(container) {
        container.innerHTML = `
            <div class="dash-card-header" onclick="socket.emit('settings_action',{action:'show'})">
                <span class="dash-card-icon"><i class="icon-settings"></i></span> Settings
            </div>
            <div class="dash-card-body settings-card-body">
                Integrations &amp; system config
            </div>`;
    },

    render(container, data) {
        const spotify = data.spotify || {};
        const spotifyStatus = spotify.connected
            ? `<span class="settings-connected">Connected</span> — ${spotify.device || 'no active device'}`
            : '<span class="settings-disconnected">Not connected</span>';

        const system = data.system || {};

        const themes = [
            { id: 'default', name: 'Default', desc: 'Dark blue' },
            { id: 'retro-crt', name: 'Retro CRT', desc: '80s green phosphor' },
        ];
        const currentTheme = GlaDOS.getTheme ? GlaDOS.getTheme() : 'default';
        const themeBtns = themes.map(t => {
            const active = t.id === currentTheme ? ' active' : '';
            return `<button class="theme-btn${active}" onclick="GlaDOS.setTheme('${t.id}');socket.emit('settings_action',{action:'show'})">
                <div class="theme-btn-name">${GlaDOS.esc(t.name)}</div>
                <div class="theme-btn-desc">${GlaDOS.esc(t.desc)}</div>
            </button>`;
        }).join('');

        container.innerHTML = `
            <div class="view-title">Settings</div>

            <div class="settings-section">
                <div class="settings-section-title"><i class="icon-palette"></i> Theme</div>
                <div class="theme-picker">${themeBtns}</div>
            </div>

            <div class="settings-section">
                <div class="settings-section-title"><i class="icon-music"></i> Spotify</div>
                <div class="settings-row">
                    <span class="settings-label">Status</span>
                    <span class="settings-value">${spotifyStatus}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label"></span>
                    <a href="/spotify/auth" class="settings-btn" target="_blank">
                        ${spotify.connected ? 'Reconnect' : 'Connect Spotify'}
                    </a>
                </div>
            </div>

            <div class="settings-section">
                <div class="settings-section-title"><i class="icon-settings"></i> System</div>
                <div class="settings-row">
                    <span class="settings-label">Model</span>
                    <span class="settings-value">${GlaDOS.esc(system.model || '—')}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">Backend</span>
                    <span class="settings-value">${GlaDOS.esc(system.client_type || '—')}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">NLP Mode</span>
                    <span class="settings-value">${system.nlp_mode ? 'On' : 'Off'}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">Voice Core</span>
                    <span class="settings-value">${GlaDOS.esc(system.voice_core || '—')}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">Knowledge RAG</span>
                    <span class="settings-value">${system.knowledge_enabled ? 'On (' + (system.knowledge_points || '?') + ' points)' : 'Off'}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">Memory</span>
                    <span class="settings-value">${system.memory_enabled ? 'On' : 'Off'}</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">Plugins</span>
                    <span class="settings-value">${system.plugin_count || '?'} loaded</span>
                </div>
                <div class="settings-row">
                    <span class="settings-label">TTS Fade</span>
                    <span class="settings-value">${system.tts_fade_ms || '?'}ms</span>
                </div>
            </div>

            <div class="settings-section">
                <div class="settings-section-title"><i class="icon-link"></i> Links</div>
                <div class="settings-row">
                    <a href="/wiki/" class="settings-btn">Wiki Documentation</a>
                    <a href="/shopping" class="settings-btn">Mobile Shopping List</a>
                </div>
            </div>
        `;
    }
};
