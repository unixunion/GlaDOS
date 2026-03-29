#!/bin/bash

# First, change to the script's directory
cd "$(dirname "$0")"

echo "Creating Virtual Environment..."
python3 -m venv .venv
source .venv/bin/activate
echo "Installing Dependencies..."

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    python -m spacy download en_core_web_sm
else
    echo "Error: requirements.txt not found in $(pwd)"
    exit 1
fi

echo "Downloading Models..."

# Use simple arrays instead of associative arrays for better compatibility
urls=(
    "https://github.com/dnhkng/GlaDOS/releases/download/0.1/glados.onnx"
    "https://github.com/dnhkng/GlaDOS/releases/download/0.1/nemo-parakeet_tdt_ctc_110m.onnx"
    "https://github.com/dnhkng/GlaDOS/releases/download/0.1/phomenizer_en.onnx"
    "https://github.com/dnhkng/GlaDOS/releases/download/0.1/silero_vad.onnx"
)
files=(
    "models/glados.onnx"
    "models/nemo-parakeet_tdt_ctc_110m.onnx"
    "models/phomenizer_en.onnx"
    "models/silero_vad.onnx"
)

# Check if curl is installed
if ! command -v curl &> /dev/null; then
    echo "curl is not installed. Installing curl..."
    sudo apt-get update
    sudo apt-get install -y curl
fi

# Loop through arrays by index
for i in "${!urls[@]}"; do
    echo "Checking file: ${files[$i]}"
    if [ -f "${files[$i]}" ]; then
        echo "File ${files[$i]} already exists."
    else
        echo "File ${files[$i]} does not exist. Downloading..."
        mkdir -p "$(dirname "${files[$i]}")" # Create the directory if it doesn't exist
        curl -L "${urls[$i]}" --output "${files[$i]}"
        if [ -f "${files[$i]}" ]; then
            echo "Download successful."
        else
            echo "Download failed."
        fi
    fi
done

echo "Installation Complete!"

# Keep the terminal window open to see any errors
echo "Press any key to close..."
read -n 1
