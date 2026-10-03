# `(root)/` - the package root: one public import surface

`nailong_agent_sdk/__init__.py` is the only file at the package root. It contains no logic; it re-exports the supported public names so consumers write `from nailong_agent_sdk import BaseAgent`.

| File | Lines | Role |
|---|---:|---|
| [`__init__.py`](#__init__py---the-public-api-surface-re-exports-only) | 744 | the public API surface (re-exports only) |

---

### `__init__.py` - the public API surface (re-exports only)

*744 lines · depends on: `agent/base_agent/__init__.py`, `agent/graph_agent_executor.py`, `agent/model.py`, `agent/openai_compatible/__init__.py`, `agent/orchestrator/__init__.py`, `agent/runtime.py`, `agent/specialists.py`, `agent/verification.py`, `foundations/benchmarks.py`, `foundations/contracts.py`, `foundations/optimization/__init__.py`, `integrations/__init__.py`, `mcp/client.py`, `mcp/client_bridge.py`, `mcp/client_types.py`, `mcp/server.py`, `memory/context.py`, `memory/context_projection.py`, `memory/context_selection.py`, `memory/episode_models.py`, `memory/episode_scoring.py`, `memory/episode_store.py`, `memory/episodes.py`, `observability/audit_log.py`, `observability/metric_definitions.py`, `observability/metrics.py`, `observability/profiler.py`, `observability/telemetry_helpers.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `specifications/documents.py`, `specifications/evidence_graph.py`, `specifications/gate.py`, `specifications/gate_models.py`, `specifications/git_models.py`, `specifications/git_versioning.py`, `specifications/preprocessing.py`, `specifications/retrieval.py`, `specifications/retrieval_models.py`, `specifications/vision.py`, `state/controller_runtime.py`, `state/coordination_records.py`, `state/graph.py`, `state/graph_models.py`, `state/harness_coordinator.py`, `state/orchestration.py`, `state/orchestration_models.py`, `state/planning.py`, `state/project_state_engine.py`, `state/project_state_models.py`, `state/project_state_store.py`, `state/run_state_store.py`, `state/shared_state.py`, `state/stage_gates.py`, `tools/approvals.py`, `tools/artifacts.py`, `tools/core/__init__.py`, `tools/delegation.py`, `tools/policy.py`, `tools/registry.py`, `tools/sandbox.py`, `tools/sandbox_models.py`, `tools/supervisor.py`, `tools/task_models.py`, `tools/tasks.py`, `tools/worktree_models.py`, `tools/worktrees.py` · used by: no other module (entry point or re-exported only)*

**Role in the workflow.** Imports 352 names from 67 deep modules and lists exactly those in `__all__`. The developer catalogue (`developer_tools/catalog.py`) reads exactly this list, so a name that is not in `__all__` is not public API. Importing the package imports the `mcp` and `httpx` libraries because `mcp/client.py` is re-exported; the server itself and the optional LangChain, LangGraph, TypeSafe and Redis libraries are only imported when used. Every submodule import runs this file first, so its import order is the effective load order, which matters for the package's two folder-level cycles (`tools` with `state`, and `agent` with `integrations`).

