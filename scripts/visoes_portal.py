"""visoes_portal.py — como transformar uma visão do portal (normasinternet2) em texto.

Regra de exibição, medida contra o portal em 13/09/2026 na IN RFB 2.121/2022 (idAto 127905):
em cada visão (vigente, original) o portal manda TODOS os segmentos, de todas as versões, e
marca com `omitir` os que não aparecem naquela visão. **Mostrar = `omitir` falso.**

  - com essa regra cada dispositivo sai em uma única versão (0 idSegmento com mais de uma) e o
    art. 171 sai só na redação atual, nas duas visões;
  - concatenar tudo (o que o extrator antigo fazia) repete redações antigas: art. 171 três
    vezes, +16% de texto — o texto dos atos alterados na base mistura versões;
  - filtrar por `compilado` perde 237 segmentos exibidos na vigente e o art. 171 na original.

Funções puras, sem rede e sem banco.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime


def normalizar(texto: str | None) -> str:
    """Desfaz entidades HTML (`&nbsp;` etc.) e espaços repetidos; mantém quebras de linha."""
    t = html.unescape(texto or "").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def _todos(visao: dict) -> list[dict]:
    return list(visao.get("ementas") or []) + list(visao.get("outrosSegmentos") or [])


def segmentos_exibidos(visao: dict) -> list[dict]:
    """Os segmentos que o portal mostra nesta visão, na ordem do ato."""
    return sorted((s for s in _todos(visao) if not s.get("omitir")),
                  key=lambda s: s.get("ordemSegmentoAto") or 0)


def corpo_em_pdf(visao: dict) -> bool:
    """True se algum segmento exibido traz o corpo só como anexo (sem texto no JSON)."""
    return any(s.get("arquivoBinario") and not normalizar(s.get("textoIntegra"))
               for s in segmentos_exibidos(visao))


def texto_da_visao(visao: dict) -> str:
    """Texto da visão: ementas (marcadas como no ato_content atual) + corpo, na ordem do ato."""
    ids_ementa = {id(s) for s in visao.get("ementas") or []}
    partes = []
    for s in segmentos_exibidos(visao):
        t = normalizar(s.get("textoIntegra"))
        if t:
            partes.append(f"## EMENTA\n{t}" if id(s) in ids_ementa else t)
    return "\n\n".join(partes)


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def alterado(visao: dict) -> bool:
    return bool(visao.get("historico")) or any(
        (s.get("versaoSegmento") or 1) > 1 for s in _todos(visao))


def status_vigencia(visao: dict) -> str:
    """Só o vocabulário do portal: vigente / não vigente (a causa fica na aresta `interrompe`)."""
    if not visao.get("vigente"):
        return "nao_vigente"
    return "vigente_alterado" if alterado(visao) else "vigente_nunca_alterado"


def situacao(visao: dict) -> dict:
    """O que o portal disse, bruto, para `ato.situacao_portal`."""
    campos = ("idAto", "vigente", "visao", "exibirVisoes", "integro", "dataPublicacao",
              "dataVigenciaInicio", "ehRepublicado")
    return {c: visao.get(c) for c in campos}


def data_dmy(valor: str | None) -> date | None:
    """'30/12/2022' (ou com hora) → date; None se vazio ou fora do formato."""
    if not valor:
        return None
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    return None


def data_iso(valor: str | None) -> date | None:
    try:
        return date.fromisoformat(valor[:10]) if valor else None
    except ValueError:
        return None


def linhas_segmento(visao: dict) -> list[dict]:
    """Todas as versões de todos os segmentos, com as marcas — para `ato_segmento`."""
    linhas = []
    for s in _todos(visao):
        if s.get("idSegmento") is None:
            continue
        linhas.append({
            "id_segmento": int(s["idSegmento"]),
            "versao_segmento": int(s.get("versaoSegmento") or 1),
            "ordem": s.get("ordemSegmentoAto"),
            "id_tipo_segmento": s.get("idTipoSegmento"),
            "id_assunto": s.get("idAssunto"),
            "texto_integra": s.get("textoIntegra"),
            "is_original": bool(s.get("original")),
            "is_compilado": bool(s.get("compilado")),
            "is_tachado": bool(s.get("tachado")),
            "is_omitido": bool(s.get("omitir")),
            "is_agendado": bool(s.get("agendado")),
            "raw": {k: s.get(k) for k in ("mapper", "arquivoBinario", "ancorasOrigem",
                                          "ancorasDestino")},
        })
    return linhas


def linhas_historico(visao: dict) -> list[dict]:
    """Anotações de alteração — para `ato_alteracao_historico`."""
    linhas = []
    for h in visao.get("historico") or []:
        if h.get("idAto") is None or h.get("idSegmento") is None:
            continue
        linhas.append({
            "id_segmento_alvo": int(h["idSegmento"]),
            "id_ato_modificador_portal": int(h["idAto"]),
            "data_inicio_vigencia": data_dmy(h.get("dataInicioVigencia")),
            "data_republicacao": data_dmy(h.get("dataRepublicacao")),
            "texto_anotacao": h.get("texto"),
            "raw_anotacao_id": h.get("idAnotacao"),
            "eh_agendamento": bool(h.get("ehAgendamento")),
            "texto_agendamento": h.get("textoAgendamento"),
        })
    return linhas
