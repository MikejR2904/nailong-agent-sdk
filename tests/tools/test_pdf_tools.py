import asyncio

import pytest

from nailong_agent_sdk.tools.core.helpers import (
    _fetch_public_text,
    _parse_pdf_page,
    _render_pdf_page,
)
from tests.support.pdfs import build_pdf

URL = "https://example.com/spec.pdf"
SHORT = ["Hello clock tree", "Second page about dividers", "Third page"]


def parse(raw, page, max_chars, cache=None):
    return _parse_pdf_page(raw, URL, page, max_chars, cache)


def test_pages_that_fit_are_returned_together_with_nothing_left_to_continue_from():
    result = parse(build_pdf(SHORT), 1, 5000)
    assert result["total_pages"] == 3 and result["pages_included"] == [1, 2, 3]
    assert result["next_page"] is None and result["truncated"] is False
    assert "text_truncated" not in result and "omitted_chars" not in result
    for text in SHORT:
        assert text in result["content"]


def test_a_continuation_starts_at_the_page_the_previous_call_named():
    raw = build_pdf(SHORT)
    first = parse(raw, 1, 80)
    assert first["pages_included"] == [1] and first["next_page"] == 2
    assert first["truncated"] is True and "text_truncated" not in first
    second = parse(raw, first["next_page"], 5000)
    assert second["pages_included"] == [2, 3] and second["next_page"] is None
    assert "Hello clock tree" not in second["content"]


def test_a_first_page_longer_than_max_chars_says_how_much_was_cut():
    words = "alpha " * 200
    raw = build_pdf([words, "short second page"])
    result = parse(raw, 1, 500)
    assert result["pages_included"] == [1] and result["next_page"] == 2
    assert len(result["content"]) == 500
    assert result["truncated"] is True and result["text_truncated"] is True
    assert result["omitted_chars"] > 0


def test_a_last_page_that_is_cut_is_not_reported_as_complete():
    raw = build_pdf(["omega " * 200])
    result = parse(raw, 1, 500)
    assert result["next_page"] is None and len(result["content"]) == 500
    assert result["truncated"] is True and result["text_truncated"] is True
    assert result["omitted_chars"] == len(parse(raw, 1, 20000)["content"]) - 500


def test_a_page_outside_the_document_names_the_valid_range():
    with pytest.raises(ValueError, match=r"page 9 is out of range; this document has 3 page\(s\)"):
        parse(build_pdf(SHORT), 9, 500)
    with pytest.raises(ValueError, match="Valid pages are 1 through 3"):
        parse(build_pdf(SHORT), 0, 500)


def test_a_cached_pdf_is_parsed_without_fetching_it_again():
    raw = build_pdf(SHORT)
    result = _fetch_public_text(URL, 5000, page=2, pdf_bytes_cache={URL: raw})
    assert result["pages_included"] == [2, 3] and "Second page about dividers" in result["content"]


def test_rendering_a_page_stores_a_png_under_a_stable_reference():
    raw = build_pdf(SHORT)
    images = {}
    result = _render_pdf_page(URL, 2, 1.0, {URL: raw}, images)
    assert result["total_pages"] == 3 and result["page"] == 2
    kind, png = images[result["image_ref"]]
    assert kind == "png" and png.startswith(b"\x89PNG") and result["byte_count"] == len(png)
    again = _render_pdf_page(URL, 2, 1.0, {URL: raw}, {})
    assert again["image_ref"] == result["image_ref"]


def test_rendering_a_page_outside_the_document_names_the_valid_range():
    raw = build_pdf(SHORT)
    with pytest.raises(ValueError, match="render_pdf_page page 9 is out of range"):
        _render_pdf_page(URL, 9, 1.0, {URL: raw}, {})


def test_the_dispatcher_passes_a_continuation_through(tmp_path):
    from nailong_agent_sdk.tools.artifacts import ArtifactStore
    from nailong_agent_sdk.tools.core import CoreToolDispatcher, CoreToolServices

    raw = build_pdf(["bravo " * 200, "second page"])
    services = CoreToolServices(
        root=tmp_path,
        artifacts=ArtifactStore(tmp_path),
        declared_output_paths=(),
        pdf_bytes_cache={URL: raw},
    )
    result = asyncio.run(
        CoreToolDispatcher(services).execute("web_fetch", {"url": URL, "max_chars": 500})
    )
    assert result.status == "succeeded"
    assert result.output["text_truncated"] is True and result.output["next_page"] == 2
