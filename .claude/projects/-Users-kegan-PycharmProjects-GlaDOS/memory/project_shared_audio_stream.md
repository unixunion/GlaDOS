---
name: Shared audio input stream
description: OpenWakeWord and Whisper share a single sd.InputStream to avoid macOS PortAudio segfaults
type: project
---

Two simultaneous `sd.InputStream` instances (openwakeword + whisper) plus a TTS output stream caused PortAudio segfaults on macOS. Fixed by removing the openwakeword module's own stream — it now receives audio via `push_audio()` queue, fed from the whisper module's audio callback.

**Why:** macOS CoreAudio crashes with 3 simultaneous PortAudio streams (2 input + 1 output). The `PaMacCore (AUHAL) Error on line 2523: err='-50'` and subsequent segfault.

**How to apply:** `openwakeword_detection_module.py` no longer imports or uses `sounddevice`. The whisper module's `_audio_callback` pushes audio to `self.wakeword_module.push_audio()`. Wired together via `wakeword_module` param in `WhisperVoiceDetectionModule.__init__()`.