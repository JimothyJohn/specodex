# PHASE 5 RECOVERY — get the stranded auth-hardening work onto master

## Status

`gh pr list` says PRs #3, #5, #6, #7, #8 (Phases 5a, 5c, 5d, 5e, 5f) are
MERGED. As of 2026-05-03, **Phase 5d is also on master** (commit `3ef96a7`
landed via merge `c63df04` — the prior version of this doc misread the
state). Phases 5a / 5c / 5e / 5f remain stranded — they were merged into
the **`feat-auth-phase1` PR branch** instead of into master. Net effect:
**~1.1k lines of auth-hardening code are not on production.**

Phase work that's still missing from master:

| Phase | Commit | Footprint | Where it currently lives |
|-------|--------|-----------|--------------------------|
| 5a SES email sender | `a14613a` | 5 files, +202/-17 | local `feat-auth-phase5a-ses`; also on `origin/feat-auth-phase1` |
| 5c refresh-token revocation | `a679fdf` | 5 files, +135/-1 | local `feat-auth-phase5c-revoke`; also on `origin/feat-auth-phase1` |
| 5e auth audit logging | `28d6fdc` | 3 files, +400/-6 | local `feat-auth-phase5e-audit`; also on `origin/feat-auth-phase1` |
| 5f WAF CloudWatch alarms | `ae1144c` | 5 files, +365 | local `feat-auth-phase5f-alarms` only — base was `feat-auth-phase5b-waf` (already merged + deleted) |

The `specodex-csp` worktree (Phase 5d, branch `feat-auth-phase5d-csp`) is
safe to remove now that 5d is confirmed on master:

```sh
git worktree remove /Users/nick/github/specodex-csp
git branch -D feat-auth-phase5d-csp
```

## Recovery plan (recommended)

One stacked PR rather than four small ones — the phases were originally
sequenced to land together, the test surface is small, and reviewers would
rather look at the auth-hardening bundle once than four times.

### Step 1 — fresh branch off current master

```sh
git checkout master
git pull --ff-only
git checkout -b feat-auth-phase5-tail
```

### Step 2 — cherry-pick the phase commits in order

Order matters: 5a / 5c / 5e all touch the auth backend; 5f touches WAF
infra. Pick auth first, infra second, so any rebase conflict is isolated
to the auth file cluster.

```sh
git cherry-pick a14613a   # 5a SES
git cherry-pick a679fdf   # 5c revoke
git cherry-pick 28d6fdc   # 5e audit
git cherry-pick ae1144c   # 5f alarms
```

Expected conflict surface, by file:

- `app/backend/src/routes/auth.ts` — 5c and 5e both edit. 5e adds audit logging
  lines; 5c adds revocation lines. Order is 5c then 5e, so 5e's hunk should
  apply on top cleanly. If not, hand-resolve and re-run `npm test`.
- `app/infrastructure/lib/config.ts` — 5a adds SES env reads. Should not
  collide with current master.
- `app/infrastructure/lib/auth/auth-stack.ts` — 5a wires SES into the user
  pool. If `auth-stack.ts` was edited on master since the phase was authored,
  re-thread the wiring.
- `app/infrastructure/lib/waf/site-web-acl.ts` — 5f reads metric names off
  the existing WAF stack. Read the current shape before assuming the field
  layout 5f expected is still there.

### Step 3 — local verification

```sh
./Quickstart verify
```

Mirrors CI exactly. Red here = red on the PR. Specifically watch for:

- `app/backend` tests — `auth-audit.test.ts` (5e) and `AuthContext.test.tsx`
  (5c) are the two largest additions; both have to pass.
- `app/infrastructure` tests — `auth-stack.test.ts` (5a),
  `waf-alarms.test.ts` (5f).
- CDK synth (`cdk synth` via `verify`) — the WAF-alarms additions
  register new constructs at synth time. Any drift from the current
  stack shape will surface here.

### Step 4 — push and open one PR

```sh
git push -u origin feat-auth-phase5-tail
gh pr create --base master --head feat-auth-phase5-tail \
  --title "auth Phase 5 tail: 5a SES + 5c revoke + 5e audit + 5f alarms" \
  --body "$(cat <<'EOF'
## Summary
- Recovers Phase 5a/5c/5e/5f from stranded PR branches that were merged
  into the Phase 1 PR branch instead of master.
- Each commit is the original phase commit cherry-picked verbatim;
  conflicts (if any) are noted in the per-commit messages.

## Why this PR exists
PRs #3/#5/#7 were merged into `feat-auth-phase1`, not master. PR #8 was
based on `feat-auth-phase5b-waf` which had already been merged and deleted.
(Phase 5d / PR #6 already on master; not in this bundle.)

## Test plan
- [ ] `./Quickstart verify` green locally before push
- [ ] CI green
- [ ] `cdk diff` against the current prod stack shows ONLY: SES domain
      identity (5a), WAF alarms (5f)
- [ ] After deploy, `aws cognito-idp list-user-pool-clients` shows the
      refresh-token revocation flag enabled (5c)
- [ ] After deploy, trigger a sign-in and grep CloudWatch for the audit log
      record format added by 5e
EOF
)"
```

### Step 5 — clean up after merge

Once the PR lands on master:

```sh
git checkout master && git pull --ff-only

# Tear down the four stranded worktrees (5d's specodex-csp is already
# safe to remove independently — see Status section)
git worktree remove /Users/nick/github/specodex-ses
git worktree remove /Users/nick/github/specodex-revoke
git worktree remove /Users/nick/github/specodex-audit
git worktree remove /Users/nick/github/specodex-alarms

# Delete the stranded local branches (force-delete; they aren't strictly
# merged because the cherry-picks have new SHAs)
git branch -D feat-auth-phase5a-ses feat-auth-phase5c-revoke \
  feat-auth-phase5e-audit feat-auth-phase5f-alarms

# And the now-redundant origin tracking branch
git push origin --delete feat-auth-phase1
```

## Fallback option

If verify is red and the conflicts go beyond a few lines, **back out** —
abort the cherry-pick chain (`git cherry-pick --abort`), open one PR per
phase, and let reviewers stage them. The bundle is an ergonomic preference,
not a correctness requirement.

## Why not just merge `origin/feat-auth-phase1` into master?

Tempting — it already has 5a/5c/5e merged into it. But:

1. It does **not** have 5f (only on local `feat-auth-phase5f-alarms`,
   based on the deleted `feat-auth-phase5b-waf`).
2. It's missing the rust-era frontend cleanup that landed on master,
   so a back-merge would create a noisy reconciliation commit.

Cherry-picking the four focused commits is cleaner.
