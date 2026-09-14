"""sinal.py — prompt e leitura do sinal da matéria (AUTORIZA / VEDA / CONDICIONA / INDETERMINADO).

Tirado de `classificar_sinal_haiku.py` em 13/09/2026 para o `categorizar_nuvem.py` usar o mesmo
prompt sem importar o SDK da Anthropic (o CI instala só psycopg, pytest e ruff). Funções puras.
"""
from __future__ import annotations

MAX_FATO_CHARS = 2_500
MAX_SOLUCAO_CHARS = 4_000
VALIDAS = {"AUTORIZA", "VEDA", "CONDICIONA", "INDETERMINADO"}


SISTEMA_PROMPT = """Voce e um classificador especialista em direito tributario federal brasileiro.

Sua tarefa: dado UM trecho de materia (proveniente de Solucao de Consulta, Solucao de Divergencia, Instrucao Normativa, Decreto, ou outro ato normativo da Receita Federal), classifique a POSICAO JURIDICA da Receita sobre a pretensao do contribuinte (geralmente: tomar credito de PIS/COFINS, IRPJ, etc., ou aplicar regime/aliquota especifica).

Responda SOMENTE uma das quatro etiquetas (sem explicacao, sem aspas, sem pontuacao, sem nada antes ou depois):

AUTORIZA       — A Receita admite/permite/reconhece o direito do contribuinte (pode tomar o credito; pode aplicar a aliquota; e considerado insumo; nao incide imposto; pode descontar; gera direito).
VEDA           — A Receita rejeita/proibe/nao admite o direito (nao pode tomar o credito; nao se aplica; nao e insumo; e tributado; nao gera direito).
CONDICIONA     — A Receita admite o direito SOMENTE se cumpridos requisitos especificos (depende de comprovacao; somente se essencial e relevante; desde que efetivamente utilizado em X; admitido para alguns itens da lista mas vedado para outros).
INDETERMINADO  — Nao ha posicao tributaria clara: ato meramente procedimental, ou trata de aspecto formal sem decidir merito, ou solucao parcial inconclusiva, ou texto truncado/insuficiente.

Regras criticas (leia com atencao):

1) DECISAO FINAL DA RECEITA prevalece sobre fundamentacao intermediaria.
   Exemplo: "Por impossibilidade de montagem previa, a empresa pode tomar credito sobre X"
   -> AUTORIZA (a decisao final foi favoravel; "impossibilidade" e fundamento, nao decisao).

2) Quando ha LISTA com itens admitidos E itens vedados na mesma materia -> CONDICIONA.
   Exemplo: "EPI, vestimenta tecnica e treinamento sao insumos. Limpeza geral e refeitorio nao sao."
   -> CONDICIONA.

3) Quando a materia diz "para fins de X, considera-se Y" sem decidir admissao -> INDETERMINADO.
   Exemplo: "Para fins do art. 3o, considera-se insumo o que..." (definicao abstrata).

4) "Vedado tomar credito sobre A, salvo quando B" -> CONDICIONA (admite com requisito).

5) Reformulacao/cancelamento de SC anterior, sem nova decisao de merito -> INDETERMINADO.
   MAS se o novo ato decide merito diferente do anterior, classificar pelo novo merito.

6) Solucao que reconhece o direito mas exige procedimento (e.g. ressarcimento via PER/DCOMP, com homologacao posterior) -> AUTORIZA (o direito existe; procedimento e tramite).

7) Solucao que confirma incidencia tributaria (ex: "incide PIS/COFINS sobre receita X") em consulta sobre se aplica isencao/aliquota zero -> VEDA.

8) Aliquota zero, suspensao, isencao, nao-incidencia: tratam-se de DESONERACOES.
   - Se a Receita confirma que a desoneracao SE APLICA ao caso -> AUTORIZA.
   - Se a Receita afasta a desoneracao (logo, ha tributacao normal) -> VEDA.

Responda APENAS com UMA das quatro palavras: AUTORIZA, VEDA, CONDICIONA ou INDETERMINADO."""  # noqa: E501


def montar_user_content(m: dict) -> str:
    fato = (m.get("fato_consultado") or "").strip()
    solucao = (m.get("solucao") or "").strip()
    ementa = (m.get("ementa_trecho") or "").strip()
    tema = m.get("tema_especifico") or ""
    natureza = m.get("natureza") or ""

    if len(fato) > MAX_FATO_CHARS:
        fato = fato[:MAX_FATO_CHARS] + " [...truncado]"
    if len(solucao) > MAX_SOLUCAO_CHARS:
        solucao = solucao[:MAX_SOLUCAO_CHARS] + " [...truncado]"

    partes = [
        "<materia>",
        f"<tema>{tema}</tema>",
        f"<natureza>{natureza}</natureza>",
    ]
    if ementa:
        partes.append(f"<ementa_trecho>{ementa}</ementa_trecho>")
    if fato:
        partes.append(f"<fato_consultado>{fato}</fato_consultado>")
    if solucao:
        partes.append(f"<solucao>{solucao}</solucao>")
    partes.append("</materia>")
    partes.append("\nClassifique:")
    return "\n".join(partes)


def parse_sinal(text: str) -> str | None:
    """Extrai o sinal limpo do output do LLM. Tolerante a variacoes minimas."""
    if not text:
        return None
    t = text.strip().upper().strip(".:;-`'\"\n\r\t ")
    # pega so a primeira "palavra" (eliminando dois-pontos, parenteses, etc.)
    primeira = t.split()[0] if t.split() else ""
    primeira = primeira.strip(".:;-`'\"")
    if primeira in VALIDAS:
        return primeira
    # fallback: substring
    for v in VALIDAS:
        if v in t:
            return v
    return None
