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
    # Originally the plural 's' had nowhere to go and came back as an
    # unmatched span. The -s row (audit 1a) gives it one, so the same split
    # now resolves fully — the graceful degradation this covered is only
    # reachable for genuinely unknown material.
    cands = segment("strengths", lex)
    assert surfaces(cands[0]) == ["strength", "s"]
    assert kinds(cands[0]) == ["free", "suffix"]


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


# affixes.csv carries the `reviewed` column, so this fixture reproduces the
# deployed lexicon's curation state — and therefore its costs, since
# reviewed rows skip UNREVIEWED_PENALTY. Analyses that depend on curation
# (disturbed, comfortable, receive, sexual) are pinnable because of it.
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
    ("disturbed", ["dis", "turb", "ed"]),   # turb
    ("anxious", ["anx", "ious"]),           # anx
    ("circus", ["circ", "us"]),             # circ
    ("private", ["priv", "ate"]),           # priv
    ("cities", ["citi", "es"]),             # civ
    ("vulnerable", ["vulner", "able"]),     # vuln
    ("liquid", ["liquid"]),                 # liqu
    ("comfortable", ["com", "fort", "able"]),   # fort
    ("anonymous", ["an", "onym", "ous"]),   # onym
    ("scheme", ["schem", "e"]),             # schem
    ("receive", ["re", "ceive"]),           # cap extension
    ("sexual", ["sex", "ual"]),             # -al extension
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


# --- per-word affix exceptions (audit priority 3) ---------------------------
# The review queue's "dismiss" only deletes the queue entry, so a false match
# kept firing forever. Removing the form is not an option either: ab- is real
# in absent, aud is real in audible. The block is therefore per word.

@pytest.mark.parametrize("word,blocked", [
    ("about", "ab"),        # Old English onbutan
    ("fraud", "aud"),       # fraus/fraud-, unrelated to audire
    ("path", "path"),       # Old English paeth, not Greek pathos
    ("person", "per"),      # persona, unanalyzable
    ("really", "re"),       # real + -ly
    ("laptop", "top"),      # Old English topp, not Greek topos
    ("cemetery", "meter"),  # koimeterion, not Greek metron
    ("diary", "dia"),       # Latin dies, not Greek dia-
    ("heaven", "ven"),      # Old English heofon, not Latin venire
    ("stopped", "ped"),     # the -ed suffix, not Latin ped- 'foot'
])
def test_exception_suppresses_the_false_affix_reading(lex, word, blocked):
    top = segment(word, lex)[0]
    for p in top.pieces:
        if p.surface == blocked:
            assert p.kind in ("free", "unknown"), (
                f"{word}: {blocked!r} still read as {p.kind}"
            )


def test_exception_is_scoped_to_its_word(lex):
    # Blocking a form must not disable it everywhere: the same morphemes
    # still analyse other words.
    assert "ab" in surfaces(segment("absent", lex)[0])
    aud = next(p for p in segment("audible", lex)[0].pieces if p.surface == "aud")
    assert aud.kind == "root"
    top = next(p for p in segment("topic", lex)[0].pieces if p.surface == "top")
    assert top.kind == "root"


def test_blocked_span_can_still_match_as_a_free_word(lex):
    # laptop keeps both pieces — 'top' simply stops claiming Greek topos.
    pieces = {p.surface: p.kind for p in segment("laptop", lex)[0].pieces}
    assert pieces == {"lap": "free", "top": "free"}


def test_exception_restores_the_whole_word_reading(lex):
    # With the spurious prefix gone, common words stop decomposing at all.
    for word in ("about", "again", "away", "after", "never", "okay", "until"):
        assert surfaces(segment(word, lex)[0]) == [word], word


# --- native inflectional morphemes (audit priority 1a) ----------------------

@pytest.mark.parametrize("word,expected", [
    ("windows", ["window", "s"]),
    ("parents", ["parent", "s"]),
    ("knocked", ["knock", "ed"]),
    ("looked", ["look", "ed"]),
    ("moving", ["mov", "ing"]),
    ("talking", ["talk", "ing"]),
    ("wishes", ["wish", "es"]),
    ("duties", ["dut", "ies"]),
    ("fifty", ["fif", "ty"]),
    ("fourth", ["four", "th"]),
])
def test_inflection_is_split_off(lex, word, expected):
    assert surfaces(segment(word, lex)[0]) == expected


@pytest.mark.parametrize("word", [
    # Words that merely *end* in an inflectional string. Splitting them would
    # assert a plural/past/ordinal that isn't there, so each is blocked by a
    # per-word exception.
    "loss", "less", "mess", "dress", "possible",
    "south", "smooth", "teeth", "wealth",
    "shed", "succeed", "perhaps",
])
def test_false_inflection_is_blocked(lex, word):
    assert surfaces(segment(word, lex)[0]) == [word], word


@pytest.mark.parametrize("word", [
    # The 1-letter allomorphs of -ed (d, t) and 2-letter -est (st) are
    # deliberately absent: they shredded these everyday words.
    "said", "find", "told", "wait", "went", "kind", "mind", "dead",
    "first", "last", "best", "night",
])
def test_excluded_inflection_allomorphs_leave_words_whole(lex, word):
    assert surfaces(segment(word, lex)[0]) == [word], word


# --- re-spans (audit priority 5) --------------------------------------------
# The queue surfaced these at the wrong boundary (clu for clus, occa for cas,
# aggr for gress). A root row carrying the correct allomorphs re-cuts them.

@pytest.mark.parametrize("word,expected", [
    ("aggressive", ["ag", "gress", "ive"]),
    ("exclusive", ["ex", "clus", "ive"]),
    ("include", ["in", "clude"]),
    ("recognise", ["re", "cogn", "ise"]),
    ("practical", ["pract", "ic", "al"]),
    ("magnificent", ["magnif", "ic", "ent"]),
    ("appropriate", ["ap", "propri", "ate"]),
    ("incident", ["in", "cid", "ent"]),
    ("insult", ["in", "sult"]),
    ("assume", ["as", "sume"]),
    ("create", ["cre", "ate"]),
    ("national", ["nat", "ion", "al"]),
    ("served", ["serv", "ed"]),
    ("plague", ["plag", "ue"]),
])
def test_respan_produces_the_correct_boundary(lex, word, expected):
    assert surfaces(segment(word, lex)[0]) == expected


@pytest.mark.parametrize("word", [
    # Words that merely look like they contain one of the new roots. Each is
    # a different etymon, so an exception keeps the false claim out.
    "greet",    # Old English gretan, not gradi
    "creep",    # Old English creopan, not creare
    "salad",    # Latin sal 'salt', not salire 'to leap'
    "dive",     # Old English dyfan, not dividere
])
def test_respan_roots_do_not_overreach(lex, word):
    top = segment(word, lex)[0]
    for p in top.pieces:
        assert p.kind in ("free", "unknown", "suffix"), f"{word} -> {surfaces(top)}"


# --- the -ia suffix (held-back two-letter approvals) -------------------------
# -ia is the one short form from that batch worth curating: it names places
# and diseases. The others (ad-'s ac/af/al/ap/ar/at, de-, ex-/ef-, in-'s
# im/il/ir) stay at reviewed=0 — approving them re-cut hundreds of words for
# the worse (arrested -> ar+rested, images -> im+ages), which the penalty
# correctly suppresses.

@pytest.mark.parametrize("word,expected", [
    ("pneumonia", ["pneu", "mon", "ia"]),
    ("hysteria", ["hys", "ter", "ia"]),
    ("syria", ["syr", "ia"]),
    ("india", ["ind", "ia"]),
    ("olympia", ["olymp", "ia"]),
    ("homophobia", ["homo", "phob", "ia"]),
    ("pizzeria", ["pizzer", "ia"]),
])
def test_ia_suffix_splits_place_and_disease_names(lex, word, expected):
    assert surfaces(segment(word, lex)[0]) == expected


@pytest.mark.parametrize("word", [
    # -ia must not strand a residue too short to be a morpheme. These are
    # blocked per-word rather than by dropping the form.
    "via", "cia", "mia", "gaia", "shia", "croatia", "columbia",
])
def test_ia_never_strands_a_short_residue(lex, word):
    assert not any(
        p.surface == "ia" for p in segment(word, lex)[0].pieces
    ), word
