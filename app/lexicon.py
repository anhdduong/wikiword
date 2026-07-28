"""In-memory lexicon for segment(): affix surface forms + free-word list.

Free words come from a frequency-ranked list (seed/en_50k.txt, one
"word count" pair per line, most frequent first), filtered to alphabetic
words of length >= MIN_FREE_LEN. Rank doubles as the false-friend signal.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

WORDLIST_PATH = Path(__file__).parent.parent / "seed" / "en_50k.txt"
MIN_FREE_LEN = 3

# Function words that must never be treated as a serious root candidate
# (the classic therapist -> the + rapist trap). Only length >= MIN_FREE_LEN
# entries matter, but keep the full set for clarity.
FUNCTION_WORDS = frozenset(
    "the a an and or but nor of in on at to as is are was were be been by for "
    "with it its this that these those not no so if then than he she we you "
    "they me my his her our your their them do did does done have has had "
    "will would can could shall should may might must".split()
)


@dataclass(frozen=True)
class FormEntry:
    affix_id: int
    canonical: str
    kind: str  # prefix | root | suffix | combining_form
    reviewed: bool


@dataclass(frozen=True)
class Lexicon:
    forms: dict[str, tuple[FormEntry, ...]]
    free_words: frozenset[str]
    freq_rank: dict[str, int]  # 0 = most frequent
    max_form_len: int
    # word -> forms whose affix reading is wrong *in that word only*
    # (about/ab, fraud/aud). The form keeps working everywhere else, and the
    # span can still match as a free word.
    exceptions: dict[str, frozenset[str]]

    def blocked(self, word: str, form: str) -> bool:
        return form in self.exceptions.get(word, frozenset())


def load_lexicon(
    conn: sqlite3.Connection,
    wordlist_path: Path = WORDLIST_PATH,
    min_free_len: int = MIN_FREE_LEN,
) -> Lexicon:
    forms: dict[str, list[FormEntry]] = {}
    for row in conn.execute(
        "SELECT f.form, a.id, a.canonical, a.type, a.reviewed"
        " FROM affix_form f JOIN affix a ON a.id = f.affix_id"
    ):
        forms.setdefault(row["form"], []).append(
            FormEntry(row["id"], row["canonical"], row["type"], bool(row["reviewed"]))
        )

    free_words: set[str] = set()
    freq_rank: dict[str, int] = {}
    with open(wordlist_path, encoding="utf-8") as f:
        for rank, line in enumerate(f):
            word = line.split()[0]
            if len(word) >= min_free_len and word.isalpha():
                free_words.add(word)
                freq_rank.setdefault(word, rank)

    exceptions: dict[str, set[str]] = {}
    try:
        for row in conn.execute("SELECT word, form FROM affix_exception"):
            exceptions.setdefault(row["word"], set()).add(row["form"])
    except sqlite3.OperationalError:
        pass  # database predates migration 003

    return Lexicon(
        forms={k: tuple(v) for k, v in forms.items()},
        free_words=frozenset(free_words),
        freq_rank=freq_rank,
        max_form_len=max(map(len, forms), default=0),
        exceptions={k: frozenset(v) for k, v in exceptions.items()},
    )
