"""lote_categorizacao.py — categorização pela API em lote da Anthropic (Message Batches).

Metade do preço da chamada direta, resultado em até 24 h (decisão do dono em 24/09/2026 para as
Soluções de Consulta). O lote manda exatamente os pedidos que o modo automático faria; a resposta
de cada um vai para o mesmo arquivo em disco que a chamada direta usaria
(`categorizar_nuvem.arquivo_resposta`). Depois, o `aplicar` roda o modo automático sobre os atos do
lote: portão de qualidade e gravação sem pagar as matérias de novo — só o sinal e os vetores são
pedidos na hora (e partes cortadas no limite de tokens, que são divididas e pedidas direto).

Manifesto de cada lote em RFB_ATOS_DADOS/categorizacao/lotes/<batch_id>.json.

Uso:
    python lote_categorizacao.py enviar --tipos SOLUCAO_CONSULTA --limit 6000   # não grava no banco
    python lote_categorizacao.py status                                         # lotes em andamento
    python lote_categorizacao.py aplicar                                        # os que terminaram
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import psycopg

import categorizar_nuvem as cn

LOTE_MAX = 1_500     # pedidos por lote: o limite é 256 MB e cada pedido leva ~60 KB de prompt
DESCONTO_LOTE = 0.5  # a API em lote cobra metade


def pasta_lotes() -> Path:
    base = Path(os.environ.get("RFB_ATOS_DADOS", r"C:\td-rfb-atos-dados"))
    pasta = base / "categorizacao" / "lotes"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def pedidos(atos: list[dict], *, modelo: str | None = None,
            limite: int | None = None) -> tuple[list[dict], list[dict]]:
    """(pedidos, fora): um pedido por parte ainda sem resposta em disco; `fora` são os atos que o
    modo automático trata sem modelo (só ementa curta, sem texto)."""
    saida, fora = [], []
    for ato in atos:
        escolhido = modelo or cn.escolher_modelo(ato["tipo_ato"], ato["numero"], ato["ano"])
        try:
            partes, sistema = cn.preparar(ato, ato.get("texto_completo") or "", limite)
        except cn.FalhaCategorizacao as e:
            fora.append({"ato_id": ato["id"], "motivo": str(e)})
            continue
        for parte in partes:
            for usuario, arquivo in _faltam(ato, parte, len(partes), sistema, escolhido):
                saida.append({"custom_id": f"a{ato['id']}-{arquivo.stem}", "ato_id": ato["id"],
                              "arquivo": str(arquivo), "modelo": escolhido,
                              "params": cn.argumentos_da_chamada(escolhido, sistema, usuario,
                                                                 cn.MAX_TOKENS)})
    return saida, fora


def _faltam(ato: dict, parte, total: int, sistema: list[dict], modelo: str,
            profundidade: int = 0) -> list[tuple[str, Path]]:
    """Pedidos que faltam para esta parte. Resposta cortada no limite de tokens já em disco: pede as
    metades, na mesma divisão que o `_categorizar_parte` faria (revisão da PR #6)."""
    usuario = cn.mensagem_usuario(ato, parte, total)
    arquivo = cn.arquivo_resposta(ato["id"], modelo, sistema, usuario, cn.MAX_TOKENS)
    if not arquivo.exists():
        return [(usuario, arquivo)]
    guardada = json.loads(arquivo.read_text(encoding="utf-8"))
    if (guardada.get("parada") != "max_tokens" or len(parte.texto) < 2 * cn.MINIMO_PARTE
            or profundidade >= 3):
        return []
    faltam = []
    for i, sub in enumerate(cn.particionar(parte.texto, len(parte.texto) // 2 + 1), 1):
        sub = cn.Parte(f"{parte.rotulo}.{i}", sub.texto, sub.caminho or parte.caminho)
        faltam += _faltam(ato, sub, total, sistema, modelo, profundidade + 1)
    return faltam


def estimar_lote(atos: list[dict], modelo: str | None = None) -> float:
    return DESCONTO_LOTE * sum(cn.estimar(a, modelo or cn.MODELO_PADRAO)["custo"] for a in atos)


def em_voo() -> set[int]:
    """Atos num lote enviado e ainda não baixado, ou sendo criado há menos de 24 h (não podem ir
    de novo)."""
    agora = time.time()
    voando = {a for m in manifestos("enviado") for a in m["atos"]}
    for m in manifestos("criando"):
        criado = time.mktime(time.strptime(m["criado_em"], "%Y-%m-%dT%H:%M:%S"))
        if agora - criado < 24 * 3600:
            voando |= set(m["atos"])
    return voando


def _fatias(todos: list[dict], tamanho: int) -> list[list[dict]]:
    """Lotes de até `tamanho` pedidos sem separar as partes de um mesmo ato (revisão da PR #6)."""
    por_ato: dict[int, list[dict]] = {}
    for x in todos:
        por_ato.setdefault(x["ato_id"], []).append(x)
    fatias, atual = [], []
    for grupo in por_ato.values():
        if atual and len(atual) + len(grupo) > tamanho:
            fatias.append(atual)
            atual = []
        atual += grupo
    if atual:
        fatias.append(atual)
    return fatias


def enviar(atos: list[dict], cliente, *, modelo: str | None = None, limite: int | None = None,
           teto_usd: float | None = None, manual: bool = False, saida=print) -> list[str]:
    """Monta e envia os lotes. `manual`: a rotina noturna não aplica o lote — só o `aplicar` com o
    batch_id (ex.: reenvio depois de mudar a taxonomia, aplicado primeiro numa amostra)."""
    voando = em_voo()
    if voando:
        antes = len(atos)
        atos = [a for a in atos if a["id"] not in voando]
        if antes != len(atos):
            saida(f"[lote] {antes - len(atos)} atos já estão num lote em andamento: ficam fora")
    if teto_usd is not None:
        escolhidos, gasto = [], 0.0
        for a in atos:
            custo = estimar_lote([a], modelo)
            if gasto + custo > teto_usd:
                continue            # não cabe; um ato caro não segura os baratos atrás dele
            escolhidos.append(a)
            gasto += custo
        if len(escolhidos) < len(atos):
            saida(f"[lote] teto de US$ {teto_usd:.2f}: vão {len(escolhidos)} de {len(atos)} atos "
                  f"(~US$ {gasto:.2f}, estimativa conservadora)")
        atos = escolhidos
    todos, fora = pedidos(atos, modelo=modelo, limite=limite)
    if fora:
        saida(f"[lote] {len(fora)} atos fora do lote (o modo automático trata sem modelo)")
    ids = []
    for fatia in _fatias(todos, LOTE_MAX):
        manifesto = {
            "estado": "criando", "criado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "manual": manual,
            "atos": sorted({x["ato_id"] for x in fatia}),
            "pedidos": {x["custom_id"]: {"ato_id": x["ato_id"], "arquivo": x["arquivo"],
                                         "modelo": x["modelo"]} for x in fatia}}
        # o manifesto vem ANTES do lote: se o processo cair entre os dois, os atos ficam 24 h
        # fora da fila em vez de irem num segundo lote pago (revisão da PR #6, Grok)
        provisorio = pasta_lotes() / f"criando-{uuid.uuid4().hex}.json"
        provisorio.write_text(json.dumps(manifesto, ensure_ascii=False), encoding="utf-8")
        lote = cliente.messages.batches.create(
            requests=[{"custom_id": x["custom_id"], "params": x["params"]} for x in fatia])
        manifesto.update(batch_id=lote.id, estado="enviado")
        _salvar(manifesto)
        provisorio.unlink()
        saida(f"[lote] enviado {lote.id}: {len(fatia)} pedidos, {len(manifesto['atos'])} atos")
        ids.append(lote.id)
    return ids


def manifestos(estado: str | None = None) -> list[dict]:
    lista = [json.loads(f.read_text(encoding="utf-8"))
             for f in sorted(pasta_lotes().glob("*.json"))]
    return [m for m in lista if estado is None or m["estado"] == estado]


def _salvar(manifesto: dict) -> None:
    (pasta_lotes() / f"{manifesto['batch_id']}.json").write_text(
        json.dumps(manifesto, ensure_ascii=False), encoding="utf-8")


def baixar(batch_id: str, cliente, *, saida=print) -> dict | None:
    """Grava em disco as respostas de um lote terminado. None se o lote ainda está rodando."""
    arquivo = pasta_lotes() / f"{batch_id}.json"
    manifesto = json.loads(arquivo.read_text(encoding="utf-8"))
    lote = cliente.messages.batches.retrieve(batch_id)
    if lote.processing_status != "ended":
        saida(f"[lote] {batch_id}: {lote.processing_status}")
        return None
    uso = cn.Uso()
    ok = falhas = 0
    for r in cliente.messages.batches.results(batch_id):
        pedido = manifesto["pedidos"].get(r.custom_id)
        if pedido is None or r.result.type != "succeeded":
            falhas += 1
            continue
        msg = r.result.message
        u = msg.usage
        uso_d = {"entrada": u.input_tokens, "saida": u.output_tokens,
                 "cache_criado": u.cache_creation_input_tokens or 0,
                 "cache_lido": u.cache_read_input_tokens or 0}
        uso.somar(pedido["modelo"], uso_d, False)
        destino = Path(pedido["arquivo"])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps({
            "modelo": pedido["modelo"], "lote": batch_id, "uso": uso_d,
            "texto": "".join(b.text for b in msg.content if b.type == "text"),
            "parada": msg.stop_reason or "", "em": time.strftime("%Y-%m-%dT%H:%M:%S")},
            ensure_ascii=False), encoding="utf-8")
        ok += 1
    manifesto.update(estado="baixado", baixado_em=time.strftime("%Y-%m-%dT%H:%M:%S"),
                     resultado={"respostas": ok, "falhas": falhas, "uso": uso.por_modelo,
                                "custo_usd": round(uso.custo() * DESCONTO_LOTE, 2)})
    _salvar(manifesto)
    saida(f"[lote] {batch_id} baixado: {manifesto['resultado']}")
    return manifesto["resultado"]


def aplicar(batch_id: str, cliente, *, conectar, chamar, embed=None, paralelo: int = 4,
            teto_usd: float | None = None, so_atos: list[int] | None = None,
            saida=print) -> list[dict]:
    """Baixa (se preciso) e roda o modo automático sobre os atos do lote. Com `so_atos`, só esses
    (amostra): o lote continua "baixado" para o resto."""
    manifesto = json.loads((pasta_lotes() / f"{batch_id}.json").read_text(encoding="utf-8"))
    if manifesto["estado"] == "enviado" and baixar(batch_id, cliente, saida=saida) is None:
        return []
    with conectar() as conn:
        alvo = [a for a in manifesto["atos"] if so_atos is None or a in set(so_atos)]
        if not alvo:                    # lista vazia em carregar_atos não filtraria nada
            saida(f"[lote] {batch_id}: nenhum dos atos pedidos está no lote")
            return []
        atos = cn.carregar_atos(conn, ids=alvo)
    uso = cn.Uso()
    run_id = f"lote-{batch_id[-12:]}-{time.strftime('%Y%m%dT%H%M%S')}"
    resultados = cn.automatico(atos, chamar, conectar=conectar, run_id=run_id, uso=uso,
                               embed=embed, paralelo=paralelo, teto_usd=teto_usd,
                               custo_max_ato=None, so_disco=True, saida=saida)
    manifesto = json.loads((pasta_lotes() / f"{batch_id}.json").read_text(encoding="utf-8"))
    estados: dict[str, int] = {}
    for r in resultados:
        e = cn.estado_do_resultado(r)
        estados[e] = estados.get(e, 0) + 1
    # erro ou sinal/vetor incompleto: o lote fica "baixado" e é reaplicado na próxima rodada
    # (reaplicar não recategoriza — só completa o que faltou)
    completo = (not estados.get("erro") and not estados.get("incompleto")
                and so_atos is None)
    manifesto.update(estado="aplicado" if completo else "baixado",
                     aplicado_em=time.strftime("%Y-%m-%dT%H:%M:%S"),
                     aplicacao={"run_id": run_id, **estados,
                                "custo_direto_usd": round(uso.custo(), 2)})
    _salvar(manifesto)
    saida(f"[lote] {batch_id} aplicado: {manifesto['aplicacao']}")
    return resultados


def cliente_anthropic():
    from credenciais import resolver
    chave = resolver("anthropic")
    from anthropic import Anthropic
    return Anthropic(api_key=chave, max_retries=5)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enviar", help="monta e envia os lotes da fila (não grava no banco)")
    e.add_argument("--tipos", nargs="+", help="tipos da fila (obrigatório sem --ids-arquivo)")
    e.add_argument("--ids-arquivo", help="um id de ato por linha: manda esses, fora da fila (ex.: "
                   "reenvio dos atos em revisão depois de mudar a taxonomia)")
    e.add_argument("--limit", type=int, default=2000)
    e.add_argument("--plano", action="store_true", help="só conta e estima, não envia")
    sub.add_parser("status", help="lotes enviados e o estado de cada um")
    a = sub.add_parser("aplicar", help="baixa os lotes terminados e grava (rfb_writer)")
    a.add_argument("batch_id", nargs="?", help="um lote (padrão: todos os terminados)")
    a.add_argument("--paralelo", type=int, default=4)
    a.add_argument("--teto-usd", type=float, help="gasto direto máximo (sinal e partes cortadas)")
    a.add_argument("--atos-arquivo", help="um id por linha: aplica só esses atos do lote (amostra)")
    p.add_argument("--dsn")
    args = p.parse_args()

    if args.cmd == "enviar":
        with psycopg.connect(cn._dsn(args.dsn, False), autocommit=True) as conn:
            if args.ids_arquivo:
                ids = [int(x) for x in Path(args.ids_arquivo).read_text(encoding="utf-8").split()]
                atos = cn.carregar_atos(conn, ids=ids)[:args.limit]
            elif args.tipos:
                atos = cn.carregar_atos(conn, pendentes=True, limit=args.limit, tipos=args.tipos)
            else:
                p.error("enviar pede --tipos ou --ids-arquivo")
        todos, fora = pedidos(atos)
        print(f"{len(atos)} atos na fila | {len(todos)} pedidos | {len(fora)} fora (sem modelo) | "
              f"~US$ {estimar_lote(atos):.2f} no lote (preço de referência com o desconto)")
        if args.plano:
            return
        enviar(atos, cliente_anthropic(), manual=bool(args.ids_arquivo))
        return
    cliente = cliente_anthropic()
    if args.cmd == "status":
        for m in manifestos():
            extra = ""
            if m["estado"] == "enviado":
                lote = cliente.messages.batches.retrieve(m["batch_id"])
                c = lote.request_counts
                extra = (f" | {lote.processing_status}: {c.succeeded} ok, {c.processing} "
                         f"processando, {c.errored} erro")
            print(f"{m.get('batch_id', '(criando)')} {m['estado']:<9} {len(m['pedidos'])} pedidos, "
                  f"{len(m['atos'])} atos{extra}")
        return
    dsn = cn._dsn(args.dsn, True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        cn.exigir_schema(conn)
        cn.exigir_permissoes(conn)
    alvos = [args.batch_id] if args.batch_id else [
        m["batch_id"] for m in manifestos()
        if m["estado"] in ("enviado", "baixado") and not m.get("manual")]
    so_atos = ([int(x) for x in Path(args.atos_arquivo).read_text(encoding="utf-8").split()]
               if args.atos_arquivo else None)
    chamar = cn.chamador_anthropic()
    import rotina_noturna as rn           # mesma trava da rotina: nunca dois aplicar juntos
    trava = rn.travar(rn._pasta())
    if trava is None:
        sys.exit("[lote] a rotina noturna (ou outro aplicar) está rodando: tente depois")
    problemas = 0
    try:
        for batch_id in alvos:
            r = aplicar(batch_id, cliente, conectar=lambda: psycopg.connect(dsn, autocommit=True),
                        chamar=chamar, paralelo=args.paralelo, teto_usd=args.teto_usd,
                        so_atos=so_atos)
            # erro ou sinal/vetor faltando: saída 1 (o lote fica "baixado" para reaplicar)
            problemas += sum(1 for x in r if cn.estado_do_resultado(x) in ("erro", "incompleto"))
    finally:
        trava.unlink(missing_ok=True)   # a trava nunca fica presa se o aplicar cair no meio
    sys.exit(1 if problemas else 0)


if __name__ == "__main__":
    main()
