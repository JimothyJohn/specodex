# TODO

Questions and tasks for Nick that agents can't decide alone. The backlog proper is `todo/` (one file per area); this file is the decision inbox. Answer with an `answer:` line under the item.

## Open questions for Nick

- [x] **envelope-extract: what accuracy policy for backfilled dimensions?** — `todo/DIMENSIONS.md` Phase 2 reads L/W/H + weight off datasheet pages. A dry run on 2026-11-24, checked by hand against the source PDFs: weights correct (14/14 on Apex AB/ABR); simple inline motors correct (Yaskawa SGMGV-05D: L 179, □90); gearheads wrong (Apex AB090 length read as the Ø80 pilot instead of L2+L8 = 126.5; ABR090 right-angle height read as 100 instead of ~200). Nothing has been written to any table. Code: branch `feat/envelope-extract-20261124`, worktree `.claude/worktrees/envelope`, `./Quickstart envelope-extract --plan` (1,326 datasheets / 31,884 rows on dev).
      needs: Nick's call on how wrong "approximate" may be
      result: envelope-extract ships as a PR and runs with --apply on dev + staging
      options: A (recommended) weight for all types now + dims only where two independent Gemini passes agree within 10% per axis, rest withheld (~2x Gemini, ~$50 total) | B single-pass dims everywhere as approximate, source page linked in the detail modal | C dims for motors + drives only, gearheads/actuators withheld
      est: 3h
      difficulty: med
      id: envelope-dims-accuracy-policy
      asked: 2026-11-24
      by: claude session (dimensions)
      resolved: 2026-11-25 — Nick chose A in session. Shipped #508–#511; applied to dev (4,459 rows) and staging (1,897 rows). Coverage and accuracy notes in todo/DIMENSIONS.md Phase 2.

- [ ] **Phase 3 interface dims: what agreement tolerance?** — Shaft, collet bore, pilot and thread values will be stored only when a standard table and the drawing agree, or two independent reads agree. Otherwise they're withheld behind a "see drawing p.N" link. Is 0.05 mm the right agreement tolerance, or must values match exactly to the printed precision?
      needs: Nick's call
      result: tolerance constant fixed in the Phase 3 cross-check
      options: 0.05 mm | exact to printed precision
      depends: envelope-dims-accuracy-policy
      id: interface-dims-tolerance
      asked: 2026-11-24
      by: claude session (dimensions)
