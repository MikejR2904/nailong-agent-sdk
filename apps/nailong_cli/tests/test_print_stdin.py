from __future__ import annotations

import json

from tests.conftest import final, run_cli


def prompt_text(setup) -> str:
    return json.dumps(setup.model.calls[0].prompt.model_dump())


def test_print_with_no_prompt_reads_it_from_standard_input(project, home):
    code, out, _, setup = run_cli(
        project, home, ["-p"], final("answered"), lines="summarise MARKER-TEXT\n"
    )
    assert code == 0 and "answered" in out
    assert "MARKER-TEXT" in prompt_text(setup)


def test_print_with_a_prompt_and_piped_input_sends_both(project, home):
    _, _, _, setup = run_cli(project, home, ["-p", "Explain"], final(), lines="PIPED-BODY\n")
    text = prompt_text(setup)
    assert "Explain" in text and "PIPED-BODY" in text


def test_print_with_nothing_anywhere_says_what_is_missing(project, home):
    code, _, err, _ = run_cli(project, home, ["-p"], final(), lines="")
    assert code == 64 and "-p needs a prompt" in err
