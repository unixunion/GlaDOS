class Glados2:
    def __init__(self,
                 voice_model: str,
                 speaker_id: Optional[int],
                 completion_url: str,
                 model: str,
                 api_key: str | None = None,
                 wake_word: str | None = None,
                 personality_preprompt: Sequence[dict[str, str]] = DEFAULT_PERSONALITY_PREPROMPT,
                 announcement: str | None = None,
                 interruptible: bool = True,
                 config: GladosConfig | None = None) -> None:
        """
        Initializes the VoiceRecognition class, setting up necessary models, streams, and queues.

        This class is not thread-safe, so you should only use it from one thread. It works like this:
        1. The audio stream is continuously listening for input.
        2. The audio is buffered until voice activity is detected. This is to make sure that the
            entire sentence is captured, including before voice activity is detected.
        2. While voice activity is detected, the audio is stored, together with the buffered audio.
        3. When voice activity is not detected after a short time (the PAUSE_LIMIT), the audio is
            transcribed. If voice is detected again during this time, the timer is reset and the
            recording continues.
        4. After the voice stops, the listening stops, and the audio is transcribed.
        5. If a wake word is set, the transcribed text is checked for similarity to the wake word.
        6. The function is called with the transcribed text as the argument.
        7. The audio stream is reset (buffers cleared), and listening continues.

        Args:
            config:
            wake_word (str, optional): The wake word to use for activation. Defaults to None.
        """
        self.completion_url = completion_url
        self.model = model
        self.wake_word = wake_word
        self._vad_model = vad.VAD(model_path=str(Path.cwd() / "models" / VAD_MODEL))
        self._asr_model = asr.AudioTranscriber()
        self._tts = tts.Synthesizer(
            model_path=str(Path.cwd() / "models" / voice_model),
            speaker_id=speaker_id,
        )

        # warm up onnx ASR model
        self._asr_model.transcribe_file("data/0.wav")

        # LLAMA_SERVER_HEADERS
        self.prompt_headers = {
            "Authorization": (
                f"Bearer {api_key}" if api_key else "Bearer your_api_key_here"
            ),
            "Content-Type": "application/json",
        }

        # Initialize sample queues and state flags
        self._samples: List[np.ndarray] = []
        self._sample_queue: queue.Queue[Tuple[np.ndarray, np.ndarray]] = queue.Queue()
        self._buffer: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=BUFFER_SIZE // VAD_SIZE
        )
        self._recording_started = False
        self._gap_counter = 0


        # initialize the plugin architecture
        self.plugin_manager = PluginManager()
        load_plugins("plugins")
        self.confidence_threshold = config.intent_confidence_threshold


        # llm client abstraction
        self.llm_client = LLMClient(
            url=completion_url,
            model=model,
            headers=self.prompt_headers,
            config=config
        )

        # asyncio.run(self.llm_client.chat("What is three minus one?"))

        # intent classification setup
        # self.intent_classifier = IntentClassifier(config.intents)

        # logger.info("Running pre-initialization for plugins...")
        # self.plugin_manager.pre_initialize_plugins({
        #     "llm_client": self.llm_client,
        #     "ingredient_set": set(),
        # })

        # personality configuration
        logger.success(f"Preprompt: {personality_preprompt}")
        self._messages = personality_preprompt

        self.llm_queue: queue.Queue[str] = queue.Queue()
        self.tts_queue: queue.Queue[str] = queue.Queue()
        self.processing = False
        self.currently_speaking = False
        self.interruptible = interruptible
        self.shutdown_event = threading.Event()

        # llm_thread = threading.Thread(target=self.process_LLM)
        # llm_thread.start()
        #
        # tts_thread = threading.Thread(target=self.process_TTS_thread)
        # tts_thread.start()

        if announcement:
            audio = self._tts.generate_speech_audio(announcement)
            logger.success(f"TTS text: {announcement}")
            sd.play(audio, self._tts.rate)
            if not self.interruptible:
                sd.wait()

        def audio_callback(
                indata: np.ndarray, frames: int, time: Any, status: CallbackFlags
        ):
            data = indata.copy().squeeze()  # Reduce to single channel if necessary
            vad_value = self._vad_model.process_chunk(data)
            vad_confidence = vad_value > VAD_THRESHOLD
            asyncio.run_coroutine_threadsafe(
                self._sample_queue.put((data, vad_confidence)),
                asyncio.get_event_loop(),
            )

        self._sample_queue = asyncio.Queue()
        self.input_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            callback=audio_callback,
            blocksize=int(SAMPLE_RATE * VAD_SIZE / 1000),
        )


    @property
    def messages(self) -> Sequence[dict[str, str]]:
        return self._messages

    @classmethod
    def from_config(cls, config: GladosConfig):
        personality_preprompt = []
        for line in config.personality_preprompt:
            personality_preprompt.append(
                {"role": list(line.keys())[0], "content": list(line.values())[0]}
            )

        return cls(
            voice_model=config.voice_model,
            speaker_id=config.speaker_id,
            completion_url=config.completion_url,
            model=config.model,
            api_key=config.api_key,
            wake_word=config.wake_word,
            personality_preprompt=personality_preprompt,
            announcement=config.announcement,
            interruptible=config.interruptible,
            config=config
        )

    @classmethod
    def from_yaml(cls, path: str):
        return cls.from_config(GladosConfig.from_yaml(path))

    # def start_listen_event_loop(self):
    #     """
    #     Starts the Glados voice assistant, continuously listening for input and responding.
    #     """
    #     self.input_stream.start()
    #     logger.success("Audio Modules Operational")
    #     logger.success("Listening...")
    #     # Loop forever, but is 'paused' when new samples are not available
    #     try:
    #         while True:
    #             sample, vad_confidence = self._sample_queue.get()
    #             self._handle_audio_sample(sample, vad_confidence)
    #     except KeyboardInterrupt:
    #         self.shutdown_event.set()
    #         self.input_stream.stop()

    async def start(self):
        """
        Starts the Glados assistant.
        """
        logger.success("Starting Glados...")
        self.input_stream.start()
        await asyncio.gather(
            self.process_llm(),
            self.process_tts(),
            self.listen_for_audio(),
        )

    async def listen_for_audio(self):
        """
        Continuously listens for audio input and processes it.
        """
        while not self.shutdown_event.is_set():
            try:
                sample, vad_confidence = await self._sample_queue.get()
                if vad_confidence:
                    logger.info("Voice activity detected.")
                    # Process voice activity...
            except Exception as e:
                logger.error(f"Error in listen_for_audio: {e}")

    def _handle_audio_sample(self, sample: np.ndarray, vad_confidence: bool):
        """
        Handles the processing of each audio sample.

        If the recording has not started, the sample is added to the circular buffer.

        If the recording has started, the sample is added to the samples list, and the pause
        limit is checked to determine when to process the detected audio.

        Args:
            sample (np.ndarray): The audio sample to process.
            vad_confidence (bool): Whether voice activity is detected in the sample.
        """
        if not self._recording_started:
            self._manage_pre_activation_buffer(sample, vad_confidence)
        else:
            self._process_activated_audio(sample, vad_confidence)

    def _manage_pre_activation_buffer(self, sample: np.ndarray, vad_confidence: bool):
        """
        Manages the circular buffer of audio samples before activation (i.e., before the voice is detected).

        If the buffer is full, the oldest sample is discarded to make room for new ones.

        If voice activity is detected, the audio stream is stopped, and the processing is turned off
        to prevent overlap with the LLM and TTS threads.

        Args:
            sample (np.ndarray): The audio sample to process.
            vad_confidence (bool): Whether voice activity is detected in the sample.
        """
        if self._buffer.full():
            self._buffer.get()  # Discard the oldest sample to make room for new ones
        self._buffer.put(sample)

        if vad_confidence:  # Voice activity detected
            sd.stop()  # Stop the audio stream to prevent overlap
            self.processing = (
                False  # Turns off processing on threads for the LLM and TTS!!!
            )
            self._samples = list(self._buffer.queue)
            self._recording_started = True

    def _process_activated_audio(self, sample: np.ndarray, vad_confidence: bool):
        """
        Processes audio samples after activation (i.e., after the wake word is detected).

        Uses a pause limit to determine when to process the detected audio. This is to
        ensure that the entire sentence is captured before processing, including slight gaps.
        """

        self._samples.append(sample)

        if not vad_confidence:
            self._gap_counter += 1
            if self._gap_counter >= PAUSE_LIMIT // VAD_SIZE:
                self._process_detected_audio()
        else:
            self._gap_counter = 0

    def _wakeword_detected(self, text: str) -> bool:
        """
        Calculates the nearest Levenshtein distance from the detected text to the wake word.

        This is used as 'Glados' is not a common word, and Whisper can sometimes mishear it.
        """
        assert self.wake_word is not None, "Wake word should not be None"

        words = text.split()
        closest_distance = min(
            [distance(word.lower(), self.wake_word) for word in words]
        )
        return closest_distance < SIMILARITY_THRESHOLD

    def _process_detected_audio(self):
        """
        Processes the detected audio and generates a response.

        This function is called when the pause limit is reached after the voice stops.
        It transcribes the audio and checks for the wake word if it is set. If the wake
        word is detected, the detected text is sent to the LLM model for processing.
        The audio stream is then reset, and listening continues.
        """
        logger.debug("Detected pause after speech. Processing...")
        self.input_stream.stop()

        detected_text = self.asr(self._samples)

        if detected_text:

            logger.success(f"ASR text: '{detected_text}'")
            # intent_data = self.detect_intent(detected_text)
            # logger.success(f"Intent responded with {intent_data}")

            if self.wake_word and not self._wakeword_detected(detected_text):
                logger.info(f"Required wake word {self.wake_word=} not detected.")
            else:
                # if (intent_data):
                #     self.llm_queue.put(f"{detected_text}{intent_data}")
                # else:
                self.llm_client.llm_queue.put(f"{detected_text}")
                # self.llm_queue.put(f"{detected_text}")
                self.processing = True
                self.currently_speaking = True

        if not self.interruptible:
            while self.currently_speaking:
                time.sleep(PAUSE_TIME)

        self.reset()
        self.input_stream.start()

    def asr(self, samples: List[np.ndarray]) -> str:
        """
        Performs automatic speech recognition on the collected samples.
        """
        audio = np.concatenate(samples)

        detected_text = self._asr_model.transcribe(audio)
        return detected_text

    def reset(self):
        """
        Resets the recording state and clears buffers.
        """
        logger.debug("Resetting recorder...")
        self._recording_started = False
        self._samples.clear()
        self._gap_counter = 0
        with self._buffer.mutex:
            self._buffer.queue.clear()

    def replace_numbers_with_words(self, text):
        # Define a function to convert a number to its word equivalent
        def number_to_words(match):
            number = int(match.group())
            return num2words(number)

        # Use regex to find all numbers in the string and replace them with their word equivalents
        result = re.sub(r'\b\d+\b', number_to_words, text)
        return result

    def _filter_special_tags(self, text: str) -> str:
        """
        Filters out special tags or metadata from the text.

        Args:
            text (str): The text to filter.

        Returns:
            str: The filtered text.
        """
        # Match patterns for tags and metadata (e.g., _INTERNAL_, START_TOKEN_, etc.)
        # Remove entire blocks like "_INTERNAL_PLUGIN_[...]_" or specific tokens
        filtered_text = re.sub(
            r"(_[A-Z_]+_[A-Z_]+(?:\[[^\]]*\])?)|START_TOKEN_[A-Z_]+|END_TOKEN_[A-Z_]+",
            "",
            text
        )
        # Remove extra whitespace caused by tag removal
        filtered_text = re.sub(r"\s{2,}", " ", filtered_text).strip()
        return filtered_text



    # def process_TTS_thread(self):
    #     """
    #     Processes the LLM-generated text using the TTS model, dynamically invokes plugins,
    #     and incorporates their results into the conversation context for further processing.
    #     """
    #     assistant_text = []  # The assistant's spoken text
    #     full_context = []  # Full context, including plugin results, for conversational memory
    #     finished = False
    #     interrupted = False
    #
    #     while not self.shutdown_event.is_set():
    #         try:
    #             start = time.time()
    #             generated_text = self.llm_client.tts_queue.get(timeout=PAUSE_TIME)
    #
    #             if generated_text == "<EOS>":  # End of stream signal
    #                 finished = True
    #             elif not generated_text:
    #                 logger.warning("Empty string sent to TTS")  # Should not happen!
    #             else:
    #                 logger.success(f"LLM text (unfiltered): {generated_text}")
    #
    #                 # Filter tags for user-friendly TTS output
    #                 filtered_text = self._filter_special_tags(generated_text)
    #                 logger.success(f"LLM text (filtered): {filtered_text}")
    #
    #                 if filtered_text:
    #                     # Generate TTS audio for the filtered text
    #                     audio = self._tts.generate_speech_audio(filtered_text)
    #                     logger.info(
    #                         f"TTS Complete, length: {len(audio) / self._tts.rate:.2f}s"
    #                     )
    #                     total_samples = len(audio)
    #
    #                     if total_samples:
    #                         sd.play(audio, self._tts.rate)
    #
    #                         interrupted, percentage_played = self.percentage_played(total_samples)
    #
    #                         if interrupted:
    #                             clipped_text = self.clip_interrupted_sentence(filtered_text, percentage_played)
    #                             logger.info(f"TTS interrupted at {percentage_played}%: {clipped_text}")
    #
    #                             assistant_text.append(clipped_text)
    #                         else:
    #                             assistant_text.append(filtered_text)
    #
    #                     # Always store the full conversational memory (including tags or metadata)
    #                     full_context.append(generated_text)
    #
    #             if finished:
    #                 # Add the full context to memory
    #                 self.messages.append({"role": "assistant", "content": " ".join(full_context)})
    #                 logger.success(f"context length: {len(self.messages)}")
    #
    #                 # Reset variables for the next interaction
    #                 assistant_text = []
    #                 full_context = []
    #                 finished = False
    #                 interrupted = False
    #                 self.currently_speaking = False
    #
    #         except queue.Empty:
    #             pass

    # def process_TTS_thread(self):
    #     """
    #     Processes the LLM-generated text using the TTS model and plays the audio.
    #     """
    #     while not self.shutdown_event.is_set():
    #         try:
    #             # Retrieve text from the TTS queue
    #             generated_text = self.tts_queue.get(timeout=PAUSE_TIME)
    #
    #             if generated_text == "<EOS>":  # End of stream signal
    #                 logger.info("End of TTS stream.")
    #                 continue
    #
    #             if not generated_text:
    #                 logger.warning("Empty string sent to TTS.")
    #                 continue
    #
    #             logger.info(f"Processing TTS for text: {generated_text}")
    #
    #             # Generate and play TTS audio
    #             # filtered_text = self._filter_special_tags(generated_text)
    #             # logger.info(f"Filtered TTS text: {filtered_text}")
    #
    #             audio = self._tts.generate_speech_audio(generated_text)
    #             sd.play(audio, self._tts.rate)
    #             sd.wait()
    #
    #         except queue.Empty:
    #             continue
    #         except Exception as e:
    #             logger.error(f"Error in process_TTS_thread: {e}")

    def clip_interrupted_sentence(
            self, generated_text: str, percentage_played: float
    ) -> str:
        """
        Clips the generated text if the TTS was interrupted.

        Args:

            generated_text (str): The generated text from the LLM model.
            percentage_played (float): The percentage of the audio played before the TTS was interrupted.

            Returns:

            str: The clipped text.

        """
        tokens = generated_text.split()
        words_to_print = round((percentage_played / 100) * len(tokens))
        text = " ".join(tokens[:words_to_print])

        # If the TTS was cut off, make that clear
        if words_to_print < len(tokens):
            text = text + "<INTERRUPTED>"
        return text

    def percentage_played(self, total_samples: int) -> Tuple[bool, int]:
        interrupted = False
        start_time = time.time()
        played_samples = 0.0

        try:
            stream = sd.get_stream()

            while stream and stream.active:
                time.sleep(PAUSE_TIME)
                if self.processing is False:
                    sd.stop()
                    with self.llm_client.tts_queue.mutex:
                        self.llm_client.tts_queue.queue.clear()
                    interrupted = True
                    break

                try:
                    if not stream.active:
                        break
                except sd.PortAudioError:
                    break

        except (sd.PortAudioError, RuntimeError):
            logger.debug("Audio stream already closed or invalid")

        elapsed_time = (
                time.time() - start_time + 0.12
        )  # slight delay to ensure all audio timing is correct
        played_samples = elapsed_time * self._tts.rate

        # Calculate percentage of audio played
        percentage_played = min(int((played_samples / total_samples * 100)), 100)
        return interrupted, percentage_played

    # def process_LLM(self):
    #     """
    #     Processes the detected text using the LLM model.
    #     """
    #     while not self.shutdown_event.is_set():
    #         try:
    #             detected_text = self.llm_client.llm_queue.get(timeout=0.1)
    #             asyncio.run(self.llm_client.chat(detected_text))
    #
    #             # self.messages.append({"role": "user", "content": detected_text})
    #             #
    #             data = {
    #                 "model": self.model,
    #                 "stream": True,
    #                 "messages": self.messages,
    #             }
    #             # logger.debug(f"starting request on {self.messages=}")
    #             # logger.debug("Performing request to LLM server...")
    #
    #             # Perform the request and process the stream
    #
    #             with requests.post(
    #                     self.completion_url,
    #                     headers=self.prompt_headers,
    #                     json=data,
    #                     stream=True,
    #             ) as response:
    #                 sentence = []
    #                 for line in response.iter_lines():
    #                     if self.processing is False:
    #                         break  # If the stop flag is set from new voice input, halt processing
    #                     if line:  # Filter out empty keep-alive new lines
    #                         try:
    #                             cleaned_line = self._clean_raw_bytes(line)
    #                             if cleaned_line:  # Add check for empty cleaned line
    #                                 chunk = self._process_chunk(cleaned_line)
    #                                 if chunk:
    #                                     sentence.append(chunk)
    #                                     # If there is a pause token, send the sentence to the TTS queue
    #                                     if chunk in [
    #                                         ",",
    #                                         ".",
    #                                         "!",
    #                                         "?",
    #                                         ":",
    #                                         ";",
    #                                         "?!",
    #                                         "\n",
    #                                         "\n\n",
    #                                     ]:
    #                                         self._process_sentence(sentence)
    #                                         sentence = []
    #                         except Exception as e:
    #                             logger.error(f"Error processing line: {e}")
    #                             continue
    #
    #                 if self.processing and sentence:
    #                     self._process_sentence(sentence)
    #                 self.llm_client.tts_queue.put("<EOS>")  # Add end of stream token to the queue
    #         except queue.Empty:
    #             time.sleep(PAUSE_TIME)

    async def process_llm(self):
        """
        Processes text detected from ASR using the LLM.
        """
        while not self.shutdown_event.is_set():
            try:
                detected_text = await self.llm_queue.get()
                logger.info(f"Processing LLM input: {detected_text}")
                response = await self.handle_llm_response(detected_text)
                if response:
                    await self.tts_queue.put(response)
            except Exception as e:
                logger.error(f"Error in process_llm: {e}")

    async def process_tts(self):
        """
        Processes LLM responses and plays audio using TTS.
        """
        while not self.shutdown_event.is_set():
            try:
                generated_text = await self.tts_queue.get()
                if generated_text == "<EOS>":
                    logger.info("End of TTS stream.")
                    continue
                logger.info(f"Processing TTS for text: {generated_text}")
                audio = self._tts.generate_speech_audio(generated_text)
                sd.play(audio, self._tts.rate)
                await asyncio.sleep(len(audio) / self._tts.rate)
            except Exception as e:
                logger.error(f"Error in process_tts: {e}")

    async def handle_llm_response(self, detected_text: str) -> str:
        """
        Handles LLM responses asynchronously.
        """
        logger.info(f"Handling LLM response for text: {detected_text}")
        try:
            response = await self.llm_client.chat(detected_text)
            logger.info(f"LLM response: {response}")
            return response
        except Exception as e:
            logger.error(f"Error in LLM response handling: {e}")
            return ""

    def _process_sentence(self, current_sentence: List[str]):
        """
        Join text, remove inflections and actions, and send to the TTS queue.

        The LLM like to *whisper* things or (scream) things, and prompting is not a 100% fix.
        We use regular expressions to remove text between ** and () to clean up the text.
        Finally, we remove any non-alphanumeric characters/punctuation and send the text
        to the TTS queue.
        """
        sentence = "".join(current_sentence)
        sentence = re.sub(r"\*.*?\*|\(.*?\)", "", sentence)
        sentence = (
            sentence.replace("\n\n", ". ")
            .replace("\n", ". ")
            .replace("  ", " ")
            .replace(":", " ")
        )
        if sentence:
            self.llm_client.tts_queue.put(sentence)

    def _clean_raw_bytes(self, line):
        """
        Cleans the raw bytes from the server and converts to OpenAI format.

        Args:
            line (bytes): The raw bytes from the server

        Returns:
            dict or None: Parsed JSON response in OpenAI format, or None if parsing fails
        """
        try:
            # Handle OpenAI format
            if line.startswith(b"data: "):
                json_str = line.decode("utf-8")[6:]  # Remove 'data: ' prefix
                return json.loads(json_str)
            # Handle Ollama format
            else:
                return json.loads(line.decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to parse server response: {e}")
            return None

    def _process_chunk(self, line):
        """
        Processes a single line of text from the LLM server.

        Args:
            line (dict): The line of text from the LLM server.
        """
        if not line or not isinstance(line, dict):
            return None

        try:
            # Handle OpenAI format
            if "choices" in line:
                content = line.get("choices", [{}])[0].get("delta", {}).get("content")
                return content if content else None
            # Handle Ollama format
            else:
                content = line.get("message", {}).get("content")
                return content if content else None
        except Exception as e:
            logger.error(f"Error processing chunk: {e}")
            return None