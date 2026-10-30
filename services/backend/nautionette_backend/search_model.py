"""Pinned, CPU-only model artifacts. No conversation text leaves this process."""

from .search_index import MODEL, MODEL_REVISION


def load_encoder(cache_dir: str, *, local_files_only: bool = False):
    from fastembed import TextEmbedding
    from huggingface_hub import snapshot_download

    model_path = snapshot_download(
        repo_id="Qdrant/bge-small-en-v1.5-onnx-Q",
        revision=MODEL_REVISION,
        cache_dir=cache_dir,
        local_files_only=local_files_only,
        token=False,
        allow_patterns=["*.json", "*.onnx", "vocab.txt"],
    )
    return TextEmbedding(
        model_name=MODEL,
        cache_dir=cache_dir,
        specific_model_path=model_path,
        local_files_only=True,
        threads=1,
        providers=["CPUExecutionProvider"],
    )
