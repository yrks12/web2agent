"""Recipe Engine tests with a fake Browser. No Playwright, no network."""

from dataclasses import dataclass, field
from typing import Any

from agentify.recipe import Engine, Recipe, RecipeFailure


@dataclass
class FakePage:
    url: str = "https://example.com/"
    body_text: str = ""
    locator_count: int = 1

    def locator(self, *args, **kwargs):
        return self

    def evaluate(self, expr: str, *args, **kwargs):
        if "innerText" in expr:
            return self.body_text
        if "(()=>" in expr.replace(" ", ""):
            return {"stub": True}
        return None

    # Locator-ish methods
    def scroll_into_view_if_needed(self, timeout=None): pass
    def click(self, timeout=None):
        self.last_click = True
    def fill(self, value, timeout=None):
        self.last_fill = value
    def press(self, key):
        self.last_press = key
    def select_option(self, value=None, label=None, timeout=None):
        self.last_select = value or label
    def inner_text(self, timeout=None):
        return "extracted-text"
    def input_value(self, timeout=None):
        return "extracted-value"
    def get_attribute(self, name, timeout=None):
        return f"attr-{name}"
    def count(self):
        return self.locator_count
    @property
    def first(self):
        return self
    def get_by_role(self, role, name=None):
        return self
    def get_by_text(self, text, exact=False):
        return self


@dataclass
class FakeBrowser:
    page: FakePage = field(default_factory=FakePage)
    actions: list = field(default_factory=list)

    def goto(self, url, wait_ms=0):
        self.page.url = url
        self.actions.append(("goto", url))

    def wait(self, ms):
        self.actions.append(("wait", ms))
        return f"waited {ms}"

    def scroll(self, direction):
        self.actions.append(("scroll", direction))
        return f"scrolled {direction}"

    def press_key(self, key):
        self.actions.append(("press_key", key))
        return f"pressed {key}"


def test_substitution_in_recipe_steps():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t",
        description="d",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}},
        steps=[
            {"op": "goto", "url": "https://e.com/?name={{name}}"},
            {"op": "type", "target": {"role": "textbox", "name": "Name"}, "text": "{{name}}"},
        ],
    )
    Engine(browser).execute(recipe, {"name": "Jane"})
    assert browser.actions[0] == ("goto", "https://e.com/?name=Jane")
    assert browser.page.last_fill == "Jane"


def test_verify_passes_when_text_present():
    browser = FakeBrowser(page=FakePage(body_text="Thanks for your submission"))
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "verify", "kind": "page_text_contains", "value": "thanks", "case_insensitive": True}],
    )
    Engine(browser).execute(recipe, {})  # no exception


def test_verify_fails_loudly():
    browser = FakeBrowser(page=FakePage(body_text="something else"))
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "verify", "kind": "page_text_contains", "value": "thanks"}],
    )
    try:
        Engine(browser).execute(recipe, {})
    except RecipeFailure as e:
        assert e.step_index == 0
        return
    raise AssertionError("should have raised RecipeFailure")


def test_unknown_op_fails():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "teleport", "destination": "moon"}],
    )
    try:
        Engine(browser).execute(recipe, {})
    except RecipeFailure as e:
        assert "teleport" in e.reason
        return
    raise AssertionError("should have raised RecipeFailure")


def test_extract_stores_into_result():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[
            {"op": "extract", "key": "title", "target": {"role": "heading"}, "attr": "text"},
        ],
    )
    result = Engine(browser).execute(recipe, {})
    assert result == {"title": "extracted-text"}


def test_press_op_on_target():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "press", "key": "ArrowDown", "target": {"role": "combobox"}}],
    )
    Engine(browser).execute(recipe, {})
    assert browser.page.last_press == "ArrowDown"


def test_press_op_without_target_uses_keyboard():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "press", "key": "Enter"}],
    )
    Engine(browser).execute(recipe, {})
    assert ("press_key", "Enter") in browser.actions
