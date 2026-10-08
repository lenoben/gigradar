"""Text embeddings for the semantic part of job scoring.

`Embedder` is the seam: FastEmbedder (fastembed/ONNX, optional dependency, imported lazily)
in real runs, deterministic fakes in tests. Vectors are L2-normalized, so cosine = dot product.

    python -m gigradar.embed --download [--config PATH]

downloads the configured model ([scoring].model, default BAAI/bge-small-en-v1.5, ~67 MB) once
into [scoring].model_dir (default %LOCALAPPDATA%/gigradar/models). Scheduled runs then load it
offline (local_files_only + HF_HUB_OFFLINE): no network.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

Vector = list[float]


class EmbedderError(Exception):
    """fastembed missing, model not downloaded, or the model failed to load."""


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


def preload_runtime() -> None:
    """Import onnxruntime now, before anything else loads a C++ runtime.

    windows-toasts' dependency `winrt` bundles an old MSVCP140.dll (14.29). If it is loaded before
    onnxruntime, onnxruntime's native code ends up on that old copy and the process dies with an access
    violation (0xC0000005) when the model loads. Whichever DLL loads first wins, so scoring runs call this
    before the notifier (and so winrt) is built. Reproduced: `import windows_toasts` then
    `import onnxruntime` crashes; the reverse order works."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return  # fastembed not installed: FastEmbedder reports that when scoring actually needs it


def normalize(vector: Sequence[float]) -> Vector:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return [0.0] * len(vector)
    return [x / norm for x in vector]


def cosine(a: Vector, b: Vector) -> float:
    """Dot product of two normalized vectors."""
    return sum(x * y for x, y in zip(a, b, strict=True))


class FastEmbedder:
    def __init__(self, model: str, cache_dir: Path, offline: bool) -> None:
        if offline:
            # Before fastembed/huggingface_hub are imported: they read it at import time.
            os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbedderError("fastembed is not installed: pip install -r requirements-scoring.txt") from exc
        try:
            self._model = TextEmbedding(model_name=model, cache_dir=str(cache_dir), local_files_only=offline)
        except ValueError as exc:  # unknown model, or not in cache_dir while offline
            hint = " (run: python -m gigradar.embed --download)" if offline else ""
            raise EmbedderError(f"can't load model {model} from {cache_dir}: {exc}{hint}") from exc
        self.model_id = model

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        return [normalize([float(x) for x in vector]) for vector in self._model.embed(list(texts))]


def main(argv: Sequence[str]) -> int:
    from gigradar.config import ConfigError, default_paths, load_config, load_dotenv

    parser = argparse.ArgumentParser(prog="python -m gigradar.embed", description=__doc__.split("\n")[0])
    parser.add_argument("--download", action="store_true", required=True,
                        help="download the configured model once (network), then check it loads offline")
    parser.add_argument("--config", type=Path, default=default_paths()[0], help="gigradar.toml path")
    args = parser.parse_args(argv)

    environ = dict(os.environ)
    try:
        load_dotenv(args.config.parent / ".env", environ)
        cfg = load_config(args.config, environ).scoring
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2
    print(f"model {cfg.model} -> {cfg.model_dir}")
    try:
        started = time.perf_counter()
        FastEmbedder(cfg.model, cfg.model_dir, offline=False).embed(["warm-up"])
        print(f"downloaded/verified in {time.perf_counter() - started:.1f} s")
        started = time.perf_counter()
        dim = len(FastEmbedder(cfg.model, cfg.model_dir, offline=True).embed(["offline check"])[0])
        print(f"offline load OK in {time.perf_counter() - started:.1f} s, dim {dim}")
    except EmbedderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
