"""
classificar_sinal_haiku.py — Classifica cada materia em AUTORIZA/VEDA/CONDICIONA/INDETERMINADO via Anthropic Batch API.

Substitui a heuristica keyword de detectar_conflitos.py:classificar() por
classificacao semantica robusta. O Haiku 4.5 batch sai ~$8 para as 41k materias.

Workflow:
    1. py classificar_sinal_haiku.py prepare --output ../batches/sinal_v1.jsonl
    2. py classificar_sinal_haiku.py submit ../batches/sinal_v1.jsonl
    3. py classificar_sinal_haiku.py status <batch_id>     (poll ate 'ended')
    4. py classificar_sinal_haiku.py apply <batch_id>

Pre-requisito: migration 008_sinal_materia.sql aplicada (coluna ato_materia.sinal).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from loguru import logger
from psycopg.rows import dict_row

from db import get_conn
from lib.sinal import SISTEMA_PROMPT, montar_user_content, parse_sinal

DEFAULT_MODEL = "claude-haiku-4-5"


def system_blocks() -> list[dict]:
    return [
        {
            "type": "text",
            "text": SISTEMA_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
    ]


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def cmd_prepare(args):
    where = ["m.solucao IS NOT NULL"]
    if not args.reclassificar:
        where.append("m.sinal IS NULL")
    params: list[Any] = []
    if args.tema:
        where.append("m.tema_especifico = %s")
        params.append(args.tema)
    if args.so_vinculantes:
        where.append("a.eficacia LIKE 'vinculante%%'")

    sql = f"""
        SELECT m.id, m.tema_especifico, m.natureza,
               m.ementa_trecho, m.fato_consultado, m.solucao
        FROM ato_materia m
        JOIN atos a ON a.id = m.ato_id
        WHERE {' AND '.join(where)}
        ORDER BY m.id
    """
    if args.limit:
        sql += f" LIMIT {args.limit}"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    total_user_chars = 0
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            with output.open("w", encoding="utf-8") as f:
                sys_blocks = system_blocks()
                for m in cur:
                    user_msg = montar_user_content(m)
                    req = {
                        "custom_id": f"materia_{m['id']}",
                        "params": {
                            "model": args.model,
                            "max_tokens": args.max_tokens,
                            "system": sys_blocks,
                            "messages": [{"role": "user", "content": user_msg}],
                        },
                    }
                    f.write(json.dumps(req, ensure_ascii=False) + "\n")
                    n += 1
                    total_user_chars += len(user_msg)

    logger.success(f"{n} requests gravados em {output}")
    logger.info(f"  variavel total: {total_user_chars:,} chars (~{total_user_chars//4:,} tokens)")
    media = (total_user_chars // n) if n else 0
    logger.info(f"  media por request: {media} chars (~{media//4} tokens)")
    logger.info(f"  proximo passo: py classificar_sinal_haiku.py submit {output}")


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

def cmd_submit(args):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("[erro] ANTHROPIC_API_KEY nao definido em .env")

    client = Anthropic()
    requests_list = []
    with open(args.jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            requests_list.append(Request(
                custom_id=d["custom_id"],
                params=MessageCreateParamsNonStreaming(**d["params"]),
            ))

    logger.info(f"Submetendo {len(requests_list)} requests...")
    batch = client.messages.batches.create(requests=requests_list)
    logger.success(f"batch_id: {batch.id}")
    logger.info(f"status: {batch.processing_status}")

    Path(f"{args.jsonl}.batch_id").write_text(batch.id)
    logger.info(f"  proximo passo: py classificar_sinal_haiku.py status {batch.id}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    client = Anthropic()
    batch = client.messages.batches.retrieve(args.batch_id)
    rc = batch.request_counts
    print(f"batch_id:     {batch.id}")
    print(f"status:       {batch.processing_status}")
    print(f"created_at:   {batch.created_at}")
    print(f"ended_at:     {batch.ended_at}")
    print(f"counts:")
    print(f"  processing:  {rc.processing}")
    print(f"  succeeded:   {rc.succeeded}")
    print(f"  errored:     {rc.errored}")
    print(f"  canceled:    {rc.canceled}")
    print(f"  expired:     {rc.expired}")
    if batch.processing_status == "ended":
        print(f"\n>> proximo passo: py classificar_sinal_haiku.py apply {batch.id}")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def cmd_apply(args):
    client = Anthropic()
    out_path = Path(args.results_out or f"results_sinal_{args.batch_id}.jsonl")

    ok, fail, parse_err, db_err = 0, 0, 0, 0
    contagem = {"AUTORIZA": 0, "VEDA": 0, "CONDICIONA": 0, "INDETERMINADO": 0}

    with get_conn() as conn:
        with out_path.open("w", encoding="utf-8") as out_f:
            for result in client.messages.batches.results(args.batch_id):
                custom_id = result.custom_id
                materia_id = int(custom_id.replace("materia_", ""))

                if result.result.type != "succeeded":
                    fail += 1
                    out_f.write(json.dumps({
                        "custom_id": custom_id,
                        "type": result.result.type,
                    }, ensure_ascii=False) + "\n")
                    continue

                msg = result.result.message
                text = ""
                for block in msg.content:
                    if block.type == "text":
                        text += block.text

                sinal = parse_sinal(text)
                modelo = msg.model

                out_f.write(json.dumps({
                    "custom_id": custom_id,
                    "raw": text[:200],
                    "sinal": sinal,
                }, ensure_ascii=False) + "\n")

                if sinal is None:
                    parse_err += 1
                    logger.warning(f"{custom_id}: nao classificavel -> raw={text[:120]!r}")
                    continue

                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE ato_materia
                            SET sinal = %s,
                                sinal_classified_at = now(),
                                sinal_model = %s
                            WHERE id = %s
                            """,
                            (sinal, modelo, materia_id),
                        )
                    conn.commit()
                    ok += 1
                    contagem[sinal] += 1
                except Exception as e:
                    db_err += 1
                    logger.error(f"{custom_id}: erro DB: {e}")
                    conn.rollback()

    logger.success(f"FIM: {ok} ok | {fail} batch failed | {parse_err} parse err | {db_err} db err")
    logger.info(f"distribuicao: {contagem}")
    logger.info(f"raw: {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="Gera JSONL de requests pra batch")
    p.add_argument("--output", required=True, help="caminho do .jsonl de saida")
    p.add_argument("--tema", help="filtra por tema_especifico")
    p.add_argument("--so-vinculantes", dest="so_vinculantes", action="store_true",
                   help="so atos com eficacia like 'vinculante%%'")
    p.add_argument("--reclassificar", action="store_true",
                   help="inclui materias ja classificadas (default: pula sinal IS NOT NULL)")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"default {DEFAULT_MODEL}")
    p.add_argument("--max-tokens", dest="max_tokens", type=int, default=20,
                   help="max_tokens por request (default 20; classificacao retorna 1 palavra)")

    p = sub.add_parser("submit", help="Submete batch JSONL")
    p.add_argument("jsonl")

    p = sub.add_parser("status", help="Checa status de um batch")
    p.add_argument("batch_id")

    p = sub.add_parser("apply", help="Busca resultados e atualiza ato_materia.sinal")
    p.add_argument("batch_id")
    p.add_argument("--results-out", help="caminho pra salvar raw results (.jsonl)")

    args = parser.parse_args()
    {"prepare": cmd_prepare, "submit": cmd_submit,
     "status": cmd_status, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
