# Voice & Speech

## Wake Word

Say **"GlaDOS"** or **"Hey GlaDOS"** to get her attention. A confirmation beep plays when listening starts so you know when to speak.

After GlaDOS finishes speaking, she automatically listens for a follow-up (with a beep) for about 10 seconds — no need to say the wake word again for a quick conversation.

## Interrupting

Say the wake word while GlaDOS is talking to immediately interrupt. TTS stops, the speech queue is flushed, and she switches to listening for your new command.

## Mute / Unmute

| Say this (after wake word) | What happens |
|---|---|
| "stop listening" / "go to sleep" / "mute yourself" / "be quiet" | Mutes — stops listening for commands |
| "start listening" / "wake up" / "unmute" / "resume listening" | Unmutes — resumes normal operation |

When muted, the wake word still works but only listens briefly for an unmute command. Everything else is ignored.

## Quick Commands

These are handled instantly by the speech system without involving the LLM:

| Command | Action |
|---------|--------|
| "stop" / "cancel" / "silence" / "dismiss" | Dismiss ringing alarm, or stop music |
| "stop listening" / "go to sleep" | Mute |
| "start listening" / "wake up" | Unmute |

Priority for stop commands: ringing alarm > playing music > pass to LLM.

## Display Feedback

When you speak, the transcribed text appears on the display as a teal toast in quotes so you can see what GlaDOS heard.

---

## Technical Details

### Voice Cores

GlaDOS supports two TTS backends (set `voice_core` in config):

- **`glados`** (default) — Piper/ONNX TTS using `glados.onnx`. The classic GlaDOS voice.
- **`kokoro`** — Kokoro ONNX TTS. Multiple voices, requires `pip install kokoro-onnx` and model download. See [Configuration](configuration.md).

### TTS Buffer Mode

Controls latency vs prosody trade-off (`tts_buffer_mode` in config):

- **`sentence`** (default) — waits for `.!?` before speaking. Best quality, 1-3s latency.
- **`clause`** — splits on commas, colons too. ~0.5-1s to first audio.
- **`word`** — flushes every N words. Fastest but can sound choppy with Piper.

### Text Preprocessing

Before TTS, text is automatically processed: numbers to words, cooking abbreviations expanded (Tbsp → tablespoon, oz → ounce), unicode normalized, think tags stripped.
