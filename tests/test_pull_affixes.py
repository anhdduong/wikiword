import csv
import json

import pytest

from scripts import pull_wiktionary_affixes as pw


def api_body(titles, cont=None):
    data = {"query": {"categorymembers": [{"title": t} for t in titles]}}
    if cont:
        data["continue"] = {"cmcontinue": cont}
    return json.dumps(data).encode()


def kaikki_body(*entries):
    return "\n".join(json.dumps(e) for e in entries).encode()


MONO_ENTRY = {
    "pos": "prefix",
    "senses": [
        {"glosses": ["one; single."]},
        {"glosses": ["alone."]},
        {"glosses": ["(chemistry) containing one atom."]},
    ],
    "etymology_templates": [
        {"name": "root", "args": {"1": "en", "2": "ine-pro"}},
        {"name": "der", "args": {"1": "en", "2": "grc", "3": "μόνος"},
         "expansion": "Ancient Greek μόνος (mónos)"},
    ],
    "forms": [
        {"form": "mon-", "tags": ["alternative"]},
        {"form": "monkeys", "tags": ["plural"]},
    ],
}


def test_category_members_paginates():
    pages = [api_body(["mono-", "poly-"], cont="next"), api_body(["auto-"])]
    urls = []

    def http_get(url):
        urls.append(url)
        return 200, pages[len(urls) - 1]

    titles = pw.category_members("English prefixes", http_get)
    assert titles == ["mono-", "poly-", "auto-"]
    assert "cmcontinue=next" in urls[1]


def test_category_members_error_raises():
    with pytest.raises(RuntimeError, match="503"):
        pw.category_members("English prefixes", lambda url: (503, b""))


def test_title_filters():
    assert pw.TITLE_RE["prefix"].fullmatch("mono-")
    assert not pw.TITLE_RE["prefix"].fullmatch("Anglo-")   # segmenter is a-z
    assert not pw.TITLE_RE["prefix"].fullmatch("-ous")
    assert pw.TITLE_RE["suffix"].fullmatch("-ous")
    assert pw.TITLE_RE["suffix"].fullmatch("-o-")          # interfix shape
    assert not pw.TITLE_RE["suffix"].fullmatch("-'s")


def test_draft_row_extracts_facts():
    row = pw.draft_row("mono-", "prefix", kaikki_body(MONO_ENTRY))
    canonical, typ, origin, src, gloss, note, forms, wikt = row
    assert (canonical, typ, wikt) == ("mono-", "prefix", "mono-")
    assert origin == "Ancient Greek"
    assert src == "monos"  # romanized from the expansion's parens
    assert gloss == "one; single; alone"  # first two senses, periods stripped
    assert note == ""
    assert forms == "mono|mon"  # alternative forms only, hyphens stripped


def test_draft_row_alt_of_becomes_note():
    entry = {
        "pos": "suffix",
        "senses": [{"glosses": ["Alternative form of -ise"],
                    "alt_of": [{"word": "-ise"}]}],
    }
    row = pw.draft_row("-ize", "suffix", kaikki_body(entry))
    assert row[4] == ""  # no gloss of its own
    assert "alternative form of -ise" in row[5]


def test_draft_row_nothing_usable_is_none():
    assert pw.draft_row("-x", "suffix", b"") is None
    assert pw.draft_row("-x", "suffix", kaikki_body({"pos": "suffix",
                                                     "senses": []})) is None


def make_http(kaikki_map, api_pages):
    def http_get(url):
        if url.startswith(pw.API_BASE):
            return 200, api_pages.pop(0)
        for title, body in kaikki_map.items():
            if url == pw.kaikki_url(title):
                return body
        raise AssertionError(f"unexpected fetch: {url}")
    return http_get


def seed_csv(tmp_path, canonicals):
    p = tmp_path / "affixes.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(pw.HEADER)
        for c, t in canonicals:
            w.writerow([c, t, "Latin", "", "gloss", "", c.strip("-"), c])
    return p


def test_pull_skips_curated_and_writes_draft(tmp_path):
    seed = seed_csv(tmp_path, [("mono-", "prefix")])
    out = tmp_path / "draft.csv"
    http_get = make_http(
        {"neo-": (200, kaikki_body(MONO_ENTRY | {"senses": [
            {"glosses": ["new, recent"]}]})),
         "gone-": (404, b"")},
        api_pages=[api_body(["mono-", "neo-", "gone-", "Anglo-"])],
    )
    stats = pw.pull(["prefix"], out=out, seed_csv=seed,
                    http_get=http_get, sleep_s=0, log=lambda *a: None)
    assert stats == {"titles": 3, "known": 1, "no_kaikki": 1,
                     "no_facts": 0, "written": 1}
    rows = list(csv.DictReader(out.open()))
    assert [r["canonical"] for r in rows] == ["neo-"]
    assert rows[0]["gloss"] == "new, recent"


def test_pull_resumes_without_refetch_or_duplicate_header(tmp_path):
    seed = seed_csv(tmp_path, [])
    out = tmp_path / "draft.csv"
    http = make_http(
        {"neo-": (200, kaikki_body({"pos": "prefix",
                                    "senses": [{"glosses": ["new"]}]}))},
        api_pages=[api_body(["neo-"]), api_body(["neo-"])],
    )
    for _ in range(2):  # second run: neo- already drafted, no kaikki fetch
        pw.pull(["prefix"], out=out, seed_csv=seed, http_get=http,
                sleep_s=0, log=lambda *a: None)
    lines = out.read_text().splitlines()
    assert len(lines) == 2  # one header + one row
