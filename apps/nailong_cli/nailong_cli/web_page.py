# ruff: noqa: E501
"""The page `nailong --serve` shows at `/`, and the OpenAPI description at `/openapi.json`."""

from __future__ import annotations

from typing import Any

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'"
)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nailong</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; margin: 0; background: #fafafa; color: #1a1a1a; }
  main { max-width: 760px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 18px; }
  #log { white-space: pre-wrap; background: #fff; border: 1px solid #ddd; border-radius: 6px;
         padding: 12px; min-height: 240px; }
  .tool { color: #555; }
  form { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  textarea { flex: 1 1 100%; min-height: 72px; }
  input, select, textarea, button { font: inherit; padding: 6px 8px; }
  @media (prefers-color-scheme: dark) {
    body { background: #161616; color: #e6e6e6; }
    #log { background: #1f1f1f; border-color: #333; }
    .tool { color: #aaa; }
  }
</style>
</head>
<body>
<main>
  <h1>nailong</h1>
  <div id="log" aria-live="polite"></div>
  <form id="form">
    <input id="token" type="password" placeholder="server token" autocomplete="off" size="28">
    <select id="mode"><option value="plan">plan (read-only)</option>
      <option value="accept-edits">accept-edits</option></select>
    <textarea id="prompt" placeholder="What should nailong do?"></textarea>
    <button type="submit">Send</button>
  </form>
</main>
<script>
const log = document.getElementById("log");
const token = document.getElementById("token");
token.value = sessionStorage.getItem("nailong-token") || "";
let session = null;
function add(text, cls) {
  const line = document.createElement("span");
  if (cls) line.className = cls;
  line.textContent = text;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}
document.getElementById("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = document.getElementById("prompt").value.trim();
  if (!prompt) return;
  sessionStorage.setItem("nailong-token", token.value);
  add("\\n> " + prompt + "\\n");
  const body = { prompt, mode: document.getElementById("mode").value };
  if (session) body.session = session;
  const response = await fetch("/run?stream=1", {
    method: "POST",
    headers: { "Authorization": "Bearer " + token.value, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) { add("Refused (HTTP " + response.status + "): " + await response.text() + "\\n"); return; }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "", streamed = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = pending.indexOf("\\n\\n")) >= 0) {
      const chunk = pending.slice(0, cut); pending = pending.slice(cut + 2);
      if (!chunk.startsWith("data: ")) continue;
      const item = JSON.parse(chunk.slice(6));
      if (item.type === "session") session = item.id;
      else if (item.type === "text") { streamed = true; add(item.delta); }
      else if (item.type === "tool_use") add("\\n* " + item.tool + " " + JSON.stringify(item.arguments).slice(0, 100) + "\\n", "tool");
      else if (item.type === "tool_result") add("  " + item.status + "\\n", "tool");
      else if (item.type === "result") { if (!streamed && item.answer) add(item.answer); add("\\n[" + item.status + "]\\n", "tool"); }
      else if (item.type === "error" || item.type === "exit") add("\\n" + (item.message || "exit " + item.code) + "\\n");
    }
  }
});
</script>
</body>
</html>
"""


def openapi(version: str) -> dict[str, Any]:
    event = {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {
                "enum": [
                    "session",
                    "text",
                    "reasoning",
                    "tool_use",
                    "tool_result",
                    "result",
                    "error",
                    "exit",
                ]
            }
        },
        "additionalProperties": True,
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "nailong service", "version": version},
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "RunRequest": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 100000},
                        "mode": {
                            "enum": ["plan", "accept-edits", "default"],
                            "default": "plan",
                            "description": "default needs approvals true and ?stream=1.",
                        },
                        "approvals": {
                            "type": "boolean",
                            "description": "Ask the client before each tool that needs approval: "
                            "an approval_request event, answered with POST /approve.",
                        },
                        "session": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9_.-]{1,64}$",
                            "description": "Continue this saved session.",
                        },
                    },
                },
                "RunResult": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "answer": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                        "steps": {"type": "integer"},
                        "cost": {"type": ["object", "null"]},
                        "session": {"type": ["string", "null"]},
                    },
                },
                "Event": event,
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                    "required": ["error"],
                },
            },
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Is the service up (no token needed)",
                    "responses": {"200": {"description": "ok and the version"}},
                }
            },
            "/run": {
                "post": {
                    "summary": "Run one request; add ?stream=1 for server-sent events of Event",
                    "security": [{"bearer": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RunRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "The result, or an event stream",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RunResult"}
                                },
                                "text/event-stream": {
                                    "schema": {"$ref": "#/components/schemas/Event"}
                                },
                            },
                        },
                        "400": {"description": "Bad request, with the reason"},
                        "401": {"description": "Missing or wrong token"},
                        "413": {"description": "Body too large"},
                    },
                }
            },
            "/approve": {
                "post": {
                    "summary": "Answer one approval_request event of a streamed run",
                    "security": [{"bearer": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "allow"],
                                    "properties": {
                                        "id": {"type": "string"},
                                        "allow": {"type": "boolean"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "The run carries on"},
                        "400": {"description": "Body is not {id, allow}"},
                        "404": {"description": "No such approval is waiting (timed out?)"},
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "This description",
                    "responses": {"200": {"description": "OpenAPI 3.1 document"}},
                }
            },
            "/": {
                "get": {
                    "summary": "A small web page for sending requests",
                    "responses": {"200": {"description": "HTML"}},
                }
            },
        },
    }
