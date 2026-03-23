# Functions & Tools

## Timers

- `"set a timer for 5 minutes"` — starts a countdown, fires an event + audio alert when done
- `"list timers"` — shows active timers and time remaining
- Timer alerts flash the display and play a sound

## Alarms

- `"set an alarm for 5pm tomorrow"` — natural language time (powered by `dateparser`)
- `"list my alarms"` — shows all pending alarms
- `"cancel the morning alarm"` — cancels by description or time
- Alarm fires a repeating audio alert until dismissed
- Pauses music during alarm, resumes after dismissal
- Dismiss by saying "stop", "cancel", "silence", "dismiss"

## Recipes

Get a recipe CSV from [Kaggle](https://www.kaggle.com/datasets/wilmerarltstrmberg/recipe-dataset-over-2m) and place in `plugin_data/recipes/dataset.csv`.

- `"search for a recipe for pizza"` — returns matching recipes
- `"select recipe banana bread"` — selects and displays on screen
- Selected recipes are automatically pushed to the display

## Music Player (Spotify)

Controls Spotify playback via the Spotipy API.

- `"play ben howard"` — fuzzy matches and plays
- `"stop the music"` / `"pause"` / `"resume"` — playback controls
- `"what song is playing"` — current track info
- `"list spotify devices"` — shows available playback devices
- `"next track"` / `"previous track"` — skip controls

Music is paused when an alarm fires and resumed after dismissal.

## Display

Web-based display at `http://<host>:5001` for a kitchen iPad or browser.

- Shows an idle clock by default
- Activity indicator pill in top-left
- Status toasts at bottom (listening, thinking, speaking, user speech)
- `"show me the recipe"` — displays current recipe
- `"display the timer"` — shows live countdown
- `"clear the screen"` — returns to idle

## Vision (POC)

Camera images are scanned and sent to a vision model for description.

- `"what is happening in the kitchen"` — observes a specific room
- Background scanning can auto-trigger vacuum if dirt is detected
- Requires `vision_enabled: true` in config

## Memory

Persistent cross-session memory powered by ChromaDB vector search. Memory operations are detected by the IntentClassifier and handled pre-LLM (no tool calls) so they work reliably with any model.

- `"remember that I prefer celsius"` — IntentClassifier detects "remember" intent, fact extracted and stored directly in ChromaDB
- `"don't forget the garage code is 1234"` — same flow, stored as an explicit fact
- `"I want you to remember my cat's name is Luna"` — classifier handles paraphrasing naturally
- `"do you remember what I said about the kitchen?"` — IntentClassifier detects "recall" intent, broad memory search, results injected as context
- `"what are my preferences?"` — triggers recall, LLM responds using injected memory results
- Automatic retrieval also happens before every LLM call — relevant past exchanges and stored facts are injected as context without needing to ask

Requires `memory_enabled: true` in config.

## System

- `"list all plugins"` — shows loaded plugins and their descriptions
- `"get logs"` — retrieves error and diagnostic logs
- `"what time is it"` — current time and date
