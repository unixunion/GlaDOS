# Installation

## Prerequisites

Install a local LLM server:
1. Download [LM Studio](https://lmstudio.ai) or [Ollama](https://github.com/ollama/ollama)
2. Download a model (see [LLM Models](models.md) for recommendations)
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

## Post-Install (All Platforms)

Download OpenWakeWord preprocessor models (required for wake word detection):
```bash
python3 -c "import openwakeword; openwakeword.utils.download_models()"
```

## Running Modes

| Command | Input | Output | Audio HW |
|---------|-------|--------|----------|
| `python main.py` | Voice (mic + wake word) | TTS (speaker) | Yes |
| `python main.py --text` | Keyboard | TTS (speaker) | TTS only |
| `python main.py --no-speech` | Keyboard | Text (console) | None |

## Raspberry Pi (NLP-only mode)

GlaDOS runs on Raspberry Pi without an LLM server using pure NLP mode. Requires Python 3.11+ (Bookworm).

```bash
# Install audio dependencies
sudo apt update && sudo apt install libportaudio2

# Clone and set up
git clone https://github.com/unixunion/glados.git
cd glados
python3 -m venv .venv && source .venv/bin/activate

# NLP-only mode (no LLM needed)
pip install -r requirements_rpi.txt

# OR: with cloud LLM support (Anthropic Claude, remote LM Studio)
pip install -r requirements_rpi_llm.txt

# Download OpenWakeWord preprocessor models (required, ~2MB)
python3 -c "import openwakeword; openwakeword.utils.download_models()"

# Edit glados_config.yml:
#   nlp_mode: true              # for NLP-only
#   nlp_confidence_threshold: 0.4

python main.py
```

The RPi requirements files use `--extra-index-url https://download.pytorch.org/whl/cpu` to pull CPU-only PyTorch automatically — no NVIDIA/CUDA downloads (~2GB saved).

| File | Includes | Use case |
|------|----------|----------|
| `requirements_rpi.txt` | Voice + NLP + Display | No LLM, pure NLP mode |
| `requirements_rpi_llm.txt` | Above + OpenAI/Anthropic clients + ChromaDB memory | Cloud LLM via API |

All NLP-capable tools (31 out of 33) work without an LLM. See [NLP Mode](nlp-mode.md) for the coverage table.

## Changing the Model

Update `model` in `glados_config.yml` to match the model name in your LLM server.
