"""
extract_segmentos.py — Popula ato_segmento + ato_alteracao_historico via /visao/original.

Sem LLM, sem custo. So HTTP + parse estrutural.

Uso:
    py extract_segmentos.py                          # toda a base com content_disponivel
    py extract_segmentos.py --tipos INSTRUCAO_NORMATIVA --orgao RFB
    py extract_segmentos.py --shards 4 --shard 0     # paralelo
    py extract_segmentos.py --redo                   # reprocessa atos ja processados
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any

import requests
from loguru import logger
from psycopg.rows import dict_row
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

from db import get_conn

API_BASE = "https://normasinternet2.receita.fazenda.gov.br/api/consulta-externa/ato"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
})


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def fetch_visao(id_portal: int, visao: str) -> dict | None:
    """Tenta buscar uma visao do ato. None se 406/404."""
    if visao == "relacional":
        path = "visao-relacional"
    else:
        path = f"visao/{visao}"
    url = f"{API_BASE}/{id_portal}/{path}"
    r = SESSION.get(url, timeout=30)
    if r.status_code in (404, 406):
        return None
    r.raise_for_status()
    return r.json()


def parse_data_dmy(s: str | None) -> str | None:
    """dd/MM/yyyy → yyyy-MM-dd"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        try:
            return datetime.strptime(s, "%d/%m/%Y %H:%M:%S").strftime("%Y-%m-%d")
        except Exception:
            return None


def processar_ato(conn, ato_id: int, id_portal: int) -> dict:
    """Busca /visao/original, popula segmentos e historico. Retorna stats."""
    stats = {"segmentos": 0, "historico": 0, "skipped": False}

    data = None
    for visao in ("original", "vigente"):
        try:
            data = fetch_visao(id_portal, visao)
            if data:
                break
        except Exception as e:
            logger.debug(f"id_portal={id_portal} visao={visao}: {e}")
    if not data:
        stats["skipped"] = True
        return stats

    # 1) Segmentos
    segmentos_combinados = (data.get("ementas") or []) + (data.get("outrosSegmentos") or [])
    if not segmentos_combinados:
        stats["skipped"] = True
        return stats

    with conn.cursor() as cur:
        # idempotente: limpa segmentos antigos do ato
        cur.execute("DELETE FROM ato_segmento WHERE ato_id = %s", (ato_id,))

        for seg in segmentos_combinados:
            id_seg = seg.get("idSegmento")
            if id_seg is None:
                continue
            versao = seg.get("versaoSegmento") or 1
            cur.execute(
                """
                INSERT INTO ato_segmento (
                  ato_id, id_segmento, versao_segmento, ordem, id_tipo_segmento, id_assunto,
                  texto_integra, is_original, is_compilado, is_tachado, is_omitido, is_agendado, raw
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ato_id, id_segmento, versao_segmento) DO UPDATE SET
                  texto_integra = EXCLUDED.texto_integra,
                  is_original = EXCLUDED.is_original,
                  is_compilado = EXCLUDED.is_compilado,
                  is_tachado = EXCLUDED.is_tachado,
                  is_omitido = EXCLUDED.is_omitido,
                  is_agendado = EXCLUDED.is_agendado,
                  raw = EXCLUDED.raw
                """,
                (
                    ato_id, id_seg, versao,
                    seg.get("ordemSegmentoAto"),
                    seg.get("idTipoSegmento"),
                    seg.get("idAssunto"),
                    seg.get("textoIntegra"),
                    bool(seg.get("original")),
                    bool(seg.get("compilado")),
                    bool(seg.get("tachado")),
                    bool(seg.get("omitir")),
                    bool(seg.get("agendado")),
                    json.dumps({k: seg.get(k) for k in ("mapper","arquivoBinario","ancorasOrigem","ancorasDestino")}),
                ),
            )
            stats["segmentos"] += 1

        # 2) Historico de alteracoes
        # Limpa apenas as anotacoes deste ato_alvo
        cur.execute("DELETE FROM ato_alteracao_historico WHERE ato_alvo_id = %s", (ato_id,))

        for h in data.get("historico") or []:
            id_modif_portal = h.get("idAto")
            if id_modif_portal is None:
                continue
            # Resolve ato_modificador_id local via id_portal (pode ser NULL se nao crawlamos ainda)
            cur.execute("SELECT id FROM atos WHERE id_portal = %s LIMIT 1", (id_modif_portal,))
            row = cur.fetchone()
            ato_modif_id = row[0] if row else None

            cur.execute(
                """
                INSERT INTO ato_alteracao_historico (
                  ato_alvo_id, id_segmento_alvo, ato_modificador_id, id_ato_modificador_portal,
                  data_inicio_vigencia, data_republicacao, texto_anotacao, raw_anotacao_id,
                  eh_agendamento, texto_agendamento
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ato_alvo_id, id_segmento_alvo, raw_anotacao_id) DO UPDATE SET
                  ato_modificador_id = EXCLUDED.ato_modificador_id,
                  data_inicio_vigencia = EXCLUDED.data_inicio_vigencia,
                  data_republicacao = EXCLUDED.data_republicacao,
                  texto_anotacao = EXCLUDED.texto_anotacao
                """,
                (
                    ato_id, h.get("idSegmento"), ato_modif_id, id_modif_portal,
                    parse_data_dmy(h.get("dataInicioVigencia")),
                    parse_data_dmy(h.get("dataRepublicacao")),
                    h.get("texto"),
                    h.get("idAnotacao"),
                    bool(h.get("ehAgendamento")),
                    h.get("textoAgendamento"),
                ),
            )
            stats["historico"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tipos", nargs="+", help="Filtra por tipo_ato")
    parser.add_argument("--orgao", help="Filtra por orgao_emissor")
    parser.add_argument("--ano", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--redo", action="store_true",
                        help="Reprocessa atos que ja tem segmentos (default: skip)")
    parser.add_argument("--shards", type=int)
    parser.add_argument("--shard", type=int)
    args = parser.parse_args()

    where = [
        "a.id_portal IS NOT NULL",
        "a.status_vigencia IS DISTINCT FROM 'publicacao_repetida'",
        "a.status_vigencia IS DISTINCT FROM 'nao_disponivel_portal'",
    ]
    params: list[Any] = []
    if not args.redo:
        where.append("NOT EXISTS (SELECT 1 FROM ato_segmento s WHERE s.ato_id = a.id)")
    if args.tipos:
        where.append("a.tipo_ato = ANY(%s)")
        params.append(args.tipos)
    if args.orgao:
        where.append("a.orgao_emissor = %s")
        params.append(args.orgao)
    if args.ano:
        where.append("EXTRACT(YEAR FROM a.data_publicacao) = %s")
        params.append(args.ano)
    if args.shards and args.shard is not None:
        where.append("(a.id_portal %% %s) = %s")
        params.append(args.shards); params.append(args.shard)

    sql = f"""
      SELECT a.id, a.id_portal
      FROM atos a
      WHERE {' AND '.join(where)}
      ORDER BY a.data_publicacao DESC
    """
    if args.limit:
        sql += f" LIMIT {args.limit}"

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            atos = cur.fetchall()

        if not atos:
            logger.info("Nenhum ato pendente.")
            return

        logger.info(f"Processando {len(atos)} atos...")
        ok, sk, err = 0, 0, 0
        total_seg, total_hist = 0, 0
        for ato in tqdm(atos, desc="extract_segmentos"):
            try:
                st = processar_ato(conn, ato["id"], ato["id_portal"])
                if st["skipped"]:
                    sk += 1
                else:
                    ok += 1
                    total_seg += st["segmentos"]
                    total_hist += st["historico"]
                conn.commit()
                time.sleep(0.15)  # rate-limit gentil
            except Exception as e:
                err += 1
                logger.error(f"ato_id={ato['id']} id_portal={ato['id_portal']}: {e}")
                conn.rollback()
                continue

        logger.success(
            f"FIM: {ok} ok | {sk} skipped (sem visao) | {err} erros | "
            f"segmentos={total_seg:,} historico={total_hist:,}"
        )


if __name__ == "__main__":
    main()
