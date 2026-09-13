"""carregar_ato_portal.py — carrega um ato a partir das visões do portal (vigente + original).

F1.8 da recoleta (a IN RFB 2.121/2022, ato 16630, sem texto na base), escrito para servir ao
extrator corrigido (F2c). A regra de exibição está em `visoes_portal.py`.

Dry-run por padrão (lê como `ratio_leitura`). Com `--aplicar` grava como `rfb_writer`, numa
transação:
    ato_content              texto da visão vigente (uma linha por ato, como hoje)
    ato_texto_visao          texto da visão original (foto com sha256)
    ato_segmento             todas as versões dos segmentos, com as marcas do portal
    ato_alteracao_historico  anotações de alteração
    ato                      content_disponivel, status_vigencia, situacao_portal,
                             analise_completa = false (o texto mudou: precisa categorizar)
    ato_coleta               resultado da coleta
e registra antes/depois de toda alteração em linha existente em `ato_mudanca`, com o run_id.
Exige as migrations 010 e 011.

Uso:
    python carregar_ato_portal.py --ato 16630 --baixar                 # plano, sem gravar
    python carregar_ato_portal.py --ato 16630 --json-vigente V.json --json-original O.json
    python carregar_ato_portal.py --ato 16630 --baixar --aplicar
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

import visoes_portal as vp

API = "https://normasinternet2.receita.fazenda.gov.br/api/consulta-externa/ato"
DADOS = Path(os.environ.get("RFB_ATOS_DADOS", r"C:\td-rfb-atos-dados")) / "portal"
UA = {"User-Agent": "Mozilla/5.0 (td-rfb-atos carregar_ato_portal)",
      "Accept": "application/json"}


def _json(valor):
    """Valor serializável para ato_mudanca (datas viram texto ISO)."""
    if isinstance(valor, date):
        return valor.isoformat()
    return valor


def baixar(id_portal: int, visao: str) -> tuple[dict, Path, int, str]:
    """GET da visão no portal (timeout longo: atos grandes passam de 3 MB); salva o JSON bruto."""
    import requests

    url = f"{API}/{id_portal}/visao/{visao}"
    resp = requests.get(url, timeout=300, headers=UA)
    resp.raise_for_status()
    DADOS.mkdir(parents=True, exist_ok=True)
    caminho = DADOS / f"{id_portal}_{visao}_{time.strftime('%Y%m%d')}.json"
    caminho.write_bytes(resp.content)
    return resp.json(), caminho, len(resp.content), url


def exigir_schema(conn) -> None:
    faltando = [nome for nome, sql in (
        ("ato_coleta (migration 010)", "select to_regclass('rfb_atos.ato_coleta')"),
        ("ato_texto_visao (migration 011)", "select to_regclass('rfb_atos.ato_texto_visao')"),
    ) if conn.execute(sql).fetchone()[0] is None]
    if faltando:
        sys.exit(f"[erro] faltam objetos: {', '.join(faltando)}. Aplique as migrations 010 e 011.")


def aplicar(conn, ato_id: int, vigente: dict, original: dict | None, *, run_id: str,
            origem_vigente: str, caminho_original: str | None = None,
            bytes_baixados: int | None = None) -> dict:
    """Grava o ato numa transação; devolve um resumo. Idempotente para as mesmas visões."""
    texto = vp.texto_da_visao(vigente)
    if not texto:
        raise ValueError("a visão vigente não tem texto exibível")
    if vp.corpo_em_pdf(vigente):
        raise ValueError("o corpo do ato está em anexo PDF — este carregador não extrai PDF")
    mudancas: list[tuple[str, str, object, object]] = []
    with conn.transaction():
        ato = conn.execute(
            "SELECT id_portal, content_disponivel, analise_completa, status_vigencia, "
            "data_vigencia_inicio, situacao_portal FROM rfb_atos.ato WHERE id = %s FOR UPDATE",
            (ato_id,)).fetchone()
        if ato is None:
            raise ValueError(f"ato {ato_id} não existe")
        id_portal, content_disp, analise, status, inicio, sit_atual = ato
        if id_portal != int(vigente["idAto"]):
            raise ValueError(
                f"idAto da visão ({vigente['idAto']}) difere do id_portal ({id_portal})")

        antigo = conn.execute("SELECT texto_completo FROM rfb_atos.ato_content WHERE ato_id = %s",
                              (ato_id,)).fetchone()
        antigo = antigo[0] if antigo else None
        if antigo != texto:
            mudancas.append((
                "ato_content", "texto_completo",
                {"sha256": vp.sha256(antigo), "caracteres": len(antigo)} if antigo else None,
                {"sha256": vp.sha256(texto), "caracteres": len(texto)},
            ))
        conn.execute(
            "INSERT INTO rfb_atos.ato_content (ato_id, texto_completo, origem_extracao, "
            "fonte_extracao, caracteres, tipo_versao) VALUES (%s, %s, %s, 'json_portal', %s, "
            "'vigente') ON CONFLICT (ato_id) DO UPDATE SET "
            "texto_completo = EXCLUDED.texto_completo, "
            "origem_extracao = EXCLUDED.origem_extracao, fonte_extracao = EXCLUDED.fonte_extracao, "
            "caracteres = EXCLUDED.caracteres, tipo_versao = EXCLUDED.tipo_versao",
            (ato_id, texto, origem_vigente, len(texto)))

        if original is not None:
            texto_ori = vp.texto_da_visao(original)
            if texto_ori:
                conn.execute(
                    "INSERT INTO rfb_atos.ato_texto_visao (ato_id, visao, sha256, texto, "
                    "caminho_json, fonte, run_id) VALUES (%s, 'original', %s, %s, %s, "
                    "'normasinternet2', %s) ON CONFLICT DO NOTHING",
                    (ato_id, vp.sha256(texto_ori), texto_ori, caminho_original, run_id))

        segmentos = vp.linhas_segmento(vigente)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rfb_atos.ato_segmento (ato_id, id_segmento, versao_segmento, ordem, "
                "id_tipo_segmento, id_assunto, texto_integra, is_original, is_compilado, "
                "is_tachado, is_omitido, is_agendado, raw) VALUES (%(ato_id)s, %(id_segmento)s, "
                "%(versao_segmento)s, %(ordem)s, %(id_tipo_segmento)s, %(id_assunto)s, "
                "%(texto_integra)s, %(is_original)s, %(is_compilado)s, %(is_tachado)s, "
                "%(is_omitido)s, %(is_agendado)s, %(raw)s) "
                "ON CONFLICT (ato_id, id_segmento, versao_segmento) DO UPDATE SET "
                "ordem = EXCLUDED.ordem, texto_integra = EXCLUDED.texto_integra, "
                "is_original = EXCLUDED.is_original, is_compilado = EXCLUDED.is_compilado, "
                "is_tachado = EXCLUDED.is_tachado, is_omitido = EXCLUDED.is_omitido, "
                "is_agendado = EXCLUDED.is_agendado, raw = EXCLUDED.raw",
                [{**s, "ato_id": ato_id, "raw": Jsonb(s["raw"])} for s in segmentos])

            historico = vp.linhas_historico(vigente)
            for h in historico:
                modificador = conn.execute("SELECT id FROM rfb_atos.ato WHERE id_portal = %s "
                                           "ORDER BY id LIMIT 1",
                                           (h["id_ato_modificador_portal"],)).fetchone()
                h["ato_modificador_id"] = modificador[0] if modificador else None
            cur.executemany(
                "INSERT INTO rfb_atos.ato_alteracao_historico (ato_alvo_id, id_segmento_alvo, "
                "ato_modificador_id, id_ato_modificador_portal, data_inicio_vigencia, "
                "data_republicacao, texto_anotacao, raw_anotacao_id, eh_agendamento, "
                "texto_agendamento) VALUES (%(ato_id)s, %(id_segmento_alvo)s, "
                "%(ato_modificador_id)s, %(id_ato_modificador_portal)s, "
                "%(data_inicio_vigencia)s, %(data_republicacao)s, %(texto_anotacao)s, "
                "%(raw_anotacao_id)s, %(eh_agendamento)s, %(texto_agendamento)s) "
                "ON CONFLICT (ato_alvo_id, id_segmento_alvo, raw_anotacao_id) DO UPDATE SET "
                "ato_modificador_id = EXCLUDED.ato_modificador_id, "
                "data_inicio_vigencia = EXCLUDED.data_inicio_vigencia, "
                "texto_anotacao = EXCLUDED.texto_anotacao",
                [{**h, "ato_id": ato_id} for h in historico])

        novo = {
            "content_disponivel": True,
            "analise_completa": False,
            "status_vigencia": vp.status_vigencia(vigente),
            "situacao_portal": vp.situacao(vigente),
            "data_vigencia_inicio": inicio or vp.data_iso(vigente.get("dataVigenciaInicio")),
        }
        atual = {"content_disponivel": content_disp, "analise_completa": analise,
                 "status_vigencia": status, "situacao_portal": sit_atual,
                 "data_vigencia_inicio": inicio}
        mudancas += [("ato", c, atual[c], v) for c, v in novo.items() if atual[c] != v]
        conn.execute(
            "UPDATE rfb_atos.ato SET content_disponivel = %s, analise_completa = %s, "
            "status_vigencia = %s, situacao_portal = %s, data_vigencia_inicio = %s, "
            "atualizado_em = now() WHERE id = %s",
            (novo["content_disponivel"], novo["analise_completa"], novo["status_vigencia"],
             Jsonb(novo["situacao_portal"]), novo["data_vigencia_inicio"], ato_id))

        conn.execute(
            "INSERT INTO rfb_atos.ato_coleta (ato_id, pdf_status, tentativas, primeira_tentativa, "
            "ultima_tentativa, url_tentada, http_status, content_type, bytes_baixados, sha256, "
            "origem, observacao) VALUES (%s, 'baixado', 1, now(), now(), %s, 200, "
            "'application/json', %s, %s, 'carregar_ato_portal', %s) "
            "ON CONFLICT (ato_id) DO UPDATE SET pdf_status = 'baixado', "
            "tentativas = rfb_atos.ato_coleta.tentativas + 1, "
            "primeira_tentativa = COALESCE(rfb_atos.ato_coleta.primeira_tentativa, now()), "
            "ultima_tentativa = now(), url_tentada = EXCLUDED.url_tentada, http_status = 200, "
            "content_type = EXCLUDED.content_type, bytes_baixados = EXCLUDED.bytes_baixados, "
            "sha256 = EXCLUDED.sha256, origem = EXCLUDED.origem, observacao = EXCLUDED.observacao",
            (ato_id, origem_vigente, bytes_baixados, vp.sha256(texto),
             f"teor da visão vigente do portal (run {run_id})"))

        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rfb_atos.ato_mudanca (run_id, ato_id, tabela, campo, antes, depois) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(run_id, ato_id, t, c, Jsonb(_json(a)), Jsonb(_json(d)))
                 for t, c, a, d in mudancas])

    return {"ato_id": ato_id, "caracteres": len(texto), "segmentos": len(segmentos),
            "exibidos": len(vp.segmentos_exibidos(vigente)), "historico": len(historico),
            "status_vigencia": novo["status_vigencia"], "mudancas": len(mudancas)}


def _dsn(cli: str | None, escrita: bool) -> str:
    if cli:
        return cli
    if os.environ.get("RFB_ATOS_DSN"):
        return os.environ["RFB_ATOS_DSN"]
    from credenciais import CredencialAusente, resolver_dsn
    try:
        return resolver_dsn("escrita" if escrita else "leitura")
    except CredencialAusente as e:
        sys.exit(f"[erro] {e} Ou use --dsn.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ato", type=int, required=True, help="rfb_atos.ato.id")
    p.add_argument("--baixar", action="store_true", help="busca as visões no portal")
    p.add_argument("--json-vigente", type=Path)
    p.add_argument("--json-original", type=Path)
    p.add_argument("--aplicar", action="store_true", help="grava (padrão: só mostra o plano)")
    p.add_argument("--run-id")
    p.add_argument("--dsn")
    args = p.parse_args()
    run_id = args.run_id or f"carregar-{args.ato}-{time.strftime('%Y%m%dT%H%M%S')}"

    with psycopg.connect(_dsn(args.dsn, args.aplicar)) as conn:
        linha = conn.execute("SELECT id_portal, tipo_ato, numero, ano FROM rfb_atos.ato "
                             "WHERE id = %s", (args.ato,)).fetchone()
        if not linha or not linha[0]:
            sys.exit(f"[erro] ato {args.ato} não existe ou não tem id_portal")
        id_portal = linha[0]
        if args.baixar:
            vigente, cam_vig, n_bytes, origem = baixar(id_portal, "vigente")
            original, cam_ori, _, _ = baixar(id_portal, "original")
        elif args.json_vigente:
            vigente = json.loads(args.json_vigente.read_text(encoding="utf-8"))
            cam_vig, n_bytes = args.json_vigente, args.json_vigente.stat().st_size
            origem = f"{API}/{id_portal}/visao/vigente (arquivo {cam_vig.name})"
            original = (json.loads(args.json_original.read_text(encoding="utf-8"))
                        if args.json_original else None)
            cam_ori = args.json_original
        else:
            sys.exit("[erro] use --baixar ou --json-vigente")

        texto = vp.texto_da_visao(vigente)
        print(f"ato {args.ato} ({linha[1]} {linha[2]}/{linha[3]}, idAto {id_portal})")
        print(f"  vigente: {len(vp.segmentos_exibidos(vigente))} segmentos exibidos de "
              f"{len(vp.linhas_segmento(vigente))}; {len(texto):,} caracteres; "
              f"status {vp.status_vigencia(vigente)}; corpo em PDF: {vp.corpo_em_pdf(vigente)}")
        if original is not None:
            print(f"  original: {len(vp.texto_da_visao(original)):,} caracteres")
        print(f"  JSON bruto: {cam_vig}")
        if not args.aplicar:
            print("\n(dry-run: nada gravado. Use --aplicar para gravar como rfb_writer.)")
            return
        exigir_schema(conn)
        resumo = aplicar(conn, args.ato, vigente, original, run_id=run_id, origem_vigente=origem,
                         caminho_original=str(cam_ori) if cam_ori else None,
                         bytes_baixados=n_bytes)
        print(f"\n[ok] run {run_id}: {resumo}")


if __name__ == "__main__":
    main()
