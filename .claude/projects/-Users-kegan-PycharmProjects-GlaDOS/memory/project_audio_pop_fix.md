---
name: TTS audio pop fix
description: Root cause and fix for audio popping every time TTS speaks - sd.play() opens/closes PortAudio stream per sentence
type: project
---

Audio pop was caused by `sd.play()` + `sd.wait()` in `glados_speech_module.py` opening and closing a PortAudio output stream for every sentence. Fixed by using a persistent `sd.OutputStream` that stays open across sentences, writing audio with `stream.write()`.

**Why:** Each `sd.play()` call creates a new PortAudio output stream. On macOS, the open/close cycle produces an audible pop/click.

**How to apply:** The `_play_audio()` method now uses `_ensure_output_stream()` to maintain a persistent output stream. The stream is only closed in `stop()`.
