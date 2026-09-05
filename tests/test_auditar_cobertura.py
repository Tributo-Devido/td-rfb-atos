"""Testes de auditar_cobertura.py e da migration 010, contra Postgres real.

Nao ha mock de banco aqui de proposito: o que este codigo faz de dificil e SQL --
`FILTER`, `EXISTS` correlacionado, `NOT VALID`, LEFT JOIN com condicao no ON. Um
mock validaria a montagem da string e deixaria passar exatamente o tipo de erro
que importa.

Precisa de um Postgres alcancavel. Ordem de resolucao do DSN:
    1. env PGTEST_DSN
    2. env RFB_ATOS_DSN  (use so se apontar para um banco descartavel!)
Sem nenhum dos dois, os testes sao pulados.

    initdb -D /tmp/pgdata -U postgres --auth=trust
    pg_ctl -D /tmp/pgdata -o '-p 15999 -k /tmp' start
    createdb -h /tmp -p 15999 -U postgres rfbtest
    PGTEST_DSN='postgresql://postgres@/rfbtest?host=/tmp&port=15999' pytest tests/
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "tests" / "fixtures"
MIGRATION_010 = RAIZ / "migrations" / "010_ato_coleta.sql"
AUDITAR = RAIZ / "scripts" / "auditar_cobertura.py"

DSN = os.environ.get("PGTEST_DSN") or os.environ.get("RFB_ATOS_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="defina PGTEST_DSN apontando para um Postgres descartavel"
)


# ----------------------------------------------------------------------
# Infra
# ----------------------------------------------------------------------
def _executar(conn, caminho: Path) -> None:
    conn.execute(caminho.read_text(encoding="utf-8"))


def _zerar(conn) -> None:
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


@pytest.fixture()
def base():
    """Schema + seed, sem a migration 010 (o estado de hoje na nuvem)."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        _zerar(conn)
        _executar(conn, FIXTURES / "schema_rfb_atos.sql")
        _executar(conn, FIXTURES / "seed_cenarios.sql")
        yield conn
        _zerar(conn)


@pytest.fixture()
def base_com_010(base):
    """Estado de hoje + migration 010 aplicada."""
    _executar(base, MIGRATION_010)
    return base


def rodar_auditoria(*args: str) -> str:
    saida = subprocess.run(
        [sys.executable, str(AUDITAR), *args],
        capture_output=True, text=True,
        env={**os.environ, "RFB_ATOS_DSN": DSN},
    )
    assert saida.returncode == 0, saida.stderr
    return saida.stdout


def celula(relatorio: str, rotulo: str, coluna: int = 1) -> str:
    """Le a n-esima celula da linha de tabela markdown que comeca com `rotulo`."""
    for linha in relatorio.splitlines():
        if linha.startswith(f"| {rotulo} |"):
            return [c.strip() for c in linha.strip("|").split("|")][coluna]
    raise AssertionError(f"linha '{rotulo}' ausente do relatorio:\n{relatorio}")


# ----------------------------------------------------------------------
# Migration 010
# ----------------------------------------------------------------------
def test_010_e_idempotente(base):
    _executar(base, MIGRATION_010)
    _executar(base, MIGRATION_010)  # nao pode explodir nem duplicar
    n = base.execute("SELECT count(*) FROM rfb_atos.ato_coleta").fetchone()[0]
    total = base.execute("SELECT count(*) FROM rfb_atos.ato").fetchone()[0]
    assert n == total


def test_010_backfill_nao_inventa_ausencia_de_pdf(base_com_010):
    """O ponto central da 010: `pdf_disponivel = false` NAO vira
    'sem_pdf_no_portal'. Transformar duvida em fato e o defeito que a migration
    existe para corrigir -- se este teste falhar, ela virou parte do problema."""
    n = base_com_010.execute(
        "SELECT count(*) FROM rfb_atos.ato_coleta "
        "WHERE pdf_status = 'sem_pdf_no_portal'"
    ).fetchone()[0]
    assert n == 0

    # Quem tem texto foi inferido como 'baixado'; o resto fica em 'nao_tentado'.
    baixados = base_com_010.execute(
        "SELECT count(*) FROM rfb_atos.ato_coleta WHERE pdf_status = 'baixado'"
    ).fetchone()[0]
    assert baixados == 7  # atos 1,2,3,5,16707,14575,12100 (4 e 6 tem texto vazio)


def test_010_sem_pdf_exige_tentativa(base_com_010):
    """'O portal nao tem' so pode ser afirmado depois de perguntar ao portal."""
    with pytest.raises(psycopg.errors.CheckViolation):
        base_com_010.execute(
            "UPDATE rfb_atos.ato_coleta "
            "SET pdf_status = 'sem_pdf_no_portal', tentativas = 0 WHERE ato_id = 7"
        )


def test_010_invariante_bloqueia_analise_sem_conteudo(base_com_010):
    """Escrita NOVA nao pode reintroduzir o bucket dos 912."""
    with pytest.raises(psycopg.errors.CheckViolation):
        base_com_010.execute(
            "UPDATE rfb_atos.ato "
            "SET analise_completa = true, content_disponivel = false WHERE id = 7"
        )


def test_010_invariante_nao_valida_o_legado(base_com_010):
    """NOT VALID: a constraint entra sem travar nas linhas legadas que ja violam
    (16630 e 20). Se entrasse validando, a migration nao rodaria em producao."""
    violando = base_com_010.execute(
        "SELECT count(*) FROM rfb_atos.ato "
        "WHERE analise_completa AND NOT content_disponivel"
    ).fetchone()[0]
    assert violando == 2
    convalidada = base_com_010.execute(
        "SELECT convalidated FROM pg_constraint "
        "WHERE conname = 'ato_analise_exige_conteudo'"
    ).fetchone()[0]
    assert convalidada is False


def test_010_view_classifica_estagio(base_com_010):
    estagios = dict(
        base_com_010.execute(
            "SELECT ato_id, estagio FROM rfb_atos.v_cobertura_ato"
        ).fetchall()
    )
    assert estagios[1] == "5_embeddado"        # texto + materia + vetor
    assert estagios[3] == "4_categorizado"     # uma materia sem vetor
    assert estagios[2] == "3_com_texto"        # texto, zero materia
    assert estagios[16630] == "0_pendente"     # IN 2.121: nada
    assert estagios[7] == "0_pendente"


# ----------------------------------------------------------------------
# Auditoria -- contagens
# ----------------------------------------------------------------------
def test_funil_conta_cada_etapa(base):
    r = rodar_auditoria("--amostra", "3")
    assert celula(r, "0. atos na base") == "12"
    assert celula(r, "2. texto extraido") == "7"
    assert celula(r, "3. materias categorizadas") == "5"
    assert celula(r, "4. embedding completo") == "4"


def test_texto_vazio_nao_conta_como_cobertura(base):
    """Os atos 4 (texto '   ') e 6 (texto NULL) tem linha em ato_content. Contar
    a linha em vez do texto e como a base passaria a mentir sobre si mesma."""
    r = rodar_auditoria()
    assert celula(r, "2. texto extraido") == "7"  # 9 linhas em ato_content, 7 com texto
    assert celula(r, "D2", 2) == "1"              # ato 6: flag true, texto NULL
    assert celula(r, "D4", 2) == "2"              # atos 4 e 6: PDF indicado, sem texto


def test_defeitos_batem_com_o_seed(base):
    r = rodar_auditoria()
    assert celula(r, "D1", 2) == "2"   # 16630 (IN 2.121) e 20
    assert celula(r, "D3", 2) == "1"   # ato 5
    assert celula(r, "D5", 2) == "2"   # atos 2 e 16707: texto sem materia
    assert celula(r, "D6", 2) == "1"   # ato 3: materia sem vetor
    assert celula(r, "D8", 2) == "2"   # nao_disponivel_portal sem texto


def test_in_2121_aparece_em_d1_e_d8(base):
    """O caso que motivou a revisao nao pode sumir do relatorio."""
    r = rodar_auditoria("--amostra", "50")
    d1 = r.split("### D1")[1].split("###")[0]
    d8 = r.split("### D8")[1].split("###")[0]
    assert "16630" in d1
    assert "16630" in d8


def test_in_2152_nao_e_defeito(base):
    """Controle negativo: 'tem texto, ainda nao analisado' e estado coerente. Se
    a IN 2.152 aparecer em D1, a auditoria esta confundindo pendencia com erro."""
    r = rodar_auditoria("--amostra", "50")
    d1 = r.split("### D1")[1].split("###")[0]
    assert "16707" not in d1


def test_d7_avisa_quando_nao_ha_como_separar(base):
    """Sem ato_coleta, D7 mistura 'nao tem PDF' com 'nunca tentamos' -- e o
    relatorio precisa dizer isso em vez de deixar o numero passar por medicao."""
    r = rodar_auditoria()
    assert "`rfb_atos.ato_coleta` nao existe" in r
    assert celula(r, "D7", 2) == "5"   # 4, 6, 7, 16630, 20


def test_d7_encolhe_quando_a_coleta_e_registrada(base_com_010):
    """Com a 010 e tentativas registradas, a duvida vira fato e sai de D7."""
    base_com_010.execute(
        "UPDATE rfb_atos.ato_coleta "
        "SET pdf_status = 'sem_pdf_no_portal', tentativas = 1, "
        "    ultima_tentativa = now() WHERE ato_id = 7"
    )
    base_com_010.execute(
        "UPDATE rfb_atos.ato_coleta "
        "SET pdf_status = 'erro_http', http_status = 404, tentativas = 3 "
        "WHERE ato_id = 16630"
    )
    r = rodar_auditoria()
    assert "`rfb_atos.ato_coleta` nao existe" not in r
    assert celula(r, "D7", 2) == "3"                       # sobram 4, 6 e 20
    assert celula(r, "1b. sem PDF no portal (fato apurado)") == "1"


# ----------------------------------------------------------------------
# Auditoria -- comportamento
# ----------------------------------------------------------------------
def test_auditoria_e_somente_leitura(base):
    """Nao e promessa no docstring: o servidor recusa a escrita."""
    sys.path.insert(0, str(RAIZ / "scripts"))
    import auditar_cobertura as ac

    with ac.conectar(DSN) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE rfb_atos.ato SET ementa = 'x' WHERE id = 1")


def test_filtro_por_tipo_reduz_o_universo(base):
    r = rodar_auditoria("--tipo", "INSTRUCAO_NORMATIVA")
    assert celula(r, "0. atos na base") == "2"


def test_out_grava_markdown_e_csv(base, tmp_path):
    rodar_auditoria("--out", str(tmp_path))
    assert (tmp_path / "REVISAO-COBERTURA.md").exists()
    assert (tmp_path / "funil.csv").exists()
    assert (tmp_path / "defeitos.csv").exists()


def test_degrada_sem_a_coluna_embedding(base):
    """A auditoria roda contra schema parcial em vez de estourar -- ela e a
    ferramenta de diagnostico, precisa funcionar justamente quando algo falta."""
    base.execute("ALTER TABLE rfb_atos.ato_materia DROP COLUMN embedding")
    r = rodar_auditoria()
    assert celula(r, "D6", 2) == "n/d"
    assert celula(r, "0. atos na base") == "12"
