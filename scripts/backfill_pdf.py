"""backfill_pdf.py -- fecha a lacuna entre "nao tem PDF" e "nunca tentamos".

A auditoria (`auditar_cobertura.py`) mostra o buraco; este script vai atras dele.
Para cada ato sem teor, pergunta ao portal da Receita e grava o que aconteceu em
`rfb_atos.ato_coleta` (migration 010):

    baixado             o arquivo veio -- segue para extracao
    sem_pdf_no_portal   o portal respondeu e nao ha PDF     <- vira FATO, nao suposicao
    erro_http           o portal respondeu erro (http_status guardado)
    erro_rede           timeout / conexao / TLS
    bloqueado           rate limit duro, captcha

O ganho nao e so baixar arquivo: e que depois desta rodada `sem_pdf_no_portal`
passa a ser uma afirmacao com data e evidencia, e a fila de trabalho encolhe para
o que de fato falta.

ESCOPO -- de proposito, este script para no arquivo:

    [este script]  ato -> PDF em disco + ato_coleta
    [ja existe]    PDF -> ato_content        (etapa EXTRACT, MarkItDown)
    [ja existe]    ato_content -> ato_materia (etapa CATEGORIZE, Haiku)
    [ja existe]    ato_materia -> embedding   (reembed_cloud.py, idempotente)

Misturar download com extracao faria uma falha de OCR parecer falha de coleta --
que e exatamente a confusao que a migration 010 desfaz.

GOVERNANCA -- tres niveis, e o default nao escreve nada:

    (sem flag)   dry-run: monta a fila, resolve URLs, imprime o plano. Nada sai
                 pela rede, nada e gravado.
    --probe N    busca N atos de verdade e imprime o resultado cru (status,
                 content-type, tamanho, primeiros bytes). NAO grava no banco.
                 Rode isto primeiro: e como se confirma que o contrato do portal
                 e o que este script supoe, antes de qualquer escrita.
    --aplicar    busca, salva o arquivo e grava ato_coleta.

>>> O contrato HTTP com o SIJUT (formato da URL, como o portal sinaliza
>>> "sem PDF", content-type devolvido) NAO foi verificado contra o portal em
>>> tempo de escrita deste arquivo -- o ambiente onde ele foi escrito nao tem
>>> saida para normas.receita.fazenda.gov.br. Rode `--probe 5` e confira a saida
>>> antes de rodar `--aplicar` em lote. Se o portal responder diferente do
>>> suposto, o ajuste esta em `classificar_resposta()` e `resolver_url()`, que
>>> sao funcoes puras e tem teste.

Uso:
    python backfill_pdf.py                              # plano, nao faz nada
    python backfill_pdf.py --probe 5                    # confere o portal
    python backfill_pdf.py --aplicar --limite 200       # lote pequeno
    python backfill_pdf.py --aplicar --tipo INSTRUCAO_NORMATIVA
    python backfill_pdf.py --aplicar --ato 16630        # a IN 2.121 na frente
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

SCHEMA = "rfb_atos"
CLOUD_DSN_FILE = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")
PDF_DIR_PADRAO = Path(os.environ.get("RFB_ATOS_PDF_DIR", "./pdfs"))

BASE_SIJUT = "https://normas.receita.fazenda.gov.br/sijut2consulta"

# Cortesia com o portal. Nao e paranoia: derrubar o SIJUT custa a coleta inteira,
# e a fila e de dezenas de milhares -- meio segundo por ato ja resolve.
PAUSA_S = float(os.environ.get("RFB_ATOS_PAUSA_S", "0.5"))
TIMEOUT_S = 30
MAX_TENTATIVAS = 4

# Status HTTP em que insistir e util. 404 nao esta aqui: se o portal diz que nao
# existe, insistir nao muda a resposta -- muda so o consumo do portal.
RETENTAVEIS = {408, 425, 429, 500, 502, 503, 504}


# ----------------------------------------------------------------------
# Funcoes puras -- o que da para testar sem rede e sem banco
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Alvo:
    ato_id: int
    tipo_ato: str
    numero: str | None
    ano: int | None
    url_pdf: str | None
    url_html: str | None
    link: str | None
    id_portal: int | None
    tentativas: int = 0


def resolver_url(alvo: Alvo) -> str | None:
    """URL a tentar, na ordem em que a base merece confianca.

    Preferencia por URL gravada em vez de URL montada: o que o crawler viu vale
    mais que o que este script deduz. So cai no padrao do SIJUT quando ha
    `id_portal` e nenhuma URL -- e ai e palpite explicito, nao disfarcado.
    """
    for candidata in (alvo.url_pdf, alvo.link, alvo.url_html):
        if candidata and candidata.strip().startswith(("http://", "https://")):
            return candidata.strip()
    if alvo.id_portal:
        return f"{BASE_SIJUT}/link.action?idAto={alvo.id_portal}&visao=original"
    return None


@dataclass(frozen=True)
class Resultado:
    pdf_status: str
    http_status: int | None = None
    content_type: str | None = None
    bytes_baixados: int | None = None
    sha256: str | None = None
    erro: str | None = None
    corpo: bytes | None = None

    @property
    def retentavel(self) -> bool:
        if self.pdf_status == "erro_rede":
            return True
        return self.http_status in RETENTAVEIS if self.http_status else False


def classificar_resposta(
    http_status: int, content_type: str | None, corpo: bytes
) -> Resultado:
    """Traduz uma resposta HTTP em um estado de coleta.

    A distincao que carrega o script: 404 e uma resposta -- o portal falou e
    disse que nao ha. Isso e `sem_pdf_no_portal`, um fato apurado, e nao volta
    para a fila. Um 503 e o portal calado, e volta.

    O portal tambem devolve 200 com HTML quando nao ha PDF (pagina de erro ou de
    consulta). Tratar isso como sucesso encheria o disco de HTML disfarcado de
    ato -- por isso o corpo e inspecionado, nao so o status.
    """
    ct = (content_type or "").lower()

    if http_status == 404:
        return Resultado("sem_pdf_no_portal", http_status, ct, len(corpo))
    if http_status == 429:
        return Resultado("bloqueado", http_status, ct, len(corpo),
                         erro="rate limit do portal")
    if http_status >= 400:
        return Resultado("erro_http", http_status, ct, len(corpo),
                         erro=f"HTTP {http_status}")

    # Corpo vazio antes de qualquer outra coisa: um 200 com zero byte e falha de
    # transferencia, mesmo que o servidor jure `application/pdf` no cabecalho --
    # e nunca autoriza afirmar que o ato nao tem PDF.
    if not corpo:
        return Resultado("erro_http", http_status, ct, 0, erro="200 com corpo vazio")

    if corpo[:5] == b"%PDF-" or "application/pdf" in ct:
        return Resultado(
            "baixado", http_status, ct, len(corpo),
            sha256=hashlib.sha256(corpo).hexdigest(), corpo=corpo,
        )

    if "text/html" in ct or corpo[:200].lstrip()[:9].lower() in (b"<!doctype", b"<html"):
        # 200 + HTML: o portal respondeu, mas nao com o ato.
        return Resultado("sem_pdf_no_portal", http_status, ct, len(corpo),
                         erro="200 com HTML em vez de PDF")

    # Nao e PDF, nao e HTML, tem conteudo: guardar e deixar a extracao decidir.
    return Resultado(
        "baixado", http_status, ct, len(corpo),
        sha256=hashlib.sha256(corpo).hexdigest(), corpo=corpo,
    )


def caminho_pdf(raiz: Path, ato_id: int) -> Path:
    """Espalha em subpastas de 1.000: 30k+ arquivos em um diretorio so degrada
    listagem e backup em qualquer filesystem."""
    return raiz / f"{ato_id // 1000:04d}" / f"{ato_id}.pdf"


TETO_BACKOFF_S = 6 * 3600.0  # 6h: nova tentativa na proxima rodada, nao na mesma


def backoff_s(tentativa: int) -> float:
    """Espera ate a proxima tentativa, em segundos.

    Exponencial com jitter -- sem o jitter, uma fila inteira que tomou 503 volta
    ao portal toda junta, no mesmo instante, e reproduz a sobrecarga que causou
    o 503. O teto e aplicado DEPOIS do jitter: aplicar antes deixaria o valor
    final passar do teto em ate 50%.
    """
    bruto = (2.0**max(0, tentativa)) * 60.0 * (0.5 + random.random())
    return min(TETO_BACKOFF_S, bruto)


# ----------------------------------------------------------------------
# Rede
# ----------------------------------------------------------------------
def buscar(url: str, sessao) -> Resultado:
    try:
        resp = sessao.get(url, timeout=TIMEOUT_S, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001 -- qualquer falha de rede e a mesma classe aqui
        return Resultado("erro_rede", erro=f"{type(exc).__name__}: {exc}"[:500])
    return classificar_resposta(
        resp.status_code, resp.headers.get("Content-Type"), resp.content
    )


def abrir_sessao():
    try:
        import requests
    except ImportError:
        sys.exit("[erro] falta `requests` -- pip install requests")
    s = requests.Session()
    s.headers.update({
        "User-Agent": "td-rfb-atos/backfill (Tributo Devido; contato via TI)",
        "Accept": "application/pdf,text/html;q=0.8,*/*;q=0.5",
    })
    return s


# ----------------------------------------------------------------------
# Banco
# ----------------------------------------------------------------------
def resolver_dsn(cli: str | None) -> str:
    if cli:
        return cli
    if os.environ.get("RFB_ATOS_DSN"):
        return os.environ["RFB_ATOS_DSN"]
    if CLOUD_DSN_FILE.exists():
        return CLOUD_DSN_FILE.read_text(encoding="utf-8").strip()
    sys.exit("[erro] sem DSN. Use --dsn ou exporte RFB_ATOS_DSN.")


def exigir_010(conn) -> None:
    existe = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (f"{SCHEMA}.ato_coleta",)
    ).fetchone()[0]
    if not existe:
        sys.exit(
            f"[erro] {SCHEMA}.ato_coleta nao existe -- aplique "
            "migrations/010_ato_coleta.sql antes. Sem ela o resultado da coleta "
            "nao tem onde ser gravado, e a rodada inteira se perde."
        )


def montar_fila(conn, args) -> list[Alvo]:
    """Ordem da fila: IN primeiro (consolidam materia inteira e sao as mais
    citadas pelo proprio acervo), depois publicacao mais recente."""
    cond = [
        """NOT EXISTS (
            SELECT 1 FROM rfb_atos.ato_content c
             WHERE c.ato_id = a.id
               AND c.texto_completo IS NOT NULL
               AND length(btrim(c.texto_completo)) > 0
        )""",
        """COALESCE(cl.pdf_status, 'nao_tentado') IN
             ('nao_tentado','erro_http','erro_rede','erro_extracao')""",
        "(cl.proxima_tentativa IS NULL OR cl.proxima_tentativa <= now())",
        "COALESCE(cl.tentativas, 0) < %(max_tent)s",
    ]
    params: dict = {"max_tent": MAX_TENTATIVAS, "limite": args.limite}
    if args.ato:
        cond.append("a.id = ANY(%(ids)s)")
        params["ids"] = [int(x) for x in args.ato.split(",") if x.strip()]
    if args.tipo:
        cond.append("a.tipo_ato = ANY(%(tipos)s)")
        params["tipos"] = [t.strip() for t in args.tipo.split(",")]
    if args.ano_min is not None:
        cond.append("a.ano >= %(ano_min)s")
        params["ano_min"] = args.ano_min

    sql = f"""
        SELECT a.id, a.tipo_ato, a.numero, a.ano,
               a.url_pdf, a.url_html, a.link, a.id_portal,
               COALESCE(cl.tentativas, 0)
          FROM rfb_atos.ato a
          LEFT JOIN rfb_atos.ato_coleta cl ON cl.ato_id = a.id
         WHERE {' AND '.join(cond)}
         ORDER BY (a.tipo_ato = 'INSTRUCAO_NORMATIVA') DESC,
                  a.data_publicacao DESC NULLS LAST, a.id
         LIMIT %(limite)s
    """
    return [Alvo(*row) for row in conn.execute(sql, params).fetchall()]


def gravar(conn, alvo: Alvo, url: str | None, res: Resultado, origem: str) -> bool:
    """Grava o resultado da tentativa. Idempotente por ato_id.

    `proxima_tentativa` so e preenchida quando vale insistir; para um fato
    apurado (`sem_pdf_no_portal`) ela fica NULL e o ato sai da fila para sempre --
    e essa a diferenca entre medir e ficar rodando em circulos.

    Devolve True quando `analise_completa` teve de ser rebaixado -- ou seja,
    quando este ato era um dos que a base dava por analisado sem ter texto.
    """
    proxima = "now() + (%(espera)s || ' seconds')::interval" if res.retentavel else "NULL"
    conn.execute(
        f"""
        INSERT INTO rfb_atos.ato_coleta
            (ato_id, pdf_status, url_tentada, http_status, content_type,
             bytes_baixados, sha256, tentativas, primeira_tentativa,
             ultima_tentativa, proxima_tentativa, erro, origem)
        VALUES
            (%(ato_id)s, %(status)s, %(url)s, %(http)s, %(ct)s,
             %(bytes)s, %(sha)s, 1, now(), now(), {proxima}, %(erro)s, %(origem)s)
        ON CONFLICT (ato_id) DO UPDATE SET
            pdf_status        = EXCLUDED.pdf_status,
            url_tentada       = EXCLUDED.url_tentada,
            http_status       = EXCLUDED.http_status,
            content_type      = EXCLUDED.content_type,
            bytes_baixados    = EXCLUDED.bytes_baixados,
            sha256            = EXCLUDED.sha256,
            tentativas        = rfb_atos.ato_coleta.tentativas + 1,
            primeira_tentativa= COALESCE(rfb_atos.ato_coleta.primeira_tentativa, now()),
            ultima_tentativa  = now(),
            proxima_tentativa = EXCLUDED.proxima_tentativa,
            erro              = EXCLUDED.erro,
            origem            = EXCLUDED.origem
        """,
        {
            "ato_id": alvo.ato_id, "status": res.pdf_status, "url": url,
            "http": res.http_status, "ct": res.content_type,
            "bytes": res.bytes_baixados, "sha": res.sha256,
            "erro": res.erro, "origem": origem,
            "espera": int(backoff_s(alvo.tentativas + 1)),
        },
    )
    # Os flags de `ato` continuam sendo o que o resto do ecossistema le, entao
    # ficam em sincronia aqui -- senao a base passa a ter duas verdades.
    #
    # `analise_completa` e `content_disponivel` sao realinhados com a presenca
    # real de texto, e nao por zelo: sem isso o backfill nao consegue nem gravar.
    # O invariante da 010 (`ato_analise_exige_conteudo`) entra NOT VALID, o que
    # tolera a linha legada parada mas rejeita qualquer UPDATE nela -- e as
    # linhas legadas sao exatamente as 912 que este script existe para destravar.
    #
    # O realinhamento e defensavel porque acontece com evidencia fresca sobre
    # ESTE ato, uma linha por vez: `analise_completa = true` sem uma letra de
    # texto nunca foi verdade. Nao ha UPDATE em massa em lugar nenhum -- essa
    # decisao continua sendo do dono da base.
    linha = conn.execute(
        """
        WITH antes AS (
            SELECT id, analise_completa FROM rfb_atos.ato WHERE id = %(ato_id)s
        ), txt AS (
            SELECT EXISTS (
                SELECT 1 FROM rfb_atos.ato_content c
                 WHERE c.ato_id = %(ato_id)s
                   AND c.texto_completo IS NOT NULL
                   AND length(btrim(c.texto_completo)) > 0
            ) AS tem_texto
        )
        UPDATE rfb_atos.ato a
           SET pdf_disponivel     = %(baixado)s,
               content_disponivel = txt.tem_texto,
               analise_completa   = (a.analise_completa AND txt.tem_texto)
          FROM antes, txt
         WHERE a.id = antes.id
        RETURNING antes.analise_completa AS era, a.analise_completa AS virou
        """,
        {"baixado": res.pdf_status == "baixado", "ato_id": alvo.ato_id},
    ).fetchone()
    return bool(linha and linha[0] and not linha[1])


# ----------------------------------------------------------------------
# Modos
# ----------------------------------------------------------------------
def atos(n: int) -> str:
    return f"{n} ato" + ("" if n == 1 else "s")


def modo_plano(fila: list[Alvo]) -> int:
    sem_url = [a for a in fila if not resolver_url(a)]
    print(f"[plano] {atos(len(fila))} na fila (nada foi buscado nem gravado)")
    print(f"[plano] {len(fila) - len(sem_url)} com URL resolvida, "
          f"{len(sem_url)} sem URL nenhuma")
    print()
    for a in fila[:20]:
        print(f"  {a.ato_id:>7}  {a.tipo_ato:<28} {a.numero or '-':>8}/{a.ano or '-'}"
              f"  -> {resolver_url(a) or '(SEM URL -- nao da para tentar)'}")
    if len(fila) > 20:
        print(f"  ... e mais {len(fila) - 20}")
    if sem_url:
        print()
        print(f"[atencao] {atos(len(sem_url))} sem url_pdf, link, url_html nem "
              "id_portal. Para esses o backfill nao tem o que tentar -- e um "
              "buraco do crawler, nao da coleta.")
    print()
    print("Proximo passo: `--probe 5` para conferir o contrato do portal, "
          "depois `--aplicar`.")
    return 0


def modo_probe(fila: list[Alvo], n: int) -> int:
    sessao = abrir_sessao()
    print(f"[probe] {n} atos, resultado cru, NADA e gravado no banco")
    print()
    for a in fila[:n]:
        url = resolver_url(a)
        if not url:
            print(f"  {a.ato_id}: sem URL")
            continue
        res = buscar(url, sessao)
        print(f"  ato {a.ato_id} ({a.tipo_ato} {a.numero}/{a.ano})")
        print(f"    url        {url}")
        print(f"    http       {res.http_status}   content-type: {res.content_type}")
        print(f"    bytes      {res.bytes_baixados}")
        print(f"    -> status  {res.pdf_status}"
              + (f"   ({res.erro})" if res.erro else ""))
        if res.corpo:
            print(f"    inicio     {res.corpo[:60]!r}")
        print()
        time.sleep(PAUSA_S)
    print("Confira se `-> status` bate com a realidade de cada caso antes de "
          "rodar --aplicar. Se nao bater, ajuste classificar_resposta().")
    return 0


def modo_aplicar(conn, fila: list[Alvo], raiz: Path, origem: str) -> int:
    sessao = abrir_sessao()
    contagem: dict[str, int] = {}
    flags_rebaixados = 0
    for i, a in enumerate(fila, 1):
        url = resolver_url(a)
        if not url:
            res = Resultado("erro_http", erro="ato sem URL conhecida no cadastro")
        else:
            res = buscar(url, sessao)
            if res.pdf_status == "baixado" and res.corpo:
                destino = caminho_pdf(raiz, a.ato_id)
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(res.corpo)
        flags_rebaixados += gravar(conn, a, url, res, origem)
        conn.commit()
        contagem[res.pdf_status] = contagem.get(res.pdf_status, 0) + 1
        if i % 50 == 0 or i == len(fila):
            print(f"[{i}/{len(fila)}] " +
                  "  ".join(f"{k}={v}" for k, v in sorted(contagem.items())),
                  flush=True)
        if res.pdf_status == "bloqueado":
            print("[parando] o portal aplicou rate limit. Retome mais tarde -- a "
                  "fila e resumivel, nada se perde.", file=sys.stderr)
            break
        time.sleep(PAUSA_S)

    print()
    for k, v in sorted(contagem.items()):
        print(f"  {k:<20} {v}")
    if flags_rebaixados:
        print()
        print(f"  {flags_rebaixados} atos tiveram `analise_completa` rebaixado: "
              "estavam marcados como analisados sem uma letra de texto.")
    baixados = contagem.get("baixado", 0)
    if baixados:
        print()
        print(f"{baixados} arquivos novos em {raiz}. Proximo passo: extracao "
              "(PDF -> ato_content), depois categorizacao e reembed_cloud.py.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dsn")
    p.add_argument("--aplicar", action="store_true",
                   help="busca de verdade e grava (sem isto, so imprime o plano)")
    p.add_argument("--probe", type=int, metavar="N",
                   help="busca N atos e mostra o resultado cru, sem gravar")
    p.add_argument("--limite", type=int, default=500)
    p.add_argument("--ato", help="ato_ids separados por virgula")
    p.add_argument("--tipo", help="filtra tipo_ato")
    p.add_argument("--ano-min", type=int)
    p.add_argument("--pdf-dir", type=Path, default=PDF_DIR_PADRAO)
    args = p.parse_args(argv)

    origem = f"backfill_pdf@{time.strftime('%Y-%m-%d')}"
    with psycopg.connect(resolver_dsn(args.dsn)) as conn:
        conn.execute(f"SET search_path = {SCHEMA}, public")
        exigir_010(conn)
        fila = montar_fila(conn, args)
        if not fila:
            print("[ok] fila vazia -- nada elegivel para coleta agora.")
            return 0
        if args.probe:
            return modo_probe(fila, args.probe)
        if not args.aplicar:
            return modo_plano(fila)
        return modo_aplicar(conn, fila, args.pdf_dir, origem)


if __name__ == "__main__":
    raise SystemExit(main())
