"""
ETAPA 4 - Integracao com a nuvem (Supabase / PostgreSQL).

Este modulo e a unica parte do projeto que fala com a internet. O app.py
continua recebendo os eventos do ESP32 na rede local; quem leva esses eventos
para a nuvem e daqui.

Caminho completo dos dados:

    SW-520D -> ESP32 -> HTTP/JSON -> app.py -> SQLite local (fila)
                                            -> HTTPS/REST -> Supabase (Postgres)
                                                                  |
                                                            dashboard le daqui

Por que Supabase: ele expoe um PostgreSQL gerenciado atraves de uma API REST
(PostgREST). Isso significa que nao precisamos de driver de banco, porta
liberada no firewall nem servidor nosso na nuvem -- basta um POST HTTPS, que e
exatamente o que o projeto ja sabia fazer. A estrutura dos dados tambem nao
muda: continua sendo uma tabela relacional, igual a do SQLite.

Configuracao (servidor/.env, fora do Git):

    SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
    SUPABASE_KEY=<chave service_role>

A chave fica somente no servidor Python. Ela nunca vai para o navegador nem
para o firmware do ESP32 -- o dashboard consulta a nuvem atraves do proprio
app.py, que assina as requisicoes.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

# Nome da tabela e da view criadas por esquema_supabase.sql.
TABELA = "eventos"
VIEW_ESTATISTICAS = "estatisticas_24h"

# Tempo maximo de espera das chamadas HTTPS. Curto de proposito: se a nuvem
# demorar, o dashboard cai para o banco local em vez de travar a tela.
TIMEOUT_S = 8


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

def carregar_env(caminho=None):
    """Le servidor/.env e joga os valores em os.environ.

    Feito a mao para nao adicionar dependencia (python-dotenv) so por causa de
    duas linhas. Variaveis que ja existem no ambiente tem prioridade, o que
    permite sobrescrever tudo na linha de comando durante os testes.
    """
    arquivo = Path(caminho or Path(__file__).parent / ".env")

    if not arquivo.exists():
        return

    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()

        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")

        if chave and chave not in os.environ:
            os.environ[chave] = valor


def url_base():
    return (os.environ.get("SUPABASE_URL") or "").rstrip("/")


def chave():
    return os.environ.get("SUPABASE_KEY") or ""


def configurada():
    """True quando ha credenciais para falar com a nuvem.

    Sem isso o projeto continua funcionando 100% local -- util para
    desenvolver, e garante que uma apresentacao sem internet nao quebre.
    """
    return bool(url_base() and chave())


def cabecalhos(extra=None):
    cab = {
        # O Supabase pede a chave nos dois lugares: apikey identifica o
        # projeto, Authorization autentica a requisicao.
        "apikey": chave(),
        "Authorization": f"Bearer {chave()}",
        "Content-Type": "application/json",
    }
    if extra:
        cab.update(extra)
    return cab


def endpoint(recurso):
    return f"{url_base()}/rest/v1/{recurso}"


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------

def com_fuso(texto):
    """Transforma '2026-09-17T14:30:00' em '2026-09-17T14:30:00-03:00'.

    O SQLite guarda a hora local sem fuso. A coluna da nuvem e timestamptz, e
    sem o fuso o Postgres assumiria UTC -- os horarios apareceriam 3 horas
    adiantados no dashboard e na janela de 24h. Por isso anexamos aqui o fuso
    da maquina que recebeu o evento.
    """
    try:
        momento = datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return texto

    if momento.tzinfo is None:
        momento = momento.astimezone()

    return momento.isoformat(timespec="seconds")


def sem_fuso(texto):
    """Caminho inverso: a nuvem devolve com fuso, o resto do app espera sem.

    Converte para o horario local da maquina antes de cortar o fuso, para o
    dashboard mostrar a hora que a pessoa espera ver.
    """
    if not texto:
        return texto

    try:
        momento = datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return texto

    if momento.tzinfo is not None:
        momento = momento.astimezone().replace(tzinfo=None)

    return momento.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Escrita: e por aqui que o dado chega na nuvem
# ---------------------------------------------------------------------------

def enviar_eventos(eventos):
    """Grava uma ou mais linhas na tabela da nuvem.

    Recebe dicionarios no mesmo formato da tabela local, cada um com um
    'id_local' (o id da linha no SQLite). Esse campo e o que torna o reenvio
    seguro: a tabela da nuvem tem UNIQUE (dispositivo, id_local), e o cabecalho
    'resolution=merge-duplicates' faz o Postgres atualizar em vez de duplicar.
    Assim, se a resposta se perder no meio do caminho e o app tentar de novo, o
    evento nao entra duas vezes.

    Devolve a lista de linhas confirmadas pela nuvem. Levanta requests
    RequestException quando nao consegue falar com o servidor -- quem chama
    decide se guarda para reenviar depois.
    """
    if not eventos:
        return []

    if not configurada():
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY nao configurados")

    corpo = [
        {
            "id_local": evento["id_local"],
            "dispositivo": evento["dispositivo"],
            "tipo": evento["tipo"],
            "inclinado_frente": bool(evento["inclinado_frente"]),
            "inclinado_lateral": bool(evento["inclinado_lateral"]),
            "duracao_s": evento["duracao_s"],
            "total_alertas": evento["total_alertas"],
            "uptime_s": evento["uptime_s"],
            "recebido_em": com_fuso(evento["recebido_em"]),
        }
        for evento in eventos
    ]

    resposta = requests.post(
        endpoint(TABELA),
        json=corpo,
        headers=cabecalhos({
            "Prefer": "return=representation,resolution=merge-duplicates",
        }),
        timeout=TIMEOUT_S,
    )

    # 4xx aqui quase sempre significa esquema errado: a mensagem do Postgres
    # vem no corpo e e a informacao mais util para depurar.
    if not resposta.ok:
        raise requests.HTTPError(
            f"HTTP {resposta.status_code} ao gravar na nuvem: {resposta.text[:300]}",
            response=resposta,
        )

    return resposta.json()


# ---------------------------------------------------------------------------
# Leitura: e daqui que o dashboard se alimenta
# ---------------------------------------------------------------------------

def buscar(recurso, parametros=None, cabecalhos_extra=None):
    resposta = requests.get(
        endpoint(recurso),
        params=parametros,
        headers=cabecalhos(cabecalhos_extra),
        timeout=TIMEOUT_S,
    )

    if not resposta.ok:
        raise requests.HTTPError(
            f"HTTP {resposta.status_code} ao consultar a nuvem: {resposta.text[:300]}",
            response=resposta,
        )

    return resposta


def ultimo_evento():
    """Evento mais recente registrado na nuvem, ou None se a tabela esta vazia."""
    linhas = buscar(TABELA, {
        "select": "*",
        "order": "recebido_em.desc,id.desc",
        "limit": 1,
    }).json()

    return linhas[0] if linhas else None


def eventos_recentes(limite=20):
    """Ultimos eventos de postura, ignorando os heartbeats de status.

    O filtro 'tipo=neq.status' e sintaxe do PostgREST: vira um WHERE tipo <>
    'status' no Postgres. A consulta roda na nuvem, nao no PC.
    """
    return buscar(TABELA, {
        "select": "tipo,inclinado_frente,inclinado_lateral,duracao_s,recebido_em",
        "tipo": "neq.status",
        "order": "recebido_em.desc,id.desc",
        "limit": limite,
    }).json()


def estatisticas_24h():
    """Contagem de alertas e tempo acumulado nas ultimas 24h.

    A soma e feita pela view estatisticas_24h, dentro do Postgres. Poderiamos
    baixar as linhas e somar em Python, mas o ponto da entrega e justamente o
    processamento acontecer na nuvem.
    """
    linhas = buscar(VIEW_ESTATISTICAS, {"select": "*"}).json()

    if not linhas:
        return {"alertas": 0, "segundos": 0}

    return {
        "alertas": linhas[0].get("alertas_24h") or 0,
        "segundos": linhas[0].get("segundos_24h") or 0,
    }


def total_de_linhas():
    """Quantas linhas existem na tabela da nuvem.

    Usa o cabecalho Content-Range do PostgREST ("0-0/137"), que traz o total
    sem precisar baixar os registros.
    """
    resposta = buscar(
        TABELA,
        {"select": "id", "limit": 1},
        {"Prefer": "count=exact", "Range-Unit": "items"},
    )

    faixa = resposta.headers.get("Content-Range", "")
    _, _, total = faixa.partition("/")

    return int(total) if total.isdigit() else 0


def testar_conexao():
    """Chamada barata para conferir credenciais e esquema antes de subir o app.

    Devolve (ok, mensagem) em vez de levantar excecao: o app.py usa isso apenas
    para avisar na tela, nunca para impedir a inicializacao.
    """
    if not configurada():
        return False, "SUPABASE_URL/SUPABASE_KEY ausentes (servidor/.env)"

    try:
        total = total_de_linhas()
    except requests.RequestException as erro:
        return False, str(erro)

    return True, f"conectado, {total} eventos na tabela '{TABELA}'"
