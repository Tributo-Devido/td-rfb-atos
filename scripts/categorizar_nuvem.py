"""categorizar_nuvem.py — categoriza atos do rfb_atos na nuvem: matérias, sinal e vetor.

Leva para o schema da nuvem (`rfb_atos.ato`, `ato_content.texto_completo`) o que o
`categorize_with_llm.py` e o `categorize_batch.py` faziam no Docker local (F2c da recoleta).
Primeiro uso: a IN RFB 2.121/2022 (ato 16630), carregada do portal na F1.

Três etapas por ato, cada uma idempotente:
    1. matérias  o modelo lê o teor e devolve as matérias (tema, tributos, dispositivos, CNAEs);
                 grava ato_materia + materia_tributo/dispositivo/cnae e marca analise_completa
    2. sinal     AUTORIZA / VEDA / CONDICIONA / INDETERMINADO por matéria (Haiku, prompt de
                 `lib/sinal.py`)
    3. vetor     text-embedding-3-large (3072), o modelo de vetores do ecossistema

Diferenças deliberadas em relação aos scripts do Docker (docs/DECISOES.md, 13/09/2026):
    - texto longo é dividido em partes pelos cabeçalhos do ato (livro, título, capítulo...), em vez
      de cortado em 100 mil caracteres: a IN 2.121 tem 1,24 milhão e o corte deixaria 92% de fora;
    - só categoriza ato com texto e sem matéria: o rfb_writer não apaga, e recategorizar é decisão
      à parte;
    - as relações que o modelo sugere não são gravadas: vêm do portal, com o vocabulário do portal
      (o prompt fala em `revoga`, que a base não usa);
    - o modelo não mexe na vigência (é do portal) e só preenche `eficacia_atual` vazia;
    - o HTML do portal sai do texto enviado ao modelo; o texto guardado não muda.

Modelo: Sonnet nas INs centrais de PIS/COFINS (decisão do dono, 13/09/2026), Haiku 4.5 no resto.
As respostas ficam em disco (`RFB_ATOS_DADOS/categorizacao/<ato>/`): rodar de novo não paga duas
vezes. Toda alteração em linha existente vai para `ato_mudanca` com o run_id (exige a 011).

Uso:
    python categorizar_nuvem.py --ato 16630             # plano: partes, modelo, custo estimado
    python categorizar_nuvem.py --ato 16630 --gerar     # chama o modelo, relatório; NÃO grava
    python categorizar_nuvem.py --ato 16630 --executar  # grava o que o --gerar mostrou
    python categorizar_nuvem.py --pendentes --limit 20  # fila: com texto, sem análise, sem matéria
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from lib.embed_openai import DEFAULT_DIM, embed_texts_sync, vector_literal
from lib.sinal import SISTEMA_PROMPT as PROMPT_SINAL
from lib.sinal import montar_user_content as mensagem_sinal
from lib.sinal import parse_sinal
from normalize import (
    decompor_tributo_composto,
    normalizar_natureza,
    normalizar_regime,
    normalizar_resultado,
    normalizar_tema_especifico,
    normalizar_tema_macro,
    normalizar_tipo_norma,
    normalizar_tipo_uso,
)
from reembed_cloud import build_text

REFS = Path(__file__).resolve().parent.parent / "references"
PROMPT_SC = REFS / "prompts" / "prompt_extrator_sc.md"
PROMPT_NORMATIVO = REFS / "prompts" / "prompt_extrator_normativos.md"
TAXONOMIA = REFS / "taxonomia.json"
SCHEMAS_DIR = REFS / "schemas_metadata_tematico"
TIPOS_SC = {"SOLUCAO_CONSULTA", "SOLUCAO_DIVERGENCIA", "SOLUCAO_CONSULTA_INTERNA"}
# Rodada de pesquisa das Soluções de Consulta (24/09/2026): o portal publica a SC DISIT e COANA —
# e boa parte das COSIT — só pela ementa (assunto, tese, dispositivos), como sai no DOU; a íntegra
# (relatório, fundamentos) não existe no portal. 99% da fila pendente é assim.
_INTEGRA_SC = re.compile(r"\b(RELAT[ÓO]RIO|FUNDAMENTOS?|FUNDAMENTA[ÇC][ÃA]O)\b", re.IGNORECASE)


def base_da_analise(ato: dict) -> str:
    """'ementa' quando a SC só tem a ementa publicada; 'texto' no resto (ato_materia.base_analise,
    migration 011)."""
    if ato.get("tipo_ato") in TIPOS_SC and not _INTEGRA_SC.search(ato.get("texto_completo") or ""):
        return "ementa"
    return "texto"

MODELO_CADEIA = "claude-sonnet-5"
MODELO_MASSA = "claude-haiku-4-5-20251001"   # o que categorizou a base antiga
# Decisão do dono em 24/09/2026: categorização com Sonnet no mínimo — em todos os tipos, e no
# sinal também. O Haiku fica só como referência do legado.
MODELO_PADRAO = MODELO_CADEIA
MODELO_SINAL = MODELO_CADEIA
# Só o Haiku 4.5 recebe temperature=0: o Sonnet 5 recusa valor diferente do padrão (HTTP 400; guia
# de migração do Sonnet 5, apontado na revisão 4-LLM, Codex v4). O td-analise-piscofins já chama o
# claude-sonnet-5 sem o parâmetro (scripts/carf-categorizacao/prod_runner.py).
MODELOS_COM_TEMPERATURA = {MODELO_MASSA}
# INs centrais de PIS/COFINS (docs/DECISOES.md, 13/09/2026): (número só com dígitos, ano).
CADEIA_PISCOFINS = {("247", 2002), ("457", 2004), ("660", 2006), ("1717", 2017),
                    ("1911", 2019), ("2121", 2022)}

# Medido na IN 2.121 (13/09/2026): o Sonnet devolve ~1 token por caractere de teor. Com 60 mil
# caracteres por parte, 44 de 147 respostas estouraram o teto e foram pagas e descartadas. Com
# 25 mil e teto de 32 mil tokens, sobra folga.
LIMITE_PARTE = 25_000      # caracteres de teor por chamada
MINIMO_PARTE = 4_000       # parte cortada no limite de tokens só é dividida se tiver 2x isto
MAX_TOKENS = 32_000        # a chamada é por streaming (o prod_runner do CARF usa o mesmo)
MAX_TOKENS_SINAL = 20      # o sinal é uma palavra
FONTE_EMBEDDING = "openai_text_embedding_3_large_3072"

# Só para o plano (--executar mostra o uso real). Preço de referência por milhão de tokens
# (entrada, saída); conferir na fatura. Cache: escrever custa 1,25x a entrada, ler 0,1x.
PRECO_REFERENCIA = {MODELO_CADEIA: (3.0, 15.0), MODELO_MASSA: (1.0, 5.0)}
CARACTERES_POR_TOKEN = 3.5
SAIDA_POR_CARACTERE = 1.0  # tokens de resposta por caractere de teor (IN 2.121)
SAIDA_MINIMA = 1_500       # por chamada: ato curto ainda devolve JSON com metadados
CUSTO_MAX_ATO = 3.0        # US$: acima disso o modo automático não chama; vai para revisão
MINIMO_CORPO = 200         # caracteres de corpo além da ementa para categorizar

REGRAS_SAIDA = (
    "REGRAS DE OUTPUT:\n"
    "- Responda APENAS um JSON valido conforme o schema do prompt acima.\n"
    "- NAO inclua texto antes ou depois do JSON.\n"
    "- NAO use markdown code fences (```).\n"
    "- Comece a resposta diretamente com '{' e termine com '}'.\n"
    "- JSON COMPACTO: numa linha só, sem indentação nem espaços fora dos textos (a resposta é "
    "paga por token)."
)

_CABECALHO = re.compile(
    r"^(LIVRO|T[ÍI]TULO|CAP[ÍI]TULO|SUBSE[ÇC][ÃA]O|SE[ÇC][ÃA]O|ANEXO)\s+"
    r"([IVXLCDM]+|\d+|[ÚU]NIC[OA])\b", re.IGNORECASE)
_NIVEL = {"LIVRO": 0, "ANEXO": 0, "TITULO": 1, "CAPITULO": 2, "SECAO": 3, "SUBSECAO": 4}
_ARTIGO = re.compile(r"^Art\.\s*\d", re.IGNORECASE)
_ARTIGO_NUM = re.compile(r"^Art\.\s*(\d+[\w-]*)", re.IGNORECASE)
_TAG_QUEBRA = re.compile(r"<\s*br\s*/?\s*>|</\s*p\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class FalhaCategorizacao(RuntimeError):
    """O modelo não devolveu matérias utilizáveis; nada foi gravado para o ato."""


class AtoInapto(RuntimeError):
    """O ato não está em condição de ser categorizado (sem texto, já analisado, já com matéria)."""


class SemPermissao(RuntimeError):
    """O usuário do banco não pode gravar o que a categorização grava."""


class SemRespostaConferida(FalhaCategorizacao):
    """O --executar só grava matéria que o --gerar mostrou: sem a resposta em disco, não chama o
    modelo (revisão 4-LLM, Codex v3)."""


# ---------------------------------------------------------------------------
# Texto e partição
# ---------------------------------------------------------------------------

def texto_para_llm(texto: str | None) -> str:
    """Tira o HTML do portal (quebras viram linha) e junta espaços; os parágrafos continuam."""
    t = _TAG_QUEBRA.sub("\n", texto or "")
    t = html.unescape(_TAG.sub("", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


@dataclass
class Parte:
    rotulo: str      # "3", ou "3.1" quando uma parte cortada foi dividida
    texto: str
    caminho: str     # cabeçalhos em vigor no início da parte ("LIVRO I > TÍTULO VI (...)")


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _tam(blocos: list[str]) -> int:
    return sum(len(b) for b in blocos) + 2 * max(0, len(blocos) - 1)


def _rotulo_cabecalho(blocos: list[str], i: int) -> str:
    """Rótulo do cabeçalho `blocos[i]` com o nome dele, que o portal às vezes põe no mesmo
    segmento ("TÍTULO I<br>DO FATO GERADOR") e às vezes no seguinte ("Seção I" / "Do Crédito")."""
    linhas = [x.strip() for x in blocos[i].split("\n") if x.strip()]
    nome = linhas[1] if len(linhas) > 1 else ""
    if not nome and i + 1 < len(blocos):
        seguinte = blocos[i + 1].strip()
        if (seguinte and len(seguinte) <= 200 and "\n" not in seguinte
                and not _ARTIGO.match(seguinte) and not _CABECALHO.match(seguinte)):
            nome = seguinte
    return f"{linhas[0][:60]} ({nome[:60]})" if nome else linhas[0][:60]


def _unidades(blocos: list[str]) -> list[tuple[list[str], str]]:
    """Agrupa os blocos em unidades que começam num cabeçalho; devolve (blocos, caminho)."""
    pilha: dict[int, str] = {}
    unidades: list[tuple[list[str], str]] = []
    atual: list[str] = []
    caminho = ""
    for i, bloco in enumerate(blocos):
        m = _CABECALHO.match(bloco)
        if m:
            if atual:
                unidades.append((atual, caminho))
            nivel = _NIVEL[_sem_acento(m.group(1)).upper()]
            pilha = {k: v for k, v in pilha.items() if k < nivel}
            pilha[nivel] = _rotulo_cabecalho(blocos, i)
            atual, caminho = [], " > ".join(pilha[k] for k in sorted(pilha))
        atual.append(bloco)
    if atual:
        unidades.append((atual, caminho))
    return unidades


def _por_linha(bloco: str, limite: int) -> list[str]:
    """Bloco sozinho maior que o limite: corta entre linhas (linha gigante, corta seco)."""
    pedacos: list[str] = []
    atual = ""
    for linha in bloco.split("\n"):
        while len(linha) > limite:
            if atual:
                pedacos.append(atual)
                atual = ""
            pedacos.append(linha[:limite])
            linha = linha[limite:]
        if atual and len(atual) + 1 + len(linha) > limite:
            pedacos.append(atual)
            atual = linha
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    if atual:
        pedacos.append(atual)
    return pedacos


def _fatiar(blocos: list[str], limite: int) -> list[list[str]]:
    """Unidade maior que o limite: grupos de até `limite`, cortando de preferência antes de um
    artigo (um parágrafo ou inciso não fica separado do caput)."""
    grupos: list[list[str]] = []
    atual: list[str] = []
    for bloco in (p for b in blocos for p in ([b] if len(b) <= limite else _por_linha(b, limite))):
        if atual and _tam(atual) + 2 + len(bloco) > limite:
            corte = None
            if not _ARTIGO.match(bloco):
                corte = max((i for i, x in enumerate(atual) if i > 0 and _ARTIGO.match(x)),
                            default=None)
            if corte is not None and _tam(atual[corte:]) + 2 + len(bloco) <= limite:
                grupos.append(atual[:corte])
                atual = atual[corte:]
            else:
                grupos.append(atual)
                atual = []
        atual.append(bloco)
    if atual:
        grupos.append(atual)
    return grupos


def particionar(texto: str, limite: int | None = None) -> list[Parte]:
    """Divide o texto em partes de até `limite` caracteres, cortando nos cabeçalhos do ato.

    Juntar as partes com "\\n\\n" devolve o texto (só um bloco sozinho maior que o limite é cortado
    entre linhas, e aí some a quebra de linha do corte).
    """
    limite = limite or LIMITE_PARTE
    if len(texto) <= limite:
        return [Parte("1", texto, "")]
    unidades: list[tuple[list[str], str]] = []
    for blocos, caminho in _unidades(texto.split("\n\n")):
        if _tam(blocos) <= limite:
            unidades.append((blocos, caminho))
        else:
            unidades += [(grupo, caminho) for grupo in _fatiar(blocos, limite)]
    partes: list[Parte] = []
    atual: list[str] = []
    caminho_atual = ""
    for blocos, caminho in unidades:
        if atual and _tam(atual) + 2 + _tam(blocos) > limite:
            partes.append(Parte(str(len(partes) + 1), "\n\n".join(atual), caminho_atual))
            atual = []
        if not atual:
            caminho_atual = caminho
        atual += blocos
    if atual:
        partes.append(Parte(str(len(partes) + 1), "\n\n".join(atual), caminho_atual))
    return partes


def sumario(texto: str) -> str:
    """Cabeçalhos do ato, recuados por nível, com o primeiro artigo de cada um."""
    linhas: list[list[str]] = []
    sem_artigo: list[int] = []   # cabeçalhos ainda à espera do primeiro artigo
    blocos = texto.split("\n\n")
    for i, bloco in enumerate(blocos):
        m = _CABECALHO.match(bloco)
        if m:
            nivel = _NIVEL[_sem_acento(m.group(1)).upper()]
            linhas.append(["  " * nivel + _rotulo_cabecalho(blocos, i), ""])
            sem_artigo.append(len(linhas) - 1)
        elif sem_artigo and (a := _ARTIGO_NUM.match(bloco)):
            for i in sem_artigo:
                linhas[i][1] = f" — art. {a.group(1)}"
            sem_artigo = []
    return "\n".join(rotulo + artigo for rotulo, artigo in linhas)


def bloco_sumario(texto: str) -> dict:
    """Sumário do ato inteiro para cada parte saber onde está no todo (revisão 4-LLM de 13/09:
    sem ele, uma remissão "de que trata o art. 171" fica no vácuo). Um por ato, em cache: as
    partes seguintes pagam 10% dele."""
    return {"type": "text", "cache_control": {"type": "ephemeral"},
            "text": "<sumario_do_ato>\nCabeçalhos do ato inteiro, com o primeiro artigo de cada "
                    "um. Cada chamada recebe só uma parte do texto: use o sumário para situar o "
                    "trecho. Quando o trecho remeter a artigo de outra parte, cite o artigo sem "
                    f"presumir o conteúdo dele.\n{sumario(texto)}\n</sumario_do_ato>"}


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

def escolher_modelo(tipo_ato: str | None, numero: str | None, ano: int | None) -> str:
    """Sonnet em tudo (decisão do dono em 24/09/2026; antes, só na cadeia de PIS/COFINS)."""
    return MODELO_PADRAO


def blocos_sistema(tipo_ato: str) -> list[dict]:
    """Prompt do extrator + taxonomia + schemas temáticos; o último bloco marca o cache."""
    prompt = (PROMPT_SC if tipo_ato in TIPOS_SC else PROMPT_NORMATIVO).read_text(encoding="utf-8")
    schemas = {f.stem: json.loads(f.read_text(encoding="utf-8"))
               for f in sorted(SCHEMAS_DIR.glob("*.json")) if not f.stem.startswith("_")}
    return [
        {"type": "text", "text": prompt},
        {"type": "text",
         "text": f"<taxonomia>\n{TAXONOMIA.read_text(encoding='utf-8')}\n</taxonomia>"},
        {"type": "text",
         "text": f"<schemas_metadata_tematico>\n{json.dumps(schemas, ensure_ascii=False, indent=2)}"
                 f"\n</schemas_metadata_tematico>\n\n{REGRAS_SAIDA}",
         "cache_control": {"type": "ephemeral"}},
    ]


def mensagem_usuario(ato: dict, parte: Parte, total: int) -> str:
    fragmento = total > 1 or "." in parte.rotulo
    linhas = [
        "<ato_input>",
        f"TIPO: {ato['tipo_ato']}",
        f"NUMERO: {ato['numero']}",
        f"ORGAO: {ato.get('emissor') or ''}",
        f"PUBLICACAO: {ato.get('data_publicacao') or ''}",
        f"EMENTA: {ato.get('ementa') or '(sem ementa)'}",
    ]
    if fragmento:
        onde = f" (começa em: {parte.caminho})" if parte.caminho else ""
        linhas += ["", f"PARTE {parte.rotulo} de {total} do ato{onde}. As outras partes são "
                   "enviadas em separado: extraia só as matérias deste trecho."]
    if base_da_analise(ato) == "ementa":
        linhas += ["", "ATENÇÃO: este é o conteúdo publicado da Solução de Consulta — a ementa "
                   "(assunto, tese e dispositivos); a íntegra não é publicada. Extraia só o que a "
                   "ementa diz: fato_consultado fica null quando a ementa não o descreve."]
    linhas += ["", "CONTEUDO (trecho):" if fragmento else "CONTEUDO COMPLETO:", parte.texto,
               "</ato_input>", "",
               "Deixe relacoes_com_outros_atos como lista vazia: as relações do ato vêm do portal."]
    return "\n".join(linhas)


@dataclass
class Resposta:
    texto: str
    parada: str            # stop_reason da API ("end_turn", "max_tokens", ...)
    uso: dict              # entrada, saida, cache_criado, cache_lido (tokens)


def argumentos_da_chamada(modelo: str, sistema: list[dict], usuario: str,
                          max_tokens: int) -> dict:
    args = {"model": modelo, "max_tokens": max_tokens, "system": sistema,
            "messages": [{"role": "user", "content": usuario}]}
    if modelo in MODELOS_COM_TEMPERATURA:
        args["temperature"] = 0
    return args


def chamador_anthropic(cliente=None):
    """chamar(modelo, sistema, usuario, max_tokens) -> Resposta. Sem `cliente`, cria o da
    Anthropic com a chave de credenciais (o `cliente` injetado é para teste)."""
    if cliente is None:
        from credenciais import resolver
        chave = resolver("anthropic")  # antes do import: falta de chave dá erro claro sem o SDK
        from anthropic import Anthropic
        cliente = Anthropic(api_key=chave, max_retries=5)

    def chamar(modelo: str, sistema: list[dict], usuario: str, max_tokens: int) -> Resposta:
        with cliente.messages.stream(**argumentos_da_chamada(modelo, sistema, usuario,
                                                             max_tokens)) as fluxo:
            msg = fluxo.get_final_message()
        u = msg.usage
        return Resposta(
            "".join(b.text for b in msg.content if b.type == "text"), msg.stop_reason or "",
            {"entrada": u.input_tokens, "saida": u.output_tokens,
             "cache_criado": u.cache_creation_input_tokens or 0,
             "cache_lido": u.cache_read_input_tokens or 0})

    return chamar


@dataclass
class Uso:
    por_modelo: dict = field(default_factory=dict)
    _trava: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def somar(self, modelo: str, uso: dict, do_disco: bool) -> None:
        with self._trava:
            self._somar(modelo, uso, do_disco)

    def _somar(self, modelo: str, uso: dict, do_disco: bool) -> None:
        d = self.por_modelo.setdefault(modelo, {"chamadas": 0, "do_disco": 0, "entrada": 0,
                                                "saida": 0, "cache_criado": 0, "cache_lido": 0})
        if do_disco:
            d["do_disco"] += 1
            return
        d["chamadas"] += 1
        for k in ("entrada", "saida", "cache_criado", "cache_lido"):
            d[k] += int(uso.get(k) or 0)

    def custo(self) -> float:
        with self._trava:
            return self._custo()

    def _custo(self) -> float:
        total = 0.0
        for modelo, d in self.por_modelo.items():
            p_in, p_out = PRECO_REFERENCIA.get(modelo, (0.0, 0.0))
            total += (d["entrada"] + 1.25 * d["cache_criado"] + 0.1 * d["cache_lido"]) * p_in / 1e6
            total += d["saida"] * p_out / 1e6
        return total


def _dir_respostas(ato_id: int) -> Path:
    base = Path(os.environ.get("RFB_ATOS_DADOS", r"C:\td-rfb-atos-dados"))
    return base / "categorizacao" / str(ato_id)


def arquivo_resposta(ato_id: int, modelo: str, sistema: list[dict], usuario: str,
                     max_tokens: int) -> Path:
    """Onde fica a resposta de um pedido: o nome é o hash de modelo + prompts + trecho +
    max_tokens (o mesmo para a chamada direta e para o lote)."""
    pedido = json.dumps([modelo, sistema, usuario, max_tokens], ensure_ascii=False)
    return _dir_respostas(ato_id) / f"{hashlib.sha256(pedido.encode()).hexdigest()[:24]}.json"


def chamar_com_disco(chamar, ato_id: int, modelo: str, sistema: list[dict], usuario: str,
                     max_tokens: int, *, aceitar,
                     so_disco: bool = False) -> tuple[Resposta, bool]:
    """Chama o modelo ou reaproveita a resposta guardada para exatamente o mesmo pedido — a chave
    é o hash de modelo + prompts + trecho + max_tokens. Só guarda resposta que `aceitar` aprova.
    Com `so_disco` não chama: sem a resposta guardada, levanta SemRespostaConferida."""
    arquivo = arquivo_resposta(ato_id, modelo, sistema, usuario, max_tokens)
    if arquivo.exists():
        d = json.loads(arquivo.read_text(encoding="utf-8"))
        return Resposta(d["texto"], d["parada"], d["uso"]), True
    if so_disco:
        raise SemRespostaConferida(
            f"ato {ato_id}: sem resposta conferida para este pedido (o texto, o prompt ou o modelo "
            "mudou desde o --gerar, ou ele não rodou); rode --gerar e confira o relatório")
    r = chamar(modelo, sistema, usuario, max_tokens)
    if aceitar(r):
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps({"modelo": modelo, "texto": r.texto, "parada": r.parada,
                                       "uso": r.uso, "em": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                      ensure_ascii=False), encoding="utf-8")
    return r, False


def interpretar(texto: str) -> dict:
    """JSON da resposta, tolerando cercas de markdown e texto em volta."""
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim < ini:
        raise ValueError("a resposta não tem objeto JSON")
    return json.loads(texto[ini:fim + 1])


def _tem_texto(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validar_saida(d) -> dict:
    """Estrutura mínima da resposta (revisão 4-LLM, Codex: JSON válido não basta). Resposta
    reprovada não fica em disco e derruba o ato — nada é gravado."""
    if not isinstance(d, dict):
        raise ValueError("a resposta não é um objeto")
    materias = d.get("materias")
    if not isinstance(materias, list):
        raise ValueError("'materias' ausente ou não é lista")
    for i, m in enumerate(materias, 1):
        if not isinstance(m, dict):
            raise ValueError(f"matéria {i} não é objeto")
        if not (_tem_texto(m.get("tema_macro")) or _tem_texto(m.get("tema_especifico"))):
            raise ValueError(f"matéria {i} sem tema")
        if not any(_tem_texto(m.get(c)) for c in ("solucao", "ementa_trecho",
                                                  "fundamentacao_resumo")):
            raise ValueError(f"matéria {i} sem solução, trecho nem fundamentação")
    if d.get("ato_metadata") is not None and not isinstance(d["ato_metadata"], dict):
        raise ValueError("'ato_metadata' não é objeto")
    return d


def _materias_legiveis(r: Resposta) -> bool:
    """Vai para o disco a resposta válida e também a cortada no limite de tokens: o --executar
    (só disco) precisa saber que aquela parte foi dividida."""
    if r.parada == "max_tokens":
        return True
    try:
        validar_saida(interpretar(r.texto))
    except ValueError:
        return False
    return True


def _categorizar_parte(ato: dict, parte: Parte, total: int, sistema: list[dict], chamar, *,
                       modelo: str, uso: Uso, profundidade: int = 0,
                       so_disco: bool = False) -> list[dict]:
    usuario = mensagem_usuario(ato, parte, total)
    r, do_disco = chamar_com_disco(chamar, ato["id"], modelo, sistema, usuario, MAX_TOKENS,
                                   aceitar=_materias_legiveis, so_disco=so_disco)
    uso.somar(modelo, r.uso, do_disco)
    if r.parada == "max_tokens":
        # muitas matérias para caber na resposta: divide o trecho em dois e pede cada metade
        if len(parte.texto) < 2 * MINIMO_PARTE or profundidade >= 3:
            raise FalhaCategorizacao(f"parte {parte.rotulo}: resposta cortada no limite de tokens")
        saidas: list[dict] = []
        for i, sub in enumerate(particionar(parte.texto, len(parte.texto) // 2 + 1), 1):
            sub = Parte(f"{parte.rotulo}.{i}", sub.texto, sub.caminho or parte.caminho)
            saidas += _categorizar_parte(ato, sub, total, sistema, chamar, modelo=modelo, uso=uso,
                                         profundidade=profundidade + 1, so_disco=so_disco)
        return saidas
    try:
        saida = validar_saida(interpretar(r.texto))
    except ValueError as e:
        raise FalhaCategorizacao(f"parte {parte.rotulo}: resposta inválida ({e})") from e
    for m in saida["materias"]:
        m["_parte"] = parte.rotulo
    return [{**saida, "_parte": parte.rotulo}]


def juntar(saidas: list[dict]) -> tuple[list[dict], dict]:
    """Matérias de todas as partes, na ordem, e os metadados do ato. Divergência entre partes e
    parte sem matéria ficam registradas em `meta` (não somem em silêncio)."""
    materias = [m for s in saidas for m in s["materias"]]
    meta: dict = {}
    valores: dict[str, list] = {"eficacia": [], "abrangencia": []}
    normas: list = []
    for s in saidas:
        md = s.get("ato_metadata") or {}
        for chave, vistos in valores.items():
            if md.get(chave) and md[chave] not in vistos:
                vistos.append(md[chave])
        for n in md.get("norma_base_regulamentada") or []:
            if isinstance(n, dict) and n not in normas:
                normas.append(n)
    for chave, vistos in valores.items():
        if vistos:
            meta[chave] = vistos[0]
        if len(vistos) > 1:
            meta.setdefault("divergencias", {})[chave] = vistos
    if normas:
        meta["norma_base_regulamentada"] = normas
    vazias = [s["_parte"] for s in saidas if not s["materias"]]
    if vazias:
        meta["partes_sem_materia"] = vazias
    return materias, meta


def corpo_sem_ementa(limpo: str) -> str:
    """O texto sem os blocos de ementa (marcados '## EMENTA' pelo carregador e pelo extrator)."""
    return "\n\n".join(b for b in limpo.split("\n\n")
                         if not b.lstrip().startswith("## EMENTA")).strip()


def preparar(ato: dict, texto: str, limite: int | None = None) -> tuple[list[Parte], list[dict]]:
    """Partes do teor e prompt do sistema do ato — o que a chamada direta e o lote enviam."""
    limpo = texto_para_llm(texto)
    if not limpo:
        raise FalhaCategorizacao("texto vazio depois de tirar o HTML")
    if len(corpo_sem_ementa(limpo)) < MINIMO_CORPO:
        raise FalhaCategorizacao("só a ementa está em texto (o corpo deve estar em anexo PDF)")
    partes = particionar(limpo, limite)
    sistema = blocos_sistema(ato["tipo_ato"])
    if len(partes) > 1:
        sistema.append(bloco_sumario(limpo))
    return partes, sistema


def categorizar(ato: dict, texto: str, chamar, *, modelo: str, uso: Uso,
                limite: int | None = None, so_disco: bool = False) -> tuple[list[dict], dict]:
    """Chama o modelo parte a parte. Tudo ou nada: se uma parte falha, levanta. Com `so_disco`,
    só usa as respostas guardadas pelo --gerar."""
    partes, sistema = preparar(ato, texto, limite)
    saidas: list[dict] = []
    for parte in partes:
        saidas += _categorizar_parte(ato, parte, len(partes), sistema, chamar, modelo=modelo,
                                     uso=uso, so_disco=so_disco)
    materias, meta = juntar(saidas)
    if not materias:
        raise FalhaCategorizacao("nenhuma parte devolveu matéria")
    meta["partes"], meta["chamadas"] = len(partes), len(saidas)
    return materias, meta


# ---------------------------------------------------------------------------
# Matéria -> linhas
# ---------------------------------------------------------------------------

def _texto(v) -> str | None:
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


NOME_DO_TIPO = {
    "INSTRUCAO_NORMATIVA": "Instrução Normativa",
    "INSTRUCAO_NORMATIVA_CONJUNTA": "Instrução Normativa Conjunta",
    "SOLUCAO_CONSULTA": "Solução de Consulta",
    "SOLUCAO_DIVERGENCIA": "Solução de Divergência",
    "PARECER_NORMATIVO": "Parecer Normativo",
    "ATO_DECLARATORIO_INTERPRETATIVO": "Ato Declaratório Interpretativo",
    "ATO_DECLARATORIO_EXECUTIVO": "Ato Declaratório Executivo",
    "PORTARIA": "Portaria",
    "DECRETO": "Decreto",
}


def norma_do_ato(ato: dict) -> tuple[str, str]:
    """(tipo_norma, referência) do próprio ato, no formato que o td-analise-piscofins casa por
    tipo + número + ano (`atos_rfb.normalizar_norma`). O número vai com ponto de milhar: sem ele,
    o leitor de lá toma "2121" por 212."""
    nome = NOME_DO_TIPO.get(ato["tipo_ato"], str(ato["tipo_ato"]).replace("_", " ").title())
    digitos = re.sub(r"\D", "", str(ato["numero"] or ""))
    numero = f"{int(digitos):,}".replace(",", ".") if digitos else str(ato["numero"])
    emissor = f" {ato['emissor']}" if ato.get("emissor") else ""
    return str(ato["tipo_ato"]).lower(), f"{nome}{emissor} nº {numero}/{ato['ano']}"


def rotulo_dispositivo(d: dict) -> str | None:
    """{"artigo": "171", "paragrafo": "2º", "inciso": "IV"} -> "art. 171, § 2º, inciso IV"."""
    artigo = str(d.get("artigo") or "").strip()
    if not artigo:
        return None
    partes = [artigo if artigo.lower().startswith("art") else f"art. {artigo}"]
    if d.get("paragrafo"):
        par = str(d["paragrafo"]).strip()
        partes.append(par if par.startswith("§") or par.lower().startswith("par") else f"§ {par}")
    if d.get("inciso"):
        partes.append(f"inciso {str(d['inciso']).strip()}")
    if d.get("alinea"):
        partes.append(f"alínea {str(d['alinea']).strip()}")
    return ", ".join(partes)


def linha_materia(m: dict, ordem: int, norma_ato: tuple[str, str] | None = None) -> dict:
    """Normaliza uma matéria do modelo (mesmas regras do categorize_batch.py).

    Com `norma_ato` (de `norma_do_ato`), os artigos do próprio ato que a matéria cobre
    (`dispositivos_do_ato`) viram linhas de materia_dispositivo com tipo_uso 'dispositivo_do_ato':
    a busca por "IN 2.121, art. 171" passa a achar a matéria da própria IN.
    """
    tema_macro = normalizar_tema_macro(m.get("tema_macro")) or "__NOVO_TEMA"
    tema_esp = (normalizar_tema_especifico(m.get("tema_especifico") or f"{tema_macro}.OUTRO",
                                           tema_macro) or f"{tema_macro}.OUTRO")
    tributos: dict[tuple[str, str | None], None] = {}
    for t in m.get("tributos") or []:
        t = {"codigo": t} if isinstance(t, str) else t
        if not isinstance(t, dict):
            continue
        regime = normalizar_regime(t.get("regime"))
        for codigo in decompor_tributo_composto(t.get("codigo")):   # PIS/COFINS -> PIS, COFINS
            tributos[(codigo, regime)] = None
    dispositivos = []
    for d in m.get("dispositivos") or []:
        if isinstance(d, dict):
            dispositivos.append((
                normalizar_tipo_norma(d.get("tipo_norma")) or d.get("tipo_norma"),
                _texto(d.get("referencia")), _texto(d.get("dispositivo")),
                _texto(d.get("texto_resumido")),
                normalizar_tipo_uso(d.get("tipo_uso")) or "fundamento_principal"))
    for f in m.get("fundamentacao_externa") or []:
        if isinstance(f, dict):
            dispositivos.append((
                normalizar_tipo_norma(f.get("tipo_fonte")) or f.get("tipo_fonte"),
                _texto(f.get("referencia")), None, _texto(f.get("texto_resumido")),
                "fundamento_externo"))
    for d in (m.get("dispositivos_do_ato") or []) if norma_ato else []:
        if isinstance(d, dict) and (rotulo := rotulo_dispositivo(d)):
            dispositivos.append((*norma_ato, rotulo, _texto(d.get("texto_resumido")),
                                 "dispositivo_do_ato"))
    cnaes: dict[str, tuple] = {}
    for c in m.get("cnaes_aplicaveis") or []:
        if isinstance(c, dict) and c.get("codigo"):   # sem código não dá para gravar
            cnaes[str(c["codigo"])] = (_texto(c.get("descricao")), _texto(c.get("relevancia")),
                                       None if c.get("confianca") is None
                                       else str(c.get("confianca")))
    tags = m.get("tags") or []
    tags = [tags] if isinstance(tags, str) else [str(x) for x in tags]
    metadata = m.get("metadata_tematico")
    return {
        "ordem": ordem,
        "natureza": normalizar_natureza(m.get("natureza")) or "orientacao",
        "tema_macro": tema_macro,
        "tema_especifico": tema_esp,
        "subtema": _texto(m.get("subtema")),
        "tags": tags,
        **{c: _texto(m.get(c)) for c in ("ementa_trecho", "fato_consultado", "solucao",
                                          "fundamentacao_resumo", "tese_contribuinte",
                                          "tese_fazenda", "tese_adotada")},
        "metadata_tematico": metadata if isinstance(metadata, dict) else {},
        "resultado": normalizar_resultado(m.get("resultado")),
        "tributos": sorted({c for c, _ in tributos}),
        "regimes": sorted({r for _, r in tributos if r}),
        "_tributos": list(tributos),
        "_dispositivos": dispositivos,
        "_cnaes": cnaes,
    }


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

SQL_MATERIA = (
    "INSERT INTO rfb_atos.ato_materia (ato_id, ordem, natureza, tema_macro, tema_especifico, "
    "subtema, tags, ementa_trecho, fato_consultado, solucao, fundamentacao_resumo, "
    "tese_contribuinte, tese_fazenda, tese_adotada, metadata_tematico, resultado, tributos, "
    "regimes, base_analise, llm_model, llm_processed_at, schema_version) VALUES (%(ato_id)s, "
    "%(ordem)s, "
    "%(natureza)s, %(tema_macro)s, %(tema_especifico)s, %(subtema)s, %(tags)s, "
    "%(ementa_trecho)s, %(fato_consultado)s, %(solucao)s, %(fundamentacao_resumo)s, "
    "%(tese_contribuinte)s, %(tese_fazenda)s, %(tese_adotada)s, %(metadata_tematico)s, "
    "%(resultado)s, %(tributos)s, %(regimes)s, %(base_analise)s, %(llm_model)s, now(), 'v1') "
    "RETURNING id")


def _json(valor):
    return valor.isoformat() if isinstance(valor, date) else valor


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def persistir(conn, ato_id: int, materias: list[dict], meta: dict, *, modelo: str,
              run_id: str, base_analise: str = "texto") -> list[int]:
    """Grava as matérias e marca o ato como analisado, numa transação. Devolve os ids novos."""
    if not materias:
        raise FalhaCategorizacao("o modelo não devolveu matéria nenhuma")
    with conn.transaction():
        linha = conn.execute(
            "SELECT content_disponivel, analise_completa, eficacia_atual, "
            "metadados->'categorizacao' FROM rfb_atos.ato WHERE id = %s FOR UPDATE",
            (ato_id,)).fetchone()
        if linha is None:
            raise AtoInapto(f"ato {ato_id} não existe")
        com_texto, analisado, eficacia, cat_antes = linha
        if not com_texto:
            raise AtoInapto(f"ato {ato_id} sem texto (content_disponivel = false)")
        if analisado:
            raise AtoInapto(f"ato {ato_id} já está com analise_completa")
        if meta.get("texto_sha256"):
            # o texto pode ter mudado durante as chamadas (recoleta concorrente): trava a linha
            # do teor e confere o hash do que foi analisado
            atual = conn.execute(
                "SELECT encode(sha256(convert_to(texto_completo, 'UTF8')), 'hex') FROM "
                "rfb_atos.ato_content WHERE ato_id = %s FOR SHARE", (ato_id,)).fetchone()
            if not atual or atual[0] != meta["texto_sha256"]:
                raise AtoInapto(f"ato {ato_id}: o texto mudou durante a categorização; rode de "
                                "novo")
        if conn.execute("SELECT 1 FROM rfb_atos.ato_materia WHERE ato_id = %s LIMIT 1",
                        (ato_id,)).fetchone():
            raise AtoInapto(f"ato {ato_id} já tem matéria: recategorizar exige apagar, e o "
                            "rfb_writer não apaga")
        ids: list[int] = []
        with conn.cursor() as cur:
            for m in materias:
                cur.execute(SQL_MATERIA, {**m, "ato_id": ato_id, "llm_model": modelo,
                                          "base_analise": base_analise,
                                          "metadata_tematico": Jsonb(m["metadata_tematico"])})
                mid = cur.fetchone()[0]
                ids.append(mid)
                cur.executemany(
                    "INSERT INTO rfb_atos.materia_tributo (materia_id, tributo_codigo, regime) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    [(mid, c, r) for c, r in m["_tributos"]])
                cur.executemany(
                    "INSERT INTO rfb_atos.materia_dispositivo (materia_id, tipo_norma, "
                    "referencia, dispositivo, texto_resumido, tipo_uso) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [(mid, *d) for d in m["_dispositivos"]])
                cur.executemany(
                    "INSERT INTO rfb_atos.materia_cnae (materia_id, cnae_codigo, descricao, "
                    "relevancia, confianca) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    [(mid, codigo, *resto) for codigo, resto in m["_cnaes"].items()])

        categorizacao = {"run_id": run_id, "modelo": modelo, "materias": len(ids), **meta}
        # eficácia só se o ato não tem e as partes concordam
        proposta = None if "eficacia" in meta.get("divergencias", {}) else meta.get("eficacia")
        nova_eficacia = eficacia or proposta
        conn.execute(
            "UPDATE rfb_atos.ato SET analise_completa = true, eficacia_atual = %s, "
            "metadados = (COALESCE(metadados, '{}'::jsonb) - 'categorizacao_revisao') || %s, "
            "atualizado_em = now() "
            "WHERE id = %s",
            (nova_eficacia, Jsonb({"categorizacao": categorizacao}), ato_id))
        mudancas = [("ato", "analise_completa", False, True),
                    ("ato", "metadados.categorizacao", cat_antes, categorizacao),
                    ("ato_materia", "ids", None, ids)]
        if nova_eficacia != eficacia:
            mudancas.append(("ato", "eficacia_atual", eficacia, nova_eficacia))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rfb_atos.ato_mudanca (run_id, ato_id, tabela, campo, antes, depois) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(run_id, ato_id, t, c, Jsonb(_json(a)), Jsonb(_json(d)))
                 for t, c, a, d in mudancas])
    return ids


def classificar_sinais(conn, ato_id: int, ids: list[int], chamar, *, uso: Uso) -> dict[str, int]:
    """Sinal das matérias `ids` que ainda não têm. O script antigo exigia `solucao`; em norma, o
    extrator às vezes só preenche o trecho, e sem sinal a matéria some da busca filtrada."""
    linhas = conn.execute(
        "SELECT id, tema_especifico, natureza, ementa_trecho, fato_consultado, solucao "
        "FROM rfb_atos.ato_materia WHERE id = ANY(%s) AND sinal IS NULL "
        "AND (solucao IS NOT NULL OR ementa_trecho IS NOT NULL) ORDER BY id", (ids,)).fetchall()
    sistema = [{"type": "text", "text": PROMPT_SINAL}]
    contagem: dict[str, int] = {}
    for mid, tema, natureza, ementa, fato, solucao in linhas:
        usuario = mensagem_sinal({"tema_especifico": tema, "natureza": natureza,
                                  "ementa_trecho": ementa, "fato_consultado": fato,
                                  "solucao": solucao})
        r, do_disco = chamar_com_disco(chamar, ato_id, MODELO_SINAL, sistema, usuario,
                                       MAX_TOKENS_SINAL,
                                       aceitar=lambda r: parse_sinal(r.texto) is not None)
        uso.somar(MODELO_SINAL, r.uso, do_disco)
        sinal = parse_sinal(r.texto)
        if sinal is None:
            contagem["ilegivel"] = contagem.get("ilegivel", 0) + 1
            continue
        conn.execute("UPDATE rfb_atos.ato_materia SET sinal = %s, sinal_classified_at = now(), "
                     "sinal_model = %s WHERE id = %s", (sinal, MODELO_SINAL, mid))
        contagem[sinal] = contagem.get(sinal, 0) + 1
    return contagem


def _cast_embedding(tipo: str | None) -> str:
    """O tipo vai para dentro do SQL: só passa o esperado (halfvec/vector com dimensão; text no
    banco de teste)."""
    if not re.fullmatch(r"text|halfvec\(\d+\)|vector\(\d+\)", tipo or ""):
        raise RuntimeError(f"tipo inesperado da coluna embedding: {tipo!r}")
    return tipo


def vetorizar(conn, ids: list[int], *, embed=None) -> int:
    """Vetor das matérias `ids` que ainda não têm. Não toca em llm_model (é da categorização)."""
    linhas = conn.execute(
        "SELECT id, ementa_trecho, fato_consultado, solucao, tema_macro, tema_especifico "
        "FROM rfb_atos.ato_materia WHERE id = ANY(%s) AND embedding IS NULL ORDER BY id",
        (ids,)).fetchall()
    if not linhas:
        return 0
    vetores = (embed or embed_texts_sync)([build_text(*linha[1:]) for linha in linhas])
    if len(vetores) != len(linhas) or any(len(v) != DEFAULT_DIM for v in vetores):
        raise RuntimeError(f"vetores fora do formato: esperado {len(linhas)} x {DEFAULT_DIM}")
    # tipo da coluna vem do catálogo: halfvec(3072) na nuvem, text no banco de teste
    tipo = _cast_embedding(conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = "
        "'rfb_atos.ato_materia'::regclass AND attname = 'embedding' AND attnum > 0 "
        "AND NOT attisdropped").fetchone()[0])
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            f"UPDATE rfb_atos.ato_materia SET embedding = %s::{tipo}, embedded_at = now(), "
            "embedding_source = %s WHERE id = %s",
            [(vector_literal(v), FONTE_EMBEDDING, linha[0])
             for linha, v in zip(linhas, vetores, strict=True)])
    return len(linhas)


SQL_ATOS = (
    "SELECT a.id, a.tipo_ato, a.numero, a.ano, a.emissor, a.data_publicacao::text "
    "AS data_publicacao, a.ementa, a.content_disponivel, a.analise_completa, c.texto_completo, "
    "a.status_vigencia, c.fonte_extracao, "
    "(SELECT count(*) FROM rfb_atos.ato_materia m WHERE m.ato_id = a.id) AS n_materias "
    "FROM rfb_atos.ato a LEFT JOIN rfb_atos.ato_content c ON c.ato_id = a.id")


# Fila de pendentes: com texto, sem análise e sem matéria. Fica de fora, de propósito:
#   - ato não vigente: matéria e vetor só depois do filtro de vigência no td-analise-core (TI-7560);
#   - ato alterado com texto do extrator antigo: o texto mistura redações (D12 do plano);
#   - ato que o modo automático mandou para revisão (metadados.categorizacao_revisao).
FILA_PENDENTES = (
    "a.content_disponivel AND NOT a.analise_completa AND length(btrim(c.texto_completo)) > 0 "
    "AND NOT EXISTS (SELECT 1 FROM rfb_atos.ato_materia m WHERE m.ato_id = a.id) "
    "AND a.status_vigencia IS DISTINCT FROM 'nao_vigente' "
    "AND NOT (COALESCE(a.status_vigencia, '') = 'vigente_alterado' "
    "         AND c.fonte_extracao IS DISTINCT FROM 'json_portal') "
    "AND NOT (COALESCE(a.metadados, '{}'::jsonb) ? 'categorizacao_revisao')")


def carregar_atos(conn, *, ids: list[int] | None = None, pendentes: bool = False,
                  limit: int | None = None, tipos: list[str] | None = None,
                  excluir_tipos: list[str] | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        if ids:
            cur.execute(f"{SQL_ATOS} WHERE a.id = ANY(%s) ORDER BY a.id", (ids,))
        elif pendentes:
            cur.execute(
                f"{SQL_ATOS} WHERE {FILA_PENDENTES} "
                "AND (%(tipos)s::text[] IS NULL OR a.tipo_ato = ANY(%(tipos)s::text[])) "
                "AND (%(fora)s::text[] IS NULL OR NOT a.tipo_ato = ANY(%(fora)s::text[])) "
                "ORDER BY a.data_publicacao DESC NULLS LAST, a.id LIMIT %(limite)s",
                {"tipos": tipos, "fora": excluir_tipos, "limite": limit})
        else:
            return []
        return cur.fetchall()


def processar_ato(conn, ato: dict, chamar, *, modelo: str, run_id: str, uso: Uso,
                  sinal: bool = True, vetor: bool = True, embed=None,
                  limite: int | None = None, so_disco: bool = False) -> dict:
    """Matérias (se o ato ainda não tem), depois sinal e vetor das matérias do ato. Com
    `so_disco`, as matérias só saem das respostas conferidas no --gerar."""
    agora = ato["n_materias"] == 0
    if agora:
        if not (ato["content_disponivel"] and (ato["texto_completo"] or "").strip()):
            raise AtoInapto(f"ato {ato['id']} sem texto")
        if ato["analise_completa"]:
            raise AtoInapto(f"ato {ato['id']} já está com analise_completa")
        materias, meta = categorizar(ato, ato["texto_completo"], chamar, modelo=modelo, uso=uso,
                                     limite=limite, so_disco=so_disco)
        meta["texto_sha256"] = _sha256(ato["texto_completo"])
        norma = norma_do_ato(ato)
        linhas = [linha_materia(m, i, norma) for i, m in enumerate(materias, 1)]
        ids = persistir(conn, ato["id"], linhas, meta, modelo=modelo, run_id=run_id,
                        base_analise=base_da_analise(ato))
        partes = meta["partes"]
    else:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM rfb_atos.ato_materia WHERE ato_id = %s ORDER BY ordem", (ato["id"],))]
        partes = 0
    resumo = {"ato_id": ato["id"], "categorizado_agora": agora, "partes": partes,
              "materias": len(ids),
              "sinal": classificar_sinais(conn, ato["id"], ids, chamar, uso=uso) if sinal else {},
              "vetores": vetorizar(conn, ids, embed=embed) if vetor else 0}
    sem_sinal, sem_vetor = conn.execute(
        "SELECT count(*) FILTER (WHERE sinal IS NULL), count(*) FILTER (WHERE embedding IS NULL) "
        "FROM rfb_atos.ato_materia WHERE id = ANY(%s)", (ids,)).fetchone()
    return {**resumo, "sem_sinal": sem_sinal, "sem_vetor": sem_vetor}


def executar_lote(conn, atos: list[dict], chamar, *, run_id: str, uso: Uso,
                  modelo: str | None = None, sinal: bool = True, vetor: bool = True,
                  embed=None, limite: int | None = None, so_disco: bool = False,
                  saida=print) -> list[dict]:
    """Um ato por vez; um ato com problema não derruba os outros. As matérias gravadas ficam (a
    transação delas já fechou): rodar `--ato` de novo completa sinal e vetor."""
    resultados: list[dict] = []
    for a in atos:
        escolhido = modelo or escolher_modelo(a["tipo_ato"], a["numero"], a["ano"])
        try:
            resumo = processar_ato(conn, a, chamar, modelo=escolhido, run_id=run_id, uso=uso,
                                   sinal=sinal, vetor=vetor, embed=embed, limite=limite,
                                   so_disco=so_disco)
        except (AtoInapto, FalhaCategorizacao) as e:
            saida(f"[pulado] ato {a['id']}: {e}")
            resultados.append({"ato_id": a["id"], "erro": str(e)})
            continue
        except Exception as e:
            saida(f"[erro] ato {a['id']}: {type(e).__name__}: {e} (rode --ato {a['id']} de novo "
                  "para completar sinal e vetor)")
            resultados.append({"ato_id": a["id"], "erro": f"{type(e).__name__}: {e}"})
            continue
        saida(f"[ok] run {run_id}: {resumo}")
        resultados.append(resumo)
    return resultados


# ---------------------------------------------------------------------------
# Conferir antes de gravar (--gerar)
# ---------------------------------------------------------------------------

_NUM_ARTIGO = re.compile(r"\s*(?:art(?:igo)?\.?\s*)?(\d+)\s*[ºo°]?\s*(?:-\s*([a-z]))?",
                         re.IGNORECASE)


def _num_artigo(rotulo) -> str | None:
    m = _NUM_ARTIGO.match(str(rotulo or ""))
    return (m.group(1) + (f"-{m.group(2).lower()}" if m.group(2) else "")) if m else None


def relatorio(ato: dict, materias: list[dict], linhas: list[dict], meta: dict,
              modelo: str) -> dict:
    """O que conferir antes de gravar: matérias por parte, temas repetidos, artigos do ato citados,
    divergências. Revisão 4-LLM (Codex): o rfb_writer não apaga, então a conferência vem antes."""
    blocos = texto_para_llm(ato["texto_completo"]).split("\n\n")
    artigos = {n for b in blocos if (a := _ARTIGO_NUM.match(b)) and (n := _num_artigo(a.group(1)))}
    cobertos = {n for m in materias for d in (m.get("dispositivos_do_ato") or [])
                if isinstance(d, dict) and (n := _num_artigo(d.get("artigo")))}
    temas = Counter(linha["tema_especifico"] for linha in linhas)
    citados = len(cobertos & artigos)
    resumo = {
        "materias": len(linhas), "partes": meta.get("partes"), "chamadas": meta.get("chamadas"),
        "artigos_no_texto": len(artigos), "artigos_citados": citados,
        "cobertura_artigos": round(citados / len(artigos), 3) if artigos else None,
        "temas_repetidos": {t: n for t, n in temas.most_common() if n > 1},
        "partes_sem_materia": meta.get("partes_sem_materia", []),
        "divergencias": meta.get("divergencias", {}),
        "sem_solucao": sum(1 for linha in linhas if not linha["solucao"]),
        "sem_tributo": sum(1 for linha in linhas if not linha["tributos"]),
        "tema_fora_da_taxonomia": sum(1 for linha in linhas
                                      if linha["tema_macro"] == "__NOVO_TEMA"),
        "desancoradas": [f"matéria {linha['ordem']}: {', '.join(p)}" for linha in linhas
                         if (p := ancoragem(ato["texto_completo"], linha))],
    }
    return {"ato_id": ato["id"], "modelo": modelo, "resumo": resumo, "materias": [
        {"parte": m.get("_parte"), **{k: v for k, v in linha.items() if not k.startswith("_")},
         "dispositivos": linha["_dispositivos"], "cnaes": linha["_cnaes"]}
        for linha, m in zip(linhas, materias, strict=True)]}


def escrever_relatorio(ato: dict, run_id: str, rel: dict) -> Path:
    """JSON (máquina) e Markdown (para ler) em RFB_ATOS_DADOS/categorizacao/<ato>/."""
    pasta = _dir_respostas(ato["id"])
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"relatorio-{run_id}.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    r = rel["resumo"]
    cobertura = f" ({r['cobertura_artigos']:.0%})" if r["cobertura_artigos"] is not None else ""
    repetidos = ", ".join(f"{t} ({n})" for t, n in r["temas_repetidos"].items()) or "nenhum"
    texto = [
        f"# Categorização para conferir — ato {ato['id']} "
        f"({ato['tipo_ato']} {ato['numero']}/{ato['ano']})", "",
        f"Modelo {rel['modelo']} · run {run_id} · **nada foi gravado no banco**.", "",
        f"- **Matérias:** {r['materias']} em {r['partes']} partes ({r['chamadas']} chamadas)",
        f"- **Artigos do ato citados pelas matérias:** {r['artigos_citados']} de "
        f"{r['artigos_no_texto']}{cobertura}",
        f"- **Partes sem matéria:** {', '.join(r['partes_sem_materia']) or 'nenhuma'}",
        f"- **Temas repetidos:** {repetidos}",
        f"- **Divergências entre partes:** {r['divergencias'] or 'nenhuma'}",
        f"- **Sem solução:** {r['sem_solucao']} · **sem tributo:** {r['sem_tributo']} · "
        f"**tema fora da taxonomia:** {r['tema_fora_da_taxonomia']}", "", "## Matérias", ""]
    for m in rel["materias"]:
        proprios = [d[2] for d in m["dispositivos"] if d[4] == "dispositivo_do_ato"]
        texto += [f"### {m['ordem']}. {m['tema_especifico']} (parte {m['parte']})",
                  f"Tributos: {', '.join(m['tributos']) or '—'} · artigos: "
                  f"{'; '.join(proprios) or '—'}", "",
                  (m["solucao"] or m["ementa_trecho"] or "").strip()[:600], ""]
    caminho = pasta / f"relatorio-{run_id}.md"
    caminho.write_text("\n".join(texto), encoding="utf-8")
    return caminho


def gerar(atos: list[dict], chamar, *, run_id: str, uso: Uso, modelo: str | None = None,
          limite: int | None = None, saida=print) -> list[dict]:
    """Chama o modelo e escreve o relatório para conferência, SEM gravar no banco. As respostas
    ficam em disco: o --executar depois reaproveita e não paga as matérias de novo."""
    resultados: list[dict] = []
    for a in atos:
        if a["n_materias"] or a["analise_completa"] or not (
                a["content_disponivel"] and (a["texto_completo"] or "").strip()):
            saida(f"[pulado] ato {a['id']}: já tem matéria, já está analisado ou não tem texto")
            resultados.append({"ato_id": a["id"], "erro": "fora da fila"})
            continue
        escolhido = modelo or escolher_modelo(a["tipo_ato"], a["numero"], a["ano"])
        try:
            materias, meta = categorizar(a, a["texto_completo"], chamar, modelo=escolhido,
                                         uso=uso, limite=limite)
        except FalhaCategorizacao as e:
            saida(f"[erro] ato {a['id']}: {e}")
            resultados.append({"ato_id": a["id"], "erro": str(e)})
            continue
        norma = norma_do_ato(a)
        linhas = [linha_materia(m, i, norma) for i, m in enumerate(materias, 1)]
        rel = relatorio(a, materias, linhas, meta, escolhido)
        caminho = escrever_relatorio(a, run_id, rel)
        saida(f"[relatório] ato {a['id']}: {rel['resumo']}\n  {caminho}")
        resultados.append({"ato_id": a["id"], "relatorio": str(caminho), **rel["resumo"]})
    return resultados


# Ancoragem (revisão 4-LLM da PR #6, Gemini): o portão estrutural não pega tributo trocado nem lei
# inventada. Conferência barata, sem modelo: o tributo e o número das leis citadas pela matéria têm
# de aparecer no texto do ato (sem acento, sem maiúsculas). Tributo sem lista de nomes não é
# conferido.
NOMES_TRIBUTO = {
    "PIS": ("pis", "pasep"),
    "COFINS": ("cofins",),
    "IRPJ": ("irpj", "imposto sobre a renda", "imposto de renda", "lucro",
             "precos de transferencia"),
    "CSLL": ("csll", "contribuicao social sobre o lucro", "lucro", "precos de transferencia"),
    "IRRF": ("irrf", "retido na fonte", "retencao", "na fonte"),
    "IRPF": ("irpf", "pessoa fisica", "imposto sobre a renda", "imposto de renda"),
    # classificação fiscal (NCM, TIPI, SH) serve ao II e ao IPI: é a âncora deles nas SC e SD da
    # COANA/DIANA (piloto do aplicar, 24/09/2026: 40 falsos alarmes sem isto)
    "IPI": ("ipi", "produtos industrializados", "tipi", "ncm", "classificacao de mercadoria",
            "classificacao fiscal", "sistema harmonizado"),
    "II": ("imposto de importacao", "imposto sobre a importacao", "(ii)", " ii ", "tec ",
           "tarifa externa", "importacao", "ncm", "classificacao de mercadoria",
           "classificacao fiscal", "sistema harmonizado"),
    "IE": ("imposto de exportacao", "imposto sobre a exportacao", "exportacao"),
    "IOF": ("iof", "operacoes de credito", "operacoes financeiras"),
    "ITR": ("itr", "territorial rural"),
    "CIDE": ("cide",),
    "CONTRIB_PREV": ("previdenci", "inss", "contribuicao social", "cpp", "esocial", "gfip",
                     "seguridade"),
    "CONTRIB_TERCEIROS": ("terceiros", "sesi", "senai", "sesc", "senac", "sebrae", "senar",
                          "incra", "salario-educacao", "salario educacao"),
    "SIMPLES": ("simples",),
}
_NUMERO_LEI = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{3,6})")


def _plano(texto: str) -> str:
    return " " + re.sub(r"\s+", " ", _sem_acento(texto or "").lower()) + " "


def ancoragem(texto_ato: str, linha: dict) -> list[str]:
    """Problemas de ancoragem de uma matéria (lista vazia = ancorada)."""
    plano = _plano(texto_ato)
    so_digitos = re.sub(r"\D", "", plano)
    problemas = []
    for tributo in linha.get("tributos") or []:
        nomes = NOMES_TRIBUTO.get(tributo)
        if nomes and not any(n in plano for n in nomes):
            problemas.append(f"tributo {tributo} não aparece no texto")
    numeros_citados = []
    for d in linha.get("_dispositivos") or []:
        if d[4] == "dispositivo_do_ato":
            continue
        m = _NUMERO_LEI.search(d[1] or "")
        if m:
            numeros_citados.append(m.group(1).replace(".", ""))
    if numeros_citados:
        fora = [n for n in numeros_citados if n not in so_digitos]
        if len(fora) / len(numeros_citados) > 0.5:
            problemas.append(f"{len(fora)} de {len(numeros_citados)} normas citadas não aparecem "
                             "no texto")
    return problemas


def portao(resumo: dict) -> list[str]:
    """Regras do modo automático (aprovadas pelo dono em 24/09/2026). Lista vazia = grava; senão o
    ato vai para revisão com o relatório pronto."""
    motivos = []
    n = resumo["materias"] or 0
    if resumo.get("partes_sem_materia"):
        motivos.append(f"partes sem matéria: {', '.join(resumo['partes_sem_materia'])}")
    # tema fora da taxonomia é lacuna da taxonomia, não erro da matéria: só reprova quando é a
    # maioria (piloto de 24/09: 1 de 3 reprovava SCI boa)
    if n and resumo["tema_fora_da_taxonomia"] / n > 0.5:
        motivos.append(f"{resumo['tema_fora_da_taxonomia']} de {n} matérias fora da taxonomia")
    if n and resumo["sem_solucao"] / n > 0.2:
        motivos.append(f"{resumo['sem_solucao']} de {n} matérias sem solução")
    if resumo["artigos_no_texto"] >= 20 and (resumo["cobertura_artigos"] or 0) < 0.8:
        motivos.append(f"cobertura de artigos {resumo['cobertura_artigos']:.0%} (mínimo 80%)")
    desancoradas = resumo.get("desancoradas") or []
    if n and len(desancoradas) / n > 0.2:
        motivos.append(f"{len(desancoradas)} de {n} matérias sem âncora no texto: "
                       + "; ".join(desancoradas[:3]))
    return motivos


def marcar_revisao(conn, ato_id: int, run_id: str, motivos: list[str],
                   relatorio_md: Path | None = None) -> None:
    """Tira o ato da fila automática (metadados.categorizacao_revisao), com log."""
    marca = {"run_id": run_id, "motivos": motivos, "em": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "relatorio": str(relatorio_md) if relatorio_md else None}
    with conn.transaction():
        conn.execute("UPDATE rfb_atos.ato SET metadados = COALESCE(metadados, '{}'::jsonb) || %s, "
                     "atualizado_em = now() WHERE id = %s",
                     (Jsonb({"categorizacao_revisao": marca}), ato_id))
        conn.execute("INSERT INTO rfb_atos.ato_mudanca (run_id, ato_id, tabela, campo, antes, "
                     "depois) VALUES (%s, %s, 'ato', 'metadados.categorizacao_revisao', NULL, %s)",
                     (run_id, ato_id, Jsonb(marca)))


def _registrar_rodada(run_id: str, linha: dict) -> None:
    pasta = Path(os.environ.get("RFB_ATOS_DADOS", r"C:\td-rfb-atos-dados")) / "categorizacao"
    pasta = pasta / "rodadas"
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / f"{run_id}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")


def _completar(conn, ato: dict, ids: list[int], chamar, *, uso: Uso, embed=None) -> dict:
    """Sinal e vetor das matérias `ids` que ainda não têm; devolve o que ficou faltando."""
    sinais = classificar_sinais(conn, ato["id"], ids, chamar, uso=uso)
    vetores = vetorizar(conn, ids, embed=embed)
    sem_sinal, sem_vetor = conn.execute(
        "SELECT count(*) FILTER (WHERE sinal IS NULL), count(*) FILTER (WHERE embedding IS NULL) "
        "FROM rfb_atos.ato_materia WHERE id = ANY(%s)", (ids,)).fetchone()
    return {"sinal": sinais, "vetores": vetores, "sem_sinal": sem_sinal, "sem_vetor": sem_vetor}


def automatico_um(conn, ato: dict, chamar, *, run_id: str, uso: Uso, modelo: str,
                  embed=None, limite: int | None = None,
                  custo_max_ato: float | None = CUSTO_MAX_ATO, so_disco: bool = False) -> dict:
    """Um ato do modo automático: gera, passa pelo portão e grava — ou manda para revisão.

    Ato que já tem matérias (gravadas numa rodada que caiu antes do sinal ou do vetor) não é
    categorizado de novo: só completa sinal e vetor (revisão 4-LLM da PR #6, Codex). Com `so_disco`
    (lote), as matérias só saem das respostas em disco: faltou alguma, o ato fica pendente para o
    próximo lote, sem chamada direta."""
    rotulo = {"ato_id": ato["id"], "tipo": ato["tipo_ato"], "numero": ato["numero"],
              "ano": ato["ano"]}
    existentes = [r[0] for r in conn.execute(
        "SELECT id FROM rfb_atos.ato_materia WHERE ato_id = %s ORDER BY ordem", (ato["id"],))]
    if existentes:
        return {**rotulo, "materias": len(existentes), "completado": True,
                **_completar(conn, ato, existentes, chamar, uso=uso, embed=embed)}
    estimado = estimar(ato, modelo)["custo"]
    if custo_max_ato is not None and estimado > custo_max_ato:
        motivo = f"ato grande: ~US$ {estimado:.2f} (teto por ato US$ {custo_max_ato:.2f})"
        marcar_revisao(conn, ato["id"], run_id, [motivo])
        return {**rotulo, "revisar": [motivo]}
    materias, meta = categorizar(ato, ato["texto_completo"], chamar, modelo=modelo, uso=uso,
                                 limite=limite, so_disco=so_disco)
    norma = norma_do_ato(ato)
    linhas = [linha_materia(m, i, norma) for i, m in enumerate(materias, 1)]
    rel = relatorio(ato, materias, linhas, meta, modelo)
    motivos = portao(rel["resumo"])
    if motivos:
        caminho = escrever_relatorio(ato, run_id, rel)
        marcar_revisao(conn, ato["id"], run_id, motivos, caminho)
        return {**rotulo, "revisar": motivos, "relatorio": str(caminho)}
    meta["texto_sha256"] = _sha256(ato["texto_completo"])
    ids = persistir(conn, ato["id"], linhas, meta, modelo=modelo, run_id=run_id,
                    base_analise=base_da_analise(ato))
    return {**rotulo, "materias": len(ids), "partes": meta["partes"],
            "cobertura_artigos": rel["resumo"]["cobertura_artigos"],
            **_completar(conn, ato, ids, chamar, uso=uso, embed=embed)}


def estado_do_resultado(r: dict) -> str:
    """gravado | completado | incompleto | revisar | pendente | pulado | erro."""
    if r.get("erro"):
        return "erro"
    if r.get("revisar"):
        return "revisar"
    if r.get("pendente"):
        return "pendente"
    if r.get("pulado"):
        return "pulado"
    if r.get("sem_sinal") or r.get("sem_vetor"):
        return "incompleto"
    return "completado" if r.get("completado") else "gravado"


def automatico(atos: list[dict], chamar, *, conectar, run_id: str, uso: Uso,
               modelo: str | None = None, embed=None, paralelo: int = 4,
               teto_usd: float | None = None, custo_max_ato: float | None = CUSTO_MAX_ATO,
               limite: int | None = None, so_disco: bool = False, saida=print) -> list[dict]:
    """Modo automático (rotina noturna): cada ato em paralelo, com conexão própria. Para de começar
    atos novos quando o gasto passa do teto. Cada resultado vai para
    RFB_ATOS_DADOS/categorizacao/rodadas/<run_id>.jsonl."""
    from concurrent.futures import ThreadPoolExecutor

    def um(ato: dict) -> dict:
        if teto_usd is not None and uso.custo() >= teto_usd:
            return {"ato_id": ato["id"], "pulado": "teto de gasto da rodada"}
        escolhido = modelo or escolher_modelo(ato["tipo_ato"], ato["numero"], ato["ano"])
        try:
            with conectar() as conn:
                r = automatico_um(conn, ato, chamar, run_id=run_id, uso=uso, modelo=escolhido,
                                  embed=embed, limite=limite, custo_max_ato=custo_max_ato,
                                  so_disco=so_disco)
        except SemRespostaConferida as e:   # lote: falta resposta; volta no próximo lote
            r = {"ato_id": ato["id"], "pendente": str(e)}
        except AtoInapto as e:              # mudou de estado no meio (outra rodada): nada a fazer
            r = {"ato_id": ato["id"], "pulado": str(e)}
        except FalhaCategorizacao as e:     # só ementa, resposta inválida: sai da fila automática
            with conectar() as conn:
                marcar_revisao(conn, ato["id"], run_id, [str(e)])
            r = {"ato_id": ato["id"], "revisar": [str(e)]}
        except Exception as e:              # rede, API, banco: tenta de novo na próxima rodada
            r = {"ato_id": ato["id"], "erro": f"{type(e).__name__}: {e}", "tipo_erro": "inesperado"}
        _registrar_rodada(run_id, {**r, "custo_acumulado": round(uso.custo(), 4)})
        estado = estado_do_resultado(r)
        saida(f"[{estado}] ato {ato['id']} ({ato['tipo_ato']} {ato['numero']}/{ato['ano']}) "
              f"| gasto ~US$ {uso.custo():.2f} | "
              + json.dumps({k: v for k, v in r.items() if k not in ("ato_id",)},
                           ensure_ascii=False, default=str)[:300])
        return r

    with ThreadPoolExecutor(max_workers=max(1, paralelo)) as pool:
        return list(pool.map(um, atos))


PERMISSOES = (("rfb_atos.ato", "UPDATE"), ("rfb_atos.ato_content", "UPDATE"),
              ("rfb_atos.ato_materia", "INSERT"), ("rfb_atos.ato_materia", "UPDATE"),
              ("rfb_atos.materia_tributo", "INSERT"), ("rfb_atos.materia_dispositivo", "INSERT"),
              ("rfb_atos.materia_cnae", "INSERT"), ("rfb_atos.ato_mudanca", "INSERT"))


def exigir_permissoes(conn) -> None:
    """Confere, antes de pagar o modelo, se o usuário atual grava tudo o que será gravado
    (UPDATE no teor é o que o FOR SHARE da conferência de hash exige)."""
    faltam = [f"{priv} em {tabela}" for tabela, priv in PERMISSOES
              if not conn.execute("SELECT has_table_privilege(%s, %s)",
                                  (tabela, priv)).fetchone()[0]]
    for tabela in ("rfb_atos.ato_materia", "rfb_atos.materia_dispositivo", "rfb_atos.ato_mudanca"):
        seq = conn.execute("SELECT pg_get_serial_sequence(%s, 'id')", (tabela,)).fetchone()[0]
        if seq and not conn.execute("SELECT has_sequence_privilege(%s, 'USAGE')",
                                    (seq,)).fetchone()[0]:
            faltam.append(f"USAGE na sequência {seq}")
    if faltam:
        raise SemPermissao("faltam permissões ao usuário atual: " + "; ".join(faltam))


def codigo_saida(resultados: list[dict], *, sinal: bool = True, vetor: bool = True) -> int:
    """1 se algum ato falhou ou ficou sem o sinal/vetor pedido: agendador e CI não podem ler o
    lote como sucesso (revisão 4-LLM, Codex)."""
    for r in resultados:
        if r.get("erro") or (sinal and r.get("sem_sinal")) or (vetor and r.get("sem_vetor")):
            return 1
    return 0


# ---------------------------------------------------------------------------
# Plano e linha de comando
# ---------------------------------------------------------------------------

def estimar(ato: dict, modelo: str) -> dict:
    """Tokens e custo aproximados das matérias (sinal e vetor custam centavos)."""
    limpo = texto_para_llm(ato.get("texto_completo"))
    partes = len(particionar(limpo)) if limpo else 0
    blocos = blocos_sistema(ato["tipo_ato"]) + ([bloco_sumario(limpo)] if partes > 1 else [])
    sistema = sum(len(b["text"]) for b in blocos) / CARACTERES_POR_TOKEN
    entrada = len(limpo) / CARACTERES_POR_TOKEN + 300 * partes
    saida = max(SAIDA_MINIMA * partes, SAIDA_POR_CARACTERE * len(limpo))
    p_in, p_out = PRECO_REFERENCIA.get(modelo, (0.0, 0.0))
    custo = (entrada + 1.25 * sistema + 0.1 * sistema * max(0, partes - 1)) * p_in / 1e6
    return {"caracteres": len(limpo), "partes": partes, "entrada": int(entrada + sistema * partes),
            "saida": int(saida), "custo": custo + saida * p_out / 1e6}


def _mil(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def exigir_schema(conn) -> None:
    ok = conn.execute(
        "SELECT to_regclass('rfb_atos.ato_mudanca') IS NOT NULL AND EXISTS (SELECT 1 FROM "
        "information_schema.columns WHERE table_schema = 'rfb_atos' AND table_name = "
        "'ato_materia' AND column_name = 'base_analise')").fetchone()[0]
    if not ok:
        sys.exit("[erro] falta a migration 011 (ato_mudanca / ato_materia.base_analise).")


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
    alvo = p.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--ato", type=int, action="append", help="rfb_atos.ato.id (repetível)")
    alvo.add_argument("--pendentes", action="store_true",
                      help="com texto, sem analise_completa e sem matéria")
    p.add_argument("--limit", type=int, default=20, help="com --pendentes (padrão 20)")
    p.add_argument("--tipos", nargs="+", help="com --pendentes: só estes tipos de ato")
    p.add_argument("--excluir-tipos", nargs="+", help="com --pendentes: fora estes tipos")
    p.add_argument("--modelo", help=f"força o modelo (padrão: {MODELO_PADRAO})")
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--gerar", action="store_true",
                      help="chama o modelo e escreve o relatório para conferir; não grava")
    modo.add_argument("--executar", action="store_true",
                      help="grava como rfb_writer só o que o --gerar mostrou")
    modo.add_argument("--automatico", action="store_true",
                      help="gera, passa pelo portão de qualidade e grava; o que reprovar vai "
                           "para revisão (rotina noturna)")
    p.add_argument("--paralelo", type=int, default=4, help="com --automatico: atos simultâneos")
    p.add_argument("--teto-usd", type=float, help="com --automatico: gasto máximo da rodada")
    p.add_argument("--custo-max-ato", type=float, default=CUSTO_MAX_ATO,
                   help="com --automatico: ato estimado acima disso vai para revisão sem chamar")
    p.add_argument("--sem-sinal", action="store_true")
    p.add_argument("--sem-vetor", action="store_true")
    p.add_argument("--run-id")
    p.add_argument("--dsn")
    args = p.parse_args()
    run_id = args.run_id or f"categorizar-{time.strftime('%Y%m%dT%H%M%S')}"

    grava = args.executar or args.automatico
    dsn = _dsn(args.dsn, grava)
    with psycopg.connect(dsn, autocommit=True) as conn:
        atos = carregar_atos(conn, ids=args.ato, pendentes=args.pendentes, limit=args.limit,
                             tipos=args.tipos, excluir_tipos=args.excluir_tipos)
        faltando = sorted(set(args.ato or []) - {a["id"] for a in atos})
        for ato_id in faltando:
            print(f"ato {ato_id}: não existe")
        custo_total = 0.0
        detalhar = len(atos) <= 30
        por_tipo: dict[str, list] = {}
        for a in atos:
            modelo = args.modelo or escolher_modelo(a["tipo_ato"], a["numero"], a["ano"])
            rotulo = f"ato {a['id']} ({a['tipo_ato']} {a['numero']}/{a['ano']})"
            if a["n_materias"]:
                print(f"{rotulo}: já tem {a['n_materias']} matérias — só completa sinal e vetor")
                continue
            if not (a["content_disponivel"] and (a["texto_completo"] or "").strip()):
                print(f"{rotulo}: sem texto — fica de fora")
                continue
            e = estimar(a, modelo)
            custo_total += e["custo"]
            t = por_tipo.setdefault(a["tipo_ato"], [0, 0, 0.0])
            t[0] += 1
            t[1] += e["caracteres"]
            t[2] += e["custo"]
            if not detalhar:
                continue
            print(f"{rotulo}: {modelo} | {_mil(e['caracteres'])} caracteres sem HTML | "
                  f"{e['partes']} partes | ~{_mil(e['entrada'])} tokens de entrada, "
                  f"~{_mil(e['saida'])} de saída | ~US$ {e['custo']:.2f}")
        for tipo, (n, ch, cu) in sorted(por_tipo.items(), key=lambda x: -x[1][2]):
            print(f"  {tipo:<36} {n:>6} atos | {_mil(ch)} caracteres | ~US$ {cu:,.2f}")
        if not (args.gerar or args.executar or args.automatico):
            print(f"\ncusto estimado das matérias: ~US$ {custo_total:.2f} (preço de referência; o "
                  "real sai no fim)\n(plano: nada chamado nem gravado. --gerar chama o modelo e "
                  "escreve o relatório para conferir, sem gravar; --executar grava como "
                  "rfb_writer só o que o --gerar mostrou, sem pedir as matérias de novo "
                  "(só o sinal, uma palavra por matéria, é pedido no --executar).)")
            sys.exit(1 if faltando else 0)

        uso = Uso()
        if args.automatico:
            exigir_schema(conn)
            try:
                exigir_permissoes(conn)
            except SemPermissao as e:
                sys.exit(f"[erro] {e}")
            resultados = automatico(
                atos, chamador_anthropic(), conectar=lambda: psycopg.connect(dsn, autocommit=True),
                run_id=run_id, uso=uso, modelo=args.modelo, paralelo=args.paralelo,
                teto_usd=args.teto_usd, custo_max_ato=args.custo_max_ato)
            contagem: dict[str, int] = {}
            for r in resultados:
                e = estado_do_resultado(r)
                contagem[e] = contagem.get(e, 0) + 1
            print(f"\nrodada {run_id}: {contagem}")
        elif args.gerar:
            resultados = gerar(atos, chamador_anthropic(), run_id=run_id, uso=uso,
                               modelo=args.modelo)
        else:
            exigir_schema(conn)
            try:
                exigir_permissoes(conn)
            except SemPermissao as e:
                sys.exit(f"[erro] {e}")
            resultados = executar_lote(conn, atos, chamador_anthropic(), run_id=run_id, uso=uso,
                                       modelo=args.modelo, sinal=not args.sem_sinal,
                                       vetor=not args.sem_vetor, so_disco=True)
        for modelo, d in uso.por_modelo.items():
            print(f"uso {modelo}: {d}")
        print(f"custo das chamadas (preço de referência): ~US$ {uso.custo():.2f}")
        codigo = codigo_saida(resultados, sinal=not args.sem_sinal and grava,
                              vetor=not args.sem_vetor and grava)
        if faltando:
            codigo = 1
        sem_erro = sum(1 for r in resultados if not r.get("erro"))
        print(f"\n{sem_erro} de {len(resultados)} atos sem erro"
              + ("" if codigo == 0 else " — há erro ou pendência de sinal/vetor (saída 1)"))
        sys.exit(codigo)


if __name__ == "__main__":
    main()
