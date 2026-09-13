"""
normalize.py — Normalizacao canonica de valores categorizados pelo LLM.

Garante que tributo_codigo, tema_macro, tema_especifico, tipo_norma, tipo_uso,
regime, tipo_relacao etc. nunca tenham variantes (PIS/PASEP, pis-pasep, piscofins).

Uso:
    from normalize import normalizar_tributo, normalizar_tema_macro, ...
    code = normalizar_tributo("piscofins")  # -> ("PIS", "rejeitado_via_split")
                                            # ou levanta ValueError se invalido

Aplicacao:
    1. Em categorize_batch.aplicar_resultado_no_banco() — antes do INSERT
    2. Em backfill periodico — sweeps de toda a base
    3. Em hooks PreToolUse (futuro) — bloqueia escrita de SQL com valor invalido
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Optional

REFS = Path(__file__).resolve().parent.parent / "references"
NORMALIZACAO_PATH = REFS / "normalizacao_taxonomia.json"

_DICT = json.loads(NORMALIZACAO_PATH.read_text(encoding="utf-8"))

# Indices invertidos pre-construidos: alias_lowered -> canonico
_idx_tributo: dict[str, str] = {}
_idx_tema_macro: dict[str, str] = {}
_idx_regime: dict[str, str] = {}
_idx_tipo_norma: dict[str, str] = {}
_idx_tipo_uso: dict[str, str] = {}
_idx_tipo_relacao: dict[str, str] = {}
_idx_natureza: dict[str, str] = {}
_idx_resultado: dict[str, str] = {}

_realocar_tributo_para_tema = {}
_rejeitar_tributo: set[str] = set()
_rejeitar_tema_macro: set[str] = set()


def _norm(s: str | None) -> str:
    """Lowercase, strip, remove acentos, troca espacos/hifen por underline."""
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    # Remove acentos
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    return s.lower().replace(" ", "_").replace("-", "_").replace("/", "_")


def _build_index(section: str, target: dict[str, str]):
    sect = _DICT.get(section) or {}
    for canonico in sect.get("_canonicos", []):
        target[_norm(canonico)] = canonico
    for canonico, aliases in (sect.get("_aliases") or {}).items():
        for a in aliases:
            target[_norm(a)] = canonico


_build_index("tributos", _idx_tributo)
_build_index("temas_macro", _idx_tema_macro)
_build_index("regimes_tributarios", _idx_regime)
_build_index("tipo_norma", _idx_tipo_norma)
_build_index("tipo_uso", _idx_tipo_uso)
_build_index("tipo_relacao", _idx_tipo_relacao)
_build_index("natureza_materia", _idx_natureza)
_build_index("resultado", _idx_resultado)

_realocar_tributo_para_tema = {
    _norm(k): v for k, v in (_DICT.get("temas_macro", {}).get("_realocar_de_tributo_para_tema") or {}).items()
    if not k.startswith("_")
}
_rejeitar_tributo = {_norm(v) for v in (_DICT.get("tributos", {}).get("_rejeitar_valores") or [])}
_rejeitar_tema_macro = {_norm(v) for v in (_DICT.get("temas_macro", {}).get("_rejeitar_valores") or [])}


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def normalizar_tributo(valor: str | None) -> Optional[str]:
    """
    Retorna canonico ou None se nao foi possivel normalizar.
    Casos:
      - valor canonico ou alias conhecido -> retorna canonico
      - valor em _rejeitar_valores -> None (caller deve realocar pra tema)
      - valor desconhecido -> None (registra em _NAO_MAPEADOS log)
    """
    n = _norm(valor)
    if not n:
        return None
    if n in _rejeitar_tributo:
        return None
    return _idx_tributo.get(n)


def normalizar_tema_macro(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    if not n:
        return None
    if n in _rejeitar_tema_macro:
        return None
    if n in _idx_tema_macro:
        return _idx_tema_macro[n]
    # Pode ser um TRIBUTO mascarado de tema (LLM confundiu campo)
    if n in _realocar_tributo_para_tema:
        return _realocar_tributo_para_tema[n]
    return None


def normalizar_tema_especifico(valor: str | None, tema_macro: str | None = None) -> Optional[str]:
    """
    tema_especifico segue o padrao TEMA_MACRO.SUFIXO.
    Se valor nao tem '.', tenta inferir prefixando com tema_macro normalizado.
    Se nao bate em padrao, retorna None.
    """
    if not valor:
        return None
    s = str(valor).strip()
    if not s:
        return None
    # Limpa caracteres estranhos
    s = s.replace(" ", "_").replace("-", "_").upper()
    # Se nao tem ponto, tenta prefixar com tema_macro
    if "." not in s and tema_macro:
        s = f"{tema_macro}.{s}"
    # Se ainda nao tem ponto, deixa como esta (vai marcar __NOVO no insert)
    return s if "." in s else None


def normalizar_regime(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    return _idx_regime.get(n) if n else None


def normalizar_tipo_norma(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    return _idx_tipo_norma.get(n) if n else None


def normalizar_tipo_uso(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    return _idx_tipo_uso.get(n) if n else None


def normalizar_tipo_relacao(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    return _idx_tipo_relacao.get(n) if n else None


def normalizar_natureza(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    return _idx_natureza.get(n) if n else None


def normalizar_resultado(valor: str | None) -> Optional[str]:
    n = _norm(valor)
    return _idx_resultado.get(n) if n else None


# ---------------------------------------------------------------------------
# Decompositor de tributo composto: PIS/COFINS, IRPJ-CSLL, etc.
# ---------------------------------------------------------------------------

def decompor_tributo_composto(valor: str | None) -> list[str]:
    """
    LLM as vezes retorna 'PIS/COFINS' como UM tributo. Decompoe em multiplos.
    Retorna lista de canonicos (vazia se nada bate).
    """
    if not valor:
        return []
    s = str(valor).strip()
    # Separadores comuns
    for sep in ["/", " e ", " E ", " AND ", " and ", "&", " + ", "+", " - ", "_E_"]:
        if sep in s:
            parts = [p.strip() for p in s.split(sep) if p.strip()]
            out = []
            for p in parts:
                t = normalizar_tributo(p)
                if t and t not in out:
                    out.append(t)
            if out:
                return out
    # Sem separador: tenta normalizar simples
    t = normalizar_tributo(s)
    return [t] if t else []


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test():
    cases = [
        # (input, fn, expected)
        ("PIS", normalizar_tributo, "PIS"),
        ("piscofins", normalizar_tributo, "PIS"),  # match na tabela
        ("Pis-Pasep", normalizar_tributo, "PIS"),
        ("ISSQN", normalizar_tributo, "ISS"),
        ("FINSOCIAL", normalizar_tributo, "COFINS"),
        ("ICM", normalizar_tributo, "ICMS"),
        ("CSLL", normalizar_tributo, "CSLL"),
        ("CSL", normalizar_tributo, "CSLL"),
        ("EMPRÉSTIMO_COMPULSÓRIO", normalizar_tributo, "EMPRESTIMO_COMPULSORIO"),
        ("INSS", normalizar_tributo, "CONTRIB_PREV"),
        ("CONTAG", normalizar_tributo, "CONTRIB_TERCEIROS"),
        ("MULTA_OFICIO", normalizar_tributo, "MULTA_ISOLADA"),
        ("IN", normalizar_tributo, None),  # rejeitado
        ("6912", normalizar_tributo, None),  # rejeitado
        ("PROCESS_ADMINISTRATIVO", normalizar_tema_macro, "PROCESSO_ADMINISTRATIVO"),
        ("CREDITAMENTE", normalizar_tema_macro, "CREDITAMENTO"),
        ("LANCAMENTOS", normalizar_tema_macro, "LANCAMENTO"),
        ("PIS_COFINS", normalizar_tema_macro, "ALIQUOTA_E_BASE_CALCULO"),  # realocado
        ("CSLL", normalizar_tema_macro, "IRPJ_CSLL"),  # tributo mascarado
        ("nao_cumulativo", normalizar_regime, "nao_cumulativo"),
        ("não-cumulativo", normalizar_regime, "nao_cumulativo"),
        ("Lei Complementar", normalizar_tipo_norma, "lei_complementar"),
        ("LC", normalizar_tipo_norma, "lei_complementar"),
        ("ALT", normalizar_tipo_relacao, "altera"),
        ("REV", normalizar_tipo_relacao, "interrompe"),
    ]
    fails = 0
    for inp, fn, expected in cases:
        got = fn(inp)
        ok = got == expected
        flag = "OK" if ok else "FAIL"
        print(f"  [{flag}] {fn.__name__}({inp!r}) = {got!r}  (esperado: {expected!r})")
        if not ok:
            fails += 1
    print(f"\n{len(cases)-fails}/{len(cases)} testes OK")
    # decompor
    print(f"\ndecompor_tributo_composto('PIS/COFINS') = {decompor_tributo_composto('PIS/COFINS')}")
    print(f"decompor_tributo_composto('IRPJ E CSLL') = {decompor_tributo_composto('IRPJ E CSLL')}")
    print(f"decompor_tributo_composto('IRPJ - CSLL') = {decompor_tributo_composto('IRPJ - CSLL')}")
    return fails == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)
