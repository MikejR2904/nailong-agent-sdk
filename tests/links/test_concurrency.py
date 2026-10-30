import asyncio
import json
import threading
import time

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.support.concurrency import CALLS, run_many, verify
from tests.support.processes import run_workers


def test_forty_concurrent_agents_in_one_event_loop_share_services_without_corruption(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    try:
        results = asyncio.run(run_many(services, "loop", 40))
    finally:
        services.telemetry.close()
        services.audit_logs.close()
    problems, handle_files = verify(tmp_path, [f"loop-{i}" for i in range(40)])
    assert all(r.status is AgentRunStatus.COMPLETED for r in results)
    assert not problems and handle_files == 40 * CALLS


def test_agents_in_four_threads_share_one_services_object(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    errors = []
    statuses = []
    guard = threading.Lock()

    def worker(index):
        try:
            results = asyncio.run(run_many(services, f"thr{index}", 10))
            with guard:
                statuses.extend(r.status.value for r in results)
        except Exception as error:
            with guard:
                errors.append(f"{type(error).__name__}: {str(error)[:160]}")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    services.telemetry.close()
    services.audit_logs.close()
    run_ids = [f"thr{k}-{i}" for k in range(4) for i in range(10)]
    problems, handle_files = verify(tmp_path, run_ids)
    assert not errors and statuses.count("completed") == 40
    assert not problems and handle_files == 40 * CALLS


def test_agents_in_three_processes_share_one_run_root(tmp_path):
    script = f"""
        import asyncio, json, sys, time
        from pathlib import Path
        from tests.support.concurrency import run_many
        from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
        who = sys.argv[1]
        services = AgentRuntimeServices.open(Path(r"{tmp_path}"))
        deadline = float(sys.argv[2])
        while time.time() < deadline:
            time.sleep(0.0005)
        results = asyncio.run(run_many(services, "proc" + who, 15))
        services.telemetry.close()
        services.audit_logs.close()
        print(json.dumps([r.status.value for r in results]))
    """
    outputs = run_workers(script, 3, str(time.time() + 8), timeout=600)
    failures = [(code, err[-300:]) for code, _, err in outputs if code != 0]
    statuses = [s for code, out, _ in outputs if code == 0 for s in json.loads(out)]
    run_ids = [f"proc{k}-{i}" for k in range(3) for i in range(15)]
    problems, handle_files = verify(tmp_path, run_ids)
    assert not failures and statuses.count("completed") == 45
    assert not problems and handle_files == 45 * CALLS
