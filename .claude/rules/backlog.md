---
paths:
  - "todo/**"
  - "docs/**"
  - "scripts/gen_roadmap.py"
---

# Backlog (`todo/`), how work gets done, per-PR pages

The one-line rules in `CLAUDE.md` always apply. This loads when you touch the backlog or the docs site.

## `todo/`

Work is queued in `todo/`, one file per area (`todo/<AREA>.md`). `todo/README.md` is the dependency map, the chronological order, and the source of status. `docs/roadmap.html` is a generated kanban of the same data — run `uv run python scripts/gen_roadmap.py` after editing any `todo/*.md`.

There is **no GitHub Project board**. The old "Specodex Orchestration" board duplicated `todo/` and was deleted; any prompt, skill, or doc that still references `https://github.com/users/JimothyJohn/projects/1` or `gh project … --owner JimothyJohn 1` is stale and should be removed, never followed.

### What makes a good `todo/` entry

A well-formed doc is a small, reviewable chunk of work named by its area (`HARDENING.md`, `SEO.md`):

- **First line:** `# <AREA> — <one-line scope>`. The H1 becomes the card title in `docs/roadmap.html`.
- **Status blockquote** right after the H1 naming the current state (`📐 planned`, `🚧 in progress`, `🔴 needs sign-off`, `🎨 in flight`). The status emoji is what the roadmap renderer reads.
- **Phased plan** below, broken into independently shippable PRs.
- **Trigger conditions** if the doc has file-level triggers — add a row to `todo/README.md`'s "Trigger conditions" table so the doc surfaces when matching files are touched.

When deferring something to "after MVP soak" or "no active plan", move the file under `todo/longterm/`. That bucket is inert until Nick explicitly names a doc — never pull it into churn-plan rows, suggested order, or follow-up PRs; surface it once (with the path) when an edit lands in its trigger territory.

When `todo/<AREA>.md` ships its full scope (or its action items move into another doc), delete the file (`git rm todo/<AREA>.md`); the design rationale stays recoverable via `git log --diff-filter=D --follow -- todo/<AREA>.md`. The deletion log in `todo/README.md`'s "Recently shipped" header is the pattern.

## How work gets done

1. Pick a row from `todo/README.md`'s churn plan (the table near the bottom) or scan the kanban at `docs/roadmap.html`.
2. Branch off `dev` as `auto/<area>-<short-slug>-<yyyymmdd>` (or any descriptive name for human-driven work). `dev` is the only base — the daily orchestrator reads this file literally, so keep it unambiguous.
3. Make the smallest correct change. Run `./Quickstart verify` or the relevant subset before committing.
4. Push and open a PR into `dev`. Non-draft when CI passes and the diff doesn't touch hard-to-reverse surfaces (`CLAUDE.md` "Shipping a change" lists them).
5. After merge, update the status line in `todo/<AREA>.md` (or delete the doc if its scope fully shipped); regenerate `docs/roadmap.html`.

The hard rules for autonomous work are in `CLAUDE.md`; skip the row if any apply.

## Per-PR HTML documentation

`docs/requests/<n>.html` pages are reserved for **major functional changes** — user-visible features, schema migrations, architecture shifts, security-meaningful fixes. Dependabot bumps, lint fixes, lockfile regens, doc syncs, formatting passes, and other chores **do not** get a page; the public archive's signal-to-noise is the reason. If a PR doesn't move the product forward, it doesn't deserve a public page.

When you write one, match the manila / engineering-paper style of `docs/index.html`: same palette (`--paper #E8E2C9`, `--od #3A2C1C`, `--stencil #A88A1C`), same fonts (Oswald headlines, IBM Plex Mono body), square-bordered cells, no rounded corners or shadows. Set `body[data-issue]="PR-<n>"` for the watermark. Pull source from `gh pr view <n> --json title,body,headRefName,mergedAt,files` — never paraphrase a PR you haven't read.

The `docs/requests/` archive is historical and no longer linked from the `docs/index.html` top band. A new page still goes into `docs/requests/index.html` (newest first) so the archive stays internally navigable.
