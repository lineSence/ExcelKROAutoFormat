"""Экспорт легкой модели эмбеддингов в ONNX int8.

Скрипт запускается один раз и желательно не на самом VPS: для конвертации
нужны torch и transformers, а на 1 ГБ памяти это тяжело. На сервер достаточно
перенести два файла из папки models/:

    models/rubert-tiny2-int8.onnx
    models/rubert-tiny2-tokenizer.json

Запуск:

    pip install -r requirements-ml.txt torch transformers onnx onnxruntime
    python scripts/export_embed_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_NAME = "cointegrated/rubert-tiny2"
OUT_DIR = Path("models")
ONNX_PATH = OUT_DIR / "rubert-tiny2.onnx"
INT8_PATH = OUT_DIR / "rubert-tiny2-int8.onnx"
TOKENIZER_PATH = OUT_DIR / "rubert-tiny2-tokenizer.json"


def main() -> int:
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError:
        print("Нужны torch и transformers: pip install torch transformers")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.eval()

    # Токенизатор сохраняем одним файлом: на сервере его читает пакет tokenizers.
    tokenizer.backend_tokenizer.save(str(TOKENIZER_PATH))

    sample = tokenizer("сигареты пример", return_tensors="pt")
    inputs = (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"])

    torch.onnx.export(
        model,
        inputs,
        str(ONNX_PATH),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "tokens"},
            "attention_mask": {0: "batch", 1: "tokens"},
            "token_type_ids": {0: "batch", 1: "tokens"},
            "last_hidden_state": {0: "batch", 1: "tokens"},
        },
        opset_version=14,
        do_constant_folding=True,
    )
    print(f"ONNX сохранён: {ONNX_PATH}")

    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError:
        print("onnxruntime.quantization не найден, int8 пропущен")
        return 0

    quantize_dynamic(
        model_input=str(ONNX_PATH),
        model_output=str(INT8_PATH),
        weight_type=QuantType.QInt8,
    )
    ONNX_PATH.unlink(missing_ok=True)
    size_mb = INT8_PATH.stat().st_size / (1024 * 1024)
    print(f"int8 сохранён: {INT8_PATH} ({size_mb:.1f} МБ)")
    print("Дальше: EMBED_ENABLED=true и переобучите модель на /training")
    return 0


if __name__ == "__main__":
    sys.exit(main())
