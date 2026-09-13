"""coletar_atos_portal.py — coleta no portal atos que faltam no rfb_atos e grava direto nas tabelas
principais: ato, texto, relações e situação do portal (etapa A). Sem matérias nem vetores.

Decisão do dono em 13/09/2026: os atos que faltam para o marco dos manuais (lista em
`references/atos_marco_2026-09-13.csv`) entram direto em rfb_atos, sem a área de espera, porque o
td-analise-piscofins 1.1.0 (`vigente_em`) já carimba a vigência. Sem matérias nem vetores, eles não
entram na busca semântica: a etapa B depende do filtro de vigência no td-analise-core (TI-7560).

Por ato:
  1. acha o idAto na listagem do SIJUT (tipo + ano, sem o filtro de vigentes) pelo número. Um número
     pode ter mais de um ato no portal — o ato e a retificação dele, por exemplo: entram todos;
  2. baixa as visões vigente, original e relacional (JSON bruto em RFB_ATOS_DADOS/portal/);
  3. cria a linha em rfb_atos.ato com o que a epígrafe do portal diz e carrega texto, segmentos e
     histórico pelo `carregar_ato_portal.aplicar` (regra de exibição do portal);
  4. grava as relações da visão relacional com fonte 'normasinternet2_portal' (a mesma do extrator
     antigo). Destino fora da base fica com `destino_id_portal` e é ligado quando o destino chega;
     aresta cuja ORIGEM está fora da base não cabe em ato_relacao (origem é obrigatória) e fica
     registrada em `situacao_portal.relacoes_sem_origem`. Aresta que já existe com fonte do modelo
     (llm-batch) passa a fonte do portal, com antes/depois em ato_mudanca;
  5. ato não vigente: `data_vigencia_fim` só com a data de efeito da revogação (REV) publicada pelo
     portal (a mais antiga, se houver várias). Sem ela — o comum —, o fim fica vazio (o consumidor
     carimba "data de fim desconhecida" e não exclui) e a estimativa (início de vigência do
     revogador, ou a publicação dele) vai só para `situacao_portal.fim_vigencia`. Suspensão (SUS) e
     revogação parcial não fecham o ato.

Padrão: só o plano (lê como ratio_leitura). `--aplicar` grava como rfb_writer, um ato
por transação, com antes/depois em ato_mudanca. Exige as migrations 010 e 011.

Uso:
    python coletar_atos_portal.py --lista ../references/atos_marco_2026-09-13.csv
    python coletar_atos_portal.py --ato INSTRUCAO_NORMATIVA:1911/2019 --aplicar
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

import carregar_ato_portal as cap
import visoes_portal as vp

SIJUT = "https://normas.receita.fazenda.gov.br/sijut2consulta/consulta.action"
PREFIXO_LINK = "http://normas.receita.fazenda.gov.br/sijut2consulta/"   # como a base grava `link`
TAXONOMIA = Path(__file__).resolve().parent.parent / "references" / "taxonomia.json"
FONTE = "normasinternet2_portal"
COR_SIMBOLO = {1: "referencia", 2: "altera", 3: "interrompe", 4: "recupera", 5: "retifica",
               6: "anotacao_futura"}
PAUSA = 1.0   # segundos entre pedidos ao portal (plano v2: ~1 req/s)


def _dados() -> Path:
    return Path(os.environ.get("RFB_ATOS_DADOS", r"C:\td-rfb-atos-dados")) / "portal"


# ---------------------------------------------------------------------------
# Portal (rede isolada numa classe: os testes injetam um portal falso)
# ---------------------------------------------------------------------------

class Portal:
    """Listagem SIJUT e visões do normasinternet2, com cache em disco do dia."""

    def __init__(self):
        self._listas: dict[tuple[str, int], str] = {}

    def _get(self, url: str):
        import requests
        r = requests.get(url, timeout=300, headers=cap.UA)
        time.sleep(PAUSA)
        return r

    def listagem(self, sijut_value: int, ano: int) -> list[str]:
        """HTML de todas as páginas da listagem do tipo no ano (sem filtro de vigentes)."""
        from bs4 import BeautifulSoup
        chave = (str(sijut_value), ano)
        if chave not in self._listas:
            base = (f"{SIJUT}?tiposAtosSelecionados={sijut_value}&ano_ato={ano}"
                    "&optOrdem=Publicacao_DESC")
            paginas, total, p = [], 1, 1
            while p <= total:
                html = self._get(f"{base}&p={p}").content.decode("utf-8", errors="replace")
                if p == 1:
                    sel = BeautifulSoup(html, "html.parser").find("select", id="p")
                    opcoes = sel.find_all("option") if sel else []
                    total = max((int(o.get("value", 1)) for o in opcoes), default=1)
                paginas.append(html)
                p += 1
            self._listas[chave] = "\n".join(paginas)
        return [self._listas[chave]]

    def visao(self, id_portal: int, nome: str) -> dict | None:
        """nome: 'vigente', 'original' ou 'relacional'. Reaproveita o arquivo do dia."""
        pasta = _dados()
        arquivo = pasta / f"{id_portal}_{nome}_{time.strftime('%Y%m%d')}.json"
        if arquivo.exists():
            return json.loads(arquivo.read_text(encoding="utf-8"))
        caminho = "visao-relacional" if nome == "relacional" else f"visao/{nome}"
        r = self._get(f"{cap.API}/{id_portal}/{caminho}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(r.content)
        return r.json()


def sijut_value(tipo_ato: str) -> int:
    tipos = json.loads(TAXONOMIA.read_text(encoding="utf-8"))["tipos_ato"]["lista"]
    return next(t["sijut_value"] for t in tipos if t["codigo"] == tipo_ato)


def eficacia_padrao(tipo_ato: str) -> str | None:
    tipos = json.loads(TAXONOMIA.read_text(encoding="utf-8"))["tipos_ato"]["lista"]
    return next((t.get("eficacia_default") for t in tipos if t["codigo"] == tipo_ato), None)


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------

def linhas_da_listagem(htmls: list[str], numero: str) -> list[dict]:
    """Linhas da listagem com esse número (só dígitos): [{idAto, cols, href}]."""
    from bs4 import BeautifulSoup
    alvo = re.sub(r"\D", "", numero)
    achados = []
    for html in htmls:
        for tabela in BeautifulSoup(html, "html.parser").find_all("table", id="tabelaAtos"):
            limpa = BeautifulSoup(str(tabela).replace("<br>", "\n"), "html.parser")
            for tr in limpa.find_all("tr"):
                cols = [c.get_text(strip=True) for c in tr.find_all("td")]
                if len(cols) != 5 or re.sub(r"\D", "", cols[1]) != alvo:
                    continue
                a = tr.find("a", href=True)
                m = re.search(r"/externa/(\d+)", a["href"]) if a else None
                if m and all(x["idAto"] != int(m.group(1)) for x in achados):
                    achados.append({"idAto": int(m.group(1)), "cols": cols, "href": a["href"]})
    return achados


def emissor_do_portal(orgao: str | None) -> str:
    """Mesma regra do extract_sijut.parse_orgao_unidade: 'Cosit' → COSIT, 'Disit/SRRF08' →
    DISIT_SRRF08, 'RFBSecex' → RFBSECEX."""
    if not orgao or not orgao.strip():
        return "RFB"
    return orgao.strip().upper().replace("/", "_").replace(" ", "_")


def linha_ato(tipo_ato: str, listagem: dict, vigente: dict) -> dict:
    """A linha de rfb_atos.ato, com o que a epígrafe do portal diz."""
    epigrafe = vigente.get("epigrafe") or {}
    numero = re.sub(r"\D", "", str(epigrafe.get("numeroAto") or listagem["cols"][1]))
    data_ato = vp.data_iso(epigrafe.get("dataAto"))
    publicacao = vp.data_iso(vigente.get("dataPublicacao")) or vp.data_dmy(listagem["cols"][3])
    ano = (data_ato or publicacao).year
    ementas = [vp.normalizar(e.get("textoIntegra")) for e in vigente.get("ementas") or []]
    ementa = next((e for e in ementas if e), None) or listagem["cols"][4] or None
    link = PREFIXO_LINK + listagem["href"]
    return {
        "tipo_ato": tipo_ato, "numero": numero, "ano": ano,
        "identificador": f"{tipo_ato} {numero}/{ano}",
        "emissor": emissor_do_portal(listagem["cols"][2]),
        "data_publicacao": publicacao,
        "ementa": ementa, "link": link, "url_html": link,
        "id_portal": int(vigente["idAto"]),
        "eficacia_atual": eficacia_padrao(tipo_ato),
        "pdf_disponivel": any(s.get("arquivoBinario") for s in vp.segmentos_exibidos(vigente)),
    }


def arestas_do_portal(id_portal: int, publicacao: date | None, relacional: dict) -> list[dict]:
    """Arestas da visão relacional, na convenção da base: 'origem age sobre destino'.
    impactosPoloAtivo = outros agem sobre ESTE; impactosPoloPassivo = ESTE age sobre outros."""
    arestas = []
    for lado in ("impactosPoloAtivo", "impactosPoloPassivo"):
        for imp in relacional.get(lado) or []:
            outro = imp.get("idAto")
            if not outro:
                continue
            ativo = lado == "impactosPoloAtivo"
            sigla, hint = imp.get("sigla"), imp.get("hint")
            arestas.append({
                "origem_portal": outro if ativo else id_portal,
                "destino_portal": id_portal if ativo else outro,
                "tipo_relacao": COR_SIMBOLO.get(imp.get("corSimbolo"),
                                                f"corSimbolo_{imp.get('corSimbolo')}"),
                "observacao": f"{sigla}: {hint}" if sigla and hint else (hint or sigla),
                # data da relação = publicação de quem age; efeito só quando o portal publica
                "data_relacao": vp.data_dmy(imp.get("dataPublicacaoDMY")) if ativo else publicacao,
                "data_efeito": vp.data_dmy(imp.get("dataVigenciaPrimeiraAnotacao")),
                "parcial": "parcial" in (hint or "").lower(),
                "rotulo_outro": imp.get("epigrafeBase"),
                "sigla": sigla,
            })
    return arestas


def fim_vigencia(relacional: dict, inicio_do_revogador: dict[int, date | None]) -> tuple:
    """(data_vigencia_fim, auditoria) de ato não vigente, pelas revogações totais (REV) do portal.

    Só a data de efeito publicada pelo portal vira `data_vigencia_fim`. Sem ela o fim fica vazio e a
    melhor estimativa (início de vigência do revogador, ou a publicação dele) vai só para a
    auditoria: fechar cedo demais esconderia norma ainda aplicável — revogação com efeito diferido,
    por exemplo (revisão 4-LLM de 13/09/2026, Gemini e Grok)."""
    efeitos, estimativas = [], []
    for imp in relacional.get("impactosPoloAtivo") or []:
        if imp.get("sigla") != "REV" or "parcial" in (imp.get("hint") or "").lower():
            continue
        revogador = {"revogador_id_portal": imp.get("idAto"), "revogador": imp.get("epigrafeBase")}
        efeito = vp.data_dmy(imp.get("dataVigenciaPrimeiraAnotacao"))
        if efeito:
            efeitos.append((efeito, revogador))
            continue
        inicio = inicio_do_revogador.get(imp.get("idAto"))
        estimativa = inicio or vp.data_dmy(imp.get("dataPublicacaoDMY"))
        if estimativa:
            origem = "inicio_vigencia_do_revogador" if inicio else "publicacao_do_revogador"
            estimativas.append((estimativa, {**revogador, "origem_estimativa": origem}))
    if efeitos:
        data, revogador = min(efeitos, key=lambda c: c[0])
        return data, {"data": data.isoformat(), "origem": "data_efeito_portal", **revogador}
    if estimativas:
        data, info = min(estimativas, key=lambda c: c[0])
        return None, {"data": None, "estimativa": data.isoformat(), **info,
                      "motivo": "o portal não publica a data de efeito desta revogação"}
    return None, None


def _fim_legivel(fim: date | None, auditoria: dict | None) -> str:
    if fim:
        return f"{fim} (data de efeito do portal)"
    if auditoria:
        return (f"vazio — estimativa {auditoria['estimativa']} "
                f"({auditoria['origem_estimativa']}), só na auditoria")
    return "vazio"


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

def _id_por_portal(conn, id_portal: int) -> int | None:
    linha = conn.execute("SELECT id FROM rfb_atos.ato WHERE id_portal = %s ORDER BY id LIMIT 1",
                         (id_portal,)).fetchone()
    if linha:
        return linha[0]
    # parte da base (importada do SIJUT) tem id_portal vazio e o idAto só no link
    linha = conn.execute(
        "SELECT id FROM rfb_atos.ato WHERE id_portal IS NULL AND link LIKE %s ORDER BY id LIMIT 1",
        (f"%/externa/{id_portal}/%",)).fetchone()
    return linha[0] if linha else None


def _mudanca(conn, run_id: str, ato_id: int, tabela: str, campo: str, antes, depois) -> None:
    conn.execute("INSERT INTO rfb_atos.ato_mudanca (run_id, ato_id, tabela, campo, antes, depois) "
                 "VALUES (%s, %s, %s, %s, %s, %s)",
                 (run_id, ato_id, tabela, campo, Jsonb(antes), Jsonb(depois)))


def gravar_aresta(conn, a: dict, origem_id: int, destino_id: int | None, run_id: str) -> str:
    """Grava uma aresta do portal sem duplicar e sem apagar (o rfb_writer não apaga)."""
    campos = (a["observacao"], a["data_relacao"], a["data_efeito"], a["parcial"])
    if destino_id is not None:
        existente = conn.execute(
            "SELECT id, fonte FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
            "AND ato_destino_id = %s AND tipo_relacao = %s", (origem_id, destino_id,
                                                              a["tipo_relacao"])).fetchone()
        if existente and existente[1] == FONTE:
            return "ja_existe"
        if existente:   # a mesma aresta, dita antes pelo modelo: o portal confirma
            conn.execute("UPDATE rfb_atos.ato_relacao SET fonte = %s, observacao = %s, "
                         "data_relacao = COALESCE(%s, data_relacao), "
                         "data_efeito = COALESCE(%s, data_efeito), parcial = %s WHERE id = %s",
                         (FONTE, *campos, existente[0]))
            _mudanca(conn, run_id, origem_id, "ato_relacao", f"fonte[{existente[0]}]",
                     existente[1], FONTE)
            return "confirmada"
        externa = conn.execute(
            "SELECT id FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
            "AND ato_destino_id IS NULL AND destino_id_portal = %s AND tipo_relacao = %s",
            (origem_id, a["destino_portal"], a["tipo_relacao"])).fetchone()
        if externa:
            conn.execute("UPDATE rfb_atos.ato_relacao SET ato_destino_id = %s WHERE id = %s",
                         (destino_id, externa[0]))
            _mudanca(conn, run_id, origem_id, "ato_relacao", f"ato_destino_id[{externa[0]}]",
                     None, destino_id)
            return "ligada"
        conn.execute(
            "INSERT INTO rfb_atos.ato_relacao (ato_origem_id, ato_destino_id, destino_id_portal, "
            "tipo_relacao, observacao, data_relacao, data_efeito, parcial, fonte) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (origem_id, destino_id, a["destino_portal"], a["tipo_relacao"], *campos, FONTE))
        return "inserida"
    if conn.execute("SELECT 1 FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
                    "AND ato_destino_id IS NULL AND destino_id_portal = %s AND tipo_relacao = %s",
                    (origem_id, a["destino_portal"], a["tipo_relacao"])).fetchone():
        return "ja_existe"
    conn.execute(
        "INSERT INTO rfb_atos.ato_relacao (ato_origem_id, ato_destino_id, destino_id_portal, "
        "destino_externo, tipo_relacao, observacao, data_relacao, data_efeito, parcial, fonte) "
        "VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)",
        (origem_id, a["destino_portal"], a["rotulo_outro"], a["tipo_relacao"], *campos, FONTE))
    return "externa"


def ligar_pendentes(conn, ato_id: int, id_portal: int, run_id: str) -> int:
    """Arestas que já apontavam para este ato pelo idAto (destino fora da base) passam a apontar
    para a linha nova — sem criar a duplicata que o rfb_writer não conseguiria apagar."""
    ligadas = conn.execute(
        "UPDATE rfb_atos.ato_relacao r SET ato_destino_id = %s WHERE r.destino_id_portal = %s "
        "AND r.ato_destino_id IS NULL AND NOT EXISTS (SELECT 1 FROM rfb_atos.ato_relacao x "
        "WHERE x.ato_origem_id = r.ato_origem_id AND x.ato_destino_id = %s "
        "AND x.tipo_relacao = r.tipo_relacao) RETURNING r.id, r.ato_origem_id",
        (ato_id, id_portal, ato_id)).fetchall()
    for rel_id, origem in ligadas:
        _mudanca(conn, run_id, origem, "ato_relacao", f"ato_destino_id[{rel_id}]", None, ato_id)
    return len(ligadas)


SQL_ATO = (
    "INSERT INTO rfb_atos.ato (tipo_ato, numero, ano, identificador, emissor, data_publicacao, "
    "ementa, link, url_html, id_portal, eficacia_atual, pdf_disponivel, content_disponivel, "
    "analise_completa, fonte_origem, importado_de, importado_em) VALUES (%(tipo_ato)s, "
    "%(numero)s, %(ano)s, %(identificador)s, %(emissor)s, %(data_publicacao)s, %(ementa)s, "
    "%(link)s, %(url_html)s, %(id_portal)s, %(eficacia_atual)s, %(pdf_disponivel)s, false, false, "
    "'sijut2_rfb', 'recoleta_portal', now()) RETURNING id")


def gravar_ato(conn, linha: dict, vigente: dict, original: dict | None, relacional: dict,
               inicio_do_revogador: dict[int, date | None], *, run_id: str, origem: str) -> dict:
    """Um ato numa transação: linha, texto (carregar_ato_portal), relações e fim de vigência."""
    with conn.transaction():
        ato_id = conn.execute(SQL_ATO, linha).fetchone()[0]
        _mudanca(conn, run_id, ato_id, "ato", "criado", None,
                 {k: (v.isoformat() if isinstance(v, date) else v) for k, v in linha.items()})
        resumo = cap.aplicar(conn, ato_id, vigente, original, run_id=run_id,
                             origem_vigente=origem, permitir_anexo_pdf=True)
        ligadas = ligar_pendentes(conn, ato_id, linha["id_portal"], run_id)
        # aresta externa que não pôde ser ligada (já havia a interna): fica à vista, não some
        restantes = conn.execute(
            "SELECT count(*) FROM rfb_atos.ato_relacao WHERE destino_id_portal = %s "
            "AND ato_destino_id IS NULL", (linha["id_portal"],)).fetchone()[0]

        contagem: dict[str, int] = {}
        sem_origem = []
        for a in arestas_do_portal(linha["id_portal"], linha["data_publicacao"], relacional):
            origem_id = (ato_id if a["origem_portal"] == linha["id_portal"]
                         else _id_por_portal(conn, a["origem_portal"]))
            if origem_id is None:
                sem_origem.append({"idAto": a["origem_portal"], "epigrafe": a["rotulo_outro"],
                                   "tipo_relacao": a["tipo_relacao"], "sigla": a["sigla"],
                                   "data_relacao": a["data_relacao"] and
                                   a["data_relacao"].isoformat()})
                continue
            destino_id = (ato_id if a["destino_portal"] == linha["id_portal"]
                          else _id_por_portal(conn, a["destino_portal"]))
            estado = gravar_aresta(conn, a, origem_id, destino_id, run_id)
            contagem[estado] = contagem.get(estado, 0) + 1

        extra: dict = {"relacoes_sem_origem": sem_origem} if sem_origem else {}
        fim = None
        if not vigente.get("vigente"):
            fim, origem_fim = fim_vigencia(relacional, inicio_do_revogador)
            extra["fim_vigencia"] = origem_fim
        if fim:
            conn.execute("UPDATE rfb_atos.ato SET data_vigencia_fim = %s WHERE id = %s",
                         (fim, ato_id))
            _mudanca(conn, run_id, ato_id, "ato", "data_vigencia_fim", None, fim.isoformat())
        if extra:
            conn.execute("UPDATE rfb_atos.ato SET situacao_portal = COALESCE(situacao_portal, "
                         "'{}'::jsonb) || %s WHERE id = %s", (Jsonb(extra), ato_id))
    return {**resumo, "ato_id": ato_id, "arestas": contagem, "ligadas": ligadas,
            "pendentes_nao_ligadas": restantes,
            "sem_origem": len(sem_origem), "data_vigencia_fim": fim and fim.isoformat()}


def ja_na_base(conn, linha: dict) -> int | None:
    por_portal = _id_por_portal(conn, linha["id_portal"])
    if por_portal:
        return por_portal
    # chave natural da base, sem o emissor (a grafia do órgão varia no legado); a data separa o ato
    # da retificação dele, que tem o mesmo número e ano
    achado = conn.execute(
        "SELECT id FROM rfb_atos.ato WHERE tipo_ato = %s "
        "AND regexp_replace(numero, '\\D', '', 'g') = %s AND data_publicacao = %s",
        (linha["tipo_ato"], linha["numero"], linha["data_publicacao"])).fetchone()
    return achado[0] if achado else None


def coletar(conn, alvo: tuple[str, str, int], portal, *, aplicar: bool, run_id: str,
            saida=print) -> list[dict]:
    """Todos os atos do portal com esse (tipo, número, ano). Sem `aplicar`, só o plano."""
    tipo, numero, ano = alvo
    achados = linhas_da_listagem(portal.listagem(sijut_value(tipo), ano), numero)
    if not achados:
        saida(f"[ausente] {tipo} {numero}/{ano}: não está na listagem do portal")
        return [{"alvo": f"{tipo} {numero}/{ano}", "erro": "ausente no portal"}]
    resultados = []
    for item in achados:
        rotulo = f"{tipo} {numero}/{ano} (idAto {item['idAto']})"
        vigente = portal.visao(item["idAto"], "vigente")
        relacional = portal.visao(item["idAto"], "relacional") or {}
        if not vigente:
            saida(f"[erro] {rotulo}: visão vigente indisponível")
            resultados.append({"alvo": rotulo, "erro": "visão vigente indisponível"})
            continue
        linha = linha_ato(tipo, item, vigente)
        existente = ja_na_base(conn, linha)
        if existente:
            saida(f"[já na base] {rotulo}: ato {existente}")
            resultados.append({"alvo": rotulo, "ato_id": existente, "ja_existia": True})
            continue
        revogadores = {imp["idAto"] for imp in relacional.get("impactosPoloAtivo") or []
                       if imp.get("sigla") == "REV" and imp.get("idAto")}
        inicio = {}
        if not vigente.get("vigente"):
            for rv in revogadores:
                v = portal.visao(rv, "vigente")
                inicio[rv] = vp.data_iso(v.get("dataVigenciaInicio")) if v else None
        texto = vp.texto_da_visao(vigente)
        fim, origem_fim = (fim_vigencia(relacional, inicio) if not vigente.get("vigente")
                           else (None, None))
        saida(f"[plano] {rotulo}: {linha['identificador']} {linha['emissor']} pub "
              f"{linha['data_publicacao']} | {vp.status_vigencia(vigente)} | {len(texto):,} "
              f"caracteres | anexo PDF: {vp.corpo_em_pdf(vigente)} | "
              f"{len(arestas_do_portal(linha['id_portal'], None, relacional))} relações | fim: "
              f"{_fim_legivel(fim, origem_fim)}")
        if not aplicar:
            resultados.append({"alvo": rotulo, "plano": True, "caracteres": len(texto)})
            continue
        original = portal.visao(item["idAto"], "original")
        resumo = gravar_ato(conn, linha, vigente, original, relacional, inicio, run_id=run_id,
                            origem=f"{cap.API}/{item['idAto']}/visao/vigente")
        saida(f"[ok] {rotulo}: {resumo}")
        resultados.append({"alvo": rotulo, **resumo})
    return resultados


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------

def ler_alvos(args) -> list[tuple[str, str, int]]:
    alvos = []
    for texto in args.ato or []:
        m = re.fullmatch(r"([A-Z_]+):([\d.]+)/(\d{4})", texto.strip())
        if not m:
            sys.exit(f"[erro] --ato no formato TIPO:NUMERO/ANO, recebido {texto!r}")
        alvos.append((m.group(1), re.sub(r"\D", "", m.group(2)), int(m.group(3))))
    if args.lista:
        with open(args.lista, encoding="utf-8") as f:
            for linha in csv.DictReader(f, delimiter=";"):
                alvos.append((linha["tipo_ato"], re.sub(r"\D", "", linha["numero"]),
                              int(linha["ano"])))
    return alvos


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ato", action="append", help="TIPO:NUMERO/ANO (repetível)")
    p.add_argument("--lista", help="CSV com tipo_ato;numero;ano")
    p.add_argument("--aplicar", action="store_true", help="grava (padrão: só o plano)")
    p.add_argument("--run-id")
    p.add_argument("--dsn")
    args = p.parse_args()
    alvos = ler_alvos(args)
    if not alvos:
        sys.exit("[erro] informe --ato ou --lista")
    run_id = args.run_id or f"coletar-{time.strftime('%Y%m%dT%H%M%S')}"
    portal = Portal()
    resultados = []
    with psycopg.connect(cap._dsn(args.dsn, args.aplicar), autocommit=True) as conn:
        if args.aplicar:
            cap.exigir_schema(conn)
        for alvo in alvos:
            try:
                resultados += coletar(conn, alvo, portal, aplicar=args.aplicar, run_id=run_id)
            except Exception as e:   # um ato com problema não derruba os outros
                print(f"[erro] {alvo}: {type(e).__name__}: {e}")
                resultados.append({"alvo": str(alvo), "erro": f"{type(e).__name__}: {e}"})
    erros = [r for r in resultados if r.get("erro")]
    gravados = [r for r in resultados if r.get("ato_id") and not r.get("ja_existia")]
    print(f"\n{len(resultados)} atos no portal | {len(gravados)} gravados | {len(erros)} com erro"
          + ("" if args.aplicar else " (plano: nada gravado; use --aplicar)"))
    sys.exit(1 if erros else 0)


if __name__ == "__main__":
    main()
