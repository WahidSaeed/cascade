"""Training script for `dl` artifacts (BUILD.md §11.3): fine-tune a small transformer for sequence
classification and export it to ONNX. Runs as a separate process: the local trainer during the build,
a SageMaker Training Job in AWS (same script, same arguments).

  python dl_train_script.py --data train.jsonl --base <model dir> --out <dir> [--epochs 6 --lr 5e-4]

train.jsonl rows: {"text": ..., "label": ...}. Writes model.onnx, tokenizer.json, meta.json, manifest.json.
"""
import argparse
import collections
import json
import os
import random
import re
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max_len", type=int, default=48)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prefixes", default="")
    a = ap.parse_args()

    import numpy as np
    import torch
    from tokenizers import BertWordPieceTokenizer
    from transformers import BertConfig, BertForSequenceClassification

    t0 = time.time()
    random.seed(a.seed)
    np.random.seed(a.seed)
    torch.manual_seed(a.seed)
    rows = [json.loads(x) for x in open(a.data) if x.strip()]
    classes = sorted({str(r["label"]) for r in rows})
    index = {c: i for i, c in enumerate(classes)}
    tok = BertWordPieceTokenizer(os.path.join(a.base, "vocab.txt"), lowercase=True)
    tok.enable_truncation(a.max_len)
    tok.enable_padding(length=a.max_len)
    enc = tok.encode_batch([r["text"] for r in rows])
    ids = torch.tensor([e.ids for e in enc])
    mask = torch.tensor([e.attention_mask for e in enc])
    types = torch.tensor([e.type_ids for e in enc])
    y = torch.tensor([index[str(r["label"])] for r in rows])

    cfg = BertConfig.from_pretrained(a.base, num_labels=len(classes))
    model = BertForSequenceClassification.from_pretrained(a.base, config=cfg)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)
    n = len(rows)
    model.train()
    losses = []
    for _ in range(a.epochs):
        perm = torch.randperm(n)
        for i in range(0, n, a.batch):
            b = perm[i:i + a.batch]
            out = model(input_ids=ids[b], attention_mask=mask[b], token_type_ids=types[b], labels=y[b])
            opt.zero_grad()
            out.loss.backward()
            opt.step()
            losses.append(float(out.loss))
    model.eval()
    os.makedirs(a.out, exist_ok=True)
    torch.onnx.export(model, (ids[:1], mask[:1], types[:1]), os.path.join(a.out, "model.onnx"),
                      input_names=["input_ids", "attention_mask", "token_type_ids"], output_names=["logits"],
                      dynamic_axes={k: {0: "batch"} for k in ("input_ids", "attention_mask", "token_type_ids", "logits")},
                      opset_version=17, dynamo=False)
    tok.save(os.path.join(a.out, "tokenizer.json"))
    words = collections.Counter(w for r in rows for w in re.findall(r"[a-z']+", r["text"].lower()))
    meta = {"kind": "dl", "classes": classes, "max_len": a.max_len, "input": "quoted", "min_words": 3,
            "prefixes": [p for p in a.prefixes.split("|") if p], "guard_vocab": [w for w, c in words.items() if c >= 2],
            "base_model": os.path.basename(a.base.rstrip("/")), "epochs": a.epochs, "lr": a.lr,
            "train_size": n, "final_loss": round(sum(losses[-10:]) / max(1, len(losses[-10:])), 4),
            "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(a.out, "meta.json"), "w") as fh:
        json.dump(meta, fh)
    with open(os.path.join(a.out, "manifest.json"), "w") as fh:
        json.dump({"kind": "dl"}, fh)
    print(json.dumps({"status": "Completed", "seconds": meta["seconds"], "final_loss": meta["final_loss"]}))


if __name__ == "__main__":
    main()
