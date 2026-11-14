from app.services.translate_worker import Bpe


def test_subword_bpe_tokenizer_round_trip(tmp_path):
    codes = tmp_path / "bpe.model"
    codes.write_text("#version: 0.2\nr o\np ro\nz p\nro z\nroz p\nrozp o\nč e\nt </w>\nče t</w>\n", encoding="utf-8")
    b = Bpe(str(codes))
    toks = b.encode("Rozpočet, schválen.")
    assert toks[-1] == "." and "," in toks and all(t.endswith("@@") for t in toks[:toks.index(",")][:-1])
    assert Bpe.decode(["The", "bud@@", "get", ",", "approved", "."]) == "The budget, approved."
    assert Bpe.decode(["(", "Prague", ")", "says", "“", "no", "”"]) == "(Prague) says “no”"
