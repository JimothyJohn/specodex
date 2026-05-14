#!/usr/bin/env python3
"""Render todo/*.md into a kanban under docs/roadmap/.

Outputs:
    docs/roadmap.html              — kanban index (status columns)
    docs/roadmap/<slug>.html       — one rendered page per todo/*.md
    docs/roadmap/style.css         — shared field-manual styling

Status is inferred from todo/README.md's "churn plan" table; docs in
todo/longterm/ default to the Deferred column. Re-run after editing
any todo/*.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import markdown


REPO_ROOT = Path(__file__).resolve().parent.parent
TODO_DIR = REPO_ROOT / "todo"
DOCS_DIR = REPO_ROOT / "docs"
ROADMAP_DIR = DOCS_DIR / "roadmap"

# Columns the kanban renders. Keys map to the status emoji set used in
# todo/README.md's churn-plan table; values are the visible label and
# the slug used as a CSS class.
COLUMNS: list[tuple[str, str, str]] = [
    ("ready", "Ready", "Ready to PR, no blockers."),
    ("backlog", "Backlog", "Queued behind the row above."),
    ("signoff", "Needs Sign-off", "Blocked on an explicit human decision."),
    ("inflight", "In Flight", "Wave shipped; further waves open."),
    ("deferred", "Deferred", "Parked in todo/longterm/."),
]


@dataclass
class TodoDoc:
    """One todo/*.md file, with its rendered HTML body and metadata."""

    slug: str
    path: Path  # relative to repo root
    title: str
    body_html: str
    summary: str  # first paragraph, plain text
    status: str  # one of the COLUMNS keys
    status_label: str  # raw human-readable label, e.g. "needs sign-off"


def slugify(name: str) -> str:
    """`HARDENING.md` → `hardening`; `longterm/BOARD.md` → `longterm-board`."""
    base = name.replace("/", "-").rsplit(".", 1)[0]
    return base.lower().replace("_", "-")


def extract_title(text: str, fallback: str) -> str:
    """First H1 in the file, stripped of trailing context."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


_LIST_PREFIX = re.compile(r"^([-+*]\s|\d+\.\s)")


def _scan_paragraph(text: str, *, allow_blockquote: bool) -> list[str]:
    """Return the first prose paragraph's lines after the H1.

    Optionally include blockquote contents (with the leading `> ` stripped).
    """
    saw_h1 = False
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not saw_h1:
            if stripped.startswith("# "):
                saw_h1 = True
            continue
        if not stripped or stripped == "---":
            if buf:
                break
            continue
        if allow_blockquote and stripped.startswith(">"):
            buf.append(stripped.lstrip(">").strip())
            continue
        is_skipline = (
            stripped.startswith(("#", ">", "|", "```"))
            or _LIST_PREFIX.match(stripped) is not None
        )
        if is_skipline:
            if buf:
                break
            continue
        buf.append(stripped)
    return buf


def extract_summary(text: str) -> str:
    """First substantive content after the H1 — prose or blockquote."""
    buf = _scan_paragraph(text, allow_blockquote=False)
    if not buf:
        buf = _scan_paragraph(text, allow_blockquote=True)
    summary = " ".join(buf)
    # Strip markdown emphasis / inline code for the card preview.
    summary = re.sub(r"\*\*(.+?)\*\*", r"\1", summary)
    summary = re.sub(r"\*(.+?)\*", r"\1", summary)
    summary = re.sub(r"`([^`]+)`", r"\1", summary)
    summary = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)
    if len(summary) > 260:
        summary = summary[:257].rsplit(" ", 1)[0] + "…"
    return summary or "(no summary)"


# ---------- Status inference -------------------------------------------------

# Maps a doc-name token (the third column of the churn plan table) to its
# status. The table looks like:
#     | 7 | **DB_CLEANUP Phase 2 decision** — … | DB_CLEANUP | 🔴 needs sign-off |
# The third column is the doc reference (no .md), the fourth is the status.
CHURN_LINE = re.compile(
    r"^\|\s*\d+\s*\|\s*(?P<scope>.+?)\s*\|\s*(?P<doc>[A-Z_]+)\s*\|\s*(?P<status>.+?)\s*\|\s*$"
)

STATUS_EMOJI = {
    "🟡": "ready",
    "⚪": "backlog",
    "🔴": "signoff",
    "⏸": "deferred",
    "🎨": "inflight",
}


def parse_churn_table(readme_text: str) -> dict[str, tuple[str, str]]:
    """Return {DOC_TOKEN: (status_key, raw_label)}.

    If a doc appears in multiple rows, the first (highest-priority) row
    wins. README rows are ordered top-down by sprint priority.
    """
    out: dict[str, tuple[str, str]] = {}
    in_table = False
    for line in readme_text.splitlines():
        if "PR scope" in line and "Doc" in line and "Status" in line:
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and not line.startswith("|"):
            in_table = False
            continue
        if not in_table:
            continue
        m = CHURN_LINE.match(line)
        if not m:
            continue
        doc = m.group("doc")
        raw_status = m.group("status").strip()
        key = next(
            (v for emoji, v in STATUS_EMOJI.items() if emoji in raw_status),
            "backlog",
        )
        if doc not in out:
            out[doc] = (key, raw_status)
    return out


def status_for(
    stem: str, is_longterm: bool, churn: dict[str, tuple[str, str]]
) -> tuple[str, str]:
    """Best status guess for a todo file.

    longterm/* always go to Deferred. Otherwise consult the churn table;
    if absent, fall back to In Flight for actively-edited docs (BAUHAUS,
    BAUHAUS_FOLLOWUP) and Backlog for everything else.
    """
    if is_longterm:
        return "deferred", "deferred (longterm/)"
    if stem in churn:
        return churn[stem]
    # Active design / port docs not in the sprint churn table.
    if stem.startswith("BAUHAUS"):
        return "inflight", "in flight"
    return "backlog", "backlog"


# ---------- HTML emission ----------------------------------------------------

MD_EXTENSIONS = ["tables", "fenced_code", "toc", "sane_lists", "attr_list"]


def render_markdown(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=MD_EXTENSIONS,
        output_format="html5",
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{description}">
    <meta name="theme-color" content="#3A2C1C">
    <title>Specodex — {title}</title>
    <link rel="icon" type="image/svg+xml" href="../logo.svg">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
</head>
<body data-issue="{watermark}">
    <div class="grain" aria-hidden="true"></div>

    <header class="band">
        <div class="band-inner">
            <a class="band-brand" href="../index.html">
                <svg class="band-mark" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <rect width="120" height="120" fill="#E8E2C9"/>
                    <rect x="14" y="14" width="92" height="92" fill="none" stroke="#1A1A14" stroke-width="3"/>
                    <rect x="14" y="14" width="92" height="22" fill="#3A2C1C"/>
                    <rect x="62" y="60" width="40" height="10" fill="#A88A1C"/>
                    <line x1="14" y1="56" x2="106" y2="56" stroke="#1A1A14" stroke-width="2"/>
                    <line x1="14" y1="74" x2="106" y2="74" stroke="#1A1A14" stroke-width="2"/>
                    <line x1="14" y1="92" x2="106" y2="92" stroke="#1A1A14" stroke-width="2"/>
                    <line x1="60" y1="36" x2="60" y2="106" stroke="#1A1A14" stroke-width="2"/>
                </svg>
                <span class="wordmark">SPECODEX</span>
            </a>
            <nav class="band-nav" aria-label="Section">
                <a href="../index.html">Catalog</a>
                <a href="../roadmap.html">Roadmap</a>
                <a href="https://github.com/JimothyJohn/specodex/blob/master/{source_path}" rel="noreferrer">Source</a>
            </nav>
        </div>
    </header>

    <main>
        <section class="doc-hero">
            <div class="hero-tag">▮▮▮ {tag} ▮▮▮</div>
            <h1 class="hero-title">{title}</h1>
            <div class="doc-meta">
                <span class="status-chip status-{status}">{status_label}</span>
                <span class="doc-source"><code>todo/{source_basename}</code></span>
            </div>
        </section>

        <article class="doc-body">
{body_html}
        </article>
    </main>

    <footer>
        <div class="footer-inner">
            <a class="footer-link" href="../roadmap.html">← Back to roadmap</a>
            <span class="footer-rule" aria-hidden="true"></span>
            <a class="footer-link" href="https://github.com/JimothyJohn/specodex/blob/master/{source_path}" rel="noreferrer">Edit on GitHub</a>
        </div>
    </footer>
</body>
</html>
"""


def render_doc_page(doc: TodoDoc) -> str:
    rel_source = str(doc.path).replace("\\", "/")
    todo_rel = (
        rel_source.split("todo/", 1)[1] if "todo/" in rel_source else doc.path.name
    )
    watermark = doc.title.upper()[:24]
    return PAGE_TEMPLATE.format(
        title=html_escape(doc.title),
        description=html_escape(doc.summary)[:160],
        watermark=html_escape(watermark),
        tag=html_escape(doc.slug.upper().replace("-", " ")),
        status=doc.status,
        status_label=html_escape(doc.status_label.upper()),
        body_html=doc.body_html,
        source_path=html_escape(rel_source),
        source_basename=html_escape(todo_rel),
    )


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------- Kanban index -----------------------------------------------------

ROADMAP_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="Specodex roadmap — feature kanban driven by todo/*.md. Ready, backlog, sign-off, deferred — no PR history.">
    <meta name="theme-color" content="#3A2C1C">
    <title>Specodex — Roadmap</title>
    <link rel="icon" type="image/svg+xml" href="logo.svg">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="roadmap/style.css">
</head>
<body data-issue="ROADMAP">
    <div class="grain" aria-hidden="true"></div>

    <header class="band">
        <div class="band-inner">
            <a class="band-brand" href="index.html">
                <svg class="band-mark" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <rect width="120" height="120" fill="#E8E2C9"/>
                    <rect x="14" y="14" width="92" height="92" fill="none" stroke="#1A1A14" stroke-width="3"/>
                    <rect x="14" y="14" width="92" height="22" fill="#3A2C1C"/>
                    <rect x="62" y="60" width="40" height="10" fill="#A88A1C"/>
                    <line x1="14" y1="56" x2="106" y2="56" stroke="#1A1A14" stroke-width="2"/>
                    <line x1="14" y1="74" x2="106" y2="74" stroke="#1A1A14" stroke-width="2"/>
                    <line x1="14" y1="92" x2="106" y2="92" stroke="#1A1A14" stroke-width="2"/>
                    <line x1="60" y1="36" x2="60" y2="106" stroke="#1A1A14" stroke-width="2"/>
                </svg>
                <span class="wordmark">SPECODEX</span>
            </a>
            <nav class="band-nav" aria-label="Section">
                <a href="index.html">Catalog</a>
                <a href="roadmap.html" class="active">Roadmap</a>
                <a href="https://github.com/JimothyJohn/specodex" rel="noreferrer">GitHub</a>
            </nav>
        </div>
    </header>

    <main>
        <section class="hero">
            <div class="hero-tag">▮▮▮ FIELD ROADMAP ▮▮▮</div>
            <h1 class="hero-title">What's queued, what's ready, what's deferred.</h1>
            <p class="hero-sub">
                One card per <code>todo/*.md</code> plan doc, grouped by status. Source of
                truth lives on the
                <a href="https://github.com/users/JimothyJohn/projects/1" rel="noreferrer">orchestration board</a>;
                this page is the public, indexable snapshot. Click a card to open the full plan.
            </p>
        </section>

{columns_html}
    </main>

    <footer>
        <div class="footer-inner">
            <span>Generated from <code>todo/*.md</code> via <code>scripts/gen_roadmap.py</code>.</span>
            <span class="footer-rule" aria-hidden="true"></span>
            <a class="footer-link" href="https://github.com/JimothyJohn/specodex/tree/master/todo" rel="noreferrer">todo/ on GitHub</a>
            <a class="footer-link" href="index.html">Catalog →</a>
        </div>
    </footer>
</body>
</html>
"""


CARD_TEMPLATE = """\
                <li>
                    <a class="kanban-card" href="roadmap/{slug}.html">
                        <div class="card-head">
                            <span class="card-source">todo/{basename}</span>
                            <span class="status-chip status-{status}">{status_label}</span>
                        </div>
                        <h3 class="card-title">{title}</h3>
                        <p class="card-summary">{summary}</p>
                    </a>
                </li>
"""


def render_kanban(docs: list[TodoDoc]) -> str:
    columns_html = []
    for key, label, blurb in COLUMNS:
        cards = [d for d in docs if d.status == key]
        cards.sort(key=lambda d: d.title.lower())
        if cards:
            inner = "\n".join(
                CARD_TEMPLATE.format(
                    slug=html_escape(d.slug),
                    basename=html_escape(
                        str(d.path).split("todo/", 1)[1]
                        if "todo/" in str(d.path)
                        else d.path.name
                    ),
                    status=d.status,
                    status_label=html_escape(d.status_label.upper()),
                    title=html_escape(d.title),
                    summary=html_escape(d.summary),
                )
                for d in cards
            )
        else:
            inner = '                <li class="empty">No cards in this column.</li>\n'
        columns_html.append(
            f"""        <section class="kanban-column" aria-label="{html_escape(label)} column">
            <header class="column-head">
                <span class="column-code">{html_escape(key.upper())}</span>
                <h2 class="column-title">{html_escape(label)}</h2>
                <p class="column-blurb">{html_escape(blurb)}</p>
                <span class="column-count">{len(cards)}</span>
            </header>
            <ol class="kanban-stack">
{inner}            </ol>
        </section>"""
        )
    return "\n".join(columns_html)


# ---------- CSS --------------------------------------------------------------

STYLE_CSS = """\
/* Specodex roadmap — manila / engineering-paper field manual.
 * Shared by docs/roadmap.html (kanban index) and docs/roadmap/<slug>.html
 * (per-todo rendered pages). Mirrors docs/index.html palette + type.
 */

:root {
    --paper: #E8E2C9;
    --paper-shade: #DDD5B6;
    --od: #3A2C1C;
    --od-deep: #2A1F12;
    --ink: #1A1A14;
    --ink-soft: #3A3A2E;
    --stencil: #A88A1C;
    --stamp: #7A1F1F;
    --grid: #B8AC85;

    --font-headline: 'Oswald', 'Roboto Condensed', 'Arial Narrow', sans-serif;
    --font-body: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
    background-color: var(--paper);
    color: var(--ink);
    font-family: var(--font-body);
    font-size: 15px;
    line-height: 1.55;
    font-variant-numeric: tabular-nums;
    min-height: 100vh;
    overflow-x: hidden;
}

.grain {
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 0;
    opacity: 0.06;
    mix-blend-mode: multiply;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='280' height='280'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.10  0 0 0 0 0.10  0 0 0 0 0.08  0 0 0 0.6 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}

body > *:not(.grain) {
    position: relative;
    z-index: 1;
}

/* ===== Top band ===== */
.band {
    background-color: var(--od);
    color: var(--paper);
    border-bottom: 2px solid var(--ink);
    padding: 0.55rem 1.5rem;
}

.band-inner {
    max-width: 1300px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
}

.band-brand {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    text-decoration: none;
    color: var(--paper);
}

.band-mark {
    width: 32px;
    height: 32px;
    border: 1px solid var(--paper);
    background-color: var(--paper);
    display: block;
    flex-shrink: 0;
}

.wordmark {
    font-family: var(--font-headline);
    font-weight: 700;
    font-size: 1.6rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
}

.band-nav {
    display: flex;
    align-items: center;
    gap: 1.2rem;
    font-family: var(--font-headline);
    font-size: 0.75rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
}

.band-nav a {
    color: var(--paper);
    text-decoration: none;
    opacity: 0.78;
    border-bottom: 1px solid transparent;
    padding-bottom: 1px;
    transition: opacity 0.12s linear, border-color 0.12s linear, color 0.12s linear;
}

.band-nav a:hover,
.band-nav a:focus-visible,
.band-nav a.active {
    color: var(--stencil);
    border-bottom-color: var(--stencil);
    opacity: 1;
    outline: none;
}

/* ===== Main shell ===== */
main {
    max-width: 1300px;
    margin: 0 auto;
    padding: 3rem 1.5rem 3.5rem;
}

body[data-issue]::before {
    content: attr(data-issue);
    position: fixed;
    top: 6.5rem;
    right: 2rem;
    font-family: var(--font-headline);
    font-size: 5rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--stamp);
    opacity: 0.06;
    transform: rotate(-3deg);
    pointer-events: none;
    white-space: nowrap;
    z-index: 0;
}

/* ===== Hero (kanban index) ===== */
.hero {
    padding: 0.5rem 0 2.4rem;
    border-bottom: 1px solid var(--ink);
    margin-bottom: 2.4rem;
}

.hero-tag {
    font-family: var(--font-headline);
    font-size: 0.8rem;
    letter-spacing: 0.3em;
    color: var(--od);
    text-transform: uppercase;
    margin-bottom: 0.9rem;
    font-weight: 500;
}

.hero-title {
    font-family: var(--font-headline);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    font-size: clamp(2rem, 4vw, 3rem);
    line-height: 1.05;
    color: var(--ink);
    margin: 0 0 1rem;
    max-width: 24ch;
}

.hero-sub {
    max-width: 64ch;
    font-size: 0.95rem;
    line-height: 1.6;
    color: var(--ink-soft);
}

.hero-sub a {
    color: var(--ink);
    border-bottom: 1px solid var(--ink);
    text-decoration: none;
}
.hero-sub a:hover { color: var(--stencil); border-bottom-color: var(--stencil); }

/* ===== Kanban ===== */
.kanban-column {
    border-top: 2px solid var(--ink);
    padding: 1.4rem 0 2.4rem;
    background: linear-gradient(180deg, rgba(232, 226, 201, 0.55), transparent 200px);
}

.kanban-column + .kanban-column {
    margin-top: 0;
}

.column-head {
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-rows: auto auto;
    grid-template-areas:
        "code title count"
        "code blurb count";
    column-gap: 0.9rem;
    align-items: baseline;
    margin-bottom: 1.4rem;
}

.column-code {
    grid-area: code;
    font-family: var(--font-headline);
    font-size: 0.72rem;
    letter-spacing: 0.22em;
    background-color: var(--od);
    color: var(--paper);
    padding: 0.22rem 0.55rem;
    font-weight: 500;
    text-transform: uppercase;
    align-self: center;
}

.column-title {
    grid-area: title;
    font-family: var(--font-headline);
    font-weight: 700;
    font-size: 1.4rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink);
}

.column-blurb {
    grid-area: blurb;
    font-size: 0.85rem;
    color: var(--ink-soft);
    margin-top: 0.15rem;
}

.column-count {
    grid-area: count;
    font-family: var(--font-headline);
    font-size: 1.4rem;
    color: var(--stencil);
    font-weight: 700;
    align-self: center;
}

.kanban-stack {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 0.9rem;
}

.kanban-stack .empty {
    color: var(--ink-soft);
    font-style: italic;
    padding: 0.6rem 0;
}

.kanban-card {
    display: block;
    text-decoration: none;
    color: var(--ink);
    background-color: rgba(232, 226, 201, 0.45);
    border: 1.5px solid var(--ink);
    padding: 0.9rem 1rem 1rem;
    transition: background-color 0.12s linear, transform 0.12s linear, border-color 0.12s linear;
}

.kanban-card:hover,
.kanban-card:focus-visible {
    background-color: var(--paper-shade);
    border-color: var(--stencil);
    outline: none;
    transform: translateY(-1px);
}

.card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    margin-bottom: 0.55rem;
}

.card-source {
    font-family: var(--font-body);
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    color: var(--ink-soft);
    background-color: rgba(168, 138, 28, 0.14);
    padding: 0.1em 0.4em;
    border: 1px solid rgba(26, 26, 20, 0.18);
}

.card-title {
    font-family: var(--font-headline);
    font-weight: 600;
    font-size: 1.05rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    margin: 0 0 0.5rem;
    line-height: 1.2;
}

.card-summary {
    font-size: 0.9rem;
    color: var(--ink-soft);
    line-height: 1.55;
}

/* ===== Status chip ===== */
.status-chip {
    font-family: var(--font-headline);
    font-size: 0.66rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    padding: 0.15rem 0.5rem;
    border: 1.5px solid var(--ink);
    background-color: var(--paper);
    white-space: nowrap;
}

.status-chip.status-ready    { background-color: var(--stencil); color: var(--paper); border-color: var(--ink); }
.status-chip.status-inflight { background-color: #C97A2B;        color: var(--paper); border-color: var(--ink); }
.status-chip.status-signoff  { background-color: var(--stamp);    color: var(--paper); border-color: var(--ink); }
.status-chip.status-backlog  { background-color: var(--paper);    color: var(--ink); }
.status-chip.status-deferred { background-color: var(--paper-shade); color: var(--ink-soft); }

/* ===== Per-doc page hero ===== */
.doc-hero {
    padding: 0.5rem 0 1.8rem;
    border-bottom: 1px solid var(--ink);
    margin-bottom: 1.8rem;
}

.doc-meta {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-top: 0.6rem;
}

.doc-source code {
    font-family: var(--font-body);
    font-size: 0.85rem;
    background-color: rgba(168, 138, 28, 0.14);
    padding: 0.1em 0.4em;
    border: 1px solid rgba(26, 26, 20, 0.18);
}

/* ===== Rendered markdown body ===== */
.doc-body {
    max-width: 75ch;
    color: var(--ink);
}

.doc-body h1,
.doc-body h2,
.doc-body h3,
.doc-body h4 {
    font-family: var(--font-headline);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--ink);
    margin: 2.2rem 0 0.6rem;
    font-weight: 600;
}

.doc-body h1 { font-size: 1.7rem; margin-top: 1rem; }
.doc-body h2 { font-size: 1.3rem; border-bottom: 1px solid rgba(26,26,20,0.3); padding-bottom: 0.25rem; }
.doc-body h3 { font-size: 1.05rem; }
.doc-body h4 { font-size: 0.9rem; letter-spacing: 0.06em; }

.doc-body p,
.doc-body ul,
.doc-body ol,
.doc-body blockquote {
    margin-bottom: 1rem;
}

.doc-body ul,
.doc-body ol {
    padding-left: 1.6rem;
}

.doc-body li { margin-bottom: 0.25rem; }

.doc-body blockquote {
    border-left: 3px solid var(--stencil);
    padding: 0.4rem 0.9rem;
    background-color: rgba(168, 138, 28, 0.08);
    color: var(--ink-soft);
}

.doc-body code {
    font-family: var(--font-body);
    background-color: rgba(168, 138, 28, 0.14);
    padding: 0.05em 0.35em;
    font-size: 0.92em;
    border: 1px solid rgba(26, 26, 20, 0.18);
}

.doc-body pre {
    background-color: var(--ink);
    color: var(--paper);
    padding: 1rem 1.2rem;
    overflow-x: auto;
    margin-bottom: 1rem;
    border: 1.5px solid var(--ink);
}

.doc-body pre code {
    background: none;
    border: none;
    padding: 0;
    color: inherit;
    font-size: 0.86rem;
    line-height: 1.6;
}

.doc-body a {
    color: var(--ink);
    border-bottom: 1px solid var(--ink);
    text-decoration: none;
}

.doc-body a:hover,
.doc-body a:focus-visible {
    color: var(--stencil);
    border-bottom-color: var(--stencil);
    outline: none;
}

.doc-body table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
    margin-bottom: 1.2rem;
}

.doc-body th,
.doc-body td {
    border: 1px solid rgba(26, 26, 20, 0.35);
    padding: 0.5rem 0.7rem;
    text-align: left;
    vertical-align: top;
}

.doc-body th {
    background-color: var(--paper-shade);
    font-family: var(--font-headline);
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}

.doc-body hr {
    border: none;
    border-top: 1px solid var(--ink);
    margin: 1.8rem 0;
    opacity: 0.6;
}

/* ===== Footer ===== */
footer {
    border-top: 2px solid var(--ink);
    background-color: var(--paper-shade);
    padding: 1.2rem 1.5rem;
    margin-top: 3.5rem;
}

.footer-inner {
    max-width: 1300px;
    margin: 0 auto;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.9rem;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
}

.footer-rule {
    flex: 1;
    height: 1px;
    background-color: var(--ink);
    opacity: 0.4;
    min-width: 1.5rem;
}

.footer-link {
    font-family: var(--font-headline);
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: var(--ink);
    text-decoration: none;
    border-bottom: 1px solid var(--ink);
    padding-bottom: 1px;
}

.footer-link:hover,
.footer-link:focus-visible {
    color: var(--stencil);
    border-bottom-color: var(--stencil);
    outline: none;
}

/* ===== Responsive ===== */
@media (max-width: 720px) {
    .band { padding: 0.5rem 1rem; }
    .wordmark { font-size: 1.3rem; letter-spacing: 0.14em; }
    .band-nav { gap: 0.7rem; font-size: 0.65rem; }
    main { padding: 2rem 1rem 2.4rem; }
    .hero-title { font-size: 1.8rem; }
    body[data-issue]::before { font-size: 2.6rem; right: 0.5rem; top: 5rem; }
    .kanban-stack { grid-template-columns: 1fr; }
    .column-head {
        grid-template-columns: auto 1fr auto;
        column-gap: 0.7rem;
    }
}
"""


# ---------- Main -------------------------------------------------------------


def collect_docs() -> list[TodoDoc]:
    readme = (TODO_DIR / "README.md").read_text(encoding="utf-8")
    churn = parse_churn_table(readme)

    md_files: list[tuple[Path, bool]] = []
    for p in sorted(TODO_DIR.glob("*.md")):
        if p.name == "README.md":
            continue
        md_files.append((p, False))
    longterm = TODO_DIR / "longterm"
    if longterm.is_dir():
        for p in sorted(longterm.glob("*.md")):
            md_files.append((p, True))

    docs: list[TodoDoc] = []
    for path, is_longterm in md_files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        slug = slugify(str(rel.relative_to("todo")))
        title = extract_title(text, fallback=path.stem)
        summary = extract_summary(text)
        body_html = render_markdown(text)
        stem = path.stem
        status, status_label = status_for(stem, is_longterm, churn)
        docs.append(
            TodoDoc(
                slug=slug,
                path=rel,
                title=title,
                body_html=body_html,
                summary=summary,
                status=status,
                status_label=status_label,
            )
        )
    return docs


def main() -> int:
    ROADMAP_DIR.mkdir(parents=True, exist_ok=True)
    docs = collect_docs()

    (ROADMAP_DIR / "style.css").write_text(STYLE_CSS, encoding="utf-8")

    for doc in docs:
        (ROADMAP_DIR / f"{doc.slug}.html").write_text(
            render_doc_page(doc), encoding="utf-8"
        )

    kanban_html = ROADMAP_TEMPLATE.format(columns_html=render_kanban(docs))
    (DOCS_DIR / "roadmap.html").write_text(kanban_html, encoding="utf-8")

    print(f"Rendered {len(docs)} docs → docs/roadmap/")
    print(f"Wrote docs/roadmap.html with {len(COLUMNS)} columns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
