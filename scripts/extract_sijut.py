"""
extract_sijut.py — Crawler SIJUT2 (Receita Federal) para tabela `atos`.

Adapter do crawler legado em C:/Users/tribu/Documents/GitHub/marketing/normas/extrair_api.py
com correções:
  - UPSERT correto via chave natural (tipo_ato, numero, orgao_emissor, data_publicacao)
  - Modo incremental: pega ano corrente + ano anterior por padrão
  - Cobre TODOS os tipos de ato com sijut_value mapeado em taxonomia.json
  - Loga progresso e contagem

Uso:
    py extract_sijut.py                          # incremental (anos atual e anterior)
    py extract_sijut.py --ano 2024               # ano específico
    py extract_sijut.py --anos 2023 2024 2025    # múltiplos anos
    py extract_sijut.py --tipos SC SD            # subset de tipos
    py extract_sijut.py --full                   # todos os tipos x todos os anos (1983-atual) — backfill
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from db import get_conn

SIJUT_BASE = "http://normas.receita.fazenda.gov.br/sijut2consulta/consulta.action"

TAXONOMIA_PATH = Path(__file__).resolve().parent.parent / "references" / "taxonomia.json"


def carregar_tipos_ato() -> list[dict]:
    """Carrega tipos de ato com sijut_value definido na taxonomia."""
    data = json.loads(TAXONOMIA_PATH.read_text(encoding="utf-8"))
    tipos = data["tipos_ato"]["lista"]
    return [t for t in tipos if t.get("sijut_value") is not None and t.get("ativo", True)]


def parse_orgao_unidade(orgao_texto: str) -> str:
    """
    Normaliza 'Cosit', 'Disit/SRRF08', 'Diana/SRRF09', 'Coana' para taxonomia_orgao_emissor.codigo.
    """
    if not orgao_texto:
        return "RFB"
    t = orgao_texto.strip().upper().replace("/", "_").replace(" ", "_")
    # Cosit, Coana, RFB → COSIT, COANA, RFB
    if t in ("COSIT", "COANA", "RFB"):
        return t
    # Disit/SRRF08 → DISIT_SRRF08
    if t.startswith("DISIT") or t.startswith("DIANA"):
        return t
    # Fallback: usa texto literal em uppercase, marca como desconhecido
    logger.warning(f"orgao_unidade desconhecido: '{orgao_texto}' → registrando como '{t}'")
    return t


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def fetch_pagina(url: str) -> str:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")


def buscar_tipo_ano(tipo: dict, ano: int) -> list[dict]:
    """
    Busca todos os atos de um tipo em um ano. Retorna lista de dicts prontos para UPSERT.
    """
    sijut_value = tipo["sijut_value"]
    nome_tipo = tipo["nome"]
    codigo_tipo = tipo["codigo"]

    url_base = f"{SIJUT_BASE}?tiposAtosSelecionados={sijut_value}&ano_ato={ano}&somente_atos_vigentes=on&optOrdem=Publicacao_DESC"
    try:
        html = fetch_pagina(f"{url_base}&p=1")
    except Exception as e:
        logger.error(f"[{nome_tipo} {ano}] erro inicial: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", id="p")
    if select:
        total_pages = max(int(o.get("value", 1)) for o in select.find_all("option"))
    else:
        total_pages = 1

    logger.info(f"[{nome_tipo} {ano}] {total_pages} pagina(s)")

    coletados = []
    for page in range(1, total_pages + 1):
        try:
            html = fetch_pagina(f"{url_base}&p={page}")
        except Exception as e:
            logger.error(f"[{nome_tipo} {ano}] erro pagina {page}: {e}")
            continue
        time.sleep(1)

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id="tabelaAtos")
        if not table:
            continue

        clean = BeautifulSoup(str(table).replace("<br>", "\n"), "html.parser")
        for row in clean.find_all("tr"):
            cols = [c.get_text(strip=True) for c in row.find_all("td")]
            link_a = row.find("a", href=True)
            if len(cols) != 5:
                continue  # cabeçalho ou linha vazia
            link = f"http://normas.receita.fazenda.gov.br/sijut2consulta/{link_a['href']}" if link_a else None

            # cols: [Tipo do ato, Nº do ato, Órgão/unidade, Publicação, Ementa]
            try:
                pub = datetime.strptime(cols[3], "%d/%m/%Y").date()
            except ValueError:
                logger.warning(f"data invalida: {cols[3]}")
                continue

            coletados.append({
                "tipo_ato": codigo_tipo,
                "numero": cols[1].strip(),
                "orgao_emissor": parse_orgao_unidade(cols[2]),
                "data_publicacao": pub,
                "ementa": cols[4].strip() or None,
                "link": link,
                "eficacia": tipo.get("eficacia_default"),
            })

        logger.debug(f"[{nome_tipo} {ano}] pagina {page}/{total_pages}: +{len(coletados)} acumulados")

    logger.success(f"[{nome_tipo} {ano}] coletados: {len(coletados)}")
    return coletados


def upsert_atos(atos_data: list[dict]) -> tuple[int, int]:
    """
    UPSERT em atos pela chave natural (tipo_ato, numero, orgao_emissor, data_publicacao).
    Retorna (inseridos, atualizados).
    """
    if not atos_data:
        return 0, 0

    sql = """
        INSERT INTO atos (tipo_ato, numero, orgao_emissor, data_publicacao, ementa, link, eficacia, fonte_origem)
        VALUES (%(tipo_ato)s, %(numero)s, %(orgao_emissor)s, %(data_publicacao)s, %(ementa)s, %(link)s, %(eficacia)s, 'sijut2_rfb')
        ON CONFLICT (tipo_ato, numero, orgao_emissor, data_publicacao)
        DO UPDATE SET
            ementa = EXCLUDED.ementa,
            link = EXCLUDED.link,
            updated_at = now()
        RETURNING (xmax = 0) AS inserted;
    """

    inseridos, atualizados = 0, 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for row in atos_data:
                try:
                    cur.execute(sql, row)
                    res = cur.fetchone()
                    if res and res[0]:
                        inseridos += 1
                    else:
                        atualizados += 1
                except Exception as e:
                    logger.error(f"erro UPSERT {row['tipo_ato']} {row['numero']}/{row['data_publicacao']}: {e}")
                    conn.rollback()
                    continue
            conn.commit()
    return inseridos, atualizados


def descobrir_anos_incremental() -> list[int]:
    """Retorna [ano_atual, ano_anterior] para crawl diário incremental."""
    ano_atual = date.today().year
    return [ano_atual, ano_atual - 1]


def main():
    parser = argparse.ArgumentParser(description="Crawler SIJUT2 → tabela atos (banco LOCAL)")
    parser.add_argument("--ano", type=int, help="Ano único")
    parser.add_argument("--anos", nargs="+", type=int, help="Múltiplos anos")
    parser.add_argument("--tipos", nargs="+", help="Códigos de tipos a coletar (ex: SOLUCAO_CONSULTA SOLUCAO_DIVERGENCIA)")
    parser.add_argument("--full", action="store_true", help="Backfill completo (1983-atual, todos os tipos)")
    args = parser.parse_args()

    todos_tipos = carregar_tipos_ato()
    if args.tipos:
        tipos = [t for t in todos_tipos if t["codigo"] in args.tipos]
        if not tipos:
            sys.exit(f"[erro] Nenhum tipo encontrado para: {args.tipos}. Disponíveis: {[t['codigo'] for t in todos_tipos]}")
    else:
        tipos = todos_tipos

    if args.full:
        anos = list(range(1983, date.today().year + 1))
    elif args.ano:
        anos = [args.ano]
    elif args.anos:
        anos = args.anos
    else:
        anos = descobrir_anos_incremental()

    logger.info(f"Crawl: {len(tipos)} tipo(s) x {len(anos)} ano(s) = {len(tipos) * len(anos)} consultas")

    total_inseridos, total_atualizados = 0, 0
    for tipo in tipos:
        for ano in anos:
            atos_data = buscar_tipo_ano(tipo, ano)
            if not atos_data:
                continue
            ins, upd = upsert_atos(atos_data)
            total_inseridos += ins
            total_atualizados += upd
            logger.info(f"[{tipo['codigo']} {ano}] inseridos={ins} atualizados={upd}")

    logger.success(f"FIM. Total: inseridos={total_inseridos} atualizados={total_atualizados}")


if __name__ == "__main__":
    main()
