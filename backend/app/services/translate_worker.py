"""Child process that translates headlines with local OPUS/Argos models (CTranslate2, int8, CPU). Exits when stdin closes, so its
memory (about 300 MB with one model loaded) goes back to the server between batches.
Protocol: one JSON object per line in ({"dir": path, "texts": [...]}), one per line out ({"out": [...]} or {"error": "..."}).

Models come with one of two tokenizers: a SentencePiece model (sentencepiece.model) or subword-nmt BPE codes (bpe.model, words split
on punctuation first, pieces joined with '@@'). Both are handled here without further packages."""
import json
import os
import re
import sys

_TOK = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]", re.U)
_NOSPACE_BEFORE = re.compile(r"\s+([.,!?;:%)\]}”’»])")
_NOSPACE_AFTER = re.compile(r"([(\[{“‘«$])\s+")


class Bpe:
    """subword-nmt BPE (codes version 0.2): merges applied in rank order within each word, '</w>' marking the word end."""

    def __init__(self, path: str):
        self.ranks: dict[tuple[str, str], int] = {}
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0 and line.startswith("#version"):
                    continue
                a = line.rstrip("\n").split(" ")
                if len(a) == 2 and tuple(a) not in self.ranks:
                    self.ranks[(a[0], a[1])] = len(self.ranks)
        self.cache: dict[str, list[str]] = {}

    def word(self, w: str) -> list[str]:
        if w in self.cache:
            return self.cache[w]
        sym = list(w[:-1]) + [w[-1] + "</w>"]
        big = 1 << 30
        while len(sym) > 1:
            r, i = min((self.ranks.get((sym[k], sym[k + 1]), big), k) for k in range(len(sym) - 1))
            if r == big:
                break
            sym = sym[:i] + [sym[i] + sym[i + 1]] + sym[i + 2:]
        out = [s[:-4] if s.endswith("</w>") else s for s in sym]
        out = [s + "@@" for s in out[:-1]] + out[-1:]
        if len(self.cache) < 50_000:
            self.cache[w] = out
        return out

    def encode(self, text: str) -> list[str]:
        return [p for t in _TOK.findall(text) for p in self.word(t)]

    @staticmethod
    def decode(tokens: list[str]) -> str:
        s = " ".join(tokens).replace("@@ ", "").replace("@@", "")
        return _NOSPACE_AFTER.sub(r"\1", _NOSPACE_BEFORE.sub(r"\1", s)).strip()


def main() -> None:
    try:
        import ctranslate2
        import sentencepiece as spm
    except Exception as e:                                           # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}), flush=True)
        sys.exit(3)
    print(json.dumps({"ready": True}), flush=True)
    loaded: dict[str, tuple] = {}
    threads = int(os.environ.get("NA_TRANSLATE_THREADS", "2"))
    for line in sys.stdin:
        try:
            req = json.loads(line)
            d = req["dir"]
            if d not in loaded:
                loaded.clear()                                       # one model at a time keeps memory flat
                tr = ctranslate2.Translator(os.path.join(d, "model"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=threads)
                if os.path.exists(os.path.join(d, "sentencepiece.model")):
                    sp = spm.SentencePieceProcessor(model_file=os.path.join(d, "sentencepiece.model"))
                    loaded[d] = (tr, lambda t, sp=sp: sp.encode(t, out_type=str), lambda toks: "".join(toks).replace("\u2581", " ").strip())
                elif os.path.exists(os.path.join(d, "bpe.model")):
                    b = Bpe(os.path.join(d, "bpe.model"))
                    loaded[d] = (tr, b.encode, Bpe.decode)
                else:
                    raise RuntimeError("the model has no tokenizer file (sentencepiece.model or bpe.model)")
            tr, enc, dec = loaded[d]
            toks = [enc(t[:600]) or ["."] for t in req["texts"]]
            res = tr.translate_batch(toks, beam_size=2, max_decoding_length=160)
            print(json.dumps({"out": [dec(r.hypotheses[0]) for r in res]}), flush=True)
        except Exception as e:                                       # noqa: BLE001
            print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}), flush=True)


if __name__ == "__main__":
    main()
