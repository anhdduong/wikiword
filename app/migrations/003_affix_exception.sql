-- Per-word suppression of a surface form's affix reading.
--
-- The review queue's "dismiss" action only deletes the queue entry; it never
-- touches the lexicon, so a false match keeps firing on every future lookup
-- (about -> ab|out, fraud -> fr|aud). Removing the form outright is not an
-- option either: ab- is a real prefix in absent, aud is real in audible. The
-- match is only wrong for a particular word, so the exception is keyed that
-- way.
--
-- Blocking the affix reading does not stop the span matching as an ordinary
-- free word, which is usually the correct analysis: about becomes a whole
-- word again rather than losing its 'ab'.
CREATE TABLE affix_exception (
  word    TEXT NOT NULL,             -- "about"
  form    TEXT NOT NULL,             -- "ab" — blocked only inside that word
  reason  TEXT,                      -- why the match is wrong (etymology)
  PRIMARY KEY (word, form)
);
CREATE INDEX idx_affix_exception_word ON affix_exception(word);
