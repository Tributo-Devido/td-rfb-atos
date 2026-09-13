"""
backfill_normalize.py — Aplica normalize.normalizar_*() em registros ja gravados.

Use quando:
  - normalizacao_taxonomia.json eh atualizado com novos aliases
  - novos campos sao adicionados ao normalize.py
  - quer auditar e corrigir variantes que escaparam

Uso:
    py backfill_normalize.py --dry-run   # apenas reporta o que mudaria
    py backfill_normalize.py             # aplica alteracoes
"""
from __future__ import annotations

import argparse
from collections import Counter
from loguru import logger

from db import get_conn
from normalize import (
    normalizar_tributo, normalizar_tema_macro, normalizar_tema_especifico,
    normalizar_regime, normalizar_tipo_norma, normalizar_tipo_uso,
    normalizar_tipo_relacao, normalizar_natureza, normalizar_resultado,
    decompor_tributo_composto,
)


def backfill_tributos(conn, dry_run: bool):
    """Renormaliza materia_tributo.tributo_codigo + regime."""
    changes = Counter()
    deletes = 0
    composite_splits = 0
    with conn.cursor() as cur:
        cur.execute("""
          SELECT mt.materia_id, mt.tributo_codigo, mt.regime
          FROM materia_tributo mt
        """)
        rows = cur.fetchall()

    with conn.cursor() as cur:
        for materia_id, codigo, regime in rows:
            new_codigo = normalizar_tributo(codigo)
            new_regime = normalizar_regime(regime) if regime else regime
            if new_codigo and (new_codigo != codigo or new_regime != regime):
                changes[(codigo, new_codigo)] += 1
                if not dry_run:
                    cur.execute("""
                      UPDATE materia_tributo SET tributo_codigo=%s, regime=%s
                      WHERE materia_id=%s AND tributo_codigo=%s
                    """, (new_codigo, new_regime, materia_id, codigo))
            elif not new_codigo:
                # Tenta decompor (PIS/COFINS -> PIS + COFINS)
                decomposto = decompor_tributo_composto(codigo)
                if decomposto:
                    composite_splits += 1
                    if not dry_run:
                        cur.execute("DELETE FROM materia_tributo WHERE materia_id=%s AND tributo_codigo=%s",
                                    (materia_id, codigo))
                        for c in decomposto:
                            cur.execute("""
                              INSERT INTO materia_tributo (materia_id, tributo_codigo, regime)
                              VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                            """, (materia_id, c, new_regime))
                else:
                    deletes += 1
                    if not dry_run:
                        cur.execute("DELETE FROM materia_tributo WHERE materia_id=%s AND tributo_codigo=%s",
                                    (materia_id, codigo))
        if not dry_run:
            conn.commit()

    logger.info(f"materia_tributo: {sum(changes.values())} normalizados, {composite_splits} decompostos, {deletes} deletados")
    if changes:
        for (old, new), n in changes.most_common(20):
            logger.info(f"  {n:>4}x  {old!r:25} -> {new!r}")


def backfill_temas(conn, dry_run: bool):
    """Renormaliza ato_materia.tema_macro + tema_especifico."""
    changes_macro = Counter()
    changes_esp = Counter()
    with conn.cursor() as cur:
        cur.execute("SELECT id, tema_macro, tema_especifico FROM ato_materia")
        rows = cur.fetchall()
    with conn.cursor() as cur:
        for mid, tm, te in rows:
            new_tm = normalizar_tema_macro(tm) or tm
            new_te = normalizar_tema_especifico(te, new_tm) or te
            if new_tm != tm or new_te != te:
                if new_tm != tm: changes_macro[(tm, new_tm)] += 1
                if new_te != te: changes_esp[(te, new_te)] += 1
                if not dry_run:
                    cur.execute("UPDATE ato_materia SET tema_macro=%s, tema_especifico=%s WHERE id=%s",
                                (new_tm, new_te, mid))
        if not dry_run:
            conn.commit()
    logger.info(f"ato_materia.tema_macro: {sum(changes_macro.values())} normalizados")
    for (old, new), n in changes_macro.most_common(10):
        logger.info(f"  {n:>4}x  {old!r:30} -> {new!r}")
    logger.info(f"ato_materia.tema_especifico: {sum(changes_esp.values())} normalizados")
    for (old, new), n in changes_esp.most_common(10):
        logger.info(f"  {n:>4}x  {old!r:50} -> {new!r}")


def backfill_dispositivos(conn, dry_run: bool):
    changes_norma = Counter()
    changes_uso = Counter()
    with conn.cursor() as cur:
        cur.execute("SELECT id, tipo_norma, tipo_uso FROM materia_dispositivo")
        rows = cur.fetchall()
    with conn.cursor() as cur:
        for did, tn, tu in rows:
            new_tn = normalizar_tipo_norma(tn) or tn
            new_tu = normalizar_tipo_uso(tu) or tu
            if new_tn != tn or new_tu != tu:
                if new_tn != tn: changes_norma[(tn, new_tn)] += 1
                if new_tu != tu: changes_uso[(tu, new_tu)] += 1
                if not dry_run:
                    cur.execute("UPDATE materia_dispositivo SET tipo_norma=%s, tipo_uso=%s WHERE id=%s",
                                (new_tn, new_tu, did))
        if not dry_run:
            conn.commit()
    logger.info(f"materia_dispositivo.tipo_norma: {sum(changes_norma.values())} normalizados")
    for (old, new), n in changes_norma.most_common(10):
        logger.info(f"  {n:>4}x  {old!r:35} -> {new!r}")
    logger.info(f"materia_dispositivo.tipo_uso: {sum(changes_uso.values())} normalizados")
    for (old, new), n in changes_uso.most_common(10):
        logger.info(f"  {n:>4}x  {old!r:30} -> {new!r}")


def backfill_relacoes(conn, dry_run: bool):
    changes = Counter()
    with conn.cursor() as cur:
        cur.execute("SELECT id, tipo_relacao FROM ato_relacao")
        rows = cur.fetchall()
    with conn.cursor() as cur:
        for rid, tr in rows:
            new_tr = normalizar_tipo_relacao(tr) or tr
            if new_tr != tr:
                changes[(tr, new_tr)] += 1
                if not dry_run:
                    cur.execute("UPDATE ato_relacao SET tipo_relacao=%s WHERE id=%s", (new_tr, rid))
        if not dry_run:
            conn.commit()
    logger.info(f"ato_relacao.tipo_relacao: {sum(changes.values())} normalizados")
    for (old, new), n in changes.most_common(10):
        logger.info(f"  {n:>4}x  {old!r:30} -> {new!r}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.dry_run:
        logger.info("=== DRY RUN — nenhuma alteracao sera aplicada ===")
    with get_conn() as conn:
        backfill_tributos(conn, args.dry_run)
        backfill_temas(conn, args.dry_run)
        backfill_dispositivos(conn, args.dry_run)
        backfill_relacoes(conn, args.dry_run)
    logger.success("FIM backfill")


if __name__ == "__main__":
    main()
