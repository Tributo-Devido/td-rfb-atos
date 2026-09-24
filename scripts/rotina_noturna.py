"""rotina_noturna.py — coleta, categorização e vetores do rfb_atos, sem ninguém olhando.

Uma rodada por noite (Agendador de Tarefas do Windows, `rotina_noturna.ps1`):
  1. coleta: tudo o que o portal publicou desde a última publicação da base (menos 10 dias de
     folga) e falta na base — todos os tipos de ato (`coletar_atos_portal.coletar_novos`);
  2. categorização automática com portão de qualidade (`categorizar_nuvem.automatico`), só dos
     tipos liberados em `references/rotina_noturna.json` — cada tipo só entra depois da rodada de
     pesquisa dele (decisão do dono em 24/09/2026); o que reprova vai para revisão;
  3. resumo em RFB_ATOS_DADOS/rotina/AAAA-MM-DD.json e log em AAAA-MM-DD.log.

Trava contra duas rodadas ao mesmo tempo (rotina/rotina.lock; vencida após 12 h). Um passo com
erro não impede o outro. Saída: 0 ok; 1 algum erro (ou banco fora do ar: VPN); 2 já rodando.

Uso:
    python rotina_noturna.py              # rodada completa
    python rotina_noturna.py --sem-coleta # só categoriza a fila
    python rotina_noturna.py --plano      # mostra o que faria, sem chamar modelo nem gravar
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

import categorizar_nuvem as cn
import coletar_atos_portal as col

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
           portal=None, chamar=None, embed=None, log=print) -> dict:
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
                resumo["coleta"] = {
                    "desde": desde.isoformat(), "no_portal": len(r),
                    "gravados": sum(1 for x in r if x.get("ato_id") and not x.get("ja_existia")),
                    "erros": sum(1 for x in r if x.get("erro"))}
            except Exception as e:
                log(f"[coleta] ERRO {type(e).__name__}: {e}")
                resumo["coleta"] = {"erro": f"{type(e).__name__}: {e}"}
        try:
            atos = cn.carregar_atos(conn, pendentes=True, limit=cfg["limite_atos_por_noite"],
                                    tipos=cfg["tipos_categorizar"])
            estimado = sum(cn.estimar(a, cn.MODELO_PADRAO)["custo"] for a in atos)
            log(f"[categorização] {len(atos)} atos na fila dos tipos liberados "
                f"{cfg['tipos_categorizar']} | estimado ~US$ {estimado:.2f} | teto da noite "
                f"US$ {cfg['teto_usd_por_noite']:.2f}")
            if plano:
                resumo["categorizacao"] = {"fila": len(atos), "estimado_usd": round(estimado, 2)}
            else:
                cn.exigir_schema(conn)
                cn.exigir_permissoes(conn)
                uso = cn.Uso()
                r = cn.automatico(
                    atos, chamar or cn.chamador_anthropic(),
                    conectar=lambda: psycopg.connect(dsn, autocommit=True), run_id=run_id,
                    uso=uso, embed=embed, paralelo=cfg["paralelo"],
                    teto_usd=cfg["teto_usd_por_noite"], custo_max_ato=cfg["custo_max_ato"],
                    saida=log)
                estados: dict[str, int] = {}
                for x in r:
                    e = ("revisar" if x.get("revisar") else "erro" if x.get("erro")
                         else "pulado" if x.get("pulado") else "gravado")
                    estados[e] = estados.get(e, 0) + 1
                resumo["categorizacao"] = {"fila": len(atos), **estados,
                                           "custo_usd": round(uso.custo(), 2),
                                           "uso": uso.por_modelo}
        except SystemExit as e:
            log(f"[categorização] ERRO {e}")
            resumo["categorizacao"] = {"erro": str(e)}
        except Exception as e:
            log(f"[categorização] ERRO {type(e).__name__}: {e}")
            resumo["categorizacao"] = {"erro": f"{type(e).__name__}: {e}"}
    resumo["fim"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return resumo


def teve_erro(resumo: dict) -> bool:
    return any(isinstance(resumo.get(k), dict) and (resumo[k].get("erro") or resumo[k].get(
        "erros")) for k in ("coleta", "categorizacao"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sem-coleta", action="store_true")
    p.add_argument("--plano", action="store_true", help="não chama modelo nem grava")
    p.add_argument("--dsn")
    args = p.parse_args()
    pasta = _pasta()
    log = Log(pasta / f"{date.today().isoformat()}.log")
    trava = travar(pasta)
    if trava is None:
        log("[rotina] outra rodada em andamento (rotina.lock): saindo")
        sys.exit(2)
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        dsn = cn._dsn(args.dsn, not args.plano)
        if not banco_alcancavel(dsn):
            log("[rotina] banco fora de alcance (VPN desligada?): nada feito; tenta na próxima")
            sys.exit(1)
        log(f"[rotina] início | config {cfg}")
        resumo = rodada(cfg, dsn=dsn, coletar=not args.sem_coleta, plano=args.plano, log=log)
        (pasta / f"{date.today().isoformat()}.json").write_text(
            json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"[rotina] fim | {json.dumps(resumo, ensure_ascii=False, default=str)[:600]}")
        sys.exit(1 if teve_erro(resumo) else 0)
    finally:
        trava.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
