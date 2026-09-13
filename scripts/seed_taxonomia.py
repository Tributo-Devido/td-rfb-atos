"""
seed_taxonomia.py — Popula tabelas taxonomia_* a partir de references/taxonomia.json.

Idempotente. Roda como parte do setup OU após mudanças no taxonomia.json.

Uso:
    py seed_taxonomia.py
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from db import get_conn

TAXONOMIA_PATH = Path(__file__).resolve().parent.parent / "references" / "taxonomia.json"
SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "references" / "schemas_metadata_tematico"
WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "references" / "ranking_weights.json"


def _build_natureza_por_tipo(weights: dict) -> dict[str, str]:
    """Inverte natureza_por_tipo de {natureza: [tipos]} em {tipo: natureza}."""
    out: dict[str, str] = {}
    for natureza, tipos in (weights.get("natureza_por_tipo") or {}).items():
        for tipo in tipos:
            out[tipo] = natureza
    return out


def main():
    data = json.loads(TAXONOMIA_PATH.read_text(encoding="utf-8"))
    weights = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    natureza_map = _build_natureza_por_tipo(weights)

    with get_conn() as conn:
        with conn.cursor() as cur:

            # tipos_ato
            for t in data["tipos_ato"]["lista"]:
                cur.execute(
                    """
                    INSERT INTO taxonomia_tipo_ato (codigo, nome, sigla, sijut_value, eficacia_default, ativo, natureza)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (codigo) DO UPDATE SET
                      nome = EXCLUDED.nome,
                      sigla = EXCLUDED.sigla,
                      sijut_value = EXCLUDED.sijut_value,
                      eficacia_default = EXCLUDED.eficacia_default,
                      ativo = EXCLUDED.ativo,
                      natureza = EXCLUDED.natureza
                    """,
                    (t["codigo"], t["nome"], t.get("sigla"), t.get("sijut_value"),
                     t.get("eficacia_default"), t.get("ativo", True),
                     natureza_map.get(t["codigo"])),
                )
            logger.info(f"tipos_ato: {len(data['tipos_ato']['lista'])} upserted")

            # orgaos_emissores
            for o in data["orgaos_emissores"]["lista"]:
                cur.execute(
                    """
                    INSERT INTO taxonomia_orgao_emissor (codigo, nome, hierarquia, eficacia_default)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (codigo) DO UPDATE SET
                      nome = EXCLUDED.nome,
                      hierarquia = EXCLUDED.hierarquia,
                      eficacia_default = EXCLUDED.eficacia_default
                    """,
                    (o["codigo"], o["nome"], o.get("hierarquia"), o.get("eficacia_default")),
                )
            logger.info(f"orgaos_emissores: {len(data['orgaos_emissores']['lista'])} upserted")

            # tributos
            for t in data["tributos"]["lista"]:
                cur.execute(
                    """
                    INSERT INTO taxonomia_tributo (codigo, nome)
                    VALUES (%s, %s)
                    ON CONFLICT (codigo) DO UPDATE SET nome = EXCLUDED.nome
                    """,
                    (t["codigo"], t["nome"]),
                )
            logger.info(f"tributos: {len(data['tributos']['lista'])} upserted")

            # setores
            for s in data["setores_economicos"]["lista_referencia"]:
                cur.execute(
                    """
                    INSERT INTO taxonomia_setor_economico (codigo, nome)
                    VALUES (%s, %s)
                    ON CONFLICT (codigo) DO NOTHING
                    """,
                    (s, s.replace("_", " ").title()),
                )
            logger.info(f"setores: {len(data['setores_economicos']['lista_referencia'])} upserted")

            # tipos_relacao_ato (canônicos do portal)
            for r in data.get("tipos_relacao_ato", {}).get("lista", []):
                cur.execute(
                    """
                    INSERT INTO taxonomia_tipo_relacao (codigo, nome, cor_portal_rgb, origem_canonica)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (codigo) DO UPDATE SET
                      nome = EXCLUDED.nome,
                      cor_portal_rgb = EXCLUDED.cor_portal_rgb,
                      origem_canonica = EXCLUDED.origem_canonica
                    """,
                    (r["codigo"], r["nome"], r.get("cor_portal_rgb"),
                     r.get("cor_portal_rgb") is not None),
                )
            logger.info(f"tipos_relacao_ato: {len(data.get('tipos_relacao_ato', {}).get('lista', []))} upserted")

            # status_vigencia (canônico do portal)
            for s in data.get("status_vigencia", {}).get("lista", []):
                cur.execute(
                    """
                    INSERT INTO taxonomia_status_vigencia (codigo, nome, cor_portal_rgb)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (codigo) DO UPDATE SET
                      nome = EXCLUDED.nome,
                      cor_portal_rgb = EXCLUDED.cor_portal_rgb
                    """,
                    (s["codigo"], s["nome"], s.get("cor_portal_rgb")),
                )
            logger.info(f"status_vigencia: {len(data.get('status_vigencia', {}).get('lista', []))} upserted")

            # temas_macro + temas_especificos
            n_macro, n_especifico = 0, 0
            for tema_macro in data["temas"]["lista"]:
                cur.execute(
                    """
                    INSERT INTO taxonomia_tema_macro (codigo, descricao)
                    VALUES (%s, %s)
                    ON CONFLICT (codigo) DO UPDATE SET descricao = EXCLUDED.descricao
                    """,
                    (tema_macro["tema_macro"], tema_macro.get("descricao")),
                )
                n_macro += 1

                for esp in tema_macro["temas_especificos"]:
                    schema_path = SCHEMAS_DIR / f"{esp['codigo']}.json"
                    schema_metadata = None
                    schema_path_str = None
                    if schema_path.exists():
                        schema_metadata = json.loads(schema_path.read_text(encoding="utf-8"))
                        schema_path_str = str(schema_path.relative_to(SCHEMAS_DIR.parent.parent))

                    cur.execute(
                        """
                        INSERT INTO taxonomia_tema_especifico (codigo, tema_macro_codigo, descricao, schema_metadata, schema_path)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (codigo) DO UPDATE SET
                          tema_macro_codigo = EXCLUDED.tema_macro_codigo,
                          descricao = EXCLUDED.descricao,
                          schema_metadata = EXCLUDED.schema_metadata,
                          schema_path = EXCLUDED.schema_path
                        """,
                        (
                            esp["codigo"], tema_macro["tema_macro"],
                            esp.get("descricao"),
                            json.dumps(schema_metadata) if schema_metadata else None,
                            schema_path_str,
                        ),
                    )
                    n_especifico += 1

            logger.info(f"temas: {n_macro} macro / {n_especifico} especificos upserted")

            conn.commit()
            logger.success("Seed completo.")


if __name__ == "__main__":
    main()
