# Qualification-Target Audit — Can Stratum A Label Laya's `route_preference`?

**Date:** 2026-10-13
**Answer: largely no.** Of 66 episodes, **0** support an independently defensible
`route_preference` label as the benchmark defines it. 26 support a routing *family*,
40 support behavioural labels only. Plus two data defects found and one handled.

---

## 1. The core problem — action verbs are not StateWork identities

Stratum A's vocabulary is `observe`/`mutate`/`verify`/`recover`/`plan` — **action
categories**. Laya's `route_preference` selects a **StateWork identity** (`getter`,
`gitter`, `infrae`), each with its own transition contract and required evidence.

These are different questions:

| Question | Answerable from Stratum A? |
|---|---|
| What should the agent **do** next (observe vs mutate)? | Yes — action sequence is recorded |
| Which **StateWork should govern** this task? | Mostly no — StateWork identity is not recorded |

A human could correctly label an episode "should have verified" without establishing
whether `getter` or `gitter` should have been active. That produces valid CFW/CSW
behavioural evidence and **invalid** `route_preference` ground truth.

## 2. Audit result — 66 episodes

| Verdict | Count | Share |
|---|---|---|
| `both` (StateWork + behaviour) | **26** | 39% |
| `behavior` only | **40** | 61% |
| `statework` only | 0 | 0% |
| `neither` | 0 | 0% |

Support required: at least one route verb in the sequence (behaviour), **and** exactly
one StateWork signature in the task text (StateWork).

Also: **24 of 66 episodes have fewer than 25 words of task text** — too short to reason
about workflow selection at all.

## 3. The 26 are weaker than they look

`getter` and `gitter` **share the repository domain**. Both are repository-scoped; they
differ only by transition contract. A term match on "repository"/"git"/"commit" cannot
separate them.

Measured directly:

| Signal | Episodes |
|---|---|
| repository/git terms (getter-family) | 26 |
| **repository-truth phrasing (gitter-distinct)** | **0** |
| infrastructure terms (infrae) | 6 |

**Zero episodes carry gitter-distinguishing phrasing.** So the 26 name a *family*, not
a choice between eligible StateWorks. Since `route_preference` is precisely a choice
among eligible candidates, **0 of 66 are usable as-is**.

3 episodes matched *multiple* signatures — genuinely ambiguous.

**Conclusion: the honest count of `route_preference`-labellable episodes in Stratum A
is zero.** Labelling all 66 would have produced 66 labels of which none could support
the benchmark claim.

## 4. Data defect — crush timestamps are all epoch-zero

Every episode reports `1970-01-…`. Verified at source: `created_at` on the crush traces
is literally `'1970-01-21T08:22:22.987000Z'`.

**Not my extractor's bug — it is in the source corpus.** Blast radius:

| platform | epoch-zero |
|---|---|
| crush | **235 / 235 (100%)** |
| claude, claude_code, claude_openrouter, codex, gemini_tmp, qwen_code, zcode | 0 |

A deterministic defect in `merge_traces.py`'s crush ingestion path. Sequence-relative
analysis (what followed what) is unaffected, but chronology, ordering across traces,
and any temporal alignment are invalid until fixed.

## 5. Security — credential-shaped value, now redacted

Found `<REDACTED-KEY-PREFIX>` in `qualification/stratum_a_candidates.jsonl`, traced to source
`traces_code_only_merged.jsonl` where it appears **20 times across two distinct
values**:

- **15 occurrences of a fully unmasked 51-char base64url value** (charset-valid, no
  ellipsis) — in a live credential format
- 5 occurrences of a masked variant (`<REDACTED-KEY-PREFIX>`)

The source's redaction pipeline did **not** catch it. The value sits next to
`https://anyrouter.dev/api/v1` in task text, i.e. it looks like a real API key pasted
into a session.

**Actions taken:**
- Backed up the artifact privately, then **redacted in place** — `<REDACTED-KEY-PREFIX>` → `<REDACTED_API_KEY>`, 1 substitution, **66 episodes still parse intact**. The episode was preserved, not deleted.
- Confirmed 0 credential-shaped strings remain in my artifact.
- Did **not** modify the source corpus.

**Not done, and needing your decision:** the source value is still live in
`~/traces_dataset/`. I have not tested whether it is active. If it is a real key for
anyrouter.dev it should be **rotated**, and the source corpus needs re-scrubbing before
any further use. I'd treat that as urgent and separate from this audit.

## 6. Revised recommendation

**Do not label the 66 for `route_preference`.** They cannot support it.

What the 66 *are* good for, unchanged: behavioural research — recovery-after-error,
repeated-retry detection, obligation satisfaction. That's where the 70.4% repeat rate
and the verification-poverty finding live.

What would actually produce `route_preference` ground truth:

1. **Prospective capture** — instrument a live session where CSW actually activates a
   StateWork, so the *identity* is recorded rather than inferred from prose. This is
   the only reliable source, and it is what the original plan called for.
2. **A task corpus authored against StateWork contracts** — cases where the task
   context genuinely determines `getter` vs `gitter` vs `infrae`, the way
   `qualification/route_fixtures.py` already does structurally.
3. **More FI weeks** for detector calibration — still useful, still cheap (~22 MB/week).

## 7. Status

| Item | Value |
|---|---|
| Stratum A episodes | 66, redacted, preserved |
| `route_preference`-labellable | **0** |
| Behaviourally labellable | 66 |
| StateWork-family hint | 26 |
| Data defects found | crush epoch-zero timestamps (235/235) |
| Security | credential redacted in my artifact; **source still needs rotation** |
| Production code changed | **none** |
| Advisory status | Unchanged — statistically qualified **No**, activation **No** |

**The blocking issue is no longer "we need labels." It is "these records do not contain
the information the label would assert."** That is a more fundamental gap, and it points
at prospective StateWork-instrumented capture rather than at mining historical traces.
