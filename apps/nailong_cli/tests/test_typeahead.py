from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import CancelToken, ModelSetup

from nailong_cli.keys import BACKSPACE, ENTER, ESCAPE, OTHER, EscapeWatcher, classify
from nailong_cli.live import LiveWork
from nailong_cli.live_commands import LIVE_COMMANDS, handle_typed
from nailong_cli.render import Rationale
from nailong_cli.todo import TodoList
from tests.conftest import call, final
from tests.test_terminal_ui import Keys, fancy_console, run_fancy


class Told:
    def __init__(self, refuse: str | None = None) -> None:
        self.messages: list[str] = []
        self.refuse = refuse
        self.inbox = SimpleNamespace(pending=len(self.messages))

    def say(self, message: str) -> None:
        if self.refuse:
            raise ValueError(self.refuse)
        self.messages.append(message)


def fake_state(*, rationale: Rationale | None = None):
    console, out, err = fancy_console()
    renderer = SimpleNamespace(rationale=rationale)
    todos = TodoList()
    state = SimpleNamespace(
        console=console,
        live=LiveWork(),
        parts=SimpleNamespace(renderer=renderer, todos=todos),
    )
    return state, out, err


def typed(text: str) -> list[str]:
    return [*text, ENTER]


def test_keys_are_classified_into_named_tokens_and_single_characters():
    assert classify("\x1b") == ESCAPE
    assert classify("\r") == ENTER and classify("\n") == ENTER
    assert classify("\x08") == BACKSPACE and classify("\x7f") == BACKSPACE
    assert classify("é") == "é" and classify("\x03") == OTHER


def test_the_watcher_collects_typed_lines_and_hands_each_one_on_at_enter():
    console, _, _ = fancy_console()
    lines: list[str] = []
    keys = Keys(*typed("hello"), "w", "x", BACKSPACE, "y", ENTER)
    with EscapeWatcher(console, lambda: None, keys, on_line=lines.append):
        deadline = time.monotonic() + 5
        while len(lines) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert lines == ["hello", "wy"]


def test_text_typed_but_not_sent_is_kept_as_leftover_when_the_watcher_ends():
    console, _, _ = fancy_console()
    keys = Keys(*"half a thou")
    with EscapeWatcher(console, lambda: None, keys, on_line=lambda line: None) as watcher:
        deadline = time.monotonic() + 5
        while keys.script and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
    assert watcher.leftover == "half a thou"
    assert console._typed == ""


def test_the_spinner_line_shows_what_is_being_typed_and_that_typing_is_accepted():
    console, out, _ = fancy_console()
    console.typeahead = True
    console.start_spinner("Thinking…")
    console.set_typed("and then add tests")
    console.stop_spinner()
    text = out.getvalue()
    assert "type to queue" in text and "▸ and then add tests" in text


def test_without_a_line_handler_typing_is_ignored_and_escape_still_works():
    console, _, _ = fancy_console()
    fired = threading.Event()
    with EscapeWatcher(console, fired.set, Keys("a", "b", ESCAPE)):
        assert fired.wait(5)


def test_stop_cancels_only_the_named_agent_and_names_the_running_ones_when_wrong():
    state, out, _ = fake_state()
    first, second = CancelToken(), CancelToken()
    state.live.add("main", "main agent", first, Told())
    state.live.add("t2", "t2 [worker]", second, Told())
    LIVE_COMMANDS["stop"](state, ["t2"])
    assert second.is_cancelled() and not first.is_cancelled()
    LIVE_COMMANDS["stop"](state, ["nope"])
    text = out.getvalue()
    assert 'no running agent "nope"' in text and "main, t2" in text
    LIVE_COMMANDS["stop"](state, [])
    assert "Usage: /stop <agent-id>" in out.getvalue()


def test_interrupt_cancels_everything_and_queues_the_new_instruction_first():
    state, out, _ = fake_state()
    token = CancelToken()
    state.live.add("main", "main agent", token, Told())
    state.live.enqueue("older")
    LIVE_COMMANDS["interrupt"](state, ["do", "this", "instead"])
    assert token.is_cancelled()
    assert state.live.queued() == ["do this instead", "older"]
    assert "interrupting main" in out.getvalue()


def test_interrupt_and_broadcast_say_so_when_nothing_is_running():
    state, out, _ = fake_state()
    LIVE_COMMANDS["interrupt"](state, [])
    LIVE_COMMANDS["broadcast"](state, ["hi"])
    text = out.getvalue()
    assert "Nothing is running to interrupt." in text and "Nothing is running to tell." in text


def test_broadcast_reaches_every_running_agent_and_reports_the_ones_that_refuse():
    state, out, err = fake_state()
    open_agent, full_agent = Told(), Told(refuse="20 messages are already waiting")
    state.live.add("main", "main agent", CancelToken(), open_agent)
    state.live.add("t2", "t2 [worker]", CancelToken(), full_agent)
    LIVE_COMMANDS["broadcast"](state, ["use", "the", "blue", "theme"])
    assert open_agent.messages == ["use the blue theme"]
    assert "told main" in out.getvalue()
    assert "t2 did not take the message: 20 messages are already waiting" in err.getvalue()


def test_why_gives_the_step_the_reasoning_and_the_plan_and_admits_when_there_is_none():
    state, out, _ = fake_state()
    LIVE_COMMANDS["why"](state, [])
    assert "nothing to explain" in out.getvalue()
    state.parts.renderer.rationale = Rationale("read_file a.txt", "I need the config first", 3)
    state.parts.todos.replace([{"content": "Read config", "status": "in_progress"}])
    state.live.add("main", "main agent", CancelToken(), Told())
    LIVE_COMMANDS["why"](state, [])
    text = out.getvalue()
    assert "Right now" in text and "step 3: read_file a.txt" in text
    assert "because I need the config first" in text and "[~] Read config" in text
    state.live.remove("main")
    state.parts.renderer.rationale = Rationale("list_dir", "", 1)
    LIVE_COMMANDS["why"](state, [])
    assert "Last step" in out.getvalue() and "gave no reasoning" in out.getvalue()


def test_ps_lists_running_agents_and_waiting_prompts():
    state, out, _ = fake_state()
    LIVE_COMMANDS["ps"](state, [])
    assert "Nothing is running and nothing is queued." in out.getvalue()
    state.live.add("t1", "t1 [explorer]", CancelToken(), Told())
    state.live.enqueue("then run the tests")
    LIVE_COMMANDS["ps"](state, [])
    assert "t1" in out.getvalue() and "queued 1: then run the tests" in out.getvalue()


def test_a_typed_line_that_is_not_a_live_command_is_queued_in_order():
    state, out, _ = fake_state()
    handle_typed(state, "first thing")
    handle_typed(state, "/review")
    assert state.live.queued() == ["first thing", "/review"]
    assert "queued 2: /review" in out.getvalue()
    assert state.live.pop() == "first thing" and state.live.pop() == "/review"
    assert state.live.pop() is None


class Delayed:
    def __init__(self, inner: ScriptedModel, *seconds: float) -> None:
        self.inner = inner
        self.seconds = list(seconds)

    async def next_turn(self, context):
        if self.seconds:
            await asyncio.sleep(self.seconds.pop(0))
        return await self.inner.next_turn(context)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class HeldUntilTyped:
    def __init__(self, inner: ScriptedModel) -> None:
        self.inner = inner
        self.started = 0
        self.typed = threading.Event()

    async def next_turn(self, context):
        self.started += 1
        if self.started == 2:
            waited = 0.0
            while not self.typed.is_set() and waited < 60:
                await asyncio.sleep(0.05)
                waited += 0.05
            await asyncio.sleep(0.5)
        return await self.inner.next_turn(context)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class KeysAfterSecondCall(Keys):
    def __init__(self, held: HeldUntilTyped, *script) -> None:
        super().__init__(*script)
        self.held = held

    def poll(self, timeout):
        if self.held.started < 2:
            time.sleep(0.01)
            return None
        key = super().poll(timeout)
        if not self.script:
            self.held.typed.set()
        return key


def test_typing_during_a_turn_runs_live_commands_queues_prompts_and_tells_the_agent(project, home):
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    inner = ScriptedModel(
        [call("read_file", path="a.txt"), call("read_file", path="a.txt"), final("done")]
    )
    held = HeldUntilTyped(inner)
    setup = ModelSetup(held, "scripted", "scripted-1")
    script: list = []
    for line in ("/why", "/broadcast use the blue theme", "also write the docs"):
        script += typed(line)
    code, out, err = run_fancy(
        project,
        home,
        lines="Do the job\n/exit\n",
        keys=KeysAfterSecondCall(held, *script),
        build=lambda config, environ: setup,
    )
    assert code == 0, err
    assert "Right now" in out and ": read_file a.txt" in out
    assert "told main" in out
    assert "queued 1: also write the docs" in out
    assert "> also write the docs" in out and out.count("[completed]") == 2
    observed = [
        (item.kind, item.message)
        for model_call in inner.calls
        for item in model_call.observations
        if item.kind == "user-message"
    ]
    assert ("user-message", "use the blue theme") in observed


class Timed:
    def __init__(self, inner, first_delay):
        self.inner = inner
        self.first_delay = first_delay
        self.started = []
        self.finished = []

    async def next_turn(self, context):
        self.started.append(time.monotonic())
        if len(self.started) == 1:
            await asyncio.sleep(self.first_delay)
        result = await self.inner.next_turn(context)
        self.finished.append(time.monotonic())
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_pause_holds_the_agent_at_its_next_step_and_resume_continues_it(project, home):
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    inner = ScriptedModel([call("read_file", path="a.txt"), final("carried on")])
    model = Timed(inner, 0.8)
    script = [None] * 30 + typed("/pause") + [None] * 200 + typed("/unpause")
    code, out, err = run_fancy(
        project,
        home,
        lines="Do the job\n/exit\n",
        keys=Keys(*script),
        build=lambda config, environ: ModelSetup(model, "scripted", "scripted-1"),
    )
    assert code == 0, err
    assert "will wait at its next step" in out and "resumed main" in out
    assert "[completed]" in out
    assert model.started[1] - model.finished[0] >= 1.0


def test_pause_and_resume_when_nothing_runs_say_so(project, home):
    from tests.conftest import run_cli

    _, out, _, _ = run_cli(
        project, home, [], lines="/pause\n/unpause\n/pause job-9\n/exit\n", interactive=True
    )
    assert "Nothing is running to pause." in out and "Nothing is paused." in out
    assert 'no running command "job-9"' in out


def test_a_background_job_can_be_paused_by_name_and_continues_from_the_same_point(tmp_path):
    import sys

    from nailong_cli.background import BackgroundJobs

    counter = tmp_path / "count.txt"
    script = tmp_path / "count.py"
    script.write_text(
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{counter}')\n"
        "for i in range(40):\n"
        "    p.write_text(str(i))\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )

    def position():
        for _ in range(20):
            try:
                return int(counter.read_text())
            except (OSError, ValueError):
                time.sleep(0.01)
        return -1

    state, out, _ = fake_state()

    async def scenario():
        jobs = BackgroundJobs(tmp_path, None, None)
        started = await jobs.start(f'"{sys.executable}" "{script}"')
        deadline = time.monotonic() + 20
        while position() < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        LIVE_COMMANDS["pause"](state, [started["job"]])
        await asyncio.sleep(0.3)
        at_pause = position()
        listing = jobs.listing()
        await asyncio.sleep(1.0)
        held = position()
        LIVE_COMMANDS["unpause"](state, [started["job"]])
        await asyncio.sleep(0.6)
        moved = position()
        await jobs.close()
        return at_pause, held, moved, listing

    at_pause, held, moved, listing = asyncio.run(scenario())
    assert held == at_pause and moved > held
    assert "paused" in listing[0]
    assert "paused job-1 where it is" in out.getvalue() and "resumed job-1" in out.getvalue()
