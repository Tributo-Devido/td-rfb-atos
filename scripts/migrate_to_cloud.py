"""
migrate_to_cloud.py — ETL Docker local → ratio.rfb_atos (nuvem)

Track A.2 da migração ratio-juris (2026-05-11).

Move tudo de td_rfb_atos.public.* (Docker porta 5435) para ratio.rfb_atos.*
(SSM tunnel localhost:15432), preservando IDs e relações.

Ordem topológica (respeita FKs):
    taxonomias → atos → ato_content → ato_segmento → ato_materia
                → materia_tributo/cnae/dispositivo
                → ato_relacao → conflito_temporal
                → ato_alteracao_historico
                → ato_chunks (vazio no Docker — SKIP)

Idempotente:
    - INSERT ... ON CONFLICT DO NOTHING para taxonomias (por código natural).
    - INSERT com OVERRIDING SYSTEM VALUE pra preservar IDs do Docker.
    - ON CONFLICT (id) DO NOTHING — pula linhas já migradas.
    - Ajusta sequences ao final pra MAX(id)+1.

NÃO popula ato_materia.embedding aqui (vem em A.3 via OpenAI).

Uso:
    python migrate_to_cloud.py            # roda tudo
    python migrate_to_cloud.py --tables atos,ato_materia  # subset
    python migrate_to_cloud.py --truncate # zera nuvem antes (perigoso!)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import psycopg
from psycopg.types.json import Jsonb

# ----------------------------------------------------------------------
# DSNs
# ----------------------------------------------------------------------
DOCKER_DSN = "postgresql://td:td@localhost:5435/td_rfb_atos"

CLOUD_DSN_PATH = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")
if not CLOUD_DSN_PATH.exists():
    sys.exit(f"[erro] DSN nuvem não encontrado: {CLOUD_DSN_PATH}")
CLOUD_DSN = CLOUD_DSN_PATH.read_text(encoding="utf-8").strip()

BATCH = 2000  # tamanho do batch por COPY/INSERT


# ----------------------------------------------------------------------
# Helpers de log
# ----------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cloud_count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM rfb_atos.{table}")
    return cur.fetchone()[0]


def docker_count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


# ----------------------------------------------------------------------
# Migração por tabela
# ----------------------------------------------------------------------
def stream_table(src_cur, sql: str, batch_size: int = BATCH):
    """Itera resultset em chunks, evitando carregar tudo em RAM."""
    src_cur.execute(sql)
    while True:
        rows = src_cur.fetchmany(batch_size)
        if not rows:
            return
        yield rows


def migrate_taxonomia_tipo_ato(src, dst):
    log("->taxonomia_tipo_ato")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT codigo, nome, natureza, eficacia_default, ativo
            FROM taxonomia_tipo_ato
        """)
        rows = s.fetchall()
        # Map para schema da cloud: codigo, nome, descricao, vinculante, hierarquia
        # natureza Docker → infer vinculante (consultiva→true, normativa→true, operacional→true; tudo true por default conservador)
        # Não temos descricao no Docker — usa NULL
        # hierarquia não temos — NULL
        data = [(codigo, nome, None, True, None) for (codigo, nome, _nat, _ef, _ativo) in rows]
        d.executemany(
            """
            INSERT INTO rfb_atos.taxonomia_tipo_ato (codigo, nome, descricao, vinculante, hierarquia)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (codigo) DO NOTHING
            """,
            data,
        )
        dst.commit()
        log(f"  taxonomia_tipo_ato: {len(rows)} linhas (UPSERT)")


def migrate_taxonomia_tributo(src, dst):
    log("->taxonomia_tributo")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("SELECT codigo, nome FROM taxonomia_tributo")
        rows = s.fetchall()
        # cloud schema: codigo, nome, esfera (federal/estadual/municipal), criado_em
        # default esfera = federal (RFB)
        data = [(codigo, nome, "federal") for (codigo, nome) in rows]
        d.executemany(
            """
            INSERT INTO rfb_atos.taxonomia_tributo (codigo, nome, esfera)
            VALUES (%s, %s, %s)
            ON CONFLICT (codigo) DO NOTHING
            """,
            data,
        )
        dst.commit()
        log(f"  taxonomia_tributo: {len(rows)} linhas (UPSERT)")


def migrate_taxonomia_setor(src, dst):
    log("->taxonomia_setor")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("SELECT codigo, nome FROM taxonomia_setor_economico")
        rows = s.fetchall()
        d.executemany(
            """
            INSERT INTO rfb_atos.taxonomia_setor (codigo, nome)
            VALUES (%s, %s)
            ON CONFLICT (codigo) DO NOTHING
            """,
            rows,
        )
        dst.commit()
        log(f"  taxonomia_setor: {len(rows)} linhas (UPSERT)")


def migrate_taxonomia_tema(src, dst):
    log("->taxonomia_tema (junção tema_macro + tema_especifico)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT te.codigo AS especifico_codigo,
                   te.tema_macro_codigo,
                   tm.descricao AS macro_desc,
                   te.descricao AS especifico_desc
              FROM taxonomia_tema_especifico te
              LEFT JOIN taxonomia_tema_macro tm ON te.tema_macro_codigo = tm.codigo
        """)
        rows = s.fetchall()
        # cloud schema: codigo, tema_macro, tema_especifico, descricao
        # codigo natural = especifico_codigo (UNIQUE no especifico)
        data = [
            (esp_cod, macro_cod or "DESCONHECIDO", esp_cod, esp_desc or macro_desc)
            for (esp_cod, macro_cod, macro_desc, esp_desc) in rows
        ]
        d.executemany(
            """
            INSERT INTO rfb_atos.taxonomia_tema (codigo, tema_macro, tema_especifico, descricao)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (codigo) DO NOTHING
            """,
            data,
        )
        dst.commit()
        log(f"  taxonomia_tema: {len(rows)} linhas (UPSERT)")


def migrate_atos(src, dst):
    log("->atos (99.868)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT id, tipo_ato, numero, orgao_emissor, data_publicacao,
                   eficacia, status_vigencia, vigencia_inicio, vigencia_fim,
                   ementa, link, pdf_disponivel, content_disponivel,
                   analise_completa, metadata, fonte_origem, ato_legacy_id,
                   legacy_table, created_at, updated_at, id_portal
              FROM atos
              ORDER BY id
        """)
        total = 0
        batch = []
        for row in s:
            (id_, tipo_ato, numero, orgao, data_pub, eficacia, status_vig,
             vig_ini, vig_fim, ementa, link, pdf_disp, cont_disp, analise,
             metadata, fonte, legacy_id, legacy_tbl, created, updated, id_portal) = row
            ano = data_pub.year if data_pub else None
            batch.append((
                id_, tipo_ato, numero, ano, orgao, data_pub, vig_ini, vig_fim,
                eficacia,      # eficacia_atual (cloud) ← eficacia (docker)
                ementa, None,  # url_pdf — não temos URL canônico, deixar NULL
                link,          # url_html
                Jsonb(metadata) if metadata is not None else None,
                "docker_td_rfb_atos",  # importado_de
                # criado_em e atualizado_em: deixar defaults
                # campos novos da 006:
                status_vig, link, pdf_disp, cont_disp, analise,
                fonte, legacy_id, legacy_tbl, id_portal,
            ))
            if len(batch) >= BATCH:
                _flush_atos(d, batch)
                total += len(batch)
                if total % 10000 == 0:
                    log(f"  atos: {total}/99868")
                batch = []
        if batch:
            _flush_atos(d, batch)
            total += len(batch)
        dst.commit()
        log(f"  atos: {total} linhas inseridas")


def _flush_atos(cur, batch):
    cur.executemany(
        """
        INSERT INTO rfb_atos.ato (
            id, tipo_ato, numero, ano, emissor, data_publicacao,
            data_vigencia_inicio, data_vigencia_fim, eficacia_atual,
            ementa, url_pdf, url_html, metadados, importado_de,
            status_vigencia, link, pdf_disponivel, content_disponivel,
            analise_completa, fonte_origem, ato_legacy_id, legacy_table,
            id_portal
        )
        OVERRIDING SYSTEM VALUE
        VALUES (%s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s, %s,%s,%s, %s)
        ON CONFLICT DO NOTHING
        """,
        batch,
    )


def migrate_ato_content(src, dst):
    log("->ato_content (98.926)")
    with src.cursor() as s, dst.cursor() as d:
        # Docker: PK composto (ato_id, tipo_versao). Pegar só 'vigente' (default) — se houver duplicatas, ON CONFLICT (ato_id) DO NOTHING garante 1:1.
        s.execute("""
            SELECT ato_id, content, fonte_extracao, caracteres, paginas,
                   processed_at, tipo_versao, id_arquivo_binario, pdf_path
              FROM ato_content
              ORDER BY ato_id, (CASE WHEN tipo_versao='vigente' THEN 0 ELSE 1 END)
        """)
        total = 0
        batch = []
        for row in s:
            (ato_id, content, fonte_ext, carac, paginas, proc_at,
             tipo_ver, id_bin, pdf_path) = row
            batch.append((
                ato_id, content, fonte_ext or "docker",
                fonte_ext, carac, paginas, tipo_ver, id_bin, pdf_path,
            ))
            if len(batch) >= BATCH:
                _flush_content(d, batch)
                total += len(batch)
                if total % 20000 == 0:
                    log(f"  ato_content: {total}")
                batch = []
        if batch:
            _flush_content(d, batch)
            total += len(batch)
        dst.commit()
        log(f"  ato_content: {total} linhas tentadas (UPSERT por ato_id)")


def _flush_content(cur, batch):
    # ato_content tem UNIQUE (ato_id) na cloud — manter só 'vigente' (1:1)
    cur.executemany(
        """
        INSERT INTO rfb_atos.ato_content (
            ato_id, texto_completo, origem_extracao,
            fonte_extracao, caracteres, paginas, tipo_versao,
            id_arquivo_binario, pdf_path
        )
        VALUES (%s,%s,%s, %s,%s,%s,%s, %s,%s)
        ON CONFLICT (ato_id) DO NOTHING
        """,
        batch,
    )


def migrate_ato_segmento(src, dst):
    log("->ato_segmento (888.132) — esse é o grande!")
    # Server-side cursor (named) limita RAM no Docker; commit periódico no cloud
    # protege contra SSL drop por idle timeout no tunnel SSM.
    SEG_BATCH = 2000
    COMMIT_EVERY = 20000  # commit ratio cloud a cada 20k linhas
    with src.cursor(name="cur_seg_v2") as s, dst.cursor() as d:
        s.itersize = SEG_BATCH
        s.execute("""
            SELECT ato_id, id_segmento, versao_segmento, ordem,
                   id_tipo_segmento, id_assunto, texto_integra,
                   is_original, is_compilado, is_tachado, is_omitido,
                   is_agendado, raw, created_at
              FROM ato_segmento
              ORDER BY ato_id, id_segmento, versao_segmento
        """)
        total = 0
        since_commit = 0
        batch = []
        t0 = time.time()
        while True:
            rows = s.fetchmany(SEG_BATCH)
            if not rows:
                break
            for row in rows:
                row_list = list(row)
                row_list[12] = Jsonb(row_list[12]) if row_list[12] is not None else None
                batch.append(tuple(row_list))
            _flush_segmento(d, batch)
            total += len(batch)
            since_commit += len(batch)
            batch = []
            if since_commit >= COMMIT_EVERY:
                dst.commit()
                since_commit = 0
            if total % 50000 == 0 or total % 50000 < SEG_BATCH:
                rate = total / (time.time() - t0)
                log(f"  ato_segmento: {total}/888132 ({rate:.0f} rows/s)")
        dst.commit()
        log(f"  ato_segmento: {total} linhas")


def _flush_segmento(cur, batch):
    cur.executemany(
        """
        INSERT INTO rfb_atos.ato_segmento (
            ato_id, id_segmento, versao_segmento, ordem,
            id_tipo_segmento, id_assunto, texto_integra,
            is_original, is_compilado, is_tachado, is_omitido,
            is_agendado, raw, criado_em
        )
        VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
        ON CONFLICT (ato_id, id_segmento, versao_segmento) DO NOTHING
        """,
        batch,
    )


def migrate_ato_materia(src, dst):
    log("->ato_materia (41.164)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT id, ato_id, ordem, natureza, tema_macro, tema_especifico,
                   subtema, tags, ementa_trecho, tese_contribuinte,
                   tese_fazenda, tese_adotada, resultado, fato_consultado,
                   solucao, fundamentacao_resumo, metadata_tematico,
                   embedding_source, embedded_at, llm_model, llm_processed_at,
                   schema_version, sinal, sinal_classified_at, sinal_model
              FROM ato_materia
              ORDER BY id
        """)
        total = 0
        batch = []
        for row in s:
            # row[16] = metadata_tematico (jsonb)
            row_list = list(row)
            row_list[16] = Jsonb(row_list[16]) if row_list[16] is not None else None
            batch.append(tuple(row_list))
            if len(batch) >= BATCH:
                _flush_materia(d, batch)
                total += len(batch)
                if total % 10000 == 0:
                    log(f"  ato_materia: {total}/41164")
                batch = []
        if batch:
            _flush_materia(d, batch)
            total += len(batch)
        dst.commit()

        # Ajusta sequence
        d.execute("SELECT setval('rfb_atos.ato_materia_id_seq', (SELECT MAX(id) FROM rfb_atos.ato_materia))")
        dst.commit()
        log(f"  ato_materia: {total} linhas + sequence atualizada")


def _flush_materia(cur, batch):
    cur.executemany(
        """
        INSERT INTO rfb_atos.ato_materia (
            id, ato_id, ordem, natureza, tema_macro, tema_especifico,
            subtema, tags, ementa_trecho, tese_contribuinte,
            tese_fazenda, tese_adotada, resultado, fato_consultado,
            solucao, fundamentacao_resumo, metadata_tematico,
            embedding_source, embedded_at, llm_model, llm_processed_at,
            schema_version, sinal, sinal_classified_at, sinal_model
        )
        OVERRIDING SYSTEM VALUE
        VALUES (%s,%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,
                %s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s)
        ON CONFLICT (id) DO NOTHING
        """,
        batch,
    )


def migrate_materia_tributo(src, dst):
    log("->materia_tributo (32.481)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT materia_id, tributo_codigo, regime, codigo_receita
              FROM materia_tributo
        """)
        total = 0
        batch = []
        for row in s:
            batch.append(row)
            if len(batch) >= BATCH:
                d.executemany(
                    """
                    INSERT INTO rfb_atos.materia_tributo
                        (materia_id, tributo_codigo, regime, codigo_receita)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (materia_id, tributo_codigo, COALESCE(regime, 'NULL_REGIME')) DO NOTHING
                    """,
                    batch,
                )
                total += len(batch)
                batch = []
        if batch:
            d.executemany(
                """
                INSERT INTO rfb_atos.materia_tributo
                    (materia_id, tributo_codigo, regime, codigo_receita)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (materia_id, tributo_codigo, COALESCE(regime, 'NULL_REGIME')) DO NOTHING
                """,
                batch,
            )
            total += len(batch)
        dst.commit()
        log(f"  materia_tributo: {total}")


def migrate_materia_cnae(src, dst):
    log("->materia_cnae (12.477)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT materia_id, cnae_codigo, descricao, relevancia, confianca
              FROM materia_cnae
        """)
        rows = s.fetchall()
        d.executemany(
            """
            INSERT INTO rfb_atos.materia_cnae
                (materia_id, cnae_codigo, descricao, relevancia, confianca)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (materia_id, cnae_codigo) DO NOTHING
            """,
            rows,
        )
        dst.commit()
        log(f"  materia_cnae: {len(rows)}")


def migrate_materia_dispositivo(src, dst):
    log("->materia_dispositivo (142.812)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT id, materia_id, tipo_norma, referencia,
                   dispositivo, texto_resumido, tipo_uso
              FROM materia_dispositivo
              ORDER BY id
        """)
        total = 0
        batch = []
        for row in s:
            batch.append(row)
            if len(batch) >= BATCH:
                d.executemany(
                    """
                    INSERT INTO rfb_atos.materia_dispositivo
                        (id, materia_id, tipo_norma, referencia,
                         dispositivo, texto_resumido, tipo_uso)
                    OVERRIDING SYSTEM VALUE
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    batch,
                )
                total += len(batch)
                if total % 50000 == 0:
                    log(f"  materia_dispositivo: {total}/142812")
                batch = []
        if batch:
            d.executemany(
                """
                INSERT INTO rfb_atos.materia_dispositivo
                    (id, materia_id, tipo_norma, referencia,
                     dispositivo, texto_resumido, tipo_uso)
                OVERRIDING SYSTEM VALUE
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                batch,
            )
            total += len(batch)
        dst.commit()
        d.execute("SELECT setval('rfb_atos.materia_dispositivo_id_seq', (SELECT MAX(id) FROM rfb_atos.materia_dispositivo))")
        dst.commit()
        log(f"  materia_dispositivo: {total} + sequence atualizada")


def migrate_ato_relacao(src, dst):
    log("->ato_relacao (14.351)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT id, ato_origem_id, ato_destino_id, tipo_relacao,
                   data_relacao, parcial, observacao, fonte, created_at,
                   data_efeito, dispositivo_afetado
              FROM ato_relacao
              ORDER BY id
        """)
        rows = s.fetchall()
        d.executemany(
            """
            INSERT INTO rfb_atos.ato_relacao (
                id, ato_origem_id, ato_destino_id, tipo_relacao,
                data_relacao, parcial, observacao, fonte, criado_em,
                data_efeito, dispositivo_afetado
            )
            OVERRIDING SYSTEM VALUE
            VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            rows,
        )
        dst.commit()
        d.execute("SELECT setval('rfb_atos.ato_relacao_id_seq', (SELECT MAX(id) FROM rfb_atos.ato_relacao))")
        dst.commit()
        log(f"  ato_relacao: {len(rows)} + sequence atualizada")


def migrate_conflito_temporal(src, dst):
    log("->conflito_temporal (74.578)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT id, tema_especifico, materia_a_id, materia_b_id,
                   similaridade, sinal_a, sinal_b, data_a, data_b,
                   ato_b_supera, formal_relacao_existe, observacao, created_at
              FROM conflito_temporal
              ORDER BY id
        """)
        total = 0
        batch = []
        for row in s:
            batch.append(row)
            if len(batch) >= BATCH:
                d.executemany(
                    """
                    INSERT INTO rfb_atos.conflito_temporal (
                        id, tema_especifico, materia_a_id, materia_b_id,
                        similaridade, sinal_a, sinal_b, data_a, data_b,
                        ato_b_supera, formal_relacao_existe, observacao, criado_em
                    )
                    OVERRIDING SYSTEM VALUE
                    VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    batch,
                )
                total += len(batch)
                if total % 20000 == 0:
                    log(f"  conflito_temporal: {total}/74578")
                batch = []
        if batch:
            d.executemany(
                """
                INSERT INTO rfb_atos.conflito_temporal (
                    id, tema_especifico, materia_a_id, materia_b_id,
                    similaridade, sinal_a, sinal_b, data_a, data_b,
                    ato_b_supera, formal_relacao_existe, observacao, criado_em
                )
                OVERRIDING SYSTEM VALUE
                VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                batch,
            )
            total += len(batch)
        dst.commit()
        d.execute("SELECT setval('rfb_atos.conflito_temporal_id_seq', (SELECT MAX(id) FROM rfb_atos.conflito_temporal))")
        dst.commit()
        log(f"  conflito_temporal: {total} + sequence atualizada")


def migrate_alteracao_historico(src, dst):
    log("->ato_alteracao_historico (4.946)")
    with src.cursor() as s, dst.cursor() as d:
        s.execute("""
            SELECT id, ato_alvo_id, id_segmento_alvo, ato_modificador_id,
                   id_ato_modificador_portal, data_inicio_vigencia,
                   data_republicacao, texto_anotacao, raw_anotacao_id,
                   eh_agendamento, texto_agendamento, created_at
              FROM ato_alteracao_historico
              ORDER BY id
        """)
        rows = s.fetchall()
        d.executemany(
            """
            INSERT INTO rfb_atos.ato_alteracao_historico (
                id, ato_alvo_id, id_segmento_alvo, ato_modificador_id,
                id_ato_modificador_portal, data_inicio_vigencia,
                data_republicacao, texto_anotacao, raw_anotacao_id,
                eh_agendamento, texto_agendamento, criado_em
            )
            OVERRIDING SYSTEM VALUE
            VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            rows,
        )
        dst.commit()
        d.execute("SELECT setval('rfb_atos.ato_alteracao_historico_id_seq', (SELECT MAX(id) FROM rfb_atos.ato_alteracao_historico))")
        dst.commit()
        log(f"  ato_alteracao_historico: {len(rows)} + sequence atualizada")


def ajusta_sequence_ato(dst):
    log("->ajustando sequence rfb_atos.ato_id_seq")
    with dst.cursor() as d:
        d.execute("SELECT setval('rfb_atos.ato_id_seq', (SELECT MAX(id) FROM rfb_atos.ato))")
        dst.commit()


# ----------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------
TASKS = [
    ("taxonomia_tipo_ato", migrate_taxonomia_tipo_ato),
    ("taxonomia_tributo",  migrate_taxonomia_tributo),
    ("taxonomia_setor",    migrate_taxonomia_setor),
    ("taxonomia_tema",     migrate_taxonomia_tema),
    ("atos",               migrate_atos),
    ("ato_content",        migrate_ato_content),
    ("ato_segmento",       migrate_ato_segmento),
    ("ato_materia",        migrate_ato_materia),
    ("materia_tributo",    migrate_materia_tributo),
    ("materia_cnae",       migrate_materia_cnae),
    ("materia_dispositivo", migrate_materia_dispositivo),
    ("ato_relacao",        migrate_ato_relacao),
    ("conflito_temporal",  migrate_conflito_temporal),
    ("ato_alteracao_historico", migrate_alteracao_historico),
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--tables", help="lista CSV de tabelas (default: todas)")
    p.add_argument("--truncate", action="store_true", help="TRUNCATE rfb_atos antes (perigoso)")
    return p.parse_args()


def main():
    args = parse_args()
    selected = None
    if args.tables:
        selected = set(args.tables.split(","))

    log(f"Conectando Docker: {DOCKER_DSN}")
    src = psycopg.connect(DOCKER_DSN)
    log("Conectando Nuvem (ratio)")
    dst = psycopg.connect(CLOUD_DSN)

    if args.truncate:
        log("!! TRUNCATE solicitado — limpando rfb_atos.*")
        with dst.cursor() as d:
            d.execute("""
                TRUNCATE
                    rfb_atos.ato_alteracao_historico,
                    rfb_atos.conflito_temporal,
                    rfb_atos.ato_relacao,
                    rfb_atos.materia_dispositivo,
                    rfb_atos.materia_cnae,
                    rfb_atos.materia_tributo,
                    rfb_atos.ato_materia,
                    rfb_atos.ato_segmento,
                    rfb_atos.ato_content,
                    rfb_atos.ato_chunk,
                    rfb_atos.ato
                RESTART IDENTITY CASCADE
            """)
            dst.commit()
        log("TRUNCATE feito.")

    t0 = time.time()
    for name, fn in TASKS:
        if selected and name not in selected:
            log(f"  SKIP {name}")
            continue
        fn(src, dst)

    # Sequence do ato (com OVERRIDING SYSTEM VALUE, sequence não avança)
    ajusta_sequence_ato(dst)

    elapsed = time.time() - t0
    log(f"OK ETL completo em {elapsed/60:.1f} min")

    # Validação final
    log("Paridade final:")
    with src.cursor() as s, dst.cursor() as d:
        pairs = [
            ("atos",                    "ato"),
            ("ato_content",             "ato_content"),
            ("ato_segmento",            "ato_segmento"),
            ("ato_materia",             "ato_materia"),
            ("materia_tributo",         "materia_tributo"),
            ("materia_cnae",            "materia_cnae"),
            ("materia_dispositivo",     "materia_dispositivo"),
            ("ato_relacao",             "ato_relacao"),
            ("conflito_temporal",       "conflito_temporal"),
            ("ato_alteracao_historico", "ato_alteracao_historico"),
        ]
        for docker_tbl, cloud_tbl in pairs:
            dc = docker_count(s, docker_tbl)
            cc = cloud_count(d, cloud_tbl)
            mark = "OK" if dc == cc else "!!"
            log(f"  {mark} {docker_tbl:30s} docker={dc:>8}  cloud={cc:>8}")

    src.close()
    dst.close()


if __name__ == "__main__":
    main()
