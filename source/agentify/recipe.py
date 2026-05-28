"""Recipe = a deterministic action sequence. Engine = runs it. No LLM."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .selectors import Target, resolve


class RecipeFailure(Exception):
    def __init__(self, step_index: int, reason: str):
        super().__init__(f"step {step_index}: {reason}")
        self.step_index = step_index
        self.reason = reason


@dataclass
class Recipe:
    name: str
    description: str
    parameters: dict  # JSON Schema
    steps: list[dict] = field(default_factory=list)
    returns: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            parameters=d.get("parameters", {"type": "object", "properties": {}}),
            steps=list(d.get("steps", [])),
            returns=d.get("returns", {}),
        )


_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute(value: Any, args: dict) -> Any:
    """Replace {{param}} placeholders inside any string field, recursively."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            key = m.group(1)
            if key not in args:
                return m.group(0)
            return str(args[key])
        return _PARAM_RE.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v, args) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, args) for k, v in value.items()}
    return value


class Engine:
    """Runs a Recipe against a Browser. No LLM contact."""

    def __init__(self, browser):  # browser: agentify.browser.Browser
        self.browser = browser

    def execute(self, recipe: Recipe, args: Optional[dict] = None) -> dict:
        args = args or {}
        returned: dict[str, Any] = {}
        steps = _substitute(recipe.steps, args)

        for i, step in enumerate(steps):
            op = step.get("op")
            try:
                if op == "goto":
                    self.browser.goto(step["url"])
                elif op == "wait":
                    ms = int(step.get("ms", 500))
                    # Scale down wait times by a factor of 5 (e.g. 1000ms -> 200ms) for speed, with 50ms min
                    scaled_ms = max(50, int(ms * 0.2)) if ms > 0 else 0
                    self.browser.wait(scaled_ms)
                elif op == "scroll":
                    self.browser.scroll(step.get("direction", "down"))
                elif op == "click":
                    loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                    loc.scroll_into_view_if_needed(timeout=2000)
                    loc.click(timeout=4000)
                elif op == "type":
                    loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                    loc.scroll_into_view_if_needed(timeout=2000)
                    loc.fill(step.get("text", ""), timeout=4000)
                    if step.get("press_enter"):
                        loc.press("Enter")
                elif op == "press_enter":
                    loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                    loc.press("Enter")
                elif op == "press":
                    # Generic key press. With a target, press the key on that
                    # element; otherwise press it on whatever is focused. Used
                    # for autocomplete commit sequences (ArrowDown, Enter).
                    key = step.get("key", "Enter")
                    if step.get("target"):
                        loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                        loc.press(key)
                    else:
                        self.browser.press_key(key)
                elif op == "select":
                    loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                    value = step.get("value", "")
                    try:
                        loc.select_option(value=value, timeout=2000)
                    except Exception:
                        loc.select_option(label=value, timeout=2000)
                elif op == "extract":
                    loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                    attr = step.get("attr", "text")
                    if attr == "text":
                        val = loc.inner_text(timeout=2000).strip()
                    elif attr == "value":
                        val = loc.input_value(timeout=2000)
                    else:
                        val = loc.get_attribute(attr, timeout=2000)
                    returned[step["key"]] = val
                elif op == "js_extract":
                    # Custom JS for tricky extraction. Deterministic — no LLM.
                    expr = step["expr"]
                    val = self.browser.page.evaluate(expr)
                    returned[step["key"]] = val
                elif op == "verify":
                    import time
                    kind = step.get("kind", "page_text_contains")
                    expected = str(step.get("value", ""))
                    
                    # Poll for up to 3 seconds for verification to pass
                    start_time = time.time()
                    ok = False
                    while True:
                        if kind == "page_text_contains":
                            body_text = self.browser.page.evaluate(
                                "() => document.body.innerText || ''"
                            )
                            if step.get("case_insensitive"):
                                ok = expected.lower() in (body_text or "").lower()
                            else:
                                ok = expected in (body_text or "")
                        elif kind == "url_contains":
                            ok = expected in self.browser.page.url
                        elif kind == "element_exists":
                            try:
                                loc = resolve(self.browser.page, Target.from_dict(step["target"]), timeout_ms=100)
                                ok = loc.count() > 0
                            except Exception:
                                ok = False
                        
                        if ok:
                            break
                        if (time.time() - start_time) > 3.0:
                            break
                        time.sleep(0.05)
                        
                    if not ok:
                        raise RecipeFailure(
                            i, f"verify failed: {kind}={expected!r}"
                        )
                else:
                    raise RecipeFailure(i, f"unknown op {op!r}")
            except RecipeFailure:
                raise
            except Exception as e:
                raise RecipeFailure(i, f"{type(e).__name__}: {e}") from e

        return returned
