# Plugin Display Views

Plugins can provide their own display views — custom screens and dashboard cards — without editing the display framework. The display system dynamically loads plugin-provided JavaScript renderers at runtime.

## Architecture

```
Plugin (Python)                    Display Framework (HTML/JS)
─────────────────                  ──────────────────────────
register_view(                     fetch('/api/views')
  "pantry",                           ↓
  "plugins/pantry/views/pantry.js" loads each JS file
)                                     ↓
    ↓                              GlaDOS.views.pantry.render(container, data)
/api/views → [{js_url, ...}]      GlaDOS.views.pantry.renderCard(container)
/plugin-views/path → serves JS
```

## Registering a View

```python
class MyPlugin(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()
        self.register_view(
            view_type="my_view",                    # matches EventMessage name
            js_path="plugins/my_plugin/views/my.js", # JS file path
            css_path="plugins/my_plugin/views/my.css", # optional CSS
            dashboard_card=True,                     # provide a dashboard card
        )
```

For function-based plugins:
```python
plugin_manager.register_view("my_view", "plugins/my_plugin/views/my.js")
```

## View Module Contract

Each JS file registers itself on `GlaDOS.views`:

```javascript
// plugins/my_plugin/views/my.js
GlaDOS.views.my_view = {
    // Required: render the full-screen view
    render(container, data) {
        container.innerHTML = `
            <div class="view-title">${GlaDOS.esc(data.title || 'My View')}</div>
            <div>${GlaDOS.esc(data.content)}</div>
        `;
    },

    // Optional: render a dashboard card
    renderCard(container) {
        const d = GlaDOS.dashboardData.my_data || {};
        container.innerHTML = `
            <div class="dash-card-header" onclick="socket.emit('my_action',{action:'show'})">
                <span class="dash-card-icon">&#128200;</span> My Plugin
                <span class="dash-card-badge">${d.count || 0}</span>
            </div>
            <div class="dash-card-body">
                ${d.summary || 'No data yet'}
            </div>
        `;
    },

    // Optional: helper methods called from the rendered HTML
    doSomething(param) {
        socket.emit('my_action', { action: 'do', param });
    }
};
```

### Available Framework APIs

| API | Description |
|-----|-------------|
| `GlaDOS.esc(str)` | HTML-escape a string (XSS safe) |
| `GlaDOS.dashboardData` | Shared dashboard state object (plugins read/write their section) |
| `GlaDOS.views` | View registry (your module registers here) |
| `socket` | SocketIO client (global, for emitting events) |

### CSS Classes Available

The framework provides common CSS classes in `display.css`:

| Class | Use |
|-------|-----|
| `.view-title` | Page title with orange underline |
| `.dash-card` | Dashboard card container |
| `.dash-card-header` | Card header with icon + badge |
| `.dash-card-body` | Card content area |
| `.dash-card-badge` | Count badge (`.urgent` variant) |
| `.quick-input` | Input + button row |
| `.btn-primary` | Orange action button |

## Pushing Data to Your View

From your plugin backend, publish display events:

```python
# Full-screen view
self.event_system.publish(EventMessage(
    role="display",
    name="my_view",       # must match view_type in register_view
    content={"title": "My Data", "items": [...]},
    process_output=False,
))

# Dashboard data (updates card without navigating)
self.event_system.publish(EventMessage(
    role="display",
    name="dashboard_data",
    content={"my_data": {"count": 42, "summary": "All good"}},
    process_output=False,
))
```

## Dashboard Card Order

Cards appear in this order by default: Shopping, Pantry, Recipes, Timers, then any additional plugins. The order is defined in `display.html`:

```javascript
const _cardOrder = ['shopping_list', 'pantry', 'recipe', 'timer'];
```

New plugin cards appear after these.

## File Layout Convention

```
plugins/
  my_plugin/
    __init__.py
    my_plugin.py          # Plugin code
    views/
      my.js               # View renderer
      my.css              # Optional styles
```

## Existing View Modules

| View Type | Plugin | File | Dashboard Card |
|-----------|--------|------|---------------|
| `shopping_list` | PantryPlugin | `plugins/pantry/views/shopping.js` | Yes |
| `pantry` | PantryPlugin | `plugins/pantry/views/pantry.js` | Yes |
| `recipe` | RecipeAPI | `plugins/recipes/views/recipe.js` | Yes |
| `recipe_search` | RecipeAPI | `plugins/recipes/views/recipe.js` | No |
| `timer` | CountdownTimer | `plugins/basic/views/timer.js` | Yes |
| `info` | DisplayPlugin | `plugins/display/views/info.js` | No |
