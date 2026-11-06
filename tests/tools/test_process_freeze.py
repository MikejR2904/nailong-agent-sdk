import asyncio
import time

from nailong_agent_sdk.tools.process_freeze import FREEZER
from nailong_agent_sdk.tools.supervisor import ProcessExitKind, ProcessSupervisor
from tests.support.process_cases import template
from tests.support.tools import run


def counter_code(path):
    return (
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{path}')\n"
        "for i in range(20):\n"
        "    p.write_text(str(i))\n"
        "    time.sleep(0.1)\n"
        "print('finished')\n"
    )


def reading(path):
    for _ in range(20):
        try:
            return int(path.read_text())
        except (OSError, ValueError):
            time.sleep(0.01)
    return -1


def test_a_frozen_command_holds_its_place_and_its_time_limit_waits_for_it(tmp_path):
    marker = tmp_path / "count.txt"
    tpl = template("counter", counter_code(marker)).model_copy(update={"timeout_seconds": 3.5})
    supervisor = ProcessSupervisor([tpl])

    async def scenario():
        job = asyncio.create_task(supervisor.execute(tpl.name, cwd=tmp_path))
        deadline = time.monotonic() + 20
        while reading(marker) < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        frozen = FREEZER.freeze()
        await asyncio.sleep(0.3)
        at_freeze = reading(marker)
        await asyncio.sleep(2.5)
        still = reading(marker)
        thawed = FREEZER.thaw()
        record = await job
        return frozen, thawed, at_freeze, still, record

    frozen, thawed, at_freeze, still, record = run(scenario(), timeout=90)
    assert frozen == ["counter"] and thawed == ["counter"]
    assert still == at_freeze
    assert record.exit_kind is ProcessExitKind.SUCCEEDED and "finished" in record.output
    assert reading(marker) == 19
    assert FREEZER.entries() == []


def test_background_commands_are_only_frozen_when_named(tmp_path):
    entry = FREEZER.register(2_000_000_000, "job-1", foreground=False)
    try:
        assert FREEZER.freeze() == []
        assert not entry.frozen
    finally:
        FREEZER.unregister(2_000_000_000)
