"""reenfileirar_sem_texto.py — F1.7 da recoleta: devolve à fila os atos "analisados" sem texto.

Diagnóstico de 13/09/2026: 912 atos com `analise_completa = true` e `content_disponivel = false`,
**nenhum com matéria** — não há análise a preservar; a marca veio de uma baixa avulsa. Aqui eles
voltam para a fila (`analise_completa = false`), com antes/depois em `ato_mudanca` e uma nota em
`ato_coleta`. Recusa se algum desses atos tiver matéria: aí existe análise real e a decisão é
manual.

Depois de aplicar, o admin valida o invariante da migration 010:
    ALTER TABLE rfb_atos.ato VALIDATE CONSTRAINT ato_analise_exige_conteudo;

Uso:
    python reenfileirar_sem_texto.py            # plano (lê como ratio_leitura)
    python reenfileirar_sem_texto.py --aplicar  # grava como rfb_writer
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import psycopg

SQL_ALVOS = ("SELECT id FROM rfb_atos.ato WHERE analise_completa AND NOT content_disponivel "
             "ORDER BY id")
NOTA = "reenfileirado: analise_completa sem texto (diagnóstico de 13/09/2026)"


def alvos(conn) -> list[int]:
    return [r[0] for r in conn.execute(SQL_ALVOS).fetchall()]


def com_materia(conn, ids: list[int]) -> int:
    return conn.execute("SELECT count(DISTINCT ato_id) FROM rfb_atos.ato_materia "
                        "WHERE ato_id = ANY(%s)", (ids,)).fetchone()[0]


def aplicar(conn, ids: list[int], run_id: str) -> int:
    """Devolve os atos à fila numa transação; retorna quantos mudaram."""
    if not ids:
        return 0
    if com_materia(conn, ids):
        raise RuntimeError("há matéria em ato a reenfileirar — análise real; decidir à mão")
    with conn.transaction():
        # ::text explícito: em SELECT e em concat_ws (variádico "any") o Postgres não deduz o tipo
        conn.execute(
            "INSERT INTO rfb_atos.ato_mudanca (run_id, ato_id, tabela, campo, antes, depois) "
            "SELECT %s::text, id, 'ato', 'analise_completa', 'true'::jsonb, 'false'::jsonb "
            "FROM rfb_atos.ato WHERE id = ANY(%s) AND analise_completa AND NOT content_disponivel",
            (run_id, ids))
        n = conn.execute(
            "UPDATE rfb_atos.ato SET analise_completa = false, atualizado_em = now() "
            "WHERE id = ANY(%s) AND analise_completa AND NOT content_disponivel",
            (ids,)).rowcount
        conn.execute(
            "UPDATE rfb_atos.ato_coleta SET origem = 'reenfileirar_sem_texto', "
            "observacao = concat_ws(' | ', observacao, %s::text) WHERE ato_id = ANY(%s)",
            (NOTA, ids))
    return n


def _dsn(escrita: bool) -> str:
    if os.environ.get("RFB_ATOS_DSN"):
        return os.environ["RFB_ATOS_DSN"]
    from credenciais import CredencialAusente, resolver_dsn
    try:
        return resolver_dsn("escrita" if escrita else "leitura")
    except CredencialAusente as e:
        sys.exit(f"[erro] {e}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--run-id")
    args = p.parse_args()
    run_id = args.run_id or f"reenfileirar-{time.strftime('%Y%m%dT%H%M%S')}"
    with psycopg.connect(_dsn(args.aplicar)) as conn:
        ids = alvos(conn)
        n_mat = com_materia(conn, ids) if ids else 0
        print(f"atos com analise_completa sem texto: {len(ids)} (com matéria: {n_mat})")
        if not args.aplicar:
            print("(dry-run: nada gravado. Use --aplicar para gravar como rfb_writer.)")
            return
        n = aplicar(conn, ids, run_id)
        print(f"[ok] run {run_id}: {n} atos de volta à fila. Agora o admin roda:\n"
              "  ALTER TABLE rfb_atos.ato VALIDATE CONSTRAINT ato_analise_exige_conteudo;")


if __name__ == "__main__":
    main()
