from nailong_agent_sdk.agent.model import ModelContext
from nailong_agent_sdk.foundations.contracts import ModelBinding
from nailong_agent_sdk.memory.context import assemble_initial_context
from nailong_agent_sdk.state.project_state_engine import ProjectStateProjector
from nailong_agent_sdk.state.project_state_models import StageStateSchema, make_project_state
from tests.support.agents import definition, task

FAKE_KEY = "FAKE-TEST-KEY-do-not-use-0000"


def model_context(parameters=None, binding=None):
    agent_definition = definition()
    agent_definition = agent_definition.model_copy(
        update={
            "model_binding": binding
            or ModelBinding(provider="fake", model="fake-1", parameters=parameters or {})
        }
    )
    agent_task = task()
    state = make_project_state(
        project_id="p", revision=0, stage_schema=StageStateSchema(schema_id="s", stage="s")
    )
    return ModelContext(
        task=agent_task,
        prompt=assemble_initial_context(agent_definition, agent_task),
        iteration=1,
        project_state=ProjectStateProjector().project(state),
        observations=[],
        episodes=[],
        model_binding=agent_definition.model_binding,
        output_schema=agent_definition.output_schema,
    )
