"""Meaning-matching for the gatekeeper (gate.py): a small text model run on GitHub's own servers.

It turns each summary into a list of numbers that captures its meaning, so that "Israeli strike
kills 3 in Khan Younis" and "Missile attack on southern Gaza leaves three dead" come out close
although they share few words. No model calls, no limits: it runs in the update itself
(fastembed, an ONNX build of BAAI's bge-small-en-v1.5, about 70 MB, downloaded once from Hugging
Face and kept in the Actions cache).

Optional: if the package or the model can't be loaded, `similarity` returns None and the
gatekeeper finds candidates by word overlap alone (dedupe.py's TF-IDF), as before.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from common import log

MODEL = "BAAI/bge-small-en-v1.5"
MAX_TEXTS = 4000   # texts embedded a run at most (a few seconds on GitHub's servers)

_model = None
_failed = False
# Readings kept between runs (a file beside state.json), so a run reads only the texts it hasn't
# seen: reading the two days' events near the new ones took 11 to 14 seconds a run in a trial on
# 2026-10-13, nearly all of it texts read the run before.
_cache: dict[str, object] = {}
_used: set[str] = set()


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def load(path: Path) -> None:
    """Read the kept readings (none if the file is missing or unreadable)."""
    try:
        import numpy as np

        with np.load(path) as f:
            keys, vecs = f["keys"], f["vectors"].astype("float32")
        _cache.update({str(k): vecs[i] for i, k in enumerate(keys)})
    except Exception:  # noqa: BLE001 - first run, or the package missing
        pass


def save(path: Path) -> None:
    """Keep the readings this run used (the events near this run's new ones)."""
    keys = [k for k in _used if k in _cache]
    if not keys:
        return
    try:
        import numpy as np

        np.savez_compressed(path, keys=np.array(keys), vectors=np.stack([_cache[k] for k in keys]).astype("float16"))
    except Exception as exc:  # noqa: BLE001
        log(f"[gate] could not keep the meaning readings: {exc}")


def _load():
    global _model, _failed
    if _model is not None or _failed:
        return _model
    try:
        from fastembed import TextEmbedding

        _model = TextEmbedding(MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH") or None)
    except Exception as exc:  # noqa: BLE001 - missing package, blocked download: word overlap only
        _failed = True
        log(f"[gate] meaning-matching model unavailable ({str(exc)[:120]}); using word overlap only")
    return _model


def vectors(texts: list[str]) -> dict[str, list[float]] | None:
    """Unit vectors by text, or None when the model is unavailable."""
    texts = list(dict.fromkeys(t for t in texts if t))
    keys = {t: _key(t) for t in texts}
    _used.update(keys.values())
    todo = [t for t in texts if keys[t] not in _cache][:MAX_TEXTS]
    if todo and _load() is None:
        return None
    try:
        import numpy as np

        if todo:
            for t, v in zip(todo, _model.embed(todo, batch_size=64)):
                v = np.asarray(v, dtype="float32")
                _cache[keys[t]] = v / (float(np.linalg.norm(v)) or 1.0)
        return {t: _cache[keys[t]] for t in texts if keys[t] in _cache}
    except Exception as exc:  # noqa: BLE001
        log(f"[gate] meaning-matching failed: {str(exc)[:120]}")
        return None


def similarity(a: list[str], b: list[str], vecs: dict | None) -> float | None:
    """The closest pair of texts' cosine similarity, or None without vectors."""
    if not vecs:
        return None
    va, vb = [vecs[t] for t in a if t in vecs], [vecs[t] for t in b if t in vecs]
    if not va or not vb:
        return None
    return max(float(x @ y) for x in va for y in vb)
