import http.server
import json
import threading
from decimal import Decimal

import pytest

from nailong_agent_sdk.foundations.contracts import (
    EpisodeKind,
    ToolDefinition,
    validate_tool_arguments,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError
from tests.support.agents import definition, task


@pytest.fixture
def remote_schema_host():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = json.dumps({"type": "string"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", hits
    httpd.shutdown()
    httpd.server_close()


def tool_with(schema):
    return ToolDefinition(
        name="probe", description="d", input_schema=schema, episode_kind=EpisodeKind.EXPLORATORY
    )


def test_a_remote_schema_reference_is_refused_and_never_fetched(remote_schema_host):
    base, hits = remote_schema_host
    reference = f"{base}/remote.json"
    tool = tool_with({"type": "object", "properties": {"x": {"$ref": reference}}})
    with pytest.raises(AgentSdkError) as excinfo:
        validate_tool_arguments(tool, {"x": 5})
    assert excinfo.value.code == "SCHEMA_REFERENCE_UNRESOLVABLE"
    assert reference in str(excinfo.value)
    assert 'tool "probe" arguments' in str(excinfo.value)
    assert "Only references inside the schema itself" in str(excinfo.value)
    assert hits == []


def test_a_file_schema_reference_is_refused_without_reading_the_file(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text('{"type": "string", "description": "TOP-SECRET"}', encoding="utf-8")
    tool = tool_with({"type": "object", "properties": {"x": {"$ref": secret.as_uri()}}})
    with pytest.raises(AgentSdkError) as excinfo:
        validate_tool_arguments(tool, {"x": 5})
    assert excinfo.value.code == "SCHEMA_REFERENCE_UNRESOLVABLE"
    assert "TOP-SECRET" not in str(excinfo.value) and "TOP-SECRET" not in repr(
        excinfo.value.details
    )


def test_the_refusal_also_covers_schemas_the_validator_cache_cannot_hold(remote_schema_host):
    base, hits = remote_schema_host
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": f"{base}/remote.json"}, "n": {"const": Decimal("1.5")}},
    }
    with pytest.raises(AgentSdkError) as excinfo:
        validate_tool_arguments(tool_with(schema), {"x": 5, "n": Decimal("1.5")})
    assert excinfo.value.code == "SCHEMA_REFERENCE_UNRESOLVABLE"
    assert hits == []


def test_input_and_output_schemas_are_protected_the_same_way(remote_schema_host):
    from nailong_agent_sdk.foundations.contracts import (
        validate_candidate_output,
        validate_task_input,
    )

    base, hits = remote_schema_host
    remote = {"type": "object", "properties": {"x": {"$ref": f"{base}/remote.json"}}}
    subject = definition().model_copy(update={"input_schema": remote, "output_schema": remote})
    with pytest.raises(AgentSdkError, match="task input"):
        validate_task_input(subject, task(input={"x": 1}))
    with pytest.raises(AgentSdkError, match="candidate output"):
        validate_candidate_output(subject, {"x": 1})
    assert hits == []


def test_references_inside_the_schema_still_resolve():
    local = {
        "$defs": {"name": {"type": "string", "minLength": 2}},
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/name"}},
    }
    validate_tool_arguments(tool_with(local), {"x": "ab"})
    with pytest.raises(AgentSdkError, match="shorter than 2|too short"):
        validate_tool_arguments(tool_with(local), {"x": "a"})
    anchored = {
        "$id": "https://example.com/root.json",
        "$defs": {"name": {"type": "string"}},
        "type": "object",
        "properties": {"x": {"$ref": "https://example.com/root.json#/$defs/name"}},
    }
    validate_tool_arguments(tool_with(anchored), {"x": "ab"})
    with pytest.raises(AgentSdkError, match="is not of type 'string'"):
        validate_tool_arguments(tool_with(anchored), {"x": 5})


def test_a_reference_to_the_json_schema_metaschema_still_resolves():
    schema = {
        "type": "object",
        "properties": {"schema": {"$ref": "https://json-schema.org/draft/2020-12/schema"}},
        "required": ["schema"],
    }
    validate_tool_arguments(tool_with(schema), {"schema": {"type": "string"}})
    with pytest.raises(AgentSdkError):
        validate_tool_arguments(tool_with(schema), {"schema": {"type": 5}})
