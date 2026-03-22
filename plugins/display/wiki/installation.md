# Installation

## Prerequisites

Install a local LLM server:
1. Download [LM Studio](https://lmstudio.ai) or [Ollama](https://github.com/ollama/ollama)
2. Download a model (see [LLM Models](models) for recommendations)
3. Start the server

## CUDA (NVIDIA only)

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install safetensors
```

For other accelerators (ROCm, DirectML), see [onnxruntime install guide](https://onnxruntime.ai/docs/install/).

## Windows

1. Install Python 3.12 from Microsoft Store
2. Clone or download this repository
3. Run `install_windows.bat`
4. Run `start_windows.bat`

## macOS

Untested — contributions welcome.

## Linux

1. Install PortAudio:
   ```bash
   sudo apt update && sudo apt install libportaudio2
   ```
2. Clone or download this repository
3. Run:
   ```bash
   chmod +x install_ubuntu.sh start_ubuntu.sh
   ./install_ubuntu.sh
   ./start_ubuntu.sh
   ```

## Running Modes

| Command | Input | Output | Audio HW |
|---------|-------|--------|----------|
| `python main.py` | Voice (mic + wake word) | TTS (speaker) | Yes |
| `python main.py --text` | Keyboard | TTS (speaker) | TTS only |
| `python main.py --no-speech` | Keyboard | Text (console) | None |

## Changing the Model

Update `model` in `glados_config.yml` to match the model name in your LLM server.
