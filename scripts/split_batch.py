"""split_batch.py — divide JSONL em chunks <= 240MB (limite Anthropic = 256MB).

Uso: py split_batch.py <input.jsonl> [--max-mb 240]
Gera <input>.part01.jsonl, .part02.jsonl ...
"""
from __future__ import annotations
import argparse
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--max-mb", type=int, default=240)
    args = ap.parse_args()
    src = Path(args.input)
    max_bytes = args.max_mb * 1024 * 1024
    part = 1
    out_path = src.with_suffix(f".part{part:02d}.jsonl")
    out = out_path.open("w", encoding="utf-8")
    cur_size = 0
    n_total = 0
    n_part = 0
    with src.open(encoding="utf-8") as f:
        for line in f:
            blen = len(line.encode("utf-8"))
            if cur_size + blen > max_bytes and n_part > 0:
                out.close()
                print(f"  {out_path.name}: {n_part} requests, {cur_size/1024/1024:.1f} MB")
                part += 1
                out_path = src.with_suffix(f".part{part:02d}.jsonl")
                out = out_path.open("w", encoding="utf-8")
                cur_size = 0
                n_part = 0
            out.write(line)
            cur_size += blen
            n_part += 1
            n_total += 1
    out.close()
    print(f"  {out_path.name}: {n_part} requests, {cur_size/1024/1024:.1f} MB")
    print(f"TOTAL: {n_total} requests em {part} partes")

if __name__ == "__main__":
    main()
