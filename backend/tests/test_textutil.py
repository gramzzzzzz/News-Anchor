from app.services.textutil import canonical_url, clean_url, norm_tokens, title_key


def test_norm_tokens_handles_case_accents_possessives_and_plurals():
    assert norm_tokens("India’s Türkiye-Syria drone attacks") == ["india", "turkiye", "syria", "drone", "attack"]
    assert norm_tokens("Chess") == ["chess"] and norm_tokens("virus") == ["virus"] and norm_tokens("analysis") == ["analysis"]


def test_title_key_drops_stop_words_and_dedupes():
    k = title_key("INDIA bloc postpones 'public impeachment' of CEC to Oct. 31 due to rain, the Hindu says")
    assert "india" in k and "impeachment" in k and "the" not in k and "to" not in k
    assert len(k) == len(set(k))


def test_canonical_url_removes_tracking_but_keeps_real_params():
    a = "https://www.BBC.co.uk/news/articles/c1/?at_medium=RSS&at_campaign=rss&id=7&utm_source=x#frag"
    assert canonical_url(a) == "https://bbc.co.uk/news/articles/c1?id=7"
    assert clean_url(a) == "https://www.BBC.co.uk/news/articles/c1/?id=7"
    assert canonical_url("https://aljazeera.com/x?traffic_source=rss") == canonical_url("https://www.aljazeera.com/x")


def test_detect_lang_by_script_and_common_words():
    from app.services.textutil import detect_lang
    assert detect_lang("Binance hacked as thieves take $400 million from the exchange") == "en"
    assert detect_lang("चेन्नई में भारी बारिश, स्कूल बंद") == "hi"
    assert detect_lang("मुंबईत मुसळधार पाऊस", "mr") == "mr"                           # Devanagari shared: the publisher's language settles it
    assert detect_lang("சென்னையில் கனமழை பள்ளிகளுக்கு விடுமுறை") == "ta"
    assert detect_lang("Le gouvernement annonce une réforme des retraites pour les fonctionnaires") == "fr"
    assert detect_lang("El gobierno anuncia una reforma de las pensiones para los funcionarios") == "es"
    assert detect_lang("Die Regierung kündigt eine Reform der Renten für die Beamten an") == "de"
    assert detect_lang("Україна отримала нову допомогу") == "uk" and detect_lang("Россия и Китай подписали соглашение") == "ru"
    assert detect_lang("東京で大きな地震が発生") == "ja"
    assert detect_lang("Short", "pt") == "pt"                                          # too short to tell: the hint stands


def test_fingerprint_matches_wire_copy_not_rewrites():
    from app.services.textutil import fingerprint
    a = fingerprint("Exchange hacked, thieves take $400 million", "Hackers drained hot wallets overnight.")
    assert a == fingerprint("Exchange hacked: thieves take $400 million!", "Hackers drained hot wallets overnight")   # punctuation and case do not matter
    assert a != fingerprint("Thieves take $400 million from hacked exchange", "Hackers drained hot wallets overnight.")
    assert fingerprint("Live updates", "") is None                                      # too short to be distinctive: never shared
    boiler = "Read the full story on our website, with photos, video and more coverage from our newsroom."
    assert fingerprint("Council approves harbour plan", boiler) != fingerprint("Mayor resigns after audit report", boiler)
