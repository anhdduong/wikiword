"""§3.6 test cases for segment() — written before the implementation.

segment(word, lexicon, k=5) returns an ordered list of Candidate (best first).
Candidate.pieces is a tuple of Piece(start, end, surface, linker, kind,
affix_ids, free_match) tiling the word; kind is one of
prefix | root | suffix | combining_form | free | unknown.
"""

import pytest

from app.db import connect, migrate
from app.seed import seed
from app.lexicon import load_lexicon
from app.segment import segment


@pytest.fixture(scope="session")
def lex(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("db") / "test.db")
    migrate(conn)
    seed(conn)
    return load_lexicon(conn)


def surfaces(cand):
    return [p.surface for p in cand.pieces]


def kinds(cand):
    return [p.kind for p in cand.pieces]


ROOT_LIKE = {"root", "free", "unknown"}


def test_monolithic_clean_three_way(lex):
    cands = segment("monolithic", lex)
    assert surfaces(cands[0]) == ["mono", "lith", "ic"]
    assert kinds(cands[0]) == ["prefix", "root", "suffix"]


def test_chronology(lex):
    cands = segment("chronology", lex)
    assert surfaces(cands[0]) == ["chrono", "logy"]
    assert kinds(cands[0]) == ["root", "suffix"]


def test_photosynthesis_full_coverage(lex):
    cands = segment("photosynthesis", lex)
    # Top candidate must fully tile the word with known material.
    assert "unknown" not in kinds(cands[0])
    # A fully-affix-grounded reading with syn- as a mid-word prefix must
    # appear in the candidate list (photo | syn | the | sis).
    assert any(
        "unknown" not in kinds(c)
        and any(p.surface == "syn" and p.kind == "prefix" and p.start > 0
                for p in c.pieces)
        for c in cands
    )


def test_speedometer_linking_vowel(lex):
    cands = segment("speedometer", lex)
    # speedo|meter and speed·o|meter are both legitimate; the linking-vowel
    # analysis must rank at or near the top.
    assert any(
        c.pieces[0].linker == "o" and c.pieces[-1].surface == "meter"
        for c in cands[:2]
    )
    # Whichever reading wins, the top split is at position 6 with meter last.
    assert cands[0].pieces[0].end == 6
    assert cands[0].pieces[-1].surface == "meter"


def test_unionize_ambiguous_both_candidates(lex):
    cands = segment("unionize", lex)
    all_surfaces = [surfaces(c) for c in cands]
    assert surfaces(cands[0]) == ["union", "ize"]
    assert ["un", "ion", "ize"] in all_surfaces


def test_therapist_false_friend_not_first(lex):
    cands = segment("therapist", lex)
    assert surfaces(cands[0]) == ["therap", "ist"]
    all_surfaces = [surfaces(c) for c in cands]
    # the|rapist should exist as a candidate (the reranker's job to reject)
    # but must NOT rank first.
    assert ["the", "rapist"] in all_surfaces
    assert all_surfaces.index(["the", "rapist"]) > 0


def test_blackboard_two_free_roots(lex):
    cands = segment("blackboard", lex)
    assert surfaces(cands[0]) == ["black", "board"]
    assert kinds(cands[0]) == ["free", "free"]


def test_whole_word_never_a_single_free_piece(lex):
    for c in segment("blackboard", lex):
        assert surfaces(c) != ["blackboard"]


def test_strengths_degrades_gracefully(lex):
    cands = segment("strengths", lex)
    assert surfaces(cands[0]) == ["strength", "s"]
    assert kinds(cands[0]) == ["free", "unknown"]


def test_antidisestablishmentarianism_deep_stack(lex):
    cands = segment("antidisestablishmentarianism", lex)
    top = cands[0]
    assert "unknown" not in kinds(top)
    assert top.pieces[0].surface == "anti"
    assert top.pieces[-1].surface == "ism"
    # The dis- prefix and the suffix stack must be resolved.
    assert "dis" in surfaces(top)
    for suffix in ("arian", "ism"):
        assert suffix in surfaces(top)


def test_gibberish_whole_word_unknown(lex):
    cands = segment("qzxvqx", lex)
    assert len(cands) >= 1
    assert kinds(cands[0]) == ["unknown"]
    assert surfaces(cands[0]) == ["qzxvqx"]


@pytest.mark.parametrize("word", [
    "monolithic", "chronology", "photosynthesis", "unionize", "therapist",
    "blackboard", "strengths", "antidisestablishmentarianism", "qzxvqx",
])
def test_structural_invariants(lex, word):
    cands = segment(word, lex)
    assert 1 <= len(cands) <= 5
    for c in cands:
        ks = kinds(c)
        # Full tiling, in order.
        assert c.pieces[0].start == 0
        assert c.pieces[-1].end == len(word)
        for a, b in zip(c.pieces, c.pieces[1:]):
            assert a.end == b.start
        # At least one root-like piece (a word is not just affixes).
        assert any(k in ROOT_LIKE for k in ks)
        # No suffix before the first root-like piece; no prefix after the
        # last root-like piece.
        first_root = min(i for i, k in enumerate(ks) if k in ROOT_LIKE)
        last_root = max(i for i, k in enumerate(ks) if k in ROOT_LIKE)
        assert all(k != "suffix" for k in ks[:first_root])
        assert all(k not in ("prefix", "combining_form") for k in ks[last_root + 1:])
        # Unknown spans are maximal: never two adjacent unknowns.
        for a, b in zip(ks, ks[1:]):
            assert not (a == "unknown" and b == "unknown")


def test_no_redundant_candidates(lex):
    # demo|cracy and dem·o|cracy have identical boundaries and ground to the
    # same affix rows — only one may appear. (speedo|meter vs speed·o|meter
    # claim different morphemes and must both survive; see the linker test.)
    from app.segment import equivalent

    for word in ("democracy", "neuropathology", "chronology"):
        cands = segment(word, lex)
        for i, a in enumerate(cands):
            for b in cands[i + 1:]:
                assert not equivalent(a, b), (
                    f"{word}: duplicate candidates {surfaces(a)} / {surfaces(b)}"
                )


def test_deterministic(lex):
    a = segment("photosynthesis", lex)
    b = segment("photosynthesis", lex)
    assert [(surfaces(c), c.cost) for c in a] == [(surfaces(c), c.cost) for c in b]


def test_costs_are_ordered(lex):
    cands = segment("monolithic", lex)
    assert cands == sorted(cands, key=lambda c: c.cost)


def test_common_whole_word_beats_junk_split(lex):
    # Everyday monomorphemic words must not be shredded (that -> tha|t).
    # Frequency-gated: only common words earn the whole-word reading.
    for word in ("that", "know", "once"):
        cands = segment(word, lex)
        assert surfaces(cands[0]) == [word], word
        assert kinds(cands[0]) == ["free"], word


def test_phone_not_split_into_greek_root(lex):
    # phon|e grounds "phone" to Greek phone 'sound' and triggers a false
    # conflict with the retrieved etymology; the whole word must win.
    assert surfaces(segment("phone", lex)[0]) == ["phone"]


def test_compound_still_beats_whole_word(lex):
    # understand is common enough for a whole-word candidate, but the
    # two-free-root decomposition is cheaper and stays on top.
    cands = segment("understand", lex)
    assert surfaces(cands[0]) == ["under", "stand"]
    assert ["understand"] in [surfaces(c) for c in cands]


def test_rare_words_get_no_whole_candidate(lex):
    # therapist is beyond the frequency gate: it must decompose, and the
    # whole-word reading must not even be offered.
    assert ["therapist"] not in [surfaces(c) for c in segment("therapist", lex)]


def test_function_word_query_never_decomposes(lex):
    # they = the(theos 'god') + -y was beating the whole-word reading.
    for word in ("they", "that", "with", "would"):
        assert surfaces(segment(word, lex)[0]) == [word], word


# NOTE: this fixture seeds from affixes.csv, so every row is reviewed=0 and
# pays UNREVIEWED_PENALTY. The deployed database has 154 curated rows, and
# that discount changes some winners — disturbed/comfortable/receive/sexual
# decompose in production but not here. Curation state lives only in the
# database (see CLAUDE.md), so it cannot be reproduced from the repo, and
# only analyses that are stable under both states can be pinned below.
@pytest.mark.parametrize("word,expected", [
    # Audit priority 4 promotions. These pin the analyses the new rows were
    # added to produce, so a later cost-constant tweak can't quietly undo
    # them (the §3.6 constants and this table have to move together).
    ("object", ["ob", "ject"]),             # ob-
    ("opportunity", ["op", "port", "unity"]),
    ("occasion", ["oc", "cas", "ion"]),
    ("nature", ["nat", "ure"]),             # -ure
    ("signature", ["sign", "ature"]),
    ("miracle", ["mira", "cle"]),           # -cle
    ("volunteer", ["volunt", "eer"]),       # -eer
    ("condolescence", ["con", "dol", "escence"]),   # -esc-
    ("anxious", ["anx", "ious"]),           # anx
    ("circus", ["circ", "us"]),             # circ
    ("private", ["priv", "ate"]),           # priv
    ("cities", ["citi", "es"]),             # civ
    ("vulnerable", ["vulner", "able"]),     # vuln
    ("liquid", ["liquid"]),                 # liqu
    ("anonymous", ["an", "onym", "ous"]),   # onym
    ("scheme", ["schem", "e"]),             # schem
])
def test_promoted_morphemes_produce_expected_split(lex, word, expected):
    assert surfaces(segment(word, lex)[0]) == expected


@pytest.mark.parametrize("word", [
    # Forms deliberately excluded from the promotions. Each of these was a
    # regression observed when the audit's original form list was applied
    # verbatim, so they double as guards against re-adding them.
    "sunset",    # bare "et" gave suns+et
    "upset",     # bare "et" gave ups+et
    "sorry",     # bare "ry" gave sor+ry
    "story",     # bare "ry" gave sto+ry
    "three",     # "ee" gave thr+ee
    "ending",    # en- prefix gave en+ding
])
def test_excluded_forms_leave_these_words_alone(lex, word):
    top = surfaces(segment(word, lex)[0])
    assert top == [word] or len(top[0]) > 2, f"{word} -> {top}"
