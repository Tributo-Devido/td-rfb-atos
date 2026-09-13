"""sintese_estudos.py — Sintese executiva de cada estudo.

Le todos os deep-research/*.md de um caso e produz 99-sintese.md
"""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from anthropic import Anthropic

MODEL = "claude-opus-4-7"
BASE = Path(__file__).resolve().parent.parent / "outputs" / "wip"

CASOS = {
    "combustiveis-alcool-creditos": {
        "titulo": "Síntese — Créditos de PIS/COFINS sobre Combustíveis e Álcool/Etanol",
        "instrucao": (
            "A partir dos 6 relatorios tematicos abaixo, produza uma sintese executiva organizada em:\n"
            "1. **Matriz por hipotese de uso x conclusao da RFB** (tabela markdown com hipotese, status [✅/❌/⚠️], "
            "atos chave). Cubra combustiveis em frota, em processo industrial, em revenda, em transporte de "
            "matéria-prima, em deslocamento de funcionarios, etc. E alcool: combustivel monofasico, materia-prima "
            "industrial, credito presumido produtor, REVENDA distribuidor (Lei 12.859/2013).\n"
            "2. **Setores mais expostos** (transportador rodoviario, distribuidor combustivel, industria quimica/"
            "farmaceutica/cosmetica, posto revendedor, agroindustria).\n"
            "3. **Top 5 oportunidades de tese** ranqueadas pelo grau de fundamentacao, com argumento, ato que "
            "nega, ato que da sustentacao a contestacao.\n"
            "4. **Riscos de glosa** mais frequentes (5).\n"
            "5. **Pontos abertos** que a base nao esclarece e merecem aprofundamento.\n\n"
            "Use estritamente o material fornecido. Cite atos por numero (ex: SC COSIT 137/2025)."
        ),
    },
    "transporte-cargas-creditos": {
        "titulo": "Síntese — Créditos de PIS/COFINS no Transporte de Cargas",
        "instrucao": (
            "A partir dos 8 relatorios tematicos abaixo, produza uma sintese executiva organizada em:\n"
            "1. **Matriz por rubrica x conclusao** (tabela markdown). Cubra: frete na aquisicao (insumo, ativo, "
            "revenda, monofasico), frete na venda (vendedor CIF), combustivel/manutencao de frota, pneus, peças, "
            "subcontratacao TAC (credito presumido), ativo imobilizado (caminhao), pedagio, vale-pedagio, seguro de "
            "carga, armazenagem, cabotagem, multimodal, transporte internacional, frete na cadeia monofasica/zero.\n"
            "2. **Por tipo de empresa**: transportadora rodoviaria pura, operador logistico, industria com frota "
            "propria, agroindustria, comercio com entrega propria, e-commerce, importador.\n"
            "3. **Top 5 oportunidades de tese** ranqueadas, com argumento, ato que nega, ato que sustenta.\n"
            "4. **Riscos de glosa** frequentes (5).\n"
            "5. **Pontos abertos** que a base nao esclarece.\n\n"
            "Use estritamente o material. Cite SCs/SDs por numero."
        ),
    },
}


def main():
    client = Anthropic()
    for caso, cfg in CASOS.items():
        dr_dir = BASE / caso / "v1" / "deep-research"
        if not dr_dir.exists():
            print(f"[skip] {caso}: deep-research nao existe")
            continue
        files = sorted(dr_dir.glob("*.md"))
        if not files:
            print(f"[skip] {caso}: sem arquivos")
            continue
        out = BASE / caso / "v1" / "99-sintese.md"
        if out.exists():
            print(f"[skip] {caso}: ja existe")
            continue

        relatorios = []
        for f in files:
            relatorios.append(f"# Relatorio: {f.stem}\n\n{f.read_text(encoding='utf-8')}")
        contexto = "\n\n===\n\n".join(relatorios)
        print(f"[start] {caso}: {len(files)} relatorios, {len(contexto):,} chars")

        prompt = (
            f"# Tarefa\n{cfg['instrucao']}\n\n"
            f"# Material (relatorios tematicos sobre o caso '{caso}')\n\n{contexto}"
        )
        response = client.messages.create(
            model=MODEL,
            max_tokens=20000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n\n".join(b.text for b in response.content if b.type == "text")
        header = f"# {cfg['titulo']}\n\n*Modelo: {MODEL} (adaptive thinking) — sintese de {len(files)} relatorios tematicos*\n\n---\n\n"
        out.write_text(header + text, encoding="utf-8")
        print(f"[ok] {out.name}: {len(text):,} chars")


if __name__ == "__main__":
    main()
