# GlaDOS, a maniacal home assistant

This fork introduces a pluggable architecture, with function calling support to for GLaDOS.
WARNING! GLaDOS is maniacal, and ultimately evil, so be careful in connecting her to any real world stuff. 
you have been warned! Although you should be ok as long as you use a "safe" llm, and a copy of the laws of robotics.

This is under development right now, and lots of stuff is in a state of flux.


## Architecture

This is pretty much a total re-write of the upstream project, using a more modular approach. Features:

* Pre-prompted with the 3 laws of robotics
* Plugin support, and long-running processes instantiation of classes base: `RunnablePlugin`
* Functions, GladOS can now interact with stuff ( using llama 3.1 functions (read more)[https://docs.together.ai/docs/function-calling] )
  The functions can take arguments, enums and then do whatever you integrate them with. WARNING! Remember the bitch is evil!
  These functions register as a either standalone or as a part of plugin instances.
* Intents, to help guide the AI to select a function/tool, the plugins have "intent" strings that are used to help select the relevant tool.
* Event system, plugins, functions and parts of the architecture all use events now to talk each other and the LLM.
* Vision support, yes, its probably a bad idea, but GlaDOS can see! well its very basic POC, images can be base64 encoded and passed to a 
  vision model, which in turn responds to the chat model. And GlaDOS can trigger functions automatically then, like
  start vacuuming if there is a floor spill, or start fire supression if there is a fire, or she may just watch you burn.
  I'm using a separate host to run the vision model, and calling it over the network. A camera system needs to be implemented
  to get images from CCTV or similar.
* Migrated to Whisper for speech to text
* Wake-word "Glad-os", or "Gladys" detection via porcupine ( just register, and put your set ACCESS_TOKEN in PORCUPINE_ACCESS_TOKEN env var)

## Functions

### Timers

- "start a timer for 1 minute" - starts a timer, and fires an event when done.
- "set an alarm for 5 o clock" - not working at moment, in development

### Recipes

Get a recipe csv [recipes dataset](https://www.kaggle.com/datasets/wilmerarltstrmberg/recipe-dataset-over-2m) and plate it in `plugin_data/recipes/dataset.csv`, 
then you can use it e.g: `select a recipe for x` - selects a recipe to make or `search for a recipe for y` to get a list
of options after which you will use the _select recipe x_ statement to make it. 

## Cuda Torch, you need to install the cuda version of torch, e.g:

   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
   pip install safetensors

## Wakeword

Using porcupine, and events, GlaDOS will respond to "glados/gladys". Todo add automatic follow up for response after interactions
so the wakeword is not required for each engagement.

## Plugins

Plugins can be defined either as functions or entire running classes, this is a complete example that instantiates, and
has functions the LLM can call.

```python
class MyRunnablePlugin(RunnablePlugin):
    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self._worker_thread = None

        # register a llm function we can call from the llm
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                function=FunctionMetadata(
                    description="Hello World, greets the responder by name if known, usage example: 'hello world, "
                                "my name is kegan'",
                    parameters=Parameters(type="object", required=['name'], properties={
                        'name': ParameterType(type="string", description="name to acknowledge")
                    })
                )),
            intents=[
               "invoke the hello world function",
               "hello world, my name is joe",
               "run the hello world plugin"
            ],
            process_output=True  # process output via llm model inference,
        )(self.hello_world)

    def start(self):
        logger.info("Starting...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def ticker():
            while not self._stop_event.is_set():
                time.sleep(10)
                logger.info("tick")
                self.send_events()

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=ticker, daemon=True)
        self._worker_thread.start()
        logger.success("started")

    def stop(self):
        logger.info("Shutting down")
        self._stop_event.set()

    def hello_world(self, name: str):
        logger.info(f"hello world: {name}")
        return {
            "status": "success",
            "content": f"hello world, name passed in was {name}"
        }

    def send_events(self):
        event_system.publish(
            EventMessage(
                role="tool",
                name="hello_world",
                content={
                    "message": "Hello from a plugin! this is a self-test of the plug-in system."
                },
                process_output=True
            )
        )
        self._stop_event.set()
```

## Event System

The EventSystem can be hooked into based on topics, e.g "role.name" = topic, eg:

system.tick: simple clock tick
tools.*: the tool outputs

### Subscribing to events
```python
event_system = EventSystem()
event_system.subscribe(
   "system.listen_for_response",
   EventHook(name="listen_for_response", callback=self.listen_for_response, priority=1)
)

def listen_for_response(self, event: EventMessage):
  pass
```

### Publishing events
```python
event_system.publish(
   EventMessage(
       role="tool",
       name="hello_world",
       content={
           "message": "Hello from a plugin! this is a self-test of the plug-in system."
       },
       process_output=True  # tells the LLM to parse this payload immediately
   )
)
```

# Installation Instruction
Try this simplified process, but be aware it's still in the experimental stage!  For all operating systems, you'll first 
need to install Ollama to run the LLM.

## Models

The assistant uses the OpenAI python client, which I use with ollama hosted models locally, you can probably use online 
OpenAI client compatible services, but I have not tested it. 

### The Chat Model

The main chat model I use is a 8B chat model, e.g: `ollama pull llama3.1`

### The Vision Model

Testing the vision model can be done by running the model on a separate host, but be aware, this is just a POC that can
look at a directory of images and describe them, and feed that back to the chat model. 

`ollama pull hf.co/second-state/Llava-v1.5-7B-GGUF:latest`

You need to set `OLLAMA_HOST` environment variable on the vision model host to the IP of the host, NOT `0.0.0.0`, e.g: 
`OLLAMA_HOST=10.0.0.2`

## Install Drivers in necessary
If you are an Nvidia system with CUDA, make sure you install the necessary drivers and CUDA, info here:
https://onnxruntime.ai/docs/install/

If you are using another accelerator (ROCm, DirectML etc.), after following the instructions below for you platform, 
follow up with installing the  [best onnxruntime version](https://onnxruntime.ai/docs/install/) for your system.

## Set up a local LLM server:
1. Download and install [Ollama](https://github.com/ollama/ollama) for your operating system.
2. Once installed, download a small 2B model for testing, at a terminal or command prompt use: `ollama pull llama3.2`
3. The vision model used on a separate hose is `ollama pull hf.co/second-state/Llava-v1.5-7B-GGUF:latest`

Note: You can use any OpenAI or Ollama compatible server, local or cloud based. Just edit the glados_config.yaml and 
update the completion_url, model and the api_key if necessary.

## Windows Installation Process
1. Open the Microsoft Store, search for `python` and install Python 3.12
2. Download this repository, either:
   1. Download and unzip this repository somewhere in your home folder, or
   2. If you have Git set up, `git clone` this repository using `git clone github.com/unixunion/glados.git`
3. In the repository folder, run the `install_windows.bat`, and wait until the installation in complete.
4. Double click `start_windows.bat` to start GLaDOS!

## macOS Installation Process
Untested

## Linux Installation Process
Untested

1. Install the PortAudio library, if you don't yet have it installed:
   
         sudo apt update
         sudo apt install libportaudio2
   
2. Download this repository, either:
   1. Download and unzip this repository somewhere in your home folder, or
   2. In a terminal, `git clone` this repository using `git clone github.com/dnhkng/glados.git`
3. In a terminal, go to the repository folder and run these commands:
   
         chmod +x install_ubuntu.sh
         chmod +x start_ubuntu.sh

4. In the a terminal in the GLaODS folder, run `./install_ubuntu.sh`, and wait until the installation in complete.
5. Run  `./start_ubuntu.sh` to start GLaDOS!

## Changing the LLM Model

To use other models, use the command:
```ollama pull {modelname}```
and then add {modelname} to glados_config.yaml as the model. You can find [more models here!](https://ollama.com/library)

## Common Issues
New architecture, no idea what gremlins there are.

if you see lots of TTS like this instead of calling functions, it is related to too many plugins in the context or the system
preprompt is doing something funky with the json internals. 
`Generating TTS for: {"type" "function","name" "get camera feed","parameters{"query" "","room" ""}}`

# Todo

- plugin that can list system events, tool outputs, statuses and errors
- plugin that can serve a HTML page letting you turn any browser device into a camera, such as old phones, ipads, or laptops. 
- camera support for pan tilt zoom
- schedule plugin that can set and retrieve a schedule for activities. 
- A way to send textual data to client device, such as a ipad or laptop or phone for textual input / tweaking 
- context manager, perhaps switch contexts based on invoked tools, or collect tools in contexts of max 20 tools per context. 
  also a API to search contexts for a relevant thing, so the AI can figure out which  context to switch to. 
  ENTERTAINMENT
  CHORES
  GENERAL_ENQUIRY
  COOKING
  ??? 