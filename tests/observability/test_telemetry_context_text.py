import pytest
from pydantic import ValidationError

from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

OPTIONAL_IDENTIFIERS = (
    "project_id",
    "experiment_id",
    "cohort_id",
    "attempt_id",
    "controller_id",
    "node_id",
    "task_id",
    "agent_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "stage",
    "environment_id",
)
UNPAIRED = chr(0xD800)


@pytest.mark.parametrize("field", OPTIONAL_IDENTIFIERS)
def test_an_unpaired_surrogate_in_an_optional_id_is_refused_naming_the_field(field):
    with pytest.raises(ValidationError) as raised:
        TelemetryContext(run_id="run-1", **{field: "ab" + UNPAIRED + "cd"})
    (issue,) = raised.value.errors()
    assert issue["loc"] == (field,)
    assert issue["msg"] == (
        f"Value error, {field} contains the unpaired surrogate code point U+D800 at index 2, "
        "which cannot be encoded as UTF-8."
    )


def test_the_run_id_still_refuses_an_unpaired_surrogate():
    with pytest.raises(ValidationError) as raised:
        TelemetryContext(run_id="run" + UNPAIRED)
    assert [issue["loc"] for issue in raised.value.errors()] == [("run_id",)]


def test_a_context_with_a_bad_id_is_refused_before_anything_is_emitted(tmp_path):
    store = TelemetryStore(tmp_path)
    with pytest.raises(ValidationError):
        TelemetryContext(run_id="run-1", task_id=UNPAIRED)
    assert store.list_events("run-1") == [] and store.verify_run_chain("run-1")
    store.close()


def test_well_formed_text_in_any_script_and_unset_ids_are_accepted(tmp_path):
    text = "t" + chr(0xE9) + chr(0x65E5) + chr(0x672C) + chr(0x1F600)
    context = TelemetryContext(run_id="run-1", task_id=text, stage=text, node_id=None)
    assert context.task_id == text and context.node_id is None and context.agent_id is None
    store = TelemetryStore(tmp_path)
    store.emit(
        "t.event",
        context,
        actor=TelemetryActor(kind="system", identifier="test"),
        authority=TelemetryAuthority.DETERMINISTIC,
        status="ok",
    )
    (stored,) = store.list_events("run-1")
    assert stored.context.task_id == text and stored.context.stage == text
    store.close()
