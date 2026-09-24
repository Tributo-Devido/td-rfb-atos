"""acompanhamento.py — como o dono fica sabendo o que a rotina noturna fez.

Três saídas, todas a partir do resumo da rodada (`rotina_noturna.rodada`):
  - mensagem no Slack (webhook de entrada, em `/td/slack/rfb-atos-rotina-webhook` no SSM ou na
    variável RFB_ATOS_SLACK_WEBHOOK): o resumo da noite, e um alerta quando algo falha;
  - linha em `rfb_atos.rotina_execucao` (migration 012): o histórico, que o time também lê;
  - `conferir()`: a checagem da manhã — se nenhuma rodada terminou nas últimas horas, alerta.

Sem webhook configurado, só registra no log; sem a tabela, só no arquivo. Nunca derruba a rotina.
"""
from __future__ import annotations

import json
import socket
import time
from datetime import date, datetime, timedelta
from pathlib import Path

NOME_TIPO = {
    "ATO_DECLARATORIO_EXECUTIVO": "ADE", "SOLUCAO_CONSULTA": "SC", "PORTARIA": "Portaria",
    "INSTRUCAO_NORMATIVA": "IN", "SOLUCAO_DIVERGENCIA": "SD", "SOLUCAO_CONSULTA_INTERNA": "SCI",
    "PARECER_NORMATIVO": "PN", "ATO_DECLARATORIO_INTERPRETATIVO": "ADI",
    "ATO_DECLARATORIO_NORMATIVO": "ADN",
}


def numeros(resumo: dict) -> dict:
    """Os números da rodada, no formato da tabela rotina_execucao."""
    coleta = resumo.get("coleta") or {}
    cat = resumo.get("categorizacao") or {}
    aplicados = resumo.get("lotes_aplicados") or []
    erro_etapa = bool(coleta.get("erro") or cat.get("erro"))
    erros_col = coleta.get("erros", 0) or 0
    erros_cat = sum(x.get("erros", 0) for x in aplicados)
    if resumo.get("falhou"):
        estado = "falhou"
    elif erro_etapa or erros_col or erros_cat:
        estado = "com_erro"
    else:
        estado = "ok"
    return {
        "estado": estado,
        "atos_coletados": coleta.get("gravados", 0) or 0,
        "coletados_por_tipo": coleta.get("por_tipo") or {},
        "erros_coleta": erros_col,
        "atos_categorizados": sum(x.get("gravados", 0) for x in aplicados),
        "materias_gravadas": sum(x.get("materias", 0) for x in aplicados),
        "para_revisao": sum(x.get("revisar", 0) for x in aplicados)
        + (cat.get("para_revisao_sem_modelo", 0) or 0),
        "erros_categorizacao": erros_cat,
        "lotes_enviados": len(cat.get("lotes_enviados") or []),
        "atos_enviados": cat.get("atos_enviados", 0) or 0,
        "custo_usd": round(sum(x.get("custo_usd", 0) or 0 for x in aplicados), 2),
        "base_atualizada_ate": resumo.get("base_atualizada_ate"),
    }


def _data(valor) -> str:
    if not valor:
        return "?"
    if isinstance(valor, str):
        valor = date.fromisoformat(valor[:10])
    return valor.strftime("%d/%m/%Y")


def mensagem(resumo: dict) -> str:
    """Texto do Slack (mrkdwn)."""
    n = numeros(resumo)
    icone = {"ok": ":white_check_mark:", "com_erro": ":warning:", "falhou": ":x:"}[n["estado"]]
    noite = _data(resumo.get("inicio"))
    linhas = [f"*rfb_atos — rodada de {noite}* {icone}"]
    if resumo.get("falhou"):
        linhas.append(f"Não rodou: {resumo['falhou']}")
        return "\n".join(linhas)
    coleta = resumo.get("coleta") or {}
    if coleta.get("erro"):
        linhas.append(f"Coleta: *erro* — {coleta['erro'][:200]}")
    elif coleta:
        tipos = ", ".join(f"{q} {NOME_TIPO.get(t, t)}" for t, q in sorted(
            n["coletados_por_tipo"].items(), key=lambda x: -x[1]))
        linhas.append(f"Coleta: *{n['atos_coletados']}* atos novos"
                      + (f" ({tipos})" if tipos else "")
                      + (f" · {n['erros_coleta']} recusados pelo portal" if n["erros_coleta"]
                         else " · 0 erros"))
    cat = resumo.get("categorizacao") or {}
    if cat.get("erro"):
        linhas.append(f"Categorização: *erro* — {cat['erro'][:200]}")
    else:
        linhas.append(f"Categorização: *{n['atos_categorizados']}* atos gravados "
                      f"({n['materias_gravadas']} matérias) · {n['para_revisao']} para revisão"
                      + (f" · {n['erros_categorizacao']} com erro" if n["erros_categorizacao"]
                         else "") + f" · US$ {n['custo_usd']:.2f}")
        if n["lotes_enviados"]:
            linhas.append(f"Lote enviado: {n['atos_enviados']} atos (gravados na próxima rodada)")
    linhas.append(f"Base atualizada até {_data(n['base_atualizada_ate'])}")
    return "\n".join(linhas)


def enviar_slack(texto: str, *, postar=None, log=print) -> bool:
    """Posta no webhook; sem webhook ou com falha, só registra no log."""
    try:
        from credenciais import CredencialAusente, resolver
        try:
            url = resolver("slack")
        except CredencialAusente:
            log("[acompanhamento] sem webhook do Slack configurado: mensagem só no log")
            return False
        if postar is None:
            import requests

            def postar(u, corpo):
                return requests.post(u, json=corpo, timeout=30).status_code
        codigo = postar(url, {"text": texto})
        if codigo != 200:
            log(f"[acompanhamento] Slack respondeu {codigo}")
            return False
        return True
    except Exception as e:
        log(f"[acompanhamento] falha ao postar no Slack: {type(e).__name__}: {e}")
        return False


def registrar(conn, resumo: dict, *, log=print) -> bool:
    """Grava a rodada em rfb_atos.rotina_execucao (se a migration 012 existir)."""
    try:
        if conn.execute("SELECT to_regclass('rfb_atos.rotina_execucao')").fetchone()[0] is None:
            log("[acompanhamento] tabela rotina_execucao ausente (migration 012): só o arquivo")
            return False
        from psycopg.types.json import Jsonb
        n = numeros(resumo)
        conn.execute(
            "INSERT INTO rfb_atos.rotina_execucao (run_id, inicio, fim, estado, atos_coletados, "
            "coletados_por_tipo, erros_coleta, atos_categorizados, materias_gravadas, "
            "para_revisao, erros_categorizacao, lotes_enviados, atos_enviados, custo_usd, "
            "base_atualizada_ate, maquina, resumo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO UPDATE SET "
            "fim = EXCLUDED.fim, estado = EXCLUDED.estado, resumo = EXCLUDED.resumo",
            (resumo["run_id"], resumo["inicio"], resumo.get("fim"), n["estado"],
             n["atos_coletados"], Jsonb(n["coletados_por_tipo"]), n["erros_coleta"],
             n["atos_categorizados"], n["materias_gravadas"], n["para_revisao"],
             n["erros_categorizacao"], n["lotes_enviados"], n["atos_enviados"], n["custo_usd"],
             n["base_atualizada_ate"], socket.gethostname(),
             Jsonb(json.loads(json.dumps(resumo, default=str)))))
        return True
    except Exception as e:
        log(f"[acompanhamento] falha ao registrar a rodada no banco: {type(e).__name__}: {e}")
        return False


def ultima_rodada(pasta: Path) -> tuple[datetime | None, dict | None]:
    """A rodada mais recente pelos resumos do disco (rotina/AAAA-MM-DD.json)."""
    arquivos = sorted(pasta.glob("????-??-??.json"))
    if not arquivos:
        return None, None
    resumo = json.loads(arquivos[-1].read_text(encoding="utf-8"))
    fim = resumo.get("fim") or resumo.get("inicio")
    return (datetime.fromisoformat(fim) if fim else None), resumo


def conferir(pasta: Path, *, agora: datetime | None = None, horas: int = 26,
             postar=None, log=print) -> str:
    """Checagem da manhã: 'ok', 'atrasada' (nenhuma rodada nas últimas `horas`) ou 'com_erro'."""
    agora = agora or datetime.now()
    quando, resumo = ultima_rodada(pasta)
    if quando is None or agora - quando > timedelta(hours=horas):
        desde = quando.strftime("%d/%m/%Y %H:%M") if quando else "nunca"
        enviar_slack(f"*rfb_atos* :rotating_light: a rotina noturna *não rodou* nas últimas "
                     f"{horas} h (última rodada: {desde}). Verifique se o notebook estava ligado, "
                     "a VPN conectada e a tarefa 'td-rfb-atos rotina noturna' ativa.",
                     postar=postar, log=log)
        return "atrasada"
    if numeros(resumo)["estado"] != "ok":
        log("[acompanhamento] última rodada com erro: o resumo dela já foi para o Slack")
        return "com_erro"
    return "ok"


def painel(conn, *, noites: int = 14) -> str:
    """As últimas rodadas registradas no banco, uma por linha."""
    linhas = [f"{'início':<17} {'estado':<9} {'coletados':>9} {'categorizados':>13} "
              f"{'revisão':>7} {'US$':>7}  base até"]
    for r in conn.execute(
            "SELECT inicio, estado, atos_coletados, atos_categorizados, para_revisao, custo_usd, "
            "base_atualizada_ate FROM rfb_atos.rotina_execucao ORDER BY inicio DESC LIMIT %s",
            (noites,)):
        linhas.append(f"{r[0]:%d/%m/%Y %H:%M} {r[1]:<9} {r[2]:>9} {r[3]:>13} {r[4]:>7} "
                      f"{float(r[5] or 0):>7.2f}  {r[6] and r[6].strftime('%d/%m/%Y')}")
    return "\n".join(linhas)


def agora_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
