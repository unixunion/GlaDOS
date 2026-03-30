// Settings view renderer
GlaDOS.views.settings = {
    renderCard(container) {
        container.innerHTML = `
            <div class="dash-card-header" onclick="socket.emit('settings_action',{action:'show'})">
                <span class="dash-card-icon">&#9881;</span> Settings
            </div>
            <div class="dash-card-body" style="font-size:0.8rem;color:#666">
                Integrations &amp; system config
            </div>`;
    },

    render(container, data) {
        const spotify = data.spotify || {};
        const spotifyStatus = spotify.connected
            ? `<span style="color:#4caf50">Connected</span> — ${spotify.device || 'no active device'}`
            : '<span style="color:#888">Not connected</span>';

        const system = data.system || {};

        container.innerHTML = `
            <div class="view-title">Settings</div>

            <div class="settings-section">
                <div class="settings-section-title">&#127925; Spotify</div>
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
                <div class="settings-section-title">&#129302; System</div>
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
                <div class="settings-section-title">&#128279; Links</div>
                <div class="settings-row">
                    <a href="/wiki/" class="settings-btn">Wiki Documentation</a>
                    <a href="/shopping" class="settings-btn">Mobile Shopping List</a>
                </div>
            </div>
        `;
    }
};
