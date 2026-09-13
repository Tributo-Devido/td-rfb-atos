"""Regra de exibição do portal (offline): mostrar = `omitir` falso, uma versão por dispositivo."""
from __future__ import annotations

from datetime import date

import visoes_portal as vp


def seg(id_seg, versao, ordem, texto, **marcas):
    base = {"idSegmento": id_seg, "versaoSegmento": versao, "ordemSegmentoAto": ordem,
            "textoIntegra": texto, "original": False, "compilado": False, "omitir": False,
            "tachado": False, "agendado": False, "arquivoBinario": None}
    return {**base, **marcas}


def visao(ementas, outros, **extra):
    return {"idAto": 555, "vigente": True, "visao": "vigente", "ementas": ementas,
            "outrosSegmentos": outros, "historico": [], **extra}


VIGENTE = visao(
    [seg(1, 1, 1, "Dispõe&nbsp;sobre   X", original=True, compilado=True)],
    [seg(10, 1, 3, "Art. 1 Texto antigo", original=True, omitir=True),
     seg(10, 2, 3, "Art. 1 Texto novo", compilado=True),
     seg(11, 1, 4, "Art. 2 Igual", original=True, compilado=True),
     seg(12, 1, 5, "Capítulo II")],   # exibido sem ser `compilado` (como os 106 da IN 2.121)
    historico=[{"idAto": 777, "idSegmento": 10, "idAnotacao": 5, "texto": "[Alterado]",
                "dataInicioVigencia": "30/12/2022", "ehAgendamento": False}],
)
ORIGINAL = visao(
    [seg(1, 1, 1, "Dispõe sobre X", original=True, compilado=True)],
    [seg(10, 1, 3, "Art. 1 Texto antigo", original=True),
     seg(10, 2, 3, "Art. 1 Texto novo", compilado=True, omitir=True),
     seg(11, 1, 4, "Art. 2 Igual", original=True, compilado=True)],
)


def test_texto_vigente_traz_so_a_redacao_atual_na_ordem():
    assert vp.texto_da_visao(VIGENTE) == (
        "## EMENTA\nDispõe sobre X\n\nArt. 1 Texto novo\n\nArt. 2 Igual\n\nCapítulo II")


def test_texto_original_traz_a_redacao_publicada():
    assert "Art. 1 Texto antigo" in vp.texto_da_visao(ORIGINAL)
    assert "Texto novo" not in vp.texto_da_visao(ORIGINAL)


def test_uma_versao_por_dispositivo_exibido():
    ids = [s["idSegmento"] for s in vp.segmentos_exibidos(VIGENTE)]
    assert len(ids) == len(set(ids))


def test_segmento_exibido_sem_compilado_nao_se_perde():
    assert "Capítulo II" in vp.texto_da_visao(VIGENTE)


def test_linhas_segmento_guardam_todas_as_versoes_com_marcas():
    linhas = vp.linhas_segmento(VIGENTE)
    assert len(linhas) == 5
    antiga = next(x for x in linhas if x["id_segmento"] == 10 and x["versao_segmento"] == 1)
    assert antiga["is_omitido"] and antiga["is_original"] and not antiga["is_compilado"]


def test_status_e_situacao_so_com_o_vocabulario_do_portal():
    assert vp.status_vigencia(VIGENTE) == "vigente_alterado"
    assert vp.status_vigencia({**ORIGINAL, "outrosSegmentos": [seg(11, 1, 4, "x")]}) == (
        "vigente_nunca_alterado")
    assert vp.status_vigencia({**VIGENTE, "vigente": False}) == "nao_vigente"
    assert vp.situacao(VIGENTE)["idAto"] == 555


def test_historico_e_datas():
    (h,) = vp.linhas_historico(VIGENTE)
    assert h["id_ato_modificador_portal"] == 777
    assert h["data_inicio_vigencia"] == date(2022, 12, 30)
    assert vp.data_dmy("31/01/2023 00:00:00") == date(2023, 1, 31)
    assert vp.data_dmy("lixo") is None
    assert vp.data_iso("2022-12-20") == date(2022, 12, 20)


def test_normalizar_e_corpo_em_pdf():
    assert vp.normalizar("a&nbsp;&nbsp;b\xa0c") == "a b c"
    com_pdf = visao([], [seg(20, 1, 3, "", arquivoBinario={"idArquivoBinario": 9})])
    assert vp.corpo_em_pdf(com_pdf)
    assert not vp.corpo_em_pdf(VIGENTE)


def test_sha256_estavel():
    assert vp.sha256("abc") == vp.sha256("abc") != vp.sha256("abd")
