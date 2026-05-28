# Agentify

**Read a website once. Generate a JSON SDK. From then on, the LLM uses the
site like an API — without ever seeing the page.**

A mapper agent visits a site, proposes a list of tool functions
(`submit_contact_form`, `get_top_stories`, …), and records each one as a
deterministic recipe. At runtime, an LLM picks a tool from the schema and a
pure-replay engine executes it. No screenshots, no per-step LLM calls during
execution, no fragile prompting.

Built on Python + Playwright + OpenAI `gpt-5.4-mini`.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env   # OPENAI_API_KEY=...
```

### Phase 1 — generate an SDK for a site

```bash
agentify map --url https://news.ycombinator.com --name hackernews
```

What this does:
1. Crawls the landing page + a few same-origin nav links.
2. Sends the survey to the LLM; gets back proposed tools with JSON-schema
   parameters.
3. Shows you the proposals in the terminal — accept all or pick a subset.
4. For each accepted tool: drives the site once with sentinel placeholders
   (`__W2A_NAME__` → `{{name}}`), captures every browser action with a robust
   selector (role+name first, CSS fallback).
5. Writes `recipes/hackernews.tools.json`.

Add `--auto-approve` to skip the interactive approval step.

### Phase 2 — use the SDK

Two ways:

**Direct tool call** (no LLM, deterministic):
```bash
agentify call --site hackernews --tool get_top_stories --args '{"n": 5}'
```

**Natural language → tool pick** (LLM picks tool + arguments; never sees the page):
```bash
agentify run-mapped --site hackernews \
  --task "Give me the top 3 stories right now"
```

## How it works

```
PHASE 1 — MAP (one-shot per site)
─────────────────────────────────
        Crawler   ─→  surveys landing page + nav links
            ↓
        Proposer  ─→  LLM call: candidate tool list
            ↓
        Approver  ─→  CLI prompt: keep / drop
            ↓
        Recorder  ─→  drives site with placeholders, captures recipe
            ↓
   recipes/<slug>.tools.json

PHASE 2 — USE (every invocation)
─────────────────────────────────
   Registry   ─→  load recipes/<slug>.tools.json
        ↓
   Picker     ─→  LLM call: { tool_name, arguments }     (NO PAGE CONTENT)
        ↓
   Engine     ─→  deterministic replay of the recipe
```

### Recipe format

A site registry is `recipes/<slug>.tools.json`. Each tool has a name,
description, JSON-Schema `parameters`, and a list of `steps`:

```jsonc
{
  "name": "submit_contact_form",
  "description": "Submit a contact request.",
  "parameters": {
    "type": "object",
    "properties": {
      "name":  {"type": "string"},
      "email": {"type": "string"}
    },
    "required": ["name", "email"]
  },
  "steps": [
    {"op": "goto",   "url": "https://example.com/#contact"},
    {"op": "type",   "target": {"role": "textbox", "name": "Name *"},  "text": "{{name}}"},
    {"op": "type",   "target": {"role": "textbox", "name": "Email *"}, "text": "{{email}}"},
    {"op": "click",  "target": {"role": "button", "name": "Send"}},
    {"op": "wait",   "ms": 1500},
    {"op": "verify", "kind": "page_text_contains", "value": "thanks"}
  ]
}
```

### Engine op vocabulary (10 deterministic ops, zero LLM)

| op            | purpose                                                  |
|---------------|----------------------------------------------------------|
| `goto`        | navigate to a URL                                        |
| `click`       | click a Target                                           |
| `type`        | fill a textbox (supports `{{param}}` substitution)       |
| `select`      | pick an option in a combobox                             |
| `press_enter` | press Enter on a Target                                  |
| `scroll`      | scroll up / down / top / bottom                          |
| `wait`        | sleep N ms                                               |
| `extract`     | save text/value/attr from a Target into the result dict  |
| `js_extract`  | run arbitrary in-page JS for tricky extractions          |
| `verify`      | assert page state; failure raises `RecipeFailure`        |

### Target resolution (how recipes survive small DOM changes)

A `Target` records up to three strategies. The engine tries them in
priority order until one resolves to an element:

1. `role` + `name` — ARIA / accessibility tree (most stable).
2. `css` — recorded at map time from the element's id / name / data-testid
   / nth-of-type position.
3. `text` — visible text match.

## File-by-file

```
agentify/
├── cli.py             Typer commands: map, call, run-mapped
├── browser.py         Playwright wrapper; actions keyed by element id
├── ax_tree.py         Injected JS that builds the numbered element list
│                      used during crawling and recording
├── selectors.py       Target dataclass + multi-strategy resolver
├── recipe.py          Recipe dataclass + deterministic Engine
├── registry.py        Load/save recipes/<slug>.tools.json + OpenAI conv.
├── recorder.py        Recording Browser subclass: tees every action
│                      into a recipe step list with placeholder binding
├── mapper.py          The full Phase-1 pipeline: Crawler + Proposer +
│                      Approver + Recorder
├── llm.py             OpenAI client + system prompts + tool schema
│                      (used by the Proposer, the recording driver,
│                      and the runtime Picker)
├── agent.py           Internal observe→think→act loop used by the
│                      Recorder to drive the site during mapping
└── memory.py          Step history used by the recording loop
```

A Hebrew teaching page is at `docs/agentify.html`.

## Multi-step flows (supported)

The mapper records arbitrary **linear multi-step** flows for any site, with no
per-site code, via four site-agnostic mechanisms:

- **Realistic example inputs** — the proposer attaches an `example` to each
  parameter (carried on `ToolProposal.examples`, used by `_placeholders_for`)
  so typeaheads/live-search respond during recording.
- **Autocomplete normalization** — `_normalize_autocomplete` rewrites
  `type {{param}} into a combobox → click a named suggestion` into
  `type {{param}} → verify an option exists → click the FIRST option`
  (`{"role": "option"}` resolves to `.first`), which is parameter-independent.
- **Auto result-extraction** — `record_action_recipe` appends a `js_extract`
  of the landing page so action tools return data.
- **Self-verifying record→replay** — `_record_verified_action` replays each
  freshly recorded recipe with the example args (`_verify_replay`) and
  re-records once with the failure as a hint if it doesn't replay.

What's still missing are *non-linear* shapes (loops, branches) and a few
binding edge cases — below.

## What this does NOT handle (and how you'd extend it)

The current system works well for **single-form submissions**,
**linear multi-step flows** (fill fields → pick suggestions → submit → read),
and **single-page data extraction**. Once you've internalised how it works,
these are the real seams where it falls over — listed with the concrete
fix each one needs:

### 1. No session persistence between `call` invocations
Every `agentify call` opens a fresh Browser, executes the recipe, closes
it. So `call login(...)` followed by `call view_cart()` won't work — the
second call has no cookies. Any flow that needs auth state, shopping
carts, OAuth, or multi-step wizards is blocked.

- **Why it matters:** real apps need login.
- **Fix size:** small. Add a `Session` class in `browser.py` that keeps
  a Playwright `BrowserContext` alive across calls (keyed by `--session`
  flag). `run-mapped` already runs everything in one context, so only
  `call` has the problem.

### 2. No iteration op (no pagination, no for-each)
Recipes are straight-line sequences. You can't say "for each story on
this list, open it and extract X." To paginate HN to page 5, you'd need
5 separate recipes or a `js_extract` that does all the work in one shot.

- **Why it matters:** scraping, batch processing, "show me all..." tasks.
- **Fix size:** medium. Add `for_each {items_target, sub_steps}` to the
  Engine op vocabulary; iterate `page.locator(items_target).all()` and
  run the sub-steps with a `{{_item}}` variable in scope. Mapper has to
  learn to *propose* such recipes for list-shaped sites.

### 3. No branching (`if_verify`)
`verify` either passes or raises `RecipeFailure`. There's no "if the
cart is empty, go shop; else go to checkout." Every conditional has to
live in the runtime LLM via tool composition, which is fine for top-level
decisions but awkward for "does this modal have a Cancel button or a
Close button?" micro-branches.

- **Why it matters:** any site with dialogs, optional flows, or
  validation errors that the recipe needs to react to.
- **Fix size:** medium. Add
  `if_verify {kind, value, then: [...steps], else: [...steps]}` to
  `recipe.py`'s Engine and recurse on the chosen branch.

### 4. Shallow Crawler → shallow tool proposals
The Crawler only visits the landing page + a few same-origin nav links.
Anything deep in a flow — logged-in pages, search results, multi-step
wizards, modals — is invisible to the Proposer, so no tools are proposed
for it. That's why Wikipedia got `search_wikipedia` but not
`get_article_facts(query)`: the Crawler never visited an article.

- **Why it matters:** the Proposer is the bottleneck on how rich the
  generated SDK is.
- **Fix size:** medium-large. Make the Crawler an agent itself —
  follow forms, interact with menus, capture state at each layer. This
  is real work because crawling becomes recursive and stateful (and may
  need login fixtures).

### 5. Param binding fails on non-text actions
When the Recorder sees `type_text(id=4, text="__W2A_EMAIL__")` it knows
to bind that field to `{{email}}`. Typeahead/autocomplete *is* handled now
(see "Multi-step flows" above — the suggestion click is normalized to a
parameter-independent first-option select). But for **clicks** on radio
buttons, checkboxes, date pickers, file uploads — and for "open X by
title" flows where the agent navigates by clicking a named link instead of
typing — there's no typed text, so no sentinel to swap. Result: those
params get hardcoded to whatever the mapping agent picked (e.g. `pizza_size`
frozen to "Small" instead of `{{pizza_size}}`). Note the replay-verify pass
can still *pass* such a recipe, because the hardcoded path works for the
example value — so inspect recipes whose parameters drive a click rather
than a type.

- **Why it matters:** any form with selects, radios, checkboxes is
  partially parameterized.
- **Fix size:** medium. The Mapper needs to track which placeholder
  slot is "active" when a non-text action fires, by feeding the agent
  the synthetic-task placeholders with their parameter names and asking
  it to pick options whose visible text matches the placeholder. The
  Recorder then matches role+name+value against the placeholder map.

### Path forward

The cleanest design decision when extending is: **is the recipe a flat
sequence or a small language?**

- **Path X — keep recipes flat, push complexity to the LLM.**
  Add `Session` + improve param binding. Each tool stays a minimal
  atomic action (`login`, `view_cart`, `checkout_step_1`...). The
  runtime LLM composes them. Simple, predictable, more LLM calls.
- **Path Y — make recipes a small language.**
  Add `for_each`, `if_verify`, `subcall` to the Engine. One tool can
  encode "log in if needed, paginate to page N, extract everything,
  return." LLM only picks top-level tools. More expressive, more code
  to maintain.

The pragmatic order for this codebase: do (1) and (5) first — they're
small, high-value, and unlock most real multi-page cases. Add (2) only
when a concrete pagination task demands it. Defer (3) and (4) until
you've hit a wall with the simpler path.

## Generated SDKs in this repo

`recipes/`:
- `httpbin.tools.json` — `submit_order(...)`
- `hackernews.tools.json` — `get_top_stories`, `open_story`, `open_comments`, `browse_feed`
- `yairtech.tools.json` — `book_audit`, `get_services`, `list_resources`, `open_chat`
- `wikipedia.tools.json` — `search_wikipedia`, `log_in`, `create_account`

## Tests

```bash
pip install -e ".[dev]"
pytest          # 21 tests, no Playwright / OpenAI needed
```

## Configuration

`.env`:
```
OPENAI_API_KEY=sk-...
AGENTIFY_MODEL=gpt-5.4-mini     # any function-calling model
```
