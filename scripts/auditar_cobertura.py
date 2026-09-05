"""auditar_cobertura.py -- revisao de cobertura do banco `rfb_atos`.

Responde, com numero e com lista nominal, a pergunta operacional:

    de todos os atos da Receita que a base conhece, quais chegaram ate o fim do
    funil -- PDF baixado -> texto extraido -> materias categorizadas -> embedding --
    e onde exatamente cada um parou?

O funil:

    ato -> [1] pdf -> [2] texto (ato_content) -> [3] materia -> [4] embedding

O ponto que motiva o script: a base nao distingue "o portal nao publica PDF para
este ato" (fato legitimo, nada a fazer) de "nunca tentamos baixar" e de "tentamos
e falhou". Os tres aparecem como `pdf_disponivel = false`. Enquanto essa duvida
nao vira dado, "tem ato que nao tem PDF" nao e uma medicao -- e uma suposicao, e
casos como a IN RFB 2.121/2022 (ato_id 16630, `status_vigencia =
'nao_disponivel_portal'`, sem uma linha em `ato_content`) ficam parados
indefinidamente sem aparecer em relatorio nenhum.

Este script NAO corrige nada. Ele mede, nomeia e prioriza. A correcao e do
`backfill_pdf.py` (PDF/teor) e do `reembed_cloud.py` (embedding).

Somente leitura -- e nao por convencao: a conexao e aberta com
`default_transaction_read_only = on`, entao o proprio Postgres recusa qualquer
escrita vinda daqui.

Uso:
    python auditar_cobertura.py                        # relatorio no stdout
    python auditar_cobertura.py --out ./relatorio      # + markdown e CSVs
    python auditar_cobertura.py --amostra 20           # mais exemplos por defeito
    python auditar_cobertura.py --foco 16630,16707     # holofote em atos citados
    python auditar_cobertura.py --tipo INSTRUCAO_NORMATIVA
    python auditar_cobertura.py --dsn postgresql://... # ou env RFB_ATOS_DSN
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psycopg

SCHEMA = "rfb_atos"

# DSN da nuvem quando o script roda na maquina do analista (Windows).
CLOUD_DSN_FILE = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")

# Atos que o diagnostico do td-legislacao (D-19/D-20, 01/09/2026) nomeou.
# Servem de canario: se a revisao nao os mostra, a revisao esta errada.
FOCO_PADRAO = [
    (16630, "IN RFB 2.121/2022 -- consolidacao PIS/COFINS; sem texto em ato_content"),
    (16707, "IN RFB 2.152/2023 -- tem texto, ainda nao analisado (nao e defeito)"),
    (14575, "SC COSIT 110/2025 -- fonte indireta usada no lugar do art. 171 da 2.121"),
    (12100, "SC COSIT 267/2023 -- fonte indireta usada no lugar dos arts. 179 e 185"),
]


# ----------------------------------------------------------------------
# Conexao
# ----------------------------------------------------------------------
def resolver_dsn(cli_dsn: str | None) -> str:
    """--dsn > RFB_ATOS_DSN > arquivo de DSN da nuvem."""
    if cli_dsn:
        return cli_dsn
    env = os.environ.get("RFB_ATOS_DSN")
    if env:
        return env
    if CLOUD_DSN_FILE.exists():
        return CLOUD_DSN_FILE.read_text(encoding="utf-8").strip()
    sys.exit(
        "[erro] sem DSN. Use --dsn, ou exporte RFB_ATOS_DSN, ou garanta o "
        f"tunnel SSM e o arquivo {CLOUD_DSN_FILE}."
    )


def conectar(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True)
    with conn.cursor() as cur:
        # Trava real de leitura: o servidor recusa escrita, nao so este codigo.
        cur.execute("SET default_transaction_read_only = on")
        cur.execute(f"SET search_path = {SCHEMA}, public")
    return conn


# ----------------------------------------------------------------------
# Introspeccao -- o script se adapta ao schema que encontrar
# ----------------------------------------------------------------------
@dataclass
class Schema:
    """O que existe de fato no banco, para nao quebrar o relatorio inteiro
    quando uma coluna opcional (ex.: ato_coleta, criada pela 010) falta."""

    tabelas: set[str] = field(default_factory=set)
    colunas: dict[str, set[str]] = field(default_factory=dict)

    def tem_tabela(self, nome: str) -> bool:
        return nome in self.tabelas

    def tem_coluna(self, tabela: str, coluna: str) -> bool:
        return coluna in self.colunas.get(tabela, set())


def inspecionar(conn: psycopg.Connection) -> Schema:
    esq = Schema()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = %s
            """,
            (SCHEMA,),
        )
        for tabela, coluna in cur.fetchall():
            esq.tabelas.add(tabela)
            esq.colunas.setdefault(tabela, set()).add(coluna)
    return esq


def exigir_minimo(esq: Schema) -> None:
    faltando = [t for t in ("ato", "ato_content", "ato_materia") if not esq.tem_tabela(t)]
    if faltando:
        sys.exit(
            f"[erro] schema {SCHEMA} nao tem {', '.join(faltando)}. "
            "DSN aponta para o banco certo?"
        )


# ----------------------------------------------------------------------
# Blocos SQL reusados
# ----------------------------------------------------------------------
# "Tem texto" e uma condicao mais forte que "existe linha em ato_content": uma
# linha com texto_completo NULL ou vazio nao alimenta categorizacao nenhuma, e
# contar ela como cobertura e como a base passou a mentir sobre si mesma.
TEM_TEXTO = """EXISTS (
    SELECT 1 FROM rfb_atos.ato_content c
     WHERE c.ato_id = a.id
       AND c.texto_completo IS NOT NULL
       AND length(btrim(c.texto_completo)) > 0
)"""

TEM_MATERIA = "EXISTS (SELECT 1 FROM rfb_atos.ato_materia m WHERE m.ato_id = a.id)"

MATERIA_PENDENTE = """EXISTS (
    SELECT 1 FROM rfb_atos.ato_materia m
     WHERE m.ato_id = a.id AND m.embedding IS NULL
)"""


def clausula_pdf_indicado(esq: Schema) -> str:
    """Sinais de que existe (ou existiu) um PDF para o ato.

    Sao dois sinais independentes e nenhum e confiavel sozinho: `pdf_disponivel`
    e o flag do crawler, `pdf_path` e o caminho gravado pela extracao. Um ato com
    caminho gravado e sem flag e tao real quanto o inverso.
    """
    partes = []
    if esq.tem_coluna("ato", "pdf_disponivel"):
        partes.append("COALESCE(a.pdf_disponivel, false)")
    if esq.tem_coluna("ato_content", "pdf_path"):
        partes.append(
            "EXISTS (SELECT 1 FROM rfb_atos.ato_content c2 "
            "WHERE c2.ato_id = a.id AND c2.pdf_path IS NOT NULL)"
        )
    return "(" + " OR ".join(partes) + ")" if partes else "false"


# ----------------------------------------------------------------------
# Definicao dos defeitos
# ----------------------------------------------------------------------
@dataclass
class Defeito:
    codigo: str
    titulo: str
    porque_importa: str
    where: str
    acao: str
    disponivel: bool = True
    motivo_indisponivel: str = ""


def montar_defeitos(esq: Schema) -> list[Defeito]:
    pdf_indicado = clausula_pdf_indicado(esq)
    tem_coleta = esq.tem_tabela("ato_coleta")

    defeitos = [
        Defeito(
            codigo="D1",
            titulo="analise_completa = true sem uma linha de texto",
            porque_importa=(
                "O consumidor da base confia em analise_completa para decidir se "
                "precisa buscar o teor em outro lugar. Marcado sem texto, o flag "
                "manda ele seguir em frente sem fonte -- foi assim que os arts. "
                "171/179/185 da IN 2.121 acabaram lidos por transcricao dentro de "
                "Solucao de Consulta."
            ),
            where=f"COALESCE(a.analise_completa, false) AND NOT {TEM_TEXTO}",
            acao="Rebaixar o flag e reenfileirar a coleta (backfill_pdf.py).",
            disponivel=esq.tem_coluna("ato", "analise_completa"),
            motivo_indisponivel="coluna ato.analise_completa ausente",
        ),
        Defeito(
            codigo="D2",
            titulo="content_disponivel = true sem texto (o flag mente a favor)",
            porque_importa=(
                "Ato entra em toda consulta como se tivesse teor e volta vazio. "
                "Pior que a ausencia, porque nao aparece em nenhuma fila de trabalho."
            ),
            where=f"COALESCE(a.content_disponivel, false) AND NOT {TEM_TEXTO}",
            acao="Rebaixar o flag e reenfileirar a coleta.",
            disponivel=esq.tem_coluna("ato", "content_disponivel"),
            motivo_indisponivel="coluna ato.content_disponivel ausente",
        ),
        Defeito(
            codigo="D3",
            titulo="content_disponivel = false com texto (o flag mente contra)",
            porque_importa=(
                "Trabalho ja pago que a base esconde: o teor esta la e nenhuma "
                "consulta filtrada por content_disponivel o encontra."
            ),
            where=f"NOT COALESCE(a.content_disponivel, false) AND {TEM_TEXTO}",
            acao="Promover o flag. Nao ha coleta a refazer.",
            disponivel=esq.tem_coluna("ato", "content_disponivel"),
            motivo_indisponivel="coluna ato.content_disponivel ausente",
        ),
        Defeito(
            codigo="D4",
            titulo="PDF indicado, texto ausente (baixou e nao extraiu)",
            porque_importa=(
                "Falha de extracao, nao de coleta -- o arquivo esta em maos. "
                "Custo de conserto baixissimo: reprocessar o PDF local, sem rede."
            ),
            where=f"{pdf_indicado} AND NOT {TEM_TEXTO}",
            acao="Reextrair do PDF ja baixado (MarkItDown/OCR); nao rebaixar para coleta.",
        ),
        Defeito(
            codigo="D5",
            titulo="texto presente, zero materia (nunca categorizado)",
            porque_importa=(
                "O ato tem teor e mesmo assim e invisivel para a busca: o atomo de "
                "pesquisa e a materia, nao o ato. Texto sem materia nao responde "
                "consulta nenhuma."
            ),
            where=f"{TEM_TEXTO} AND NOT {TEM_MATERIA}",
            acao="Enfileirar na categorizacao (Haiku) -- etapa CATEGORIZE do pipeline.",
        ),
        Defeito(
            codigo="D6",
            titulo="materia sem embedding",
            porque_importa=(
                "Materia sem vetor so aparece em busca lexical; some da busca "
                "semantica e do cross-base com o CARF."
            ),
            where=MATERIA_PENDENTE,
            acao="Rodar reembed_cloud.py (idempotente, filtra embedding IS NULL).",
            disponivel=esq.tem_coluna("ato_materia", "embedding"),
            motivo_indisponivel="coluna ato_materia.embedding ausente",
        ),
        Defeito(
            codigo="D7",
            titulo="sem texto e sem nenhuma tentativa de coleta registrada",
            porque_importa=(
                "Este e o bucket da duvida, e o maior risco de leitura errada da "
                "base: nao se sabe se o portal nao publica PDF ou se ninguem "
                "perguntou. Enquanto ele nao for a zero, 'este ato nao tem PDF' e "
                "suposicao, nao medicao."
            ),
            where=(
                f"NOT {TEM_TEXTO} AND NOT EXISTS ("
                "  SELECT 1 FROM rfb_atos.ato_coleta cl"
                "   WHERE cl.ato_id = a.id AND cl.tentativas > 0)"
                if tem_coleta
                else f"NOT {TEM_TEXTO}"
            ),
            acao=(
                "Probar no portal (backfill_pdf.py) para separar sem_pdf_no_portal "
                "de defeito de coleta."
                if tem_coleta
                else "APLICAR A MIGRATION 010 primeiro -- sem ato_coleta nao ha como "
                "separar 'nao tem PDF' de 'nunca tentamos'."
            ),
        ),
        Defeito(
            codigo="D8",
            titulo="status_vigencia = 'nao_disponivel_portal' nunca reprocessado",
            porque_importa=(
                "A base registrou que a coleta falhou e parou ai. E o estado exato "
                "em que a IN RFB 2.121/2022 ficou."
            ),
            where=(
                "a.status_vigencia = 'nao_disponivel_portal' "
                f"AND NOT {TEM_TEXTO}"
            ),
            acao="Reenfileirar com prioridade; conferir se ha teor ja salvo em disco.",
            disponivel=esq.tem_coluna("ato", "status_vigencia"),
            motivo_indisponivel="coluna ato.status_vigencia ausente",
        ),
    ]
    return defeitos


# ----------------------------------------------------------------------
# Consultas
# ----------------------------------------------------------------------
def _filtros(args) -> tuple[str, list]:
    cond, params = [], []
    if args.tipo:
        cond.append("a.tipo_ato = ANY(%s)")
        params.append([t.strip() for t in args.tipo.split(",")])
    if args.ano_min is not None:
        cond.append("a.ano >= %s")
        params.append(args.ano_min)
    if args.ano_max is not None:
        cond.append("a.ano <= %s")
        params.append(args.ano_max)
    return (" AND ".join(cond) if cond else "true"), params


def contar(conn, where: str, filtro: str, params: list) -> int:
    sql = f"SELECT count(*) FROM rfb_atos.ato a WHERE ({filtro}) AND ({where})"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def amostrar(conn, where: str, filtro: str, params: list, limite: int) -> list[tuple]:
    sql = f"""
        SELECT a.id, a.tipo_ato, a.numero, a.ano, a.status_vigencia,
               left(COALESCE(a.ementa, ''), 110)
          FROM rfb_atos.ato a
         WHERE ({filtro}) AND ({where})
         ORDER BY (a.tipo_ato = 'INSTRUCAO_NORMATIVA') DESC,
                  a.ano DESC NULLS LAST, a.id
         LIMIT {int(limite)}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def funil(conn, esq: Schema, filtro: str, params: list) -> list[tuple[str, int]]:
    pdf_indicado = clausula_pdf_indicado(esq)
    tem_coleta = esq.tem_tabela("ato_coleta")
    baixado = (
        "EXISTS (SELECT 1 FROM rfb_atos.ato_coleta cl "
        "WHERE cl.ato_id = a.id AND cl.pdf_status = 'baixado')"
        if tem_coleta
        else pdf_indicado
    )
    sem_pdf = (
        "EXISTS (SELECT 1 FROM rfb_atos.ato_coleta cl "
        "WHERE cl.ato_id = a.id AND cl.pdf_status = 'sem_pdf_no_portal')"
        if tem_coleta
        else "false"
    )
    embeddado = (
        f"{TEM_MATERIA} AND NOT {MATERIA_PENDENTE}"
        if esq.tem_coluna("ato_materia", "embedding")
        else "false"
    )
    sql = f"""
        SELECT count(*)                                              AS total,
               count(*) FILTER (WHERE {baixado})                     AS pdf,
               count(*) FILTER (WHERE {sem_pdf})                     AS sem_pdf,
               count(*) FILTER (WHERE {TEM_TEXTO})                   AS texto,
               count(*) FILTER (WHERE {TEM_MATERIA})                 AS materia,
               count(*) FILTER (WHERE {embeddado})                   AS embeddado
          FROM rfb_atos.ato a
         WHERE ({filtro})
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        t, pdf, sem, txt, mat, emb = cur.fetchone()
    return [
        ("0. atos na base", t),
        ("1. PDF baixado", pdf),
        ("1b. sem PDF no portal (fato apurado)", sem),
        ("2. texto extraido", txt),
        ("3. materias categorizadas", mat),
        ("4. embedding completo", emb),
    ]


def por_recorte(conn, esq: Schema, coluna: str, filtro: str, params: list, limite: int):
    embeddado = (
        f"{TEM_MATERIA} AND NOT {MATERIA_PENDENTE}"
        if esq.tem_coluna("ato_materia", "embedding")
        else "false"
    )
    sql = f"""
        SELECT COALESCE(a.{coluna}::text, '(sem valor)')      AS recorte,
               count(*)                                       AS total,
               count(*) FILTER (WHERE {TEM_TEXTO})            AS com_texto,
               count(*) FILTER (WHERE {TEM_MATERIA})          AS com_materia,
               count(*) FILTER (WHERE {embeddado})            AS com_embedding
          FROM rfb_atos.ato a
         WHERE ({filtro})
         GROUP BY 1
         ORDER BY count(*) FILTER (WHERE NOT {TEM_TEXTO}) DESC, count(*) DESC
         LIMIT {int(limite)}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def foco(conn, esq: Schema, ids: list[int]) -> list[tuple]:
    if not ids:
        return []
    embeddado = (
        f"({TEM_MATERIA} AND NOT {MATERIA_PENDENTE})"
        if esq.tem_coluna("ato_materia", "embedding")
        else "false"
    )
    sql = f"""
        SELECT a.id, a.tipo_ato, a.numero, a.ano,
               COALESCE(a.status_vigencia, '-'),
               COALESCE(a.content_disponivel, false),
               COALESCE(a.analise_completa, false),
               {TEM_TEXTO},
               (SELECT count(*) FROM rfb_atos.ato_materia m WHERE m.ato_id = a.id),
               {embeddado}
          FROM rfb_atos.ato a
         WHERE a.id = ANY(%s)
         ORDER BY a.id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        return cur.fetchall()


# ----------------------------------------------------------------------
# Relatorio
# ----------------------------------------------------------------------
def pct(n: int, total: int) -> str:
    return "-" if not total else f"{100.0 * n / total:.1f}%"


def num(n: int) -> str:
    """Milhar com ponto, como se le em portugues."""
    return f"{n:,}".replace(",", ".")


def atos(n: int) -> str:
    return f"{num(n)} ato" + ("" if n == 1 else "s")


def tabela_md(cabecalho: list[str], linhas: list[list]) -> list[str]:
    out = ["| " + " | ".join(cabecalho) + " |",
           "|" + "|".join("---" for _ in cabecalho) + "|"]
    for ln in linhas:
        out.append("| " + " | ".join("" if c is None else str(c) for c in ln) + " |")
    return out


def montar_relatorio(conn, esq: Schema, args) -> tuple[str, dict[str, list]]:
    filtro, params = _filtros(args)
    csvs: dict[str, list] = {}
    L: list[str] = []

    L.append("# Revisao de cobertura -- `rfb_atos`")
    L.append("")
    L.append(f"Gerado em {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
             f"schema `{SCHEMA}` · somente leitura")
    if args.tipo or args.ano_min or args.ano_max:
        L.append(f"Recorte aplicado: tipo={args.tipo or 'todos'} "
                 f"ano={args.ano_min or '-'}..{args.ano_max or '-'}")
    if not esq.tem_tabela("ato_coleta"):
        L.append("")
        L.append("> **`rfb_atos.ato_coleta` nao existe.** Sem ela a revisao nao "
                 "consegue separar *o portal nao publica PDF* de *nunca tentamos* "
                 "-- os dois seguem contados juntos em D7. Aplicar a migration "
                 "`010_ato_coleta.sql` antes de tratar 'ato sem PDF' como fato.")
    L.append("")

    # ---- Funil
    L.append("## 1. Funil")
    L.append("")
    linhas_funil = funil(conn, esq, filtro, params)
    total = linhas_funil[0][1]
    L += tabela_md(
        ["etapa", "atos", "% do total"],
        [[nome, num(n), pct(n, total)] for nome, n in linhas_funil],
    )
    csvs["funil"] = [["etapa", "atos", "pct"]] + [
        [nome, n, pct(n, total)] for nome, n in linhas_funil
    ]
    L.append("")
    L.append("Cada etapa e pre-requisito da seguinte. A queda entre duas linhas "
             "consecutivas e o trabalho pendente daquela etapa -- e so dela.")
    L.append("")

    # ---- Defeitos
    L.append("## 2. Defeitos")
    L.append("")
    resumo, detalhe = [], []
    for d in montar_defeitos(esq):
        if not d.disponivel:
            resumo.append([d.codigo, d.titulo, "n/d", d.motivo_indisponivel])
            continue
        n = contar(conn, d.where, filtro, params)
        resumo.append([d.codigo, d.titulo, num(n), d.acao])
        detalhe.append((d, n))
    L += tabela_md(["#", "defeito", "atos", "acao"], resumo)
    csvs["defeitos"] = [["codigo", "defeito", "atos", "acao"], *resumo]
    L.append("")

    for d, n in detalhe:
        L.append(f"### {d.codigo} -- {d.titulo}")
        L.append("")
        L.append(f"**{atos(n)}.** {d.porque_importa}")
        L.append("")
        L.append(f"*Acao:* {d.acao}")
        L.append("")
        if n:
            amostra = amostrar(conn, d.where, filtro, params, args.amostra)
            L += tabela_md(
                ["ato_id", "tipo", "numero", "ano", "status", "ementa"],
                [list(r) for r in amostra],
            )
            csvs[f"defeito_{d.codigo}"] = [
                ["ato_id", "tipo_ato", "numero", "ano", "status_vigencia", "ementa"]
            ] + [list(r) for r in amostra]
            restantes = n - len(amostra)
            if restantes > 0:
                L.append("")
                L.append(f"*({num(restantes)} nao listado"
                         + ("" if restantes == 1 else "s")
                         + " -- use `--amostra` ou o CSV.)*")
        L.append("")

    # ---- Recortes
    secao = 2
    for coluna, titulo in (("tipo_ato", "tipo de ato"), ("ano", "ano")):
        if not esq.tem_coluna("ato", coluna):
            continue
        secao += 1
        L.append(f"## {secao}. Por {titulo}")
        L.append("")
        L.append("Ordenado por *sem texto* decrescente -- e a fila de trabalho, "
                 "nao um censo.")
        L.append("")
        linhas = por_recorte(conn, esq, coluna, filtro, params, args.recorte)
        linhas_md = [
            [r[0], num(r[1]), num(r[1] - r[2]), num(r[2]), pct(r[2], r[1]),
             num(r[3]), num(r[4])]
            for r in linhas
        ]
        L += tabela_md(
            [titulo, "atos", "sem texto", "com texto", "% texto",
             "com materia", "com embedding"],
            linhas_md,
        )
        csvs[f"por_{coluna}"] = [
            [coluna, "atos", "sem_texto", "com_texto", "pct_texto",
             "com_materia", "com_embedding"]
        ] + [[r[0], r[1], r[1] - r[2], r[2], pct(r[2], r[1]), r[3], r[4]]
             for r in linhas]
        L.append("")

    # ---- Foco
    ids = ([int(x) for x in args.foco.split(",") if x.strip()]
           if args.foco else [i for i, _ in FOCO_PADRAO])
    notas = dict(FOCO_PADRAO)
    linhas_foco = foco(conn, esq, ids)
    secao += 1
    L.append(f"## {secao}. Atos nomeados no diagnostico")
    L.append("")
    if not linhas_foco:
        L.append("Nenhum dos ato_id pedidos existe na base.")
    else:
        L += tabela_md(
            ["ato_id", "tipo", "numero", "ano", "status", "content_disp",
             "analise_compl", "tem texto", "materias", "embeddado", "nota"],
            [[*r, notas.get(r[0], "")] for r in linhas_foco],
        )
        csvs["foco"] = [
            ["ato_id", "tipo_ato", "numero", "ano", "status_vigencia",
             "content_disponivel", "analise_completa", "tem_texto",
             "n_materias", "embeddado"]
        ] + [list(r) for r in linhas_foco]
        ausentes = sorted(set(ids) - {r[0] for r in linhas_foco})
        if ausentes:
            L.append("")
            L.append("Pedidos e ausentes da base: "
                     + ", ".join(str(i) for i in ausentes))
    L.append("")

    return "\n".join(L), csvs


def gravar(destino: Path, relatorio: str, csvs: dict[str, list]) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "REVISAO-COBERTURA.md").write_text(relatorio, encoding="utf-8")
    for nome, linhas in csvs.items():
        with (destino / f"{nome}.csv").open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(linhas)
    print(f"[ok] {destino / 'REVISAO-COBERTURA.md'} + {len(csvs)} CSVs", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dsn", help="DSN Postgres (default: RFB_ATOS_DSN, depois arquivo)")
    p.add_argument("--out", type=Path, help="diretorio para markdown + CSVs")
    p.add_argument("--amostra", type=int, default=10, help="exemplos por defeito")
    p.add_argument("--recorte", type=int, default=25, help="linhas por recorte")
    p.add_argument("--foco", help="ato_ids separados por virgula")
    p.add_argument("--tipo", help="filtra tipo_ato (lista separada por virgula)")
    p.add_argument("--ano-min", type=int)
    p.add_argument("--ano-max", type=int)
    args = p.parse_args(argv)

    with conectar(resolver_dsn(args.dsn)) as conn:
        esq = inspecionar(conn)
        exigir_minimo(esq)
        relatorio, csvs = montar_relatorio(conn, esq, args)

    print(relatorio)
    if args.out:
        gravar(args.out, relatorio, csvs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
