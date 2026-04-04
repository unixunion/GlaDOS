// Add recipe form view
GlaDOS.views.recipe_add = {
    render(container, data) {
        container.innerHTML = `
            <div class="view-title">Add Recipe</div>
            <div class="ra-form">
                <label class="ra-label">Recipe Name *</label>
                <input type="text" id="ra-title" class="ra-input" placeholder="e.g. Mum's Chicken Pie">

                <label class="ra-label">Ingredients * <span class="ra-hint">one per line, with quantities</span></label>
                <textarea id="ra-ingredients" class="ra-textarea" rows="8" placeholder="2 chicken breasts\n1 sheet puff pastry\n1 onion, diced\n200ml cream"></textarea>

                <label class="ra-label">Directions * <span class="ra-hint">one step per line</span></label>
                <textarea id="ra-directions" class="ra-textarea" rows="6" placeholder="Dice chicken and fry with onion\nAdd cream and simmer 10 minutes\nTop with pastry, bake at 200C for 25 min"></textarea>

                <div class="ra-extras" id="ra-extras-toggle">
                    <button class="btn-accent-outline ra-extras-btn" onclick="document.getElementById('ra-extras-fields').classList.toggle('visible');this.textContent=this.textContent==='More options'?'Less options':'More options'">More options</button>
                </div>
                <div class="ra-extras-fields" id="ra-extras-fields">
                    <label class="ra-label">Description <span class="ra-hint">optional, for search</span></label>
                    <input type="text" id="ra-description" class="ra-input" placeholder="A comforting chicken pie with creamy filling">

                    <label class="ra-label">Servings</label>
                    <input type="number" id="ra-servings" class="ra-input" value="4" min="1" max="20" style="width:80px">
                </div>

                <div class="ra-actions">
                    <button class="btn-primary" onclick="GlaDOS.views.recipe_add.submit()">Add Recipe</button>
                    <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'browse'})">Cancel</button>
                </div>
            </div>
        `;
    },

    submit() {
        const title = document.getElementById('ra-title').value.trim();
        const ingredientsRaw = document.getElementById('ra-ingredients').value;
        const directionsRaw = document.getElementById('ra-directions').value;
        const description = document.getElementById('ra-description')?.value.trim() || '';
        const servings = parseInt(document.getElementById('ra-servings')?.value) || 4;

        const ingredients = ingredientsRaw.split('\n').map(s => s.trim()).filter(Boolean);
        const directions = directionsRaw.split('\n').map(s => s.trim()).filter(Boolean);

        // Validation
        if (!title) { this._toast('Please enter a recipe name'); return; }
        if (ingredients.length === 0) { this._toast('Please add at least one ingredient'); return; }
        if (directions.length === 0) { this._toast('Please add at least one step'); return; }

        socket.emit('recipe_action', {
            action: 'add_recipe',
            title: title,
            ingredients: ingredients,
            directions: directions,
            description: description,
            servings: servings,
        });

        this._toast(`Adding "${title}"...`);
    },

    _toast(msg) {
        let el = document.getElementById('ra-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'ra-toast';
            el.className = 'ra-toast';
            document.body.appendChild(el);
        }
        el.textContent = msg;
        el.classList.add('visible');
        clearTimeout(el._timer);
        el._timer = setTimeout(() => el.classList.remove('visible'), 4000);
    }
};
