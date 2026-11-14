import sys

import pytest

from app.services import embedder as E


class Settings:
    def __init__(self, mode):
        self.mode = mode

    def get(self, key):
        return self.mode


async def test_hashed_mode_and_off_mode(tmp_path):
    e = E.Embedder(Settings("hashed"), str(tmp_path))
    v, be = await e.embed(["Floods hit Chennai", "Floods hit Chennai suburbs"])
    assert be == "hashed" and len(v) == 2 and len(v[0]) == 384
    assert await E.Embedder(Settings("off"), str(tmp_path)).embed(["x"]) == ([], "off")


async def test_worker_protocol_survives_large_replies(tmp_path, monkeypatch):
    """A fake model worker that answers like the real one: a ready line, then one JSON line of vectors per request.
    Replies are far bigger than asyncio's default 64 KiB line limit, which broke the first real run."""
    worker = tmp_path / "fake_worker.py"
    worker.write_text('import json, sys\nprint(json.dumps({"ready": True}), flush=True)\n'
                      'for line in sys.stdin:\n    n = len(json.loads(line)["texts"])\n    print(json.dumps({"vectors": [[0.12345] * 384 for _ in range(n)]}), flush=True)\n')
    real = E.asyncio.create_subprocess_exec

    async def fake(*args, **kw):
        return await real(sys.executable, str(worker), **{k: v for k, v in kw.items()})

    monkeypatch.setattr(E.asyncio, "create_subprocess_exec", fake)
    e = E.Embedder(Settings("model"), str(tmp_path), idle_seconds=1)
    v, be = await e.embed([f"headline {i}" for i in range(300)])
    assert be == "model" and len(v) == 300 and len(v[0]) == 384 and e.loaded
    await e.close()
    assert not e.loaded


async def test_model_failure_falls_back_in_auto_mode_but_raises_in_model_mode(tmp_path, monkeypatch):
    worker = tmp_path / "bad_worker.py"
    worker.write_text('import json, sys\nprint(json.dumps({"error": "no network"}), flush=True)\nsys.exit(3)\n')
    real = E.asyncio.create_subprocess_exec

    async def fake(*args, **kw):
        return await real(sys.executable, str(worker), **kw)

    monkeypatch.setattr(E.asyncio, "create_subprocess_exec", fake)
    auto = E.Embedder(Settings("auto"), str(tmp_path))
    v, be = await auto.embed(["a headline"])
    assert be == "hashed" and "no network" in auto.last_error and await auto.backend() == "hashed"       # remembered for an hour
    strict = E.Embedder(Settings("model"), str(tmp_path))
    with pytest.raises(RuntimeError):
        await strict.embed(["a headline"])
