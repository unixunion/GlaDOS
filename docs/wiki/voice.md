# Voice

## Voice Cores

GlaDOS supports switchable TTS backends. Set `voice_core` in `glados_config.yml`:

- **`glados`** (default) — Piper/ONNX TTS. Uses `glados.onnx` model. The classic GlaDOS voice.
- **`kokoro`** — Kokoro ONNX TTS. Multiple voices (American/British, male/female), requires `pip install kokoro-onnx` and downloading model files (~340MB: `cd models/kokoro-82m-onnx && python get.py`). Set `speaker_id` to choose a voice (e.g. `"bf_isabella"` for British female Isabella).

Both cores share the same queue processing, text preprocessing, interrupt handling, and audio playback — only the synthesis engine differs. See [Configuration](configuration.md) for setup details.

## TTS Buffer Mode

Controls how quickly LLM tokens reach the speaker. The `tts_buffer_mode` config sets the granularity:

- **`sentence`** (default) — waits for a full sentence (`.!?`) before speaking. Best prosody, but 1-3s latency.
- **`clause`** — splits on commas, semicolons, colons, and em-dashes too. Good balance of speed and quality. ~0.5-1s to first audio.
- **`word`** — flushes every N words (set via `tts_word_buffer`, default 5). Fastest but can sound choppy with Piper since each TTS call generates a separate audio segment.

Kokoro handles short fragments better than Piper, so `word` mode works better with the Kokoro voice core.

### Text preprocessing

Before TTS, text is automatically processed:
- Numbers converted to spoken words
- Think tags (`[THINK]...[/THINK]`) stripped
- Cooking abbreviations expanded: Tbsp → tablespoon, tsp → teaspoon, oz → ounce, lb → pounds, pkg → package, qt → quart, pt → pint
- Unicode dashes/quotes normalized
- Special characters removed

## Wake Word

Using OpenWakeWord for wake word detection. The default model responds to "GlaDOS" and "Hey GlaDOS".

A confirmation beep plays when the system starts listening after a wake word, so you know when to speak. The beep also plays when the system auto-listens for a follow-up response after TTS finishes.

## TTS Interruption

When `interrupt_on_wakeword: true` is set in config, saying the wake word while GlaDOS is speaking will immediately stop TTS playback, flush the speech queue, and switch to listening mode with the confirmation beep.

## Mute / Unmute

Say wake word + one of these to mute:
- "stop listening", "go to sleep", "mute yourself", "be quiet"

Say wake word + one of these to unmute:
- "start listening", "resume listening", "wake up", "unmute"

When muted, the wake word still fires but only listens briefly for an unmute command. All other input is dropped.

## Intercepted Commands

These commands are handled directly by the speech system without going through the LLM:

| Command | Action |
|---------|--------|
| "stop" / "cancel" / "silence" / "dismiss" | Dismiss ringing alarm, or stop music |
| "stop listening" / "go to sleep" | Mute |
| "start listening" / "wake up" | Unmute |

Priority order for stop commands: ringing alarm > playing music > pass to LLM.

## Display Feedback

When the user speaks, the transcribed text appears briefly on the display as a teal-colored toast in quotes, so you can see what GlaDOS heard.
