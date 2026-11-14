"""Runs in a child process so the model's memory is returned to the system when it exits.
Protocol: one JSON object per line on stdin ({"texts": [...]}); one JSON line back ({"vectors": [[...]]} or {"error": "..."}).
Start-up prints {"ready": true} (or {"error": ...}). Exits when stdin closes."""
import json
import os
import sys


def main() -> None:
    try:
        from fastembed import TextEmbedding
        model = TextEmbedding(os.environ.get("NA_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
                              cache_dir=os.environ.get("NA_MODEL_DIR") or None, threads=int(os.environ.get("NA_EMBED_THREADS", "2")))
        list(model.embed(["warm up"]))
    except Exception as e:                                           # noqa: BLE001 - reported to the parent, which falls back
        print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}), flush=True)
        sys.exit(3)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            texts = json.loads(line)["texts"]
            vecs = [[round(float(x), 5) for x in v] for v in model.embed(texts, batch_size=64)]
            print(json.dumps({"vectors": vecs}), flush=True)
        except Exception as e:                                       # noqa: BLE001
            print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}), flush=True)


if __name__ == "__main__":
    main()
