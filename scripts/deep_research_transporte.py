"""deep_research_transporte.py — Deep research SOMENTE com a base td-rfb-atos.

Reusa funcoes de deep_research_combustiveis.py mas com temas de transporte de cargas.
"""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
import sys
from pathlib import Path

# reusa estrutura
sys.path.insert(0, str(Path(__file__).parent))
from deep_research_combustiveis import buscar_atos_relevantes, consolidar_ato, renderizar_contexto, MODEL, PROMPT_BASE
from anthropic import Anthropic
from db import get_conn

OUT = Path(__file__).resolve().parent.parent / "outputs" / "wip" / "transporte-cargas-creditos" / "v1" / "deep-research"

TEMAS = {
    "01-frete-aquisicao-insumo": {
        "titulo": "Frete na aquisição — quando integra o crédito do bem adquirido",
        "queries": [
            "frete aquisicao insumo bem revenda credito PIS COFINS",
            "frete pago comprador aquisicao mercadoria nao cumulativo",
            "frete CIF FOB aquisicao bem industrializacao credito",
            "frete na compra de ativo imobilizado base depreciacao",
            "frete vinculado bem com aliquota zero monofasico",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Sintetize a posicao da RFB sobre frete na aquisicao: (a) frete pago pelo COMPRADOR vinculado a aquisicao de "
            "insumo, (b) frete pago pelo comprador vinculado a aquisicao de bem para revenda, (c) frete pago pelo "
            "comprador na aquisicao de ativo imobilizado, (d) frete na aquisicao de bem com aliquota zero/monofasico/"
            "isento. Diga quando integra o custo de aquisicao e quando vai ao credito separado. Cite SCs/SDs por numero."
        ),
    },
    "02-frete-venda-vendedor": {
        "titulo": "Frete na operação de venda — vendedor que paga frete CIF",
        "queries": [
            "frete operacao venda vendedor CIF custeado credito",
            "frete entrega mercadoria propria fabrica cliente",
            "vendedor suporta frete saida industrializacao credito",
            "Lei 10.833 inciso IX frete operacao venda PIS COFINS",
            "frete revenda monofasico aliquota zero exclusao",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Explique o credito do frete na operacao de venda — quando o VENDEDOR custeia o transporte ate o cliente "
            "(modalidade CIF). Cite a previsao legal (art. 3, IX da Lei 10.833/2003). Quando ha credito e quando ha "
            "vedacao (frete na revenda de monofasico, frete na saida com aliquota zero etc). Cite SCs por numero."
        ),
    },
    "03-frota-propria-combustivel-manutencao": {
        "titulo": "Frota própria — combustível, manutenção, peças, pneus, lubrificantes",
        "queries": [
            "frota propria combustivel manutencao caminhao PIS COFINS credito",
            "veiculo proprio transporte cargas peca pneu lubrificante",
            "manutencao maquina equipamento parte e peca prazo amortizacao",
            "transportador combustivel veiculo prestacao servico transporte",
            "deslocamento empregado entre estabelecimentos frota credito",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Mapeie todas as rubricas de manutencao e operacao de frota propria de uma transportadora rodoviaria de "
            "cargas: combustivel, lubrificante, oleo, pneu (incluindo recapagem), peca, manutencao mecanica, "
            "borracharia, lavagem, motorista terceirizado. Para cada uma diga se e insumo (gera credito) ou nao. "
            "Cite SCs/SDs por numero. Identifique a SD COSIT que pacificou (se houver)."
        ),
    },
    "04-tac-subcontratacao-credito-presumido": {
        "titulo": "TAC e subcontratação — Lei 11.442/2007 e o crédito presumido específico",
        "queries": [
            "transportador autonomo TAC subcontratacao Lei 11.442 credito presumido",
            "subcontratacao servico transporte credito presumido PIS COFINS",
            "credito presumido pessoa fisica autonomo transportador",
            "agregado motorista pessoa juridica transporte cargas",
            "calculo credito presumido subcontratacao percentual aliquota",
        ],
        "filtros": {"tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Explique a sistematica do credito presumido na subcontratacao de servico de transporte de carga (Lei "
            "11.442/2007 e legislacao posterior). Quem pode aproveitar, sobre que base, quais os percentuais, quais "
            "as condicoes. Diferenca entre subcontratacao de pessoa fisica autonoma (TAC) e de pessoa juridica. "
            "Cite SCs e INs por numero. Identifique restricoes recentes."
        ),
    },
    "05-pedagio-seguro-armazenagem": {
        "titulo": "Pedágio, seguro de carga, armazenagem — créditos vinculados",
        "queries": [
            "pedagio rodoviario credito PIS COFINS transporte",
            "seguro carga responsabilidade civil transportador credito",
            "armazenagem deposito credito PIS COFINS Lei 10.637",
            "vale-pedagio empregado transporte deducao",
            "operador logistico armazem alfandegado PIS COFINS",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Discuta credito sobre pedagio (proprio e repassado), seguro de carga, vale-pedagio (Lei 10.209/2001), "
            "armazenagem (terminal portuario, armazem geral, deposito alfandegado). Cite SCs por numero, indicando "
            "as hipoteses autorizadas, vedadas e condicionadas."
        ),
    },
    "06-cabotagem-multimodal": {
        "titulo": "Cabotagem, multimodal e transporte internacional",
        "queries": [
            "cabotagem navegacao aquaviario credito PIS COFINS",
            "multimodal operador OTM credito subcontratacao transporte",
            "transporte internacional importacao exportacao credito",
            "REPETRO regime aduaneiro especial navio embarcacao",
            "frete internacional servico exportacao isencao PIS COFINS",
        ],
        "filtros": {"tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Mapeie particularidades do credito PIS/COFINS no transporte de cabotagem, multimodal (OTM Lei 9.611) e "
            "internacional. Identifique regimes especiais (REPETRO, REPORTO) que afetam credito. Cite SCs e INs."
        ),
    },
    "07-frete-monofasico-zero-isento": {
        "titulo": "Frete na cadeia de produtos monofásicos, alíquota zero e suspensos",
        "queries": [
            "frete revenda monofasico credito vedado aliquota concentrada",
            "frete aquisicao bem aliquota zero credito vedado",
            "frete em operacao isenta suspensa credito proporcional",
            "frete substituicao tributaria PIS COFINS credito",
            "manutencao credito frete saida nao tributada Lei 11.033 art 17",
        ],
        "filtros": {"tributos": ["PIS","COFINS"]},
        "instrucao": (
            "Esclareca o tratamento do frete quando o produto transportado esta em regime monofasico, aliquota zero, "
            "suspenso ou isento. Distinga frete na aquisicao do frete na venda. Mencione manutencao do credito (Lei "
            "11.033/2004 art. 17) quando a saida nao e tributada. Cite SCs/SDs por numero."
        ),
    },
    "08-oportunidades-tese-transporte": {
        "titulo": "Oportunidades de tese no transporte de cargas — onde a RFB nega",
        "queries": [
            "transporte cargas insumo essencialidade Tema 779 STJ",
            "operador logistico atividade complexa servico credito amplo",
            "frota propria mercadoria propria credito glosa",
            "frete deslocamento entre estabelecimentos mesma empresa",
            "subcontratacao pessoa fisica TAC autonomo limitacao credito",
        ],
        "filtros": {"tributos": ["PIS","COFINS"]},
        "instrucao": (
            "A partir das vedacoes da RFB nos atos da base, identifique 4-6 hipoteses concretas no setor de "
            "transporte de cargas onde o contribuinte teria fundamento de tese (Tema 779 STJ, manutencao de credito "
            "art 17 Lei 11.033, principio da nao cumulatividade ampla). Para cada uma, indique a SC que nega, o "
            "argumento contrario e o caminho processual (administrativo ou judicial)."
        ),
    },
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    client = Anthropic()
    for tema_id, config in TEMAS.items():
        out_path = OUT / f"{tema_id}.md"
        if out_path.exists():
            print(f"[skip] {tema_id}")
            continue
        try:
            print(f"[start] {tema_id}: {config['titulo']}")
            ato_ids = buscar_atos_relevantes(
                config["queries"],
                filtro_tipos=config["filtros"].get("tipos"),
                filtro_tributos=config["filtros"].get("tributos"),
                top_per_query=10,
            )[:35]
            print(f"   {len(ato_ids)} atos relevantes")
            contextos = []
            with get_conn() as conn:
                for aid in ato_ids:
                    ato = consolidar_ato(conn, aid)
                    ctx = renderizar_contexto(ato, max_chars=2500)
                    if ctx:
                        contextos.append(ctx)
            contexto = "\n\n---\n\n".join(contextos)
            print(f"   {len(contexto):,} chars")
            prompt = PROMPT_BASE.format(titulo=config["titulo"], instrucao=config["instrucao"], contexto=contexto)
            response = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            )
            text = "\n\n".join(b.text for b in response.content if b.type == "text")
            header = f"# {config['titulo']}\n\n*Modelo: {MODEL} (adaptive thinking) — base td-rfb-atos · {len(ato_ids)} atos*\n\n---\n\n"
            out_path.write_text(header + text, encoding="utf-8")
            print(f"[ok]   {tema_id} ({len(text):,} chars)")
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[ERR]  {tema_id}: {e}")


if __name__ == "__main__":
    main()
