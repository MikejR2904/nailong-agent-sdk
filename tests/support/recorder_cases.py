import threading

from nailong_agent_sdk.agent.evidence_profile import (
    EvidenceProfile,
    EvidenceSettings,
    RecorderSettings,
)
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.recorder import (
    RecorderServer,
)
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call

APPEND, READ = "append-token-aaaa", "read-token-bbbb"


def serve(core, **options):
    server = RecorderServer(core, append_token=APPEND, read_token=READ, **options)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server):
    server.shutdown()
    server.server_close()


def real_entry(root, run_id="r1", count=2):
    audit = AuditTranscriptStore(root)
    entries = [audit.append(run_id, f"e{index}", {"i": index}) for index in range(count)]
    return audit, entries


def run_with_profile(tmp_path, evidence, task_id="run-a", finish=True):
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=5),
        ScriptedModel([tool_call("c0", "echo", {"i": 0}), final()]),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    assert arun(subject.run(task(task_id))).status is AgentRunStatus.COMPLETED
    if finish:
        assert arun(services.checkpoint_after_run(task_id)) is None
    return services


def attested_profile(tmp_path, monkeypatch, key, port, **settings):
    token_file = tmp_path / "append.token"
    token_file.write_text(APPEND, encoding="utf-8")
    read_file = tmp_path / "read.token"
    read_file.write_text(READ, encoding="utf-8")
    public = tmp_path / "recorder.pub"
    public.write_text(key.public_key_hex, encoding="utf-8")
    return EvidenceProfile(
        EvidenceSettings(
            recorder=RecorderSettings(
                host="127.0.0.1",
                port=port,
                append_token_file=token_file,
                read_token_file=read_file,
                scoped_append=True,
                public_key_file=public,
                signer_id="recorder",
            ),
            **settings,
        )
    )
