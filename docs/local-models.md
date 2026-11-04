# Running on a model you host yourself

For work that must not leave your network, point the SDK or the CLI at an OpenAI-compatible server
you run: vLLM, SGLang, llama.cpp's `llama-server`, Ollama or LM Studio. Nothing in the SDK calls a
public service by itself. The only outbound connections are the ones you configure: the model
endpoint, and, if you turn them on, the OTLP exporter, a recorder, web fetch or search, and MCP
servers that use a URL.

**Status.** Tested against local fake servers that behave like these (no key, plain HTTP,
streaming, reasoning blocks in the reply). **Not yet run against a real local model**: no runtime
or GPU was available where this was written. Try your model with a one-line task first.

## What your server must offer

- `POST /v1/chat/completions`, streaming optional.
- **Tool calling is preferred, not required.** By default the agent's tools are sent as function
  tools. vLLM needs `--enable-auto-tool-choice` and a `--tool-call-parser` for the model; llama.cpp
  needs `--jinja`; Ollama needs a model with tool support. If the server rejects tools, the SDK
  switches to **prompted tools**: the tools are described in the prompt, the model replies with a
  JSON tool call as text, and the results come back as ordinary messages (see below).
- A context window large enough for the instructions and tool list (several thousand tokens).

## SDK

```python
from nailong_agent_sdk.starter import Agent, openai_chat

model = openai_chat(
    base_url="http://gpu-box:8000/v1",  # or the shortcuts "vllm", "ollama", "lmstudio", "llamacpp"
    api_key=None,                       # no Authorization header is sent
    model="your-served-model-name",
    allow_insecure_http=True,           # plain HTTP is refused unless you say so
    timeout_seconds=300,                # local models are slower
)
answer = Agent(model=model, instructions="Answer briefly.").run_sync("Hello")
```

A reply that is not valid JSON becomes the answer as written, a JSON object missing the `type`
wrapper is wrapped, and a `<think>...</think>` block (DeepSeek-style reasoning) is removed before
either. A reply the adapter cannot use at all (empty, malformed tool arguments) is shown to the
model, which is asked again up to twice in a row before the run fails with the count. Reasoning
models that return their thinking in a separate `reasoning_content` field need no handling: that
field is ignored.

## Models without tool calling

`tool_mode` on `openai_chat` (and in the CLI settings) is `auto` by default:

| Value | Behaviour |
|---|---|
| `auto` | send tools natively; if the server answers that tools (or JSON mode) are not supported, switch to prompted tools for the rest of the session and repeat the request |
| `native` | always send tools natively; a server that rejects them is an error |
| `prompted` | never send tools natively: describe them in the prompt and read the tool call from the reply |

In prompted mode the reply may be a bare JSON object, a JSON object after some prose, or one inside
a code fence; arguments written as a string are decoded; call ids are replaced with unique ones.
A reply with unusable arguments goes back to the model with the reason (the retry described
above). Auto mode also drops JSON mode (`response_format`) when the server does not know it.

It is less reliable than native tool calling. A live check on `minimax/minimax-01`, which lists no
tool support, read a file and answered correctly in two steps, but on a three-part task it
searched, summarised and stopped before the file it was asked to write. Smaller models skip steps
and emit malformed calls more often. Native tool calling is worth choosing a model for.

## CLI

```toml
# ~/.config/nailong/config.toml
provider = "openai-compatible"
model = "your-served-model-name"
base_url = "http://gpu-box:8000/v1"   # or vllm | ollama | lmstudio | llamacpp
allow_insecure_http = true            # needed for plain HTTP on anything but loopback
timeout_seconds = 300
offline = true                        # see below
# api_key_env = "GPU_KEY"             # only if the server wants a key; leave out otherwise
```

`offline = true` (or `--offline`) makes the CLI refuse to start when any setting would reach beyond
your network: a `base_url` that is not loopback, a private address, a single-label name or
`.local`/`.internal`/`.lan`; `web` or `web_search`; an MCP server with a public URL. The error
names each setting. A project file cannot switch it off: `offline` is read only from your user
file or the flag. This is a configuration check, not a firewall: it cannot see what a command the
agent runs does, so also run commands in a sandbox (`sandbox = "docker"` with no network, bubblewrap
or Seatbelt) and block egress at the network level for strict zones.

## Choosing a model

The agent loop asks the model for tool calls and, when finished, a short JSON reply. Larger
instruction-tuned and reasoning models handle this reliably; very small ones often emit malformed
tool arguments or empty replies, which the retry absorbs only up to two times. If runs fail with
"unusable reply … times in a row", raise `invalid_output_retries`, use a bigger model, or lower the
sampling temperature with `[parameters] temperature = 0.2`.
