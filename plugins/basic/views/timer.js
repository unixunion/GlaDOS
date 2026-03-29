// Timer overlay renderer
GlaDOS.views.timer = {
    _minutes: 15,
    renderCard(container) {
        container.innerHTML = `
            <div class="dash-card-header">
                <span class="dash-card-icon">&#9201;</span> Timers
            </div>
            <div class="dash-card-body" id="dash-timers">
                <div class="timer-quick-btns">
                    <button class="tq-btn" onclick="GlaDOS.views.timer.quickSelect(5)">5m</button>
                    <button class="tq-btn" onclick="GlaDOS.views.timer.quickSelect(10)">10m</button>
                    <button class="tq-btn" onclick="GlaDOS.views.timer.quickSelect(15)">15m</button>
                    <button class="tq-btn" onclick="GlaDOS.views.timer.quickSelect(30)">30m</button>
                </div>
                <div id="timer-adjust" style="display:none">
                    <div class="timer-adjust-display"><span id="timer-adjust-val">15</span> min</div>
                    <div class="timer-adjust-btns">
                        <button onclick="GlaDOS.views.timer.adjust(-10)">-10</button>
                        <button onclick="GlaDOS.views.timer.adjust(-5)">-5</button>
                        <button onclick="GlaDOS.views.timer.adjust(-1)">-1</button>
                        <button onclick="GlaDOS.views.timer.adjust(1)">+1</button>
                        <button onclick="GlaDOS.views.timer.adjust(5)">+5</button>
                        <button onclick="GlaDOS.views.timer.adjust(10)">+10</button>
                    </div>
                    <div style="display:flex;gap:6px;margin-top:6px">
                        <button class="tq-start" onclick="GlaDOS.views.timer.start()">Start</button>
                        <button class="tq-cancel" onclick="GlaDOS.views.timer.cancelAdjust()">Cancel</button>
                    </div>
                </div>
            </div>`;
    },
    quickSelect(mins) {
        this._minutes = mins;
        document.getElementById('timer-adjust-val').textContent = mins;
        document.querySelector('.timer-quick-btns').style.display = 'none';
        document.getElementById('timer-adjust').style.display = '';
    },
    adjust(delta) {
        this._minutes = Math.max(1, this._minutes + delta);
        document.getElementById('timer-adjust-val').textContent = this._minutes;
    },
    start() {
        socket.emit('timer_action', { action: 'create', minutes: this._minutes });
        this.cancelAdjust();
    },
    cancelAdjust() {
        document.querySelector('.timer-quick-btns').style.display = '';
        document.getElementById('timer-adjust').style.display = 'none';
    },
    render(container, data) {
        // Timer overlay uses a different container (timerOverlay, not viewContainer)
        const overlay = document.getElementById('timer-overlay');
        if (data.timers && data.timers.length > 0) {
            overlay.innerHTML = data.timers.map(t => {
                const desc = GlaDOS.esc(t.description || 'Timer');
                const isDone = t.type === 'timer_done' || t.type === 'alarm_done';
                const icon = t.type === 'alarm' ? '&#9200;' : isDone ? '&#9888;' : '&#9201;';
                const doneClass = isDone ? ' timer-done' : '';
                const btn = isDone
                    ? `<button class="tf-dismiss tf-dismiss-done" onclick="socket.emit('timer_action',{action:'dismiss_ring'})">Dismiss</button>`
                    : `<button class="tf-dismiss" onclick="socket.emit('timer_action',{action:'cancel',description:'${desc.replace(/'/g,"\\'")}',type:'${t.type}'})">Cancel</button>`;
                return `<div class="timer-float${doneClass}">
                    <div class="tf-icon">${icon}</div>
                    <div class="tf-name">${desc}</div>
                    <div class="tf-time">${GlaDOS.esc(t.expires_in||'--')}</div>
                    ${btn}
                </div>`;
            }).join('');
            overlay.classList.add('visible');
        } else { overlay.classList.remove('visible'); overlay.innerHTML = ''; }
    }
};
