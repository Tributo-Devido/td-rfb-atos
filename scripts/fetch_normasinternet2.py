"""
fetch_normasinternet2.py — Busca conteúdo + grafo de relações via API normasinternet2.

Para cada ato (com id_portal preenchido):
  1. GET /api/consulta-externa/ato/{idPortal}/visao/vigente
     - extrai ementas[].textoIntegra + outrosSegmentos[].textoIntegra
     - detecta outrosSegmentos[].arquivoBinario.idArquivoBinario
  2. Se houver arquivoBinario:
     - GET /api/consulta-externa/ato/{idPortal}/anexo/{idAB}
     - salva PDF em PDF_DIR/{tipo_ato}_{numero}_{orgao}_{data}.pdf
     - extrai texto via MarkItDown
  3. GET /api/consulta-externa/ato/{idPortal}/visao-relacional
     - extrai impactosPoloAtivo[] e impactosPoloPassivo[]
     - mapeia corSimbolo → tipo_relacao canônico
     - resolve idAto destino → ato_id local (best-effort)
  4. UPSERT em ato_content (tipo_versao='vigente')
  5. UPSERT em ato_relacao
  6. UPDATE atos: status_vigencia, pdf_disponivel, content_disponivel, analise_completa stays FALSE

Uso:
    py fetch_normasinternet2.py                              # processa todos pendentes
    py fetch_normasinternet2.py --limit 50
    py fetch_normasinternet2.py --tipos SOLUCAO_CONSULTA --orgao COSIT --ano 2026
    py fetch_normasinternet2.py --redo                       # reprocessa mesmo se ja tem content
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests
from loguru import logger
from psycopg.rows import dict_row
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

from db import get_conn

API_BASE = "https://normasinternet2.receita.fazenda.gov.br/api/consulta-externa/ato"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})

PDF_DIR = Path(os.environ.get("PDF_DIR", "./pdfs")).resolve()
PDF_DIR.mkdir(parents=True, exist_ok=True)

# corSimbolo (portal) → codigo canônico (taxonomia.json)
COR_SIMBOLO_MAP = {
    1: "referencia",
    2: "altera",
    3: "interrompe",
    4: "recupera",
    5: "retifica",
    6: "anotacao_futura",
}

# vigencia (portal: 1=vigente, 0=não vigente; mais granularidade vem de cor de fundo)
VIGENCIA_MAP_BASIC = {
    1: "vigente_nunca_alterado",
    0: "nao_vigente",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15))
def get_json(url: str) -> dict | None:
    r = SESSION.get(url, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15))
def get_pdf(url: str) -> bytes:
    r = SESSION.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def slugify_filename(ato: dict) -> str:
    import re
    safe_num = re.sub(r"[^a-zA-Z0-9_-]", "_", ato["numero"])
    return f"{ato['tipo_ato']}_{safe_num}_{ato['orgao_emissor']}_{ato['data_publicacao']}.pdf"


def extrair_pdf_texto(pdf_path: Path) -> str:
    from markitdown import MarkItDown
    md = MarkItDown()
    try:
        result = md.convert(str(pdf_path))
        return (result.text_content or "").strip()
    except BaseException as e:
        # MarkItDown.FileConversionException herda de BaseException (nao de Exception),
        # entao `except Exception` nao captura. Captura ampla aqui pra resiliencia.
        # KeyboardInterrupt e SystemExit ainda sao propagados pelo Python via
        # mecanismo de signal/exit code; aqui so silenciamos PDFs corrompidos.
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        logger.error(f"MarkItDown falhou em {pdf_path}: {type(e).__name__}: {e}")
        return ""


def consolidar_texto_vigente(vigente_json: dict, pdf_texto: str | None) -> tuple[str, int | None]:
    """
    Concatena ementa + corpo (do JSON ou do PDF) em texto único pra ato_content.
    Retorna (texto, id_arquivo_binario_se_houver).
    """
    partes = []
    id_ab_capturado = None

    # Ementas (sempre presente)
    for e in vigente_json.get("ementas", []) or []:
        ti = (e.get("textoIntegra") or "").strip()
        if ti:
            partes.append(f"## EMENTA\n{ti}")

    # Outros segmentos: ou textoIntegra estruturado, ou referência ao PDF
    tem_pdf_segmento = False
    for s in vigente_json.get("outrosSegmentos", []) or []:
        ti = (s.get("textoIntegra") or "").strip()
        ab = s.get("arquivoBinario")
        if ti:
            partes.append(ti)
        elif ab and ab.get("idArquivoBinario"):
            tem_pdf_segmento = True
            id_ab_capturado = ab.get("idArquivoBinario")

    if tem_pdf_segmento and pdf_texto:
        partes.append(f"## CORPO DO ATO (extraído de PDF)\n{pdf_texto}")

    return "\n\n".join(partes), id_ab_capturado


def encontrar_id_arquivo_binario(vigente_json: dict) -> int | None:
    for s in vigente_json.get("outrosSegmentos", []) or []:
        ab = s.get("arquivoBinario")
        if ab and ab.get("idArquivoBinario"):
            return ab["idArquivoBinario"]
    return None


def mapear_status_vigencia(vigente_json: dict, relacional_json: dict | None) -> str:
    """Decide status_vigencia com base nos dois JSONs."""
    if not vigente_json.get("vigente"):
        return "nao_vigente"
    # Se vigente e tem impactos passivos (foi alterado por outros), está alterado
    if relacional_json:
        passivos = relacional_json.get("impactosPoloAtivo") or []  # quem AFETA este = polo ativo dos outros
        # Cuidado com nomenclatura: no JSON, impactosPoloAtivo = "atos modificadores deste"
        # Na verdade pelo HTML que vimos: "Atos modificadores do(a) SC X" estava no polo ATIVO
        if passivos:
            return "vigente_alterado"
    return "vigente_nunca_alterado"


def processar_ato(conn, ato: dict) -> bool:
    """Processa um ato: busca JSON, PDF se houver, popula content + relacao."""
    id_portal = ato["id_portal"]
    if not id_portal:
        return False

    url_vig = f"{API_BASE}/{id_portal}/visao/vigente"
    url_rel = f"{API_BASE}/{id_portal}/visao-relacional"

    try:
        vig = get_json(url_vig)
        if vig is None:
            logger.warning(f"vigente 404 idPortal={id_portal} (ato_id={ato['id']})")
            return False
        time.sleep(0.3)
        rel = get_json(url_rel)
        time.sleep(0.3)
    except Exception as e:
        logger.error(f"erro fetch idPortal={id_portal}: {e}")
        return False

    # Detectar PDF e baixar
    id_ab = encontrar_id_arquivo_binario(vig)
    pdf_texto = None
    pdf_path: Path | None = None
    if id_ab:
        pdf_path = PDF_DIR / slugify_filename(ato)
        if not pdf_path.exists():
            try:
                pdf_url = f"{API_BASE}/{id_portal}/anexo/{id_ab}"
                pdf_bytes = get_pdf(pdf_url)
                pdf_path.write_bytes(pdf_bytes)
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"erro baixar PDF id_portal={id_portal} id_ab={id_ab}: {e}")
                pdf_path = None
        if pdf_path and pdf_path.exists():
            pdf_texto = extrair_pdf_texto(pdf_path)

    # Consolidar texto
    texto, _ = consolidar_texto_vigente(vig, pdf_texto)
    if not texto.strip():
        logger.warning(f"texto vazio idPortal={id_portal}")
        return False

    # Status vigência
    status_vig = mapear_status_vigencia(vig, rel)

    with conn.cursor() as cur:
        # ato_content (vigente)
        cur.execute(
            """
            INSERT INTO ato_content (ato_id, tipo_versao, content, fonte_extracao, caracteres, id_arquivo_binario, pdf_path)
            VALUES (%s, 'vigente', %s, %s, %s, %s, %s)
            ON CONFLICT (ato_id, tipo_versao) DO UPDATE SET
              content = EXCLUDED.content,
              fonte_extracao = EXCLUDED.fonte_extracao,
              caracteres = EXCLUDED.caracteres,
              id_arquivo_binario = EXCLUDED.id_arquivo_binario,
              pdf_path = EXCLUDED.pdf_path,
              processed_at = now()
            """,
            (
                ato["id"], texto,
                "json+pdf" if pdf_texto else "json_only",
                len(texto), id_ab,
                str(pdf_path) if pdf_path else None,
            ),
        )

        # atos: status, flags
        cur.execute(
            """
            UPDATE atos SET
              status_vigencia = %s,
              pdf_disponivel = %s,
              content_disponivel = TRUE,
              updated_at = now()
            WHERE id = %s
            """,
            (status_vig, id_ab is not None, ato["id"]),
        )

        # ato_relacao a partir do /visao-relacional
        if rel:
            for direcao, lst in [
                ("ativo", rel.get("impactosPoloAtivo") or []),       # outros que afetam ESTE
                ("passivo", rel.get("impactosPoloPassivo") or []),    # ESTE afeta outros
            ]:
                for impacto in lst:
                    id_destino_portal = impacto.get("idAto")
                    if not id_destino_portal:
                        continue
                    cur.execute("SELECT id FROM atos WHERE id_portal = %s", (id_destino_portal,))
                    row = cur.fetchone()
                    if not row:
                        continue  # destino não está na nossa base
                    ato_destino_id = row[0]

                    cor = impacto.get("corSimbolo")
                    tipo_rel = COR_SIMBOLO_MAP.get(cor, f"corSimbolo_{cor}")
                    sigla = impacto.get("sigla")
                    hint = impacto.get("hint")

                    # No grafo, por convenção: "ato_origem REVOGA ato_destino"
                    # impactosPoloPassivo: ESTE ato age sobre os destinos → origem=este, destino=lst
                    # impactosPoloAtivo: outros agem sobre ESTE → origem=lst, destino=este
                    if direcao == "passivo":
                        origem_id, destino_id = ato["id"], ato_destino_id
                    else:
                        origem_id, destino_id = ato_destino_id, ato["id"]

                    cur.execute(
                        """
                        INSERT INTO ato_relacao (ato_origem_id, ato_destino_id, tipo_relacao, observacao, fonte)
                        VALUES (%s, %s, %s, %s, 'normasinternet2_portal')
                        ON CONFLICT (ato_origem_id, ato_destino_id, tipo_relacao) DO UPDATE SET
                          observacao = EXCLUDED.observacao
                        """,
                        (origem_id, destino_id, tipo_rel, f"{sigla}: {hint}" if sigla and hint else (hint or sigla)),
                    )

        conn.commit()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tipos", nargs="+", help="Filtra por tipo_ato (ex: SOLUCAO_CONSULTA)")
    parser.add_argument("--orgao", help="Filtra por orgao_emissor (ex: COSIT)")
    parser.add_argument("--ano", type=int, help="Filtra por ano de publicacao")
    parser.add_argument("--redo", action="store_true", help="Reprocessa mesmo atos com content_disponivel=TRUE")
    parser.add_argument("--shard", type=int, help="Indice deste shard (0-based)")
    parser.add_argument("--shards", type=int, help="Total de shards (paralelismo)")
    args = parser.parse_args()

    where = ["a.id_portal IS NOT NULL", "a.status_vigencia IS DISTINCT FROM 'publicacao_repetida'"]
    params: list[Any] = []
    if not args.redo:
        where.append("a.content_disponivel = FALSE")
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
        params.append(args.shards)
        params.append(args.shard)

    sql = f"""
        SELECT id, tipo_ato, numero, orgao_emissor, data_publicacao::text, id_portal
        FROM atos a
        WHERE {' AND '.join(where)}
        ORDER BY data_publicacao DESC
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
        ok = 0
        for ato in tqdm(atos, desc="fetch"):
            try:
                if processar_ato(conn, ato):
                    ok += 1
            except Exception as e:
                logger.error(f"erro ato_id={ato['id']}: {e}")
                conn.rollback()
                continue

    logger.success(f"FIM: {ok}/{len(atos)} atos processados com sucesso")


if __name__ == "__main__":
    main()
