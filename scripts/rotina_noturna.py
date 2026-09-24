"""rotina_noturna.py — coleta, categorização e vetores do rfb_atos, sem ninguém olhando.

Uma rodada por noite (Agendador de Tarefas do Windows, `rotina_noturna.ps1`):
  1. coleta: tudo o que o portal publicou desde a última publicação da base (menos 10 dias de
     folga) e falta na base — todos os tipos de ato (`coletar_atos_portal.coletar_novos`);
  2. categorização pela API em lote da Anthropic (metade do preço; `lote_categorizacao`):
     a) aplica os lotes que terminaram — portão de qualidade e gravação; o que reprova vai para
        revisão; b) envia um lote novo com a fila dos tipos liberados em
        `references/rotina_noturna.json` (cada tipo só entra depois da rodada de pesquisa dele —
        decisão do dono em 24/09/2026), até o teto de gasto da noite. O que é enviado numa noite
        é gravado na seguinte;
  3. resumo em RFB_ATOS_DADOS/rotina/AAAA-MM-DD.json e log em AAAA-MM-DD.log; a mesma rodada
     vai para `rfb_atos.rotina_execucao` (migration 012) e para o Slack (`acompanhamento.py`).
     De manhã, `--conferir` (segunda tarefa) alerta se a rodada da noite não aconteceu.

Trava contra duas rodadas ao mesmo tempo (rotina/rotina.lock; vencida após 12 h). Um passo com
erro não impede o outro. Saída: 0 ok; 1 algum erro (ou banco fora do ar: VPN); 2 já rodando.

Uso:
    python rotina_noturna.py              # rodada completa
    python rotina_noturna.py --sem-coleta # só categoriza a fila
    python rotina_noturna.py --plano      # mostra o que faria, sem chamar modelo nem gravar
    python rotina_noturna.py --conferir   # checagem da manhã: a rodada da noite aconteceu?
    python rotina_noturna.py --painel     # as últimas rodadas registradas no banco
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg

import acompanhamento as ac
import categorizar_nuvem as cn
import coletar_atos_portal as col
import lote_categorizacao as lc

CONFIG = Path(__file__).resolve().parent.parent / "references" / "rotina_noturna.json"
FOLGA = timedelta(days=10)
TRAVA_VENCE = 12 * 3600


def _pasta() -> Path:
    pasta = Path(os.environ.get("RFB_ATOS_DADOS", r"C:\td-rfb-atos-dados")) / "rotina"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


class Log:
    """Escreve na tela e no arquivo do dia, linha a linha (a rodada pode cair no meio)."""

    def __init__(self, arquivo: Path):
        self.f = arquivo.open("a", encoding="utf-8")

    def __call__(self, texto: str) -> None:
        linha = f"{time.strftime('%H:%M:%S')} {texto}"
        print(linha, flush=True)
        self.f.write(linha + "\n")
        self.f.flush()


def travar(pasta: Path) -> Path | None:
    trava = pasta / "rotina.lock"
    if trava.exists() and time.time() - trava.stat().st_mtime > TRAVA_VENCE:
        trava.unlink()
    try:
        with trava.open("x", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    except FileExistsError:
        return None
    return trava


def banco_alcancavel(dsn: str, timeout: float = 10.0) -> bool:
    """O banco só é alcançável pela VPN: testa a porta antes de começar."""
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    try:
        with socket.create_connection((info.get("host"), int(info.get("port") or 5432)),
                                      timeout=timeout):
            return True
    except OSError:
        return False


def janela_de_coleta(ultima: date | None, hoje: date | None = None) -> date:
    """Desde quando olhar o portal: a última publicação da base (nunca no futuro) menos a folga."""
    hoje = hoje or date.today()
    return min(ultima or hoje, hoje) - FOLGA


def rodada(cfg: dict, *, dsn: str, coletar: bool = True, plano: bool = False,
           portal=None, chamar=None, embed=None, cliente_lote=None, log=print) -> dict:
    run_id = f"rotina-{time.strftime('%Y%m%dT%H%M%S')}"
    resumo: dict = {"run_id": run_id, "inicio": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with psycopg.connect(dsn, autocommit=True) as conn:
        if coletar:
            try:
                desde = janela_de_coleta(col.ultima_publicacao(conn))
                log(f"[coleta] atos publicados no portal depois de {desde}")
                r = col.coletar_novos(conn, portal or col.Portal(), desde=desde,
                                      aplicar=not plano, run_id=run_id,
                                      tipos=cfg.get("coletar_tipos"), saida=log)
                novos = [x for x in r if x.get("ato_id") and not x.get("ja_existia")]
                por_tipo: dict[str, int] = {}
                for x in novos:
                    por_tipo[x.get("tipo") or "?"] = por_tipo.get(x.get("tipo") or "?", 0) + 1
                resumo["coleta"] = {
                    "desde": desde.isoformat(), "no_portal": len(r), "gravados": len(novos),
                    "por_tipo": por_tipo, "erros": sum(1 for x in r if x.get("erro"))}
            except Exception as e:
                log(f"[coleta] ERRO {type(e).__name__}: {e}")
                resumo["coleta"] = {"erro": f"{type(e).__name__}: {e}"}
        try:
            cliente = cliente_lote or (None if plano else lc.cliente_anthropic())
            if not plano:
                cn.exigir_schema(conn)
                cn.exigir_permissoes(conn)
                aplicados = []
                for m in lc.manifestos():
                    if m["estado"] not in ("enviado", "baixado"):
                        continue
                    res = lc.aplicar(
                        m["batch_id"], cliente,
                        conectar=lambda: psycopg.connect(dsn, autocommit=True),
                        chamar=chamar or cn.chamador_anthropic(), embed=embed,
                        paralelo=cfg["paralelo"], saida=log)
                    if res:
                        feito = next((x for x in lc.manifestos() if x["batch_id"] == m["batch_id"]),
                                     {})
                        custo = ((feito.get("resultado") or {}).get("custo_usd", 0)
                                 + (feito.get("aplicacao") or {}).get("custo_direto_usd", 0))
                        aplicados.append({"batch_id": m["batch_id"], "atos": len(res),
                                          "gravados": sum(1 for x in res if x.get("materias")),
                                          "materias": sum(x.get("materias") or 0 for x in res),
                                          "revisar": sum(1 for x in res if x.get("revisar")),
                                          "erros": sum(1 for x in res if x.get("erro")),
                                          "custo_usd": round(custo, 2)})
                resumo["lotes_aplicados"] = aplicados
            atos = cn.carregar_atos(conn, pendentes=True, limit=cfg["limite_atos_por_noite"],
                                    tipos=cfg["tipos_categorizar"])
            log(f"[categorização] {len(atos)} atos na fila dos tipos liberados "
                f"{cfg['tipos_categorizar']} | ~US$ {lc.estimar_lote(atos):.2f} no lote "
                f"(estimativa conservadora) | teto da noite US$ {cfg['teto_usd_por_noite']:.2f}")
            if plano:
                resumo["categorizacao"] = {"fila": len(atos)}
            else:
                ids = lc.enviar(atos, cliente, teto_usd=cfg["teto_usd_por_noite"], saida=log)
                # o que nem vai ao lote (só ementa curta demais) sai da fila, para revisão
                _, fora = lc.pedidos([a for a in atos if a["id"] not in lc.em_voo()])
                for f in fora:
                    cn.marcar_revisao(conn, f["ato_id"], run_id, [f["motivo"]])
                enviados = sum(len(x["atos"]) for x in lc.manifestos() if x["batch_id"] in ids)
                resumo["categorizacao"] = {"fila": len(atos), "lotes_enviados": ids,
                                           "atos_enviados": enviados,
                                           "para_revisao_sem_modelo": len(fora)}
        except SystemExit as e:
            log(f"[categorização] ERRO {e}")
            resumo["categorizacao"] = {"erro": str(e)}
        except Exception as e:
            log(f"[categorização] ERRO {type(e).__name__}: {e}")
            resumo["categorizacao"] = {"erro": f"{type(e).__name__}: {e}"}
        try:
            ultima = col.ultima_publicacao(conn)
            resumo["base_atualizada_ate"] = (min(ultima, date.today()).isoformat()
                                             if ultima else None)
        except Exception:
            resumo["base_atualizada_ate"] = None
    resumo["fim"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return resumo


def teve_erro(resumo: dict) -> bool:
    if any(x.get("erros") for x in resumo.get("lotes_aplicados") or []):
        return True
    return any(isinstance(resumo.get(k), dict) and (resumo[k].get("erro") or resumo[k].get(
        "erros")) for k in ("coleta", "categorizacao"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sem-coleta", action="store_true")
    p.add_argument("--plano", action="store_true", help="não chama modelo nem grava")
    p.add_argument("--conferir", action="store_true",
                   help="checagem da manhã: alerta no Slack se a rodada da noite não aconteceu")
    p.add_argument("--painel", action="store_true", help="últimas rodadas registradas no banco")
    p.add_argument("--dsn")
    args = p.parse_args()
    pasta = _pasta()
    log = Log(pasta / f"{date.today().isoformat()}.log")
    if args.conferir:
        estado = ac.conferir(pasta, log=log)
        log(f"[conferência] {estado}")
        sys.exit(0 if estado == "ok" else 1)
    if args.painel:
        with psycopg.connect(cn._dsn(args.dsn, False)) as conn:
            print(ac.painel(conn))
        return
    trava = travar(pasta)
    if trava is None:
        log("[rotina] outra rodada em andamento (rotina.lock): saindo")
        sys.exit(2)
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        dsn = cn._dsn(args.dsn, not args.plano)
        if not banco_alcancavel(dsn):
            resumo = {"run_id": f"rotina-{time.strftime('%Y%m%dT%H%M%S')}",
                      "inicio": ac.agora_iso(), "fim": ac.agora_iso(),
                      "falhou": "banco fora de alcance (VPN desligada?); tenta na próxima noite"}
            log(f"[rotina] {resumo['falhou']}")
            _guardar(pasta, resumo)
            if not args.plano:
                ac.enviar_slack(ac.mensagem(resumo), log=log)
            sys.exit(1)
        log(f"[rotina] início | config {cfg}")
        resumo = rodada(cfg, dsn=dsn, coletar=not args.sem_coleta, plano=args.plano, log=log)
        _guardar(pasta, resumo)
        log(f"[rotina] fim | {json.dumps(resumo, ensure_ascii=False, default=str)[:600]}")
        if not args.plano:
            with psycopg.connect(dsn, autocommit=True) as conn:
                ac.registrar(conn, resumo, log=log)
            ac.enviar_slack(ac.mensagem(resumo), log=log)
        sys.exit(1 if teve_erro(resumo) else 0)
    finally:
        trava.unlink(missing_ok=True)


def _guardar(pasta: Path, resumo: dict) -> None:
    (pasta / f"{date.today().isoformat()}.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
