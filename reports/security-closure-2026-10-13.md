# Security Closure and Data-Integrity Remediation

**Date:** 2026-10-13
**Scope:** credential exposure, source-corpus scrubbing, crush timestamp defect
**Production code changed:** none in DP/CSW/CFW. **One voluntary action taken on the
source corpus is staged and verified but NOT executed** — it needs your go-ahead.

---

## 1. Credential exposure — full surface mapped

Original finding: `<REDACTED-KEY-PREFIX>` in my derived artifact. Investigating the source showed it
is far wider than first reported.

| Surface | Occurrences | Unmasked |
|---|---|---|
| `traces_dataset/traces_code_only_merged.jsonl` | 20 | 15 |
| `traces_dataset/traces_crush.jsonl` | 20 | 15 |
| `traces_dataset/scrubbed/traces_crush.jsonl` | 20 | 15 |
| `traces_dataset/scrubbed/traces_unified_scrubbed.jsonl` | 20 | 15 |
| `traces_dataset/scrubbed_v02/traces_crush.jsonl` | 20 | 15 |
| `traces_dataset/buyer_preview_v05/sample_100_research_preview.jsonl` | 10 | 5 |
| `agent_trace_corpus_buyer_preview_v05.zip` (the **distribution artifact**) | 10 | **5** |
| **Total** | **110** | **80** |

Formats: one **fully unmasked 51-char base64url** live-format key (`<REDACTED-KEY-PREFIX>` + 51),
plus a masked variant `<REDACTED-KEY-PREFIX>` that the source redaction had already truncated.

**Checked and found clean:** `AI Convos`, `.crush`, `.codex`, `.claude`, `Downloads`,
`Documents`, `intel`, `reports`, `.pm2`, `.freecodex` — no `<REDACTED-KEY-PREFIX>` occurrences.
`traces_dataset` is **not** a git repository, so there is no git-history exposure there.

**Not verified, and needing your decision:** whether any *other* copy of these traces
ever left this host. The buyer-preview zip is the concerning one — it is a packaged
distribution artifact.

**Recommended treatment:** assume compromised. Revoke/rotate the key at the provider
without testing whether it is active. If it belongs to someone else, notify the owner
rather than changing their account.

## 2. Scrubber — built and verified against synthetic keys

`~/.hermes/cache/scratch/scrub_credentials.py`

Verification-first, dry-run-able, structure-preserving, idempotent. Uses **synthetic
keys generated at runtime** — no real credential material is printed, embedded, or
echoed.

```
  len=51 charset=b64url  ->  scrubbed=True   (x3 synthetic keys)
  masked variant         ->  scrubbed=True
  idempotent             ->  True
  scrubber verification: PASS
```

The first regex (`{20,}`) **failed** verification on the masked 6-char variant — the
test caught it before any data was touched. Fixed to `{4,}` so masked values are
replaced rather than waved through as "already redacted."

**Dry run, 6 files:** 110 substitutions, 10 affected records, 8,371 records parsed
cleanly, JSON structure verified intact per record.

## 3. Staged but NOT executed

The scrub is **staged and dry-run-verified but I have not modified the source corpus.**
Irreversibly rewriting 6 files of your primary data is your call, not mine.

On your confirmation I will: back up all 6 files → scrub in place → re-verify 0
occurrences remain → re-parse and confirm the 66 Stratum A episodes remain usable.

## 4. Crush timestamp defect — root-caused and regression-tested

**Confirmed:** 269/269 crush traces epoch-zero (100%); 235/235 in the code-only subset;
all other platforms clean. Also found **12 traces with non-monotonic message
timestamps**.

**Source of truth:** `~/.crush/crush.db` stores plain Unix **seconds** —
`messages.created_at = 1788035802` → `2026-08-29T20:36:42Z`.

So the merge pipeline applied a spurious epoch scaling. I could not reproduce the exact
arithmetic (the observed ratio is ~1012×, not a clean power of ten, and the corpus
contains derived sub-session ids with `$$` in them) — **the precise mechanism in
`merge_traces.py` is not fully root-caused**, and I'd rather say so than assert one.

What *is* pinned: the source format, the defect's blast radius, and the correct parsing
contract.

**`tests/test_crush_timestamp_parsing.py` — 9 tests, all passing**, including two that
read the real DB and skip cleanly when absent. Covers seconds/ms/µs **by magnitude
detection** rather than assuming one unit, ISO passthrough, epoch-zero preservation,
monotonicity, and type failures.

**Verified to catch the bug:** simulated buggy path (`raw/1e6`) → `1970-01-01`, fixed
path → `2026-08-29`. The test discriminates.

## 5. Laya qualification — target remains 0

Unchanged and now firmer. Action verbs are not StateWork identities, and **0 of 66**
episodes carry a decidable `route_preference` label:

- 26 name a routing *family* (`getter` vs `gitter` indistinguishable — 0 carry
  gitter-distinct phrasing)
- 40 are behavioural-only
- 24 have <25 words of task text

**Instrumented CSW selection is not itself ground truth** — a system can consistently
choose the wrong StateWork. Required capture: eligible StateWorks + contract versions,
selected route + reason, task constraints, transitions, obligation satisfaction, and an
**independently adjudicated** preferred route including ambiguous cases.

## 6. Status

| Item | State |
|---|---|
| Exposure surface mapped | ✅ 110 occurrences / 80 unmasked / 6 files + 1 zip |
| Scrubber built + verified | ✅ synthetic keys, structure-preserving, idempotent |
| **Source corpus scrubbed** | ⬜ **staged, awaiting your go-ahead** |
| Credential rotation | ⬜ **your action** — assume compromised |
| Crush timestamps root-caused | ⚠️ source format confirmed; exact mechanism not fully pinned |
| Timestamp regression tests | ✅ 9 passing, verified to catch the bug |
| Stratum A redacted | ✅ 1 substitution, 66 episodes intact |
| DP `tests/` + `qualification/` | **602 passed, 6 skipped** |
| Advisory status | Unchanged — qualified **No**, activation **No** |

## 7. What I need from you

1. **Rotation decision** on the `<REDACTED-KEY-PREFIX>` key — yours or the owner's.
2. **Go-ahead to scrub** the 6 source files (backup → scrub → verify → re-parse).
3. Whether the buyer-preview zip ever left this host — that determines whether this is
   contained or a disclosure.

Everything else in the qualification path is either built and tested or correctly
identified as not-yet-possible. The next real step is prospective StateWork-instrumented
capture, and that needs no further groundwork from me.
