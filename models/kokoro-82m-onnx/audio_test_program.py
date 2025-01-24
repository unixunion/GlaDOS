import asyncio
import numpy as np
import sounddevice as sd
from scipy.signal import resample
from kokoro_onnx import Kokoro
import adaptfilt as adf

# Global Parameters
BUFFER_SIZE = 44100 * 10  # 10 seconds of audio at 44.1 kHz
SAMPLE_RATE = 44100
LATENCY_COMPENSATION = 0.1  # Adjust for device latency (in seconds)


# Ring Buffer Class
class RingBuffer:
    def __init__(self, size):
        self.size = size
        self.buffer = np.zeros(size, dtype=np.float32)
        self.index = 0

    def add(self, data):
        length = len(data)
        if length > self.size:
            data = data[-self.size:]  # Keep only the last `size` samples
            length = self.size
        end_index = (self.index + length) % self.size
        if end_index < self.index:
            self.buffer[self.index:] = data[:self.size - self.index]
            self.buffer[:end_index] = data[self.size - self.index:]
        else:
            self.buffer[self.index:end_index] = data
        self.index = end_index

    def get(self):
        """Get the entire buffer."""
        return self.buffer

    def get_segment(self, start, length):
        """Get a segment of the buffer."""
        end = (start + length) % self.size
        if end < start:
            return np.concatenate((self.buffer[start:], self.buffer[:end]))
        return self.buffer[start:end]

    def clear(self):
        """Clear the buffer."""
        self.buffer = np.zeros(self.size, dtype=np.float32)
        self.index = 0


def resample_audio(audio, original_rate, target_rate):
    """Resample audio to match the desired sample rate."""
    if original_rate != target_rate:
        num_samples = int(len(audio) * target_rate / original_rate)
        audio = resample(audio, num_samples)
    return audio


def play_audio(audio, playback_buffer):
    """Play audio and add it to the playback buffer."""
    playback_buffer.add(audio)  # Add audio to playback buffer

    # Ensure audio duration matches expected playback time
    duration = len(audio) / SAMPLE_RATE
    print(f"Playing {len(audio)} samples at {SAMPLE_RATE} Hz, duration: {duration:.2f} seconds.")

    sd.play(audio, samplerate=SAMPLE_RATE)
    sd.wait()  # Wait for playback to complete


def record_audio_with_nlms(recording_buffer, playback_buffer, filter_order=100, step_size=0.01):
    """Perform real-time adaptive filtering to cancel playback signal."""
    coeffs = np.zeros(filter_order)  # Initialize filter coefficients

    def audio_callback(indata, frames, time, status):
        nonlocal coeffs

        # Get recording (microphone input) and playback signals
        recording = indata[:, 0]
        playback = playback_buffer.get_segment(0, frames)  # Get playback segment

        # Apply NLMS adaptive filtering
        _, e, coeffs = adf.nlms(playback, recording, filter_order, step_size)

        # Add the error signal (filtered output) to the recording buffer
        recording_buffer.add(e)

    return sd.InputStream(callback=audio_callback, channels=1, samplerate=SAMPLE_RATE, dtype="float32")


def extract_recent_audio(buffer, duration, sample_rate):
    """Extract the most recent audio data from the buffer."""
    num_samples = int(duration * sample_rate)
    return buffer[-num_samples:]


async def main():
    # Initialize buffers
    playback_buffer = RingBuffer(BUFFER_SIZE)
    recording_buffer = RingBuffer(BUFFER_SIZE)

    # Load Kokoro TTS model
    tts_model_path = "kokoro-v0_19.onnx"
    voices_path = "voices.json"
    kokoro = Kokoro(tts_model_path, voices_path)

    # Start adaptive filtering for real-time recording
    print("Starting real-time audio processing...")
    with record_audio_with_nlms(recording_buffer, playback_buffer) as stream:
        while True:
            # Input text for TTS
            text = input("Enter text to speak (or 'exit' to quit): ")
            if text.lower() == "exit":
                break

            # Generate TTS output
            stream = kokoro.create_stream(
                text, voice="bf_isabella", speed=1.0, lang="en-us"
            )

            async for samples, sample_rate in stream:
                print(f"TTS Output: {len(samples)} samples at {sample_rate} Hz")

                # Resample audio if sample_rate != SAMPLE_RATE
                samples = resample_audio(samples, sample_rate, SAMPLE_RATE)

                # Play TTS audio and send to playback buffer
                play_audio(samples, playback_buffer)

            # Retrieve filtered audio from the recording buffer
            filtered_audio = extract_recent_audio(recording_buffer.get(), duration=1.0, sample_rate=SAMPLE_RATE)
            filtered_audio = filtered_audio / (np.max(np.abs(filtered_audio)) + 1e-6)
            filtered_audio = resample_audio(filtered_audio, original_rate=SAMPLE_RATE, target_rate=SAMPLE_RATE)
            sd.play(filtered_audio, samplerate=SAMPLE_RATE)
            sd.wait()

            # (Optional) Send filtered audio to your STT system
            print("Filtered audio ready for STT.")


if __name__ == "__main__":
    asyncio.run(main())
