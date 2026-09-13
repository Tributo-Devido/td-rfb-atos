"""detectar_conflitos.py — Detecta pares de materias com mesmo fato e sinais opostos.

Para cada tema_especifico:
  1. Pega todas as materias com embedding (de SCs/SDs/SCIs vinculantes)
  2. Compara par-a-par via cosine similarity
  3. Se sim > 0.85 e sinais opostos (AUTORIZA vs VEDA), marca como conflito
  4. A mais recente ato_b_supera=TRUE
  5. Persiste em conflito_temporal

Origem do `sinal`:
  - Default (recomendado): coluna ato_materia.sinal, populada por LLM via
    classificar_sinal_haiku.py (Haiku 4.5 batch). Materias com sinal NULL
    sao ignoradas. Materias com sinal=INDETERMINADO sao ignoradas.
  - Fallback: --heuristica usa keyword matching antigo (precisao baixa).

Uso:
    py detectar_conflitos.py                     # todos os temas, coluna sinal
    py detectar_conflitos.py --tema CREDITAMENTO.COMBUSTIVEL
    py detectar_conflitos.py --limiar 0.80
    py detectar_conflitos.py --heuristica        # fallback enquanto batch nao terminou
"""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
import argparse
from psycopg.rows import dict_row
from db import get_conn

CATEGORIAS_AUTORIZADO = [
    "permite", "permitido", "autorizado", "é considerado insumo",
    "podem ser considerados insumos", "geram direito",
    "gera direito a crédito", "pode apurar", "pode descontar", "é admitido",
]
CATEGORIAS_VEDADO = [
    "não permite", "vedado", "vedada", "não geram direito", "não pode apurar",
    "não constituem insumos", "não há direito", "impossibilidade",
    "não se enquadram", "não permitida", "não cabe", "não é admitido",
    "é vedada", "é vedado",
]

# Mapa LLM -> rotulo persistido em conflito_temporal (compat com detectores anteriores)
SINAL_LLM_TO_LEGACY = {
    "AUTORIZA": "AUTORIZADO",
    "VEDA": "VEDADO",
    "CONDICIONA": "CONDICIONADO",
}


def classificar(solucao: str | None) -> str:
    if not solucao:
        return "?"
    s = solucao.lower()
    a = sum(1 for k in CATEGORIAS_AUTORIZADO if k in s)
    v = sum(1 for k in CATEGORIAS_VEDADO if k in s)
    if v > a:
        return "VEDADO"
    if a > 0:
        return "AUTORIZADO"
    return "CONDICIONADO"


def detectar_para_tema(conn, tema: str, limiar: float, dry_run: bool, heuristica: bool):
    sinal_filter = "" if heuristica else "AND m.sinal IN ('AUTORIZA','VEDA','CONDICIONA')"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""
            SELECT m.id, m.solucao, m.sinal AS sinal_db, m.ato_id,
                   a.data_publicacao, a.tipo_ato, a.numero, a.orgao_emissor, a.eficacia
            FROM ato_materia m
            JOIN atos a ON a.id = m.ato_id
            WHERE m.tema_especifico = %s
              AND m.embedding IS NOT NULL
              AND a.tipo_ato IN ('SOLUCAO_CONSULTA','SOLUCAO_DIVERGENCIA','SOLUCAO_CONSULTA_INTERNA')
              AND a.eficacia LIKE 'vinculante%%'
              AND m.solucao IS NOT NULL
              {sinal_filter}
            ORDER BY a.data_publicacao
        """, [tema])
        materias = cur.fetchall()

    if len(materias) < 2:
        return 0

    for m in materias:
        if heuristica:
            m["sinal"] = classificar(m["solucao"])
        else:
            # coluna LLM -> rotulo legacy (AUTORIZA -> AUTORIZADO etc.)
            m["sinal"] = SINAL_LLM_TO_LEGACY.get(m["sinal_db"], "?")

    # Calcula similaridades par-a-par via SQL (mais rapido que pull no python)
    ids = [m["id"] for m in materias]
    sinal = {m["id"]: m["sinal"] for m in materias}
    data = {m["id"]: m["data_publicacao"] for m in materias}
    ato = {m["id"]: m["ato_id"] for m in materias}
    label = {m["id"]: f"{m['tipo_ato']} {m['numero']}/{str(m['data_publicacao'])[:4]} {m['orgao_emissor']}" for m in materias}

    # Pares com sim > limiar
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.id AS a_id, b.id AS b_id, 1 - (a.embedding <=> b.embedding) AS sim
            FROM ato_materia a
            JOIN ato_materia b ON b.id > a.id AND b.ato_id != a.ato_id
            WHERE a.id = ANY(%s) AND b.id = ANY(%s)
              AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > %s
        """, [ids, ids, limiar])
        pares = cur.fetchall()

    # Filtra pares com sinais opostos (AUTORIZADO vs VEDADO)
    conflitos = []
    for a_id, b_id, sim in pares:
        sa, sb = sinal[a_id], sinal[b_id]
        if {sa, sb} == {"AUTORIZADO", "VEDADO"}:
            # Define qual e a (mais antigo) e b (mais novo)
            if data[a_id] <= data[b_id]:
                m_a, m_b = a_id, b_id
            else:
                m_a, m_b = b_id, a_id
            conflitos.append({
                "materia_a_id": m_a, "materia_b_id": m_b,
                "similaridade": float(sim),
                "sinal_a": sinal[m_a], "sinal_b": sinal[m_b],
                "data_a": data[m_a], "data_b": data[m_b],
                "ato_b_supera": data[m_b] > data[m_a],
            })

    # Verifica se ja ha ato_relacao formal
    ato_pares = {(c["materia_a_id"], c["materia_b_id"]): (ato[c["materia_a_id"]], ato[c["materia_b_id"]]) for c in conflitos}
    formais: set[tuple[int,int]] = set()
    if ato_pares:
        ato_set = {x for pair in ato_pares.values() for x in pair}
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ato_origem_id, ato_destino_id FROM ato_relacao
                WHERE (ato_origem_id = ANY(%s) AND ato_destino_id = ANY(%s))
                   OR (ato_destino_id = ANY(%s) AND ato_origem_id = ANY(%s))
            """, [list(ato_set), list(ato_set), list(ato_set), list(ato_set)])
            for o, d in cur.fetchall():
                formais.add((o, d)); formais.add((d, o))
    for c in conflitos:
        a_ato, b_ato = ato[c["materia_a_id"]], ato[c["materia_b_id"]]
        c["formal_relacao_existe"] = (a_ato, b_ato) in formais
        c["observacao"] = f"{label[c['materia_a_id']]} ({c['sinal_a']}) -> {label[c['materia_b_id']]} ({c['sinal_b']})"

    if dry_run:
        for c in conflitos:
            print(f"  [{c['similaridade']:.3f}] {c['observacao']} | formal={c['formal_relacao_existe']}")
        return len(conflitos)

    # Persiste
    with conn.cursor() as cur:
        for c in conflitos:
            cur.execute("""
                INSERT INTO conflito_temporal
                (tema_especifico, materia_a_id, materia_b_id, similaridade, sinal_a, sinal_b,
                 data_a, data_b, ato_b_supera, formal_relacao_existe, observacao)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (materia_a_id, materia_b_id) DO UPDATE SET
                  similaridade = EXCLUDED.similaridade,
                  sinal_a = EXCLUDED.sinal_a, sinal_b = EXCLUDED.sinal_b,
                  ato_b_supera = EXCLUDED.ato_b_supera,
                  formal_relacao_existe = EXCLUDED.formal_relacao_existe,
                  observacao = EXCLUDED.observacao
            """, [tema, c["materia_a_id"], c["materia_b_id"], c["similaridade"],
                  c["sinal_a"], c["sinal_b"], c["data_a"], c["data_b"],
                  c["ato_b_supera"], c["formal_relacao_existe"], c["observacao"]])
        conn.commit()

    return len(conflitos)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tema", help="Filtrar por tema_especifico (default: todos)")
    p.add_argument("--limiar", type=float, default=0.85, help="Limiar de similaridade (default 0.85)")
    p.add_argument("--dry-run", action="store_true", help="So reporta, nao persiste")
    p.add_argument("--heuristica", action="store_true",
                   help="Usa keyword matching legacy em vez da coluna sinal (LLM)")
    p.add_argument("--limpar", action="store_true",
                   help="Apaga conflitos antigos antes de rodar (recomendado ao trocar criterio)")
    args = p.parse_args()

    with get_conn() as conn:
        if args.limpar:
            with conn.cursor() as cur:
                if args.tema:
                    cur.execute("DELETE FROM conflito_temporal WHERE tema_especifico = %s", [args.tema])
                else:
                    cur.execute("DELETE FROM conflito_temporal")
                conn.commit()
            print(f"[limpar] conflitos antigos removidos")

        if not args.heuristica:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ato_materia WHERE sinal IS NOT NULL")
                n_classificadas = cur.fetchone()[0]
            if n_classificadas == 0:
                print("[aviso] coluna sinal vazia. Rode classificar_sinal_haiku.py primeiro,")
                print("        ou use --heuristica para fallback keyword-based.")
                return
            print(f"[modo] coluna sinal LLM ({n_classificadas:,} materias classificadas)")
        else:
            print(f"[modo] heuristica keyword (legacy)")

        sinal_extra = "" if args.heuristica else "AND m.sinal IN ('AUTORIZA','VEDA','CONDICIONA')"
        if args.tema:
            temas = [args.tema]
        else:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT m.tema_especifico, COUNT(*) AS n
                    FROM ato_materia m
                    JOIN atos a ON a.id = m.ato_id
                    WHERE m.tema_especifico IS NOT NULL
                      AND m.embedding IS NOT NULL
                      AND a.tipo_ato IN ('SOLUCAO_CONSULTA','SOLUCAO_DIVERGENCIA','SOLUCAO_CONSULTA_INTERNA')
                      AND a.eficacia LIKE 'vinculante%%'
                      {sinal_extra}
                    GROUP BY m.tema_especifico
                    HAVING COUNT(*) >= 3
                    ORDER BY COUNT(*) DESC
                """)
                temas = [r[0] for r in cur.fetchall()]
            print(f"Temas com >=3 materias vinculantes: {len(temas)}")

        total = 0
        for i, tema in enumerate(temas, 1):
            n = detectar_para_tema(conn, tema, args.limiar, args.dry_run, args.heuristica)
            if n > 0:
                print(f"[{i:>3}/{len(temas)}] {tema}: {n} conflitos")
            total += n
        print(f"\nTOTAL: {total} conflitos detectados em {len(temas)} temas")


if __name__ == "__main__":
    main()
