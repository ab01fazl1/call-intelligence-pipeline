from callintel.text.normalize import Lexicon, normalize, squash


def test_arabic_and_persian_codepoints_unify():
    assert normalize("كد ملي") == normalize("کد ملی")


def test_digits_diacritics_and_tatweel():
    assert normalize("۱۲۳ ٤٥ سـلامٌ") == "123 45 سلام"


def test_zwnj_and_spacing_variants_squash_equal():
    variants = ["می‌کنم", "میکنم", "می کنم"]
    assert len({squash(v) for v in variants}) == 1


def test_multiword_phrase_matches_across_zwnj_variants():
    lex = Lexicon("t", ("تضمین می کنم",))
    assert lex.find("من بهتون تضمین می‌کنم")
    assert lex.find("من بهتون تضمین میکنم")


def test_single_word_requires_whole_token():
    lex = Lexicon("t", ("راضی",))
    assert lex.find("خیلی راضی هستم")
    assert not lex.find("خیلی ناراضی هستم")


def test_prefix_match():
    lex = Lexicon("t", ("افتضاح*",))
    assert lex.find("واقعا افتضاحه")


def test_approx_contains():
    from callintel.text.normalize import approx_contains
    assert approx_contains("برایبرسیکودملیتون", "کدملی", 1)
    assert not approx_contains("برایبرسیحسابتون", "کدملی", 1)
    assert approx_contains("abc", "abc", 0) and not approx_contains("abd", "abc", 0)


def test_fuzzy_lexicon_tolerates_asr_misspelling():
    strict = Lexicon("t", ("کد ملی",))
    assert not strict.find("لطفن کود ملیتون رو بفرماید")
    assert strict.fuzzy(0.2).find("لطفن کود ملیتون رو بفرماید")
