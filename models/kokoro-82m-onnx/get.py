import requests

# URLs for the files
urls = {
    "kokoro-v0_19.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}

# Download each file
for filename, url in urls.items():
    print(f"Downloading {filename} from {url}")
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        # Save the file
        with open(filename, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        print(f"Saved {filename}")
    else:
        print(f"Failed to download {filename} (status code: {response.status_code})")




# # /// script
# # requires-python = ">=3.12"
# # dependencies = [
# #     "numpy==2.0.2",
# #     "requests",
# #     "torch==2.5.1",
# # ]
# # ///
# # declaring requests is necessary for running
# """
# Run this file via:
# uv run scripts/fetch_voices.py
# """
#
# import io
# import json
#
# import numpy as np
# import requests
# import torch
#
# # import torch
#
# voices = [
#     "af",
#     "af_bella",
#     "af_nicole",
#     "af_sarah",
#     "af_sky",
#     "am_adam",
#     "am_michael",
#     "bf_emma",
#     "bf_isabella",
#     "bm_george",
#     "bm_lewis",
# ]
# voices_json = {}
# pattern = "https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/voices/{voice}.pt"
# model = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx"
# for voice in voices:
#     url = pattern.format(voice=voice)
#     print(f"Downloading {url}")
#     r = requests.get(url)
#     content = io.BytesIO(r.content)
#     voice_data: np.ndarray = torch.load(content).numpy()
#     voices_json[voice] = voice_data.tolist()
#
# path = "voices.json"
# with open(path, "w") as f:
#     json.dump(voices_json, f, indent=4)
# print(f"Created {path}")
#
