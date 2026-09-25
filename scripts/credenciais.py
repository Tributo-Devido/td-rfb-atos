"""credenciais.py — de onde vêm os DSNs e as chaves do rfb_atos.

Ordem, a mesma do `libs/secrets.py` do td-analise-piscofins: **variável de ambiente → SSM
Parameter Store**. Nunca arquivo local: o `~/.claude-tg-bot/ratio-pg-dsn.txt` foi aposentado em
13/09/2026 (guardava DSN de admin em texto aberto).

    nome       variável              parâmetro SSM                  usuário / uso
    leitura    RFB_ATOS_DSN_LEITURA  /td/db/ratio-pg-dsn            ratio_leitura (padrão)
    escrita    RFB_ATOS_DSN_ESCRITA  /td/batch/rfb-writer-dsn       rfb_writer (coleta e carga)
    admin      RFB_ATOS_DSN_ADMIN    /td/admin/ratio-pg-dsn-admin   ratio_admin, só migrations
                                                                    (perfil AWS `executive`)
    openai     OPENAI_API_KEY        /td/llm/openai-api-key         vetores
    anthropic  ANTHROPIC_API_KEY     /td/llm/anthropic-api-key      categorização

`RFB_ATOS_DSN`, se definida, vale para qualquer DSN — é o que os testes e o CI usam.
`RFB_ATOS_AWS_PROFILE` troca o perfil AWS da consulta ao SSM.
Nenhuma função daqui imprime ou registra o valor de uma credencial.
"""
from __future__ import annotations

import os
import subprocess
import time

REGISTRO: dict[str, tuple[str, str, str | None]] = {
    "leitura": ("RFB_ATOS_DSN_LEITURA", "/td/db/ratio-pg-dsn", None),
    "escrita": ("RFB_ATOS_DSN_ESCRITA", "/td/batch/rfb-writer-dsn", None),
    "admin": ("RFB_ATOS_DSN_ADMIN", "/td/admin/ratio-pg-dsn-admin", "executive"),
    "openai": ("OPENAI_API_KEY", "/td/llm/openai-api-key", None),
    "anthropic": ("ANTHROPIC_API_KEY", "/td/llm/anthropic-api-key", None),
    # webhook de entrada do canal que recebe o resumo da rotina noturna (acompanhamento.py)
    "slack": ("RFB_ATOS_SLACK_WEBHOOK", "/td/slack/rfb-atos-rotina-webhook", None),
}
DSNS = frozenset({"leitura", "escrita", "admin"})
OVERRIDE_DSN = "RFB_ATOS_DSN"


class CredencialAusente(RuntimeError):
    """Nenhuma fonte entregou a credencial. A mensagem diz onde configurar, sem valor."""


def _regiao() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"


_ULTIMO_ERRO: dict[str, str] = {}      # parâmetro -> motivo da última falha (sem valor)


def _ssm(parametro: str, perfil: str | None, *, tentativas: int = 3,
         espera: float = 10) -> str | None:
    """Até `tentativas` leituras, com espera crescente: queda curta de rede não vira credencial
    ausente (rodada de 25/09/2026). O motivo da última falha fica em _ULTIMO_ERRO."""
    _ULTIMO_ERRO.pop(parametro, None)        # nunca mostrar a falha de uma leitura antiga
    for i in range(tentativas):
        valor = _ssm_uma(parametro, perfil)
        definitivo = any(x in _ULTIMO_ERRO.get(parametro, "")
                         for x in ("AccessDenied", "ParameterNotFound", "UnrecognizedClient"))
        if valor or definitivo or i == tentativas - 1:   # sem permissão não passa esperando
            return valor
        time.sleep(espera * (i + 1))
    return None


def _ssm_uma(parametro: str, perfil: str | None) -> str | None:
    """Lê um SecureString do SSM; None se não conseguir (sem login AWS, sem permissão, sem rede).

    Tenta boto3 (se instalado) e cai para a CLI `aws`, como o `libs/secrets.py`.
    """
    try:
        import boto3
    except ImportError:
        boto3 = None
    if boto3 is not None:
        try:
            sessao = boto3.Session(profile_name=perfil) if perfil else boto3.Session()
            resp = sessao.client("ssm", region_name=_regiao()).get_parameter(
                Name=parametro, WithDecryption=True)
            return resp["Parameter"]["Value"].strip() or None
        except Exception as e:
            _ULTIMO_ERRO[parametro] = f"boto3: {type(e).__name__}: {str(e)[:200]}"
    cmd = ["aws", "ssm", "get-parameter", "--name", parametro, "--with-decryption",
           "--region", _regiao(), "--query", "Parameter.Value", "--output", "text"]
    if perfil:
        cmd += ["--profile", perfil]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        _ULTIMO_ERRO[parametro] = f"aws cli: {type(e).__name__}"
        return None
    valor = r.stdout.strip()
    if r.returncode == 0 and valor and valor != "None":
        return valor
    # stderr da CLI traz o motivo (sem rede, sem permissão, token expirado), nunca o valor
    motivo = (r.stderr or "").strip().splitlines()
    cli = f"aws cli: {motivo[-1][:200] if motivo else f'saída {r.returncode}'}"
    boto = _ULTIMO_ERRO.get(parametro, "")
    _ULTIMO_ERRO[parametro] = f"{boto} | {cli}" if boto.startswith("boto3") else cli
    return None


def _buscar(nome: str) -> tuple[str | None, str | None]:
    if nome not in REGISTRO:
        raise KeyError(f"credencial desconhecida: {nome!r} (conhecidas: {sorted(REGISTRO)})")
    variavel, parametro, perfil = REGISTRO[nome]
    if nome in DSNS and os.environ.get(OVERRIDE_DSN, "").strip():
        return os.environ[OVERRIDE_DSN].strip(), "env"
    valor = os.environ.get(variavel, "").strip()
    if valor:
        return valor, "env"
    valor = _ssm(parametro, os.environ.get("RFB_ATOS_AWS_PROFILE") or perfil)
    if valor:
        return valor, "ssm"
    return None, None


def resolver(nome: str) -> str:
    """Devolve a credencial `nome` ou levanta `CredencialAusente` dizendo onde configurar."""
    valor, _ = _buscar(nome)
    if valor is None:
        variavel, parametro, perfil = REGISTRO[nome]
        dica = f" com o perfil AWS `{perfil}`" if perfil else ""
        raise CredencialAusente(
            f"sem credencial `{nome}`: defina {variavel} ou garanta ssm:GetParameter em "
            f"{parametro}{dica} (região {_regiao()})."
            + (f" Última falha: {_ULTIMO_ERRO[parametro]}" if parametro in _ULTIMO_ERRO else "")
        )
    return valor


def fonte(nome: str) -> str | None:
    """De onde a credencial viria agora: 'env', 'ssm' ou None — sem devolver o valor."""
    return _buscar(nome)[1]


def resolver_dsn(tipo: str = "leitura") -> str:
    """DSN do banco `ratio`: 'leitura' (padrão), 'escrita' ou 'admin'."""
    if tipo not in DSNS:
        raise KeyError(f"tipo de DSN desconhecido: {tipo!r} (use {sorted(DSNS)})")
    return resolver(tipo)
