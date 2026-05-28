"""Mapper unit tests for the generic multi-step machinery.

No Playwright, no network — these cover the pure transforms that make the
mapper handle arbitrary multi-step flows: realistic example placeholders and
parameter-independent autocomplete normalization.
"""

from agentify.mapper import (
    ToolProposal,
    _normalize_autocomplete,
    _placeholders_for,
)


def _proposal(props, examples=None):
    return ToolProposal(
        name="t",
        description="",
        parameters={"type": "object", "properties": props},
        tool_type="action",
        start_url="https://example.com",
        examples=examples or {},
    )


def test_placeholders_prefer_examples():
    p = _proposal(
        {"frm": {"type": "string"}, "n": {"type": "integer"}},
        examples={"frm": "TLV"},
    )
    ph = _placeholders_for(p)
    assert ph["frm"] == "TLV"          # realistic example used for typeahead
    assert ph["n"] == "424242"          # numeric sentinel still used (no example)


def test_placeholders_fallback_sentinel_without_example():
    p = _proposal({"q": {"type": "string"}})
    assert _placeholders_for(p)["q"] == "__W2A_Q__"


def test_autocomplete_click_becomes_first_option():
    """type {{param}} into a combobox + click a named suggestion ->
    verify-an-option-exists + click the FIRST option (param-independent)."""
    steps = [
        {"op": "click", "target": {"role": "combobox", "name": "Where from?"}},
        {"op": "type", "target": {"role": "combobox", "name": "Where from?"}, "text": "{{frm}}"},
        {"op": "wait", "ms": 800},
        {"op": "click", "target": {"role": "option", "name": "Tel Aviv-Yafo TLV"}},
        {"op": "click", "target": {"role": "button", "name": "Search"}},
    ]
    out = _normalize_autocomplete(steps)
    # the literal-named option click is gone
    assert all(
        s.get("target", {}).get("name") != "Tel Aviv-Yafo TLV" for s in out
    )
    # replaced by a gate + a bare first-option click
    assert {"op": "verify", "kind": "element_exists", "target": {"role": "option"}} in out
    assert {"op": "click", "target": {"role": "option"}} in out
    # the trailing Search click is preserved
    assert out[-1] == {"op": "click", "target": {"role": "button", "name": "Search"}}


def test_autocomplete_leaves_plain_typing_untouched():
    """A type with no {{param}}, or with no following option click, is left alone."""
    steps = [
        {"op": "type", "target": {"role": "searchbox", "name": "q"}, "text": "{{query}}", "press_enter": True},
        {"op": "wait", "ms": 500},
    ]
    assert _normalize_autocomplete(steps) == steps
