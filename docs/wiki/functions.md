# Functions & Tools

All commands can be spoken via voice (wake word + phrase) or typed in the display chat box.

## Timers

| Say this | What happens |
|----------|-------------|
| "set a timer for 5 minutes" | Starts a countdown, shows on display |
| "set a timer for 1 hour and 30 minutes" | Compound durations work |
| "timer for 60 seconds" | Short form |
| "set a 15 minute timer" | Alternate phrasing |
| "set a timer for the eggs" | Named timer (description only, no duration — asks for duration) |
| "set a cooking timer for 10 minutes" | Named + duration |
| "list timers" / "what timers are running" | Shows active timers and time remaining |
| "how much time left on my egg timer" | Check a specific timer |
| "cancel the timer" | Cancels the only active timer |
| "cancel the egg timer" | Cancels by name |
| "cancel the 5 minute timer" | Cancels by duration description |

- Active timers show as floating cards in the bottom-right with live countdown and a Cancel button
- Dashboard has quick-create: tap 5m/10m/15m/30m presets, adjust with +/- buttons, then Start
- When a timer expires: card stays visible with "DONE" and pulsing border, sound plays persistently
- **Dismiss**: tap the Dismiss button on the card, say "stop"/"cancel", or use the mute button
- Sound auto-stops after 3 minutes if not dismissed
- Multiple timers supported — all show as separate cards
- **Durable** — timers survive restarts. On boot, unexpired timers are restored from `plugin_data/timers/timers.json`

## Alarms

Alarms are for fixed times (use **timers** for durations like "5 minutes"). Best results with AM/PM format.

| Say this | What happens |
|----------|-------------|
| "set an alarm for 5pm tomorrow" | Natural language time parsing |
| "set an alarm for 8:30 am on Sunday" | Specific day + time |
| "wake me up at 7am" | AM/PM format (recommended) |
| "alarm at 9 o'clock" | Short form (converted to 9:00) |
| "set a morning alarm for 7:30" | Named alarm |
| "list my alarms" / "what alarms do I have set" | Shows all pending alarms |
| "cancel the alarm" | Cancel by description match |
| "turn off the alarm" | Alternate phrasing |
| "dismiss the alarm" | While alarm is ringing |
| "cancel the 7am alarm" | Cancel by time |

- Alarms use the same unified ringing system as timers
- When an alarm fires: card appears with "DONE" and Dismiss button, sound plays persistently
- Pauses any playing music during alarm, resumes after dismissal
- **Dismiss**: tap the Dismiss button, say wake word + "stop"/"cancel"/"silence", or mute button
- Active alarms show alongside timers in the floating overlay
- **Durable** — alarms survive restarts. On boot, future alarms are restored from `plugin_data/alarms/alarms.json`

## Display Controls

| Button | Location | Action |
|--------|----------|--------|
| Speaker (🔈) | Top-left header | Mute/unmute TTS output. When muted, LLM still processes but doesn't speak. |
| Microphone (🎤) | Top-left header | Mute/unmute voice input. When muted, wake words and all audio input ignored. |
| Chat (☰) | Top-right header | Toggle chat drawer |
| Back (←) | Top-left header | Return to previous view |

## Recipes

~13,500 recipes across 15 categories with images. Browse, search, or let GlaDOS surprise you.

| Say this | What happens |
|----------|-------------|
| "find me a recipe for bread" | Search by keyword |
| "what can I cook with chicken" | Ingredient-based search |
| "surprise me" | Random recipe weighted by pantry match |
| "random recipe" | Pure random from full dataset |
| "lets make apple pie" | Select and start cooking |

- **Browse** button on dashboard → category grid → recipe list with thumbnails
- Integrates with [pantry](pantry.md), [shopping list](shopping.md), and [meal planner](meal-planner.md)
- After selecting, use cooking step commands (see below)
- Imperial measurements annotated with metric when `metric_annotations: true`
- **Full guide: [Recipes & Cooking](recipes.md)**

### Cooking Steps (after selecting a recipe)

These commands only work in COOKING activity context, after a recipe has been selected:

| Say this | What happens |
|----------|-------------|
| "list the ingredients" / "ingredients" | Read all ingredients |
| "what do I need" | Alternate phrasing |
| "what are the steps" / "steps" | Read all directions |
| "read the instructions" | Alternate phrasing |
| "next step" | Advance to next step |
| "what do I do next" / "keep going" | Alternate phrasing |
| "previous step" / "go back" | Go back one step |
| "can you repeat that" / "say that again" | Repeat current step |
| "I didn't catch that" | Alternate repeat phrasing |
| "first step" / "start from the beginning" | Jump to step 1 |
| "start over" | Restart recipe |
| "what are we making" / "what are we cooking" | Current recipe name |

## Music Player

Controls Spotify playback. Requires Spotify credentials — run `python3 spotify_auth.py` to authenticate.

| Say this | What happens |
|----------|-------------|
| "play radiohead" | Plays the artist (shuffled catalog) |
| "play californication" | Searches artist → playlist → album → tracks |
| "play bohemian rhapsody by queen" | "X by Y" pattern → searches track with artist filter |
| "play dummy by portishead" | Works for albums too — finds the best match |
| "play some darkwave" | Genre keyword → searches playlists (shuffled) |
| "play 80s synthwave" | Genre/era keyword → playlist search |
| "play chill jazz music" | "music" keyword → playlist search |
| "stop the music" | Stop playback |
| "pause the music" / "pause" | Pause |
| "resume the music" | Resume |
| "skip song" / "next track" | Skip to next |
| "previous track" | Go back |
| "what song is playing" | Current track info |
| "what's currently playing" | Alternate phrasing |
| "what am I listening to" | Alternate phrasing |
| "list spotify devices" | Show available speakers |
| "what speakers are connected" | Alternate phrasing |

### Smart Query Classification

The music player classifies queries to search the right Spotify type:

| Pattern | Classification | Example |
|---------|---------------|---------|
| "X by Y" | Track with artist filter | "creep by radiohead" → track search for "creep" filtered to Radiohead |
| Genre/mood keywords | Playlist search (shuffled) | "darkwave", "chill jazz", "80s synthwave" |
| "some X" / "X music" / "X vibes" | Playlist search | "some trip-hop", "workout music" |
| Everything else | Auto — tries artist → playlist → album → tracks | "the police", "lateralus" |

The auto fallback chain ensures that even ambiguous names find the right thing — "the police" tries artist search first (finds The Police), "lateralus" tries artist (no match), then playlist, then album (finds Tool's album).

### Continuous Playback

- **Playlists and artists** start shuffled for variety
- **Track searches** queue 20 related tracks so music doesn't stop after one song
- **Albums** play in order (no shuffle)

### Reconnection

If GlaDOS starts while offline, Spotify is unavailable. Once internet returns, the next `play_music` call automatically attempts to reconnect — no restart needed.

- Music is paused when an alarm fires and resumed after dismissal

## Shopping List

| Say this | What happens |
|----------|-------------|
| "add eggs to the shopping list" | Adds item (auto-categorized) |
| "we're out of butter" | Adds to list + removes from pantry |
| "what's on the shopping list" | Shows interactive list on display |
| "remove milk from the list" | Removes item |
| "shopping done" | Moves bought items to pantry |
| "lets plan shopping" | Enters planning mode |

Mobile PWA at `/shopping`, planning mode, recurring items, autocomplete. **Full guide: [Shopping List](shopping.md)**

## Pantry

| Say this | What happens |
|----------|-------------|
| "I put the chicken in freezer drawer 2" | Records storage location |
| "the bacon expires on the 24th" | Sets expiry date |
| "where is the flour" | Finds item location |
| "what's expiring soon" | Lists expiring items |
| "catalog the fridge" | Rapid-fire inventory mode — call out what you see |

Expiry tracking, shelf life estimation, location management, catalog mode. **Full guide: [Pantry & Inventory](pantry.md)**

## Meal Planning

| Say this | What happens |
|----------|-------------|
| "save this recipe" | Save to favorites |
| "plan lasagna for monday" | Add to weekly meal plan |
| "generate a shopping list" | Auto-generate from meal plan |
| "suggest meals for the week" | AI-powered meal suggestions |

Favorites, weekly plan, smart shopping list, household scaling. **Full guide: [Meal Planning](meal-planner.md)**

## Display

Web-based display at `http://<host>:5001` for a kitchen iPad or browser.

| Say this | What happens |
|----------|-------------|
| "put the recipe on screen" | Display current recipe |
| "display the timer on screen" | Show active timers |
| "clear the screen" / "clear the display" | Return to idle clock |
| "show that on the iPad" | General display command |

- Idle view shows a clock with the GlaDOS avatar
- Activity indicator pill in top-left corner
- Status toasts at bottom: listening (green), thinking (orange), speaking (red), tool call (blue)
- Timers and alarms show as live-updating cards
- Recipes auto-display when selected

## Memory

Persistent cross-session memory powered by ChromaDB vector search. Memory operations are detected by the IntentClassifier and handled pre-LLM via a chat pipeline hook — no tool calls needed, works reliably with any model.

### Remembering

| Say this | What happens |
|----------|-------------|
| "remember that I prefer celsius" | Stores as an explicit fact |
| "don't forget the garage code is 1234" | Alternate phrasing |
| "I want you to remember my cat's name is Luna" | Natural paraphrasing |
| "save to memory" | Generic save |
| "make a note that the plumber comes on Tuesday" | Note-style |
| "remember I like my coffee black" | Preference storage |

Response: "Got it, I'll remember that."

### Recalling

| Say this | What happens |
|----------|-------------|
| "do you remember what I said about the kitchen?" | Search + LLM answers naturally |
| "what are my preferences?" | Retrieves stored facts |
| "what do you know about my allergies" | Targeted recall |
| "what do you remember" | Broad recall |
| "summarise memories" | List all stored memories |
| "show me what you remember" | Alternate phrasing |

Response: LLM answers using the retrieved memories as context.

### Forgetting

| Say this | What happens |
|----------|-------------|
| "forget everything" | Clears ALL stored memories |
| "clear your memory" / "clear all memories" | Alternate phrasing |
| "erase your memory" / "wipe your memory" | Alternate phrasing |

Response: "Done. All X memories have been cleared."

### Debugging

| Say this | What happens |
|----------|-------------|
| "dump memories" / "debug memory" | Logs all memory entries to console at INFO level |
| "memory dump" / "log all memories" | Alternate phrasing |

Response: "Dumped X memories to the log." (check terminal for details)

### How it works

- **Automatic storage**: Each user+assistant exchange is saved after every response
- **Automatic retrieval**: Before each LLM call, relevant past exchanges and stored facts are injected as context
- **Pre-LLM interception**: Remember/recall/forget/debug intents are detected by the IntentClassifier and handled immediately — no LLM involvement
- **Facts have priority**: Explicitly stored facts (via "remember that...") are always included in retrieval, regardless of activity context

Requires `memory_enabled: true` in config.

## Weather

| Say this | What happens |
|----------|-------------|
| "what is the weather" | Current conditions (asks for location) |
| "is it cold today" | Temperature check |
| "weather forecast" | Forecast |
| "will it rain today" | Rain prediction |
| "how's the weather outside" | Casual phrasing |
| "weather in London" | Specific location |

## Unit Conversion

| Say this | What happens |
|----------|-------------|
| "convert 100 fahrenheit to celsius" | Temperature conversion |
| "how many grams in 2 pounds" | Weight conversion |
| "convert 5 miles to kilometers" | Distance conversion |
| "what is 1 cup in milliliters" | Volume conversion |

## Arithmetic

| Say this | What happens |
|----------|-------------|
| "what is 5 plus 7" | Addition |
| "add 2 and 2" | Alternate phrasing |
| "what is 56 minus 12" | Subtraction |
| "what is 6 times 6" | Multiplication |
| "divide 10 by 3" | Division |

## Robot Vacuum

| Say this | What happens |
|----------|-------------|
| "start vacuuming" | Start cleaning |
| "clean the kitchen" | Room-specific cleaning |
| "vacuum the carpets" | Alternate phrasing |
| "stop the vacuum cleaner" | Stop cleaning |
| "stop the roomba" | Alternate phrasing |

## Vision (POC)

Camera images are scanned and sent to a vision model for description. Requires `vision_enabled: true` in config.

| Say this | What happens |
|----------|-------------|
| "what is happening in the kitchen" | Observe a specific room |
| "what is going on in the office" | Alternate phrasing |
| "check the living room for people" | Person detection |

## System

| Say this | What happens |
|----------|-------------|
| "what time is it" / "time please" | Current time (via NLP, instant) |
| "what is the date" | Current date |
| "list all plugins" / "what are your capabilities" | Shows loaded plugins |
| "what plugins are loaded" | Alternate phrasing |
| "are there any errors" / "check logs for errors" | Diagnostic logs |
| "run a self diagnostic" | System check |

## Voice Commands (intercepted before LLM)

These are handled by the speech system directly — no LLM or NLP processing:

| Command | Action |
|---------|--------|
| "stop" / "cancel" / "silence" / "dismiss" | Dismiss ringing alarm, or stop music |
| "stop listening" / "go to sleep" | Mute — ignore all input until unmuted |
| "start listening" / "wake up" | Unmute — resume normal operation |

Priority for stop commands: ringing alarm > playing music > pass to LLM.
