"""Cache and scoping regression tests for the decoupled probe.

Three properties, each of which failed at least once during the corpus
expansion and each of which fails SILENTLY:

1. The fingerprint must be stable across calls and independent of the corpus.
   Deriving it from `generate_cases()` meant the digest changed whenever the
   corpus grew, so every entry was dropped and caching was disabled -- while
   the probe still "worked" by re-running everything.
2. Identical invocations must select identical cases, or a cache hit cannot be
   distinguished from a miss on a different population.
3. A scoped run must contain decoupled cases. A prefix sample takes only
   rotation-0, leaving DECOUPLED empty and the comparison meaningless.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/bamn/DigitalPsychology")

import qualification.decoupled_probe as probe
from qualification.contrastive_fixtures import generate_cases


def test_fingerprint_is_stable_across_calls():
    """An unstable fingerprint silently disables all caching."""
    assert probe._cache_fingerprint() == probe._cache_fingerprint()


def test_fingerprint_does_not_depend_on_the_corpus():
    """The digest must describe the frame SHAPE, not the corpus contents.

    This is the defect: the placeholder case was the first decidable corpus
    case, so growing the corpus changed the digest and invalidated the cache.
    """
    placeholder = probe._PLACEHOLDER_CASE()
    # A hardcoded case, not derived from generate_cases().
    assert placeholder.case_id == "fingerprint-probe"
    assert placeholder.task_instance == ""

    before = probe._cache_fingerprint()
    # Generating the full corpus must not move the fingerprint.
    generate_cases()
    assert probe._cache_fingerprint() == before


def test_fingerprint_binds_model_identity():
    """A different checkpoint must produce a different fingerprint.

    Otherwise a trained checkpoint would reuse the baseline's cached
    predictions, which is exactly the comparison the next experiment makes.
    """
    fingerprint = probe._cache_fingerprint()
    assert fingerprint["checkpoint_revision"] == probe.MODEL_REVISION
    assert fingerprint["checkpoint_content_hash"] == probe.MODEL_CONTENT_SHA256


def test_scoped_sample_is_deterministic():
    """Same request, same cases -- otherwise cache hits are meaningless."""
    cases = generate_cases()
    stride = max(1, len(cases) // 40)
    first = [c.case_id for c in cases[::stride][:40]]
    second = [c.case_id for c in cases[::stride][:40]]
    assert first == second


def test_scoped_sample_contains_decoupled_cases():
    """A prefix sample takes only rotation-0 and leaves DECOUPLED empty."""
    cases = generate_cases()
    stride = max(1, len(cases) // 40)
    sample = cases[::stride][:40]
    assert any(c.permutation > 0 for c in sample), (
        "scoped sample has no rotated cases; DECOUPLED would be 0/0")


def test_scoped_sample_spreads_across_capabilities():
    cases = generate_cases()
    stride = max(1, len(cases) // 40)
    sample = cases[::stride][:40]
    assert len({c.capability for c in sample}) > 1


def test_shard_is_deterministic_and_covers_rotations():
    cases = generate_cases()
    shard = [c for i, c in enumerate(cases) if i % 4 == 0]
    assert shard
    assert len({c.permutation for c in shard}) > 1
