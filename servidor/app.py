"""
ETAPA 3 + ETAPA 4 + ETAPA 5 - Gateway Python do monitor de postura.

Recebe os eventos que o ESP32 envia por HTTP na rede local, guarda em um banco
SQLite, replica cada evento para a nuvem (Supabase / PostgreSQL) e avisa o
celular quando a postura fica ruim. O dashboard le da nuvem.

    SW-520D -> ESP32 -> HTTP/JSON -> [ ESTE ARQUIVO ] -> SQLite local (fila)
                                                      -> HTTPS/REST -> Supabase
                                                      -> HTTPS/REST -> Telegram
                                                                          |
                                                                    dashboard

O SQLite nao e mais o destino dos dados: e uma fila. Todo evento entra nele com
enviado_nuvem = 0 e uma thread em segundo plano vai empurrando para a nuvem.
Se a internet cair, o ESP32 continua alertando, o Python continua registrando,
e os eventos pendentes sobem quando a conexao voltar.

Como rodar:
    pip install -r requirements.txt
    cp .env.exemplo .env      # e preencha com as credenciais do Supabase
    python app.py

Depois abra http://localhost:5000 no navegador.

O ESP32 precisa apontar para o IP deste computador no arquivo config.h.
Descubra o IP com "ip a" (Linux) ou "ipconfig" (Windows).
"""

import os
import queue
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, request

import notifica
import nuvem

app = Flask(__name__)

# Por padrao o banco fica ao lado do app.py. A variavel de ambiente POSTURA_DB
# permite apontar para outro arquivo (usada pelo script de evidencias, para nao
# misturar os dados de demonstracao com os dados reais do grupo).
BANCO = Path(os.environ.get("POSTURA_DB") or Path(__file__).parent / "postura.db")

# De quanto em quanto tempo a thread de sincronizacao tenta esvaziar a fila.
# Ela tambem e acordada na hora por cada evento novo, entao este valor so vale
# para as retentativas depois de uma falha.
INTERVALO_SINCRONIA_S = float(os.environ.get("INTERVALO_SINCRONIA_S", 5))

# Quantos eventos pendentes vao por requisicao. Em lote para nao fazer uma
# chamada HTTPS por evento quando a fila acumula depois de uma queda de rede.
LOTE_SINCRONIA = 50

# O dashboard consulta /api/status a cada 2s. Sem este cache, cada aba aberta
# viraria varias chamadas por segundo para a nuvem sem necessidade.
CACHE_STATUS_S = 2.0

# Liga quando a thread de leitura da nuvem sobe (so no servidor de verdade).
# Sem ela -- nos testes, por exemplo -- o /api/status le a nuvem na hora.
leitor_nuvem = {"ativo": False}

# Intervalo minimo entre dois avisos no celular. O firmware manda um alerta por
# episodio de postura ruim, mas quem esta com a cinta pode entortar e endireitar
# varias vezes em poucos minutos -- sem esse respiro o celular viraria uma
# metralhadora e a pessoa desligaria a notificacao no primeiro dia de uso.
#
# 60s e o meio termo escolhido pelo grupo: espacado o bastante para nao virar
# incomodo, curto o bastante para a pessoa nao passar meia hora curvada sem ser
# lembrada de novo.
INTERVALO_NOTIFICACAO_PADRAO_S = 60


def intervalo_notificacao_s():
    """Lido a cada uso, e nao uma vez na importacao.

    O servidor/.env so e carregado no fim deste arquivo, depois que o modulo ja
    foi importado -- uma constante daria o valor padrao para sempre e o ajuste
    feito no .env nao teria efeito nenhum.
    """
    return float(os.environ.get("INTERVALO_NOTIFICACAO_S") or INTERVALO_NOTIFICACAO_PADRAO_S)


# ---------------------------------------------------------------------------
# Banco de dados local (fila de envio)
# ---------------------------------------------------------------------------

def conectar():
    conexao = sqlite3.connect(BANCO, timeout=10)
    conexao.row_factory = sqlite3.Row
    return conexao


def criar_tabelas():
    with closing(conectar()) as conexao, conexao:
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS eventos (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                dispositivo       TEXT    NOT NULL,
                tipo              TEXT    NOT NULL,
                inclinado_frente  INTEGER NOT NULL DEFAULT 0,
                inclinado_lateral INTEGER NOT NULL DEFAULT 0,
                duracao_s         INTEGER NOT NULL DEFAULT 0,
                total_alertas     INTEGER NOT NULL DEFAULT 0,
                uptime_s          INTEGER NOT NULL DEFAULT 0,
                recebido_em       TEXT    NOT NULL,
                enviado_nuvem     INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_recebido ON eventos(recebido_em)"
        )

        # Bancos criados antes da Etapa 4 nao tem a coluna enviado_nuvem.
        # Em vez de pedir para apagar o postura.db (o que jogaria fora os dados
        # das entregas anteriores), acrescentamos a coluna.
        colunas = {
            linha["name"]
            for linha in conexao.execute("PRAGMA table_info(eventos)")
        }
        if "enviado_nuvem" not in colunas:
            print("[banco] migrando: adicionando coluna enviado_nuvem")
            conexao.execute(
                "ALTER TABLE eventos ADD COLUMN enviado_nuvem INTEGER NOT NULL DEFAULT 0"
            )

        # Indice parcial: so indexa o que ainda falta enviar. A fila normalmente
        # tem zero linhas, entao esta consulta fica praticamente de graca.
        conexao.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pendentes
            ON eventos(id) WHERE enviado_nuvem = 0
            """
        )


def contar_pendentes():
    with closing(conectar()) as conexao:
        return conexao.execute(
            "SELECT COUNT(*) FROM eventos WHERE enviado_nuvem = 0"
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Sincronizacao com a nuvem
# ---------------------------------------------------------------------------

# Acorda a thread de sincronizacao assim que um evento novo chega, para o dado
# subir na hora em vez de esperar o proximo ciclo.
tem_novidade = threading.Event()

# Ultimo resultado da sincronizacao, exposto em /api/nuvem e no dashboard.
estado_nuvem = {
    "configurada": False,
    "ok": None,          # None = ainda nao tentou
    "ultimo_envio": None,
    "ultimo_erro": None,
    "enviados_na_sessao": 0,
}


def sincronizar_uma_vez():
    """Envia para a nuvem os eventos que ainda nao subiram.

    Retorna quantos eventos foram confirmados pela nuvem nesta passada.
    """
    if not nuvem.configurada():
        return 0

    with closing(conectar()) as conexao:
        pendentes = conexao.execute(
            """
            SELECT id, dispositivo, tipo, inclinado_frente, inclinado_lateral,
                   duracao_s, total_alertas, uptime_s, recebido_em
            FROM eventos
            WHERE enviado_nuvem = 0
            ORDER BY id
            LIMIT ?
            """,
            (LOTE_SINCRONIA,),
        ).fetchall()

    if not pendentes:
        return 0

    lote = [dict(linha, id_local=linha["id"]) for linha in pendentes]

    try:
        nuvem.enviar_eventos(lote)
    except (requests.RequestException, RuntimeError) as erro:
        # Nao marcamos nada como enviado: as linhas continuam na fila e a
        # proxima passada tenta de novo. O UNIQUE (dispositivo, id_local) da
        # tabela na nuvem garante que uma eventual gravacao que deu certo mas
        # cuja resposta se perdeu nao vire linha duplicada.
        estado_nuvem["ok"] = False
        estado_nuvem["ultimo_erro"] = str(erro)
        print(f"[nuvem] falha ao enviar {len(lote)} evento(s): {erro}")
        return 0

    ids = [linha["id"] for linha in pendentes]
    with closing(conectar()) as conexao, conexao:
        conexao.executemany(
            "UPDATE eventos SET enviado_nuvem = 1 WHERE id = ?",
            [(i,) for i in ids],
        )

    estado_nuvem["ok"] = True
    estado_nuvem["ultimo_erro"] = None
    estado_nuvem["ultimo_envio"] = datetime.now().isoformat(timespec="seconds")
    estado_nuvem["enviados_na_sessao"] += len(ids)

    print(f"[nuvem] {len(ids)} evento(s) gravados no Supabase (ids locais {ids[0]}..{ids[-1]})")
    return len(ids)


def laco_sincronizacao():
    """Roda em segundo plano durante toda a vida do servidor.

    Fica em thread separada para que a resposta ao ESP32 nao dependa da
    internet: o firmware recebe o 201 assim que o evento entra no SQLite,
    e o salto para a nuvem acontece depois.
    """
    while True:
        # Acorda por evento novo ou pelo timeout, o que vier primeiro.
        tem_novidade.wait(timeout=INTERVALO_SINCRONIA_S)
        tem_novidade.clear()

        try:
            # Enquanto houver fila, continua mandando em lotes.
            while sincronizar_uma_vez() == LOTE_SINCRONIA:
                pass
        except Exception as erro:              # noqa: BLE001
            # A thread nunca pode morrer: se ela cair, o projeto para de
            # enviar para a nuvem em silencio.
            estado_nuvem["ok"] = False
            estado_nuvem["ultimo_erro"] = f"erro inesperado: {erro}"
            print(f"[nuvem] erro inesperado na sincronizacao: {erro}")
            time.sleep(INTERVALO_SINCRONIA_S)


def iniciar_sincronizacao():
    estado_nuvem["configurada"] = nuvem.configurada()

    if not nuvem.configurada():
        print("[nuvem] SUPABASE_URL/SUPABASE_KEY nao configurados em servidor/.env")
        print("[nuvem] o sistema roda somente local; o dashboard usa o SQLite")
        return

    thread = threading.Thread(target=laco_sincronizacao, daemon=True)
    thread.start()
    print("[nuvem] sincronizacao em segundo plano iniciada")


# ---------------------------------------------------------------------------
# Notificacao no celular (Etapa 5)
# ---------------------------------------------------------------------------

# Fila curta de proposito: se o Telegram estiver fora do ar, o que importa e o
# alerta mais recente, nao uma pilha de avisos atrasados chegando todos juntos
# quando a conexao voltar. Diferente dos eventos, que nunca podem se perder
# (por isso a fila deles e o SQLite), um aviso atrasado nao serve para nada.
fila_notificacoes = queue.Queue(maxsize=10)

# Ultimo resultado do envio, exposto em /api/notificacao e no dashboard.
estado_notificacao = {
    "ligada": False,
    "ok": None,              # None = ainda nao tentou
    "ultimo_envio": None,
    "ultimo_erro": None,
    "enviadas_na_sessao": 0,
    "ignoradas_por_intervalo": 0,
}

_ultimo_aviso = 0.0
_trava_aviso = threading.Lock()


def pode_avisar():
    """True quando ja passou o intervalo minimo desde o ultimo aviso.

    Marca o horario na mesma travada em que consulta, para dois eventos que
    chegarem juntos nao passarem os dois pela verificacao.
    """
    global _ultimo_aviso

    with _trava_aviso:
        agora = time.monotonic()

        if _ultimo_aviso and agora - _ultimo_aviso < intervalo_notificacao_s():
            return False

        _ultimo_aviso = agora
        return True


def notificar(evento):
    """Enfileira o aviso para o celular. Nunca bloqueia nem levanta excecao.

    Chamada de dentro do POST /api/eventos, com o ESP32 esperando a resposta:
    aqui so entra o que e instantaneo. O POST para o Telegram acontece na
    thread de baixo, pelo mesmo motivo que o envio para a nuvem: o firmware
    tem timeout curto e nao pode ficar preso esperando a internet.
    """
    if not estado_notificacao["ligada"]:
        return False

    if not pode_avisar():
        estado_notificacao["ignoradas_por_intervalo"] += 1
        print("  >> aviso no celular pulado (intervalo minimo entre mensagens)")
        return False

    try:
        fila_notificacoes.put_nowait(evento)
    except queue.Full:
        print("  >> aviso no celular descartado (fila cheia)")
        return False

    return True


def laco_notificacoes():
    """Roda em segundo plano durante toda a vida do servidor."""
    while True:
        evento = fila_notificacoes.get()

        try:
            notifica.notificar_alerta(evento)
        except Exception as erro:              # noqa: BLE001
            # A thread nunca pode morrer: se ela cair, o projeto para de avisar
            # o celular em silencio. Um aviso perdido nao e reenviado -- ele ja
            # estaria atrasado, e o evento em si esta salvo no banco e na nuvem.
            estado_notificacao["ok"] = False
            estado_notificacao["ultimo_erro"] = str(erro)
            print(f"[celular] falha ao avisar: {erro}")
            continue

        estado_notificacao["ok"] = True
        estado_notificacao["ultimo_erro"] = None
        estado_notificacao["ultimo_envio"] = datetime.now().isoformat(timespec="seconds")
        estado_notificacao["enviadas_na_sessao"] += 1

        print(f"[celular] aviso enviado pelo Telegram ({evento.get('duracao_s')}s torto)")


def iniciar_notificacoes():
    estado_notificacao["ligada"] = notifica.configurada()

    if not notifica.configurada():
        print("[celular] TELEGRAM_TOKEN/TELEGRAM_CHAT_ID nao configurados em servidor/.env")
        print("[celular] o sistema roda igual, so nao avisa o celular")
        return

    thread = threading.Thread(target=laco_notificacoes, daemon=True)
    thread.start()
    print("[celular] avisos pelo Telegram ligados "
          f"(no maximo um a cada {intervalo_notificacao_s():.0f}s)")


# ---------------------------------------------------------------------------
# API que o ESP32 chama
# ---------------------------------------------------------------------------

@app.post("/api/eventos")
def receber_evento():
    dados = request.get_json(silent=True)

    if not dados:
        return jsonify({"erro": "corpo da requisicao nao e JSON valido"}), 400

    evento = (
        dados.get("dispositivo", "desconhecido"),
        dados.get("tipo", "desconhecido"),
        int(bool(dados.get("inclinado_frente", False))),
        int(bool(dados.get("inclinado_lateral", False))),
        int(dados.get("duracao_s", 0)),
        int(dados.get("total_alertas", 0)),
        int(dados.get("uptime_s", 0)),
        datetime.now().isoformat(timespec="seconds"),
    )

    with closing(conectar()) as conexao, conexao:
        cursor = conexao.execute(
            """
            INSERT INTO eventos (dispositivo, tipo, inclinado_frente,
                                 inclinado_lateral, duracao_s, total_alertas,
                                 uptime_s, recebido_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            evento,
        )
        id_local = cursor.lastrowid

    print(f"[{evento[7]}] {evento[0]} -> {evento[1]} (duracao={evento[4]}s) id={id_local}")

    # Alerta e o unico tipo que vira aviso no celular: "postura_corrigida" e
    # boa noticia e "status" e o heartbeat de 30s do firmware -- notificar os
    # dois so ensinaria a pessoa a ignorar as mensagens.
    if dados.get("tipo") == "alerta_postura":
        print("  >> ALERTA: usuario com postura inadequada")
        notificar({
            "dispositivo": evento[0],
            "inclinado_frente": bool(evento[2]),
            "inclinado_lateral": bool(evento[3]),
            "duracao_s": evento[4],
            "total_alertas": evento[5],
            "recebido_em": evento[7],
        })

    # Avisa a thread de sincronizacao. Responde ao ESP32 sem esperar a nuvem:
    # o firmware tem timeout curto e nao pode ficar preso esperando a internet.
    tem_novidade.set()

    return jsonify({"ok": True, "id_local": id_local, "fila_nuvem": True}), 201


# ---------------------------------------------------------------------------
# Estado consolidado para o dashboard
# ---------------------------------------------------------------------------

_cache = {"quando": 0.0, "payload": None}
_trava_cache = threading.Lock()


def vazio(origem, detalhe=None):
    return {
        "origem": origem,
        "nuvem_detalhe": detalhe,
        "conectado": False,
        "postura_ok": None,
        "alertas_24h": 0,
        "tempo_ruim_24h_min": 0,
        "eventos_na_nuvem": 0,
        "pendentes_envio": contar_pendentes(),
        "eventos": [],
    }


def montar_resposta(origem, ultimo, alertas, recentes, total_nuvem, detalhe=None):
    # Consideramos o dispositivo online se deu sinal nos ultimos 90 segundos
    # (o heartbeat do firmware e de 30s).
    visto_em = datetime.fromisoformat(ultimo["recebido_em"])
    conectado = (datetime.now() - visto_em).total_seconds() < 90

    torto = bool(ultimo["inclinado_frente"] or ultimo["inclinado_lateral"])

    return {
        "origem": origem,
        "nuvem_detalhe": detalhe,
        "conectado": conectado,
        "dispositivo": ultimo["dispositivo"],
        "postura_ok": not torto,
        "inclinado_frente": bool(ultimo["inclinado_frente"]),
        "inclinado_lateral": bool(ultimo["inclinado_lateral"]),
        "visto_em": ultimo["recebido_em"],
        "alertas_24h": alertas["total"],
        "tempo_ruim_24h_min": round(alertas["segundos"] / 60, 1),
        "eventos_na_nuvem": total_nuvem,
        "pendentes_envio": contar_pendentes(),
        "eventos": recentes,
    }


def status_da_nuvem():
    """Monta o estado do dashboard com dados lidos do Supabase.

    Todas as consultas rodam no Postgres da nuvem: a soma das ultimas 24h vem
    da view estatisticas_24h, nao de um SUM feito aqui.
    """
    total = nuvem.total_de_linhas()
    ultimo = nuvem.ultimo_evento()

    if ultimo is None:
        return vazio("nuvem", "tabela na nuvem ainda vazia")

    estatisticas = nuvem.estatisticas_24h()

    recentes = [
        dict(linha, recebido_em=nuvem.sem_fuso(linha["recebido_em"]))
        for linha in nuvem.eventos_recentes()
    ]

    return montar_resposta(
        "nuvem",
        dict(ultimo, recebido_em=nuvem.sem_fuso(ultimo["recebido_em"])),
        {"total": estatisticas["alertas"], "segundos": estatisticas["segundos"]},
        recentes,
        total,
    )


def status_local(detalhe=None):
    """Mesmo estado, porem lido do SQLite.

    Usado quando a nuvem nao esta configurada ou nao responde. O dashboard
    mostra de onde veio o dado, para a diferenca ficar visivel na tela.
    """
    limite = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")

    with closing(conectar()) as conexao:
        ultimo = conexao.execute(
            "SELECT * FROM eventos ORDER BY id DESC LIMIT 1"
        ).fetchone()

        alertas = conexao.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(duracao_s), 0) AS segundos
            FROM eventos
            WHERE tipo = 'alerta_postura' AND recebido_em >= ?
            """,
            (limite,),
        ).fetchone()

        recentes = conexao.execute(
            """
            SELECT tipo, inclinado_frente, inclinado_lateral, duracao_s, recebido_em
            FROM eventos
            WHERE tipo != 'status'
            ORDER BY id DESC LIMIT 20
            """
        ).fetchall()

    if ultimo is None:
        return vazio("local", detalhe)

    return montar_resposta(
        "local",
        ultimo,
        alertas,
        [dict(linha) for linha in recentes],
        0,
        detalhe,
    )


def resumo_notificacao():
    """O pouco que o dashboard precisa saber sobre os avisos no celular."""
    return {
        "ligada": estado_notificacao["ligada"],
        "ok": estado_notificacao["ok"],
        "enviadas": estado_notificacao["enviadas_na_sessao"],
    }


def ler_nuvem_uma_vez():
    """Le o estado da nuvem (ou do banco local, se ela falhar) e guarda no cache."""
    if not nuvem.configurada():
        payload = status_local("nuvem nao configurada (servidor/.env)")
    else:
        try:
            payload = status_da_nuvem()
        except requests.RequestException as erro:
            print(f"[nuvem] leitura falhou, usando banco local: {erro}", flush=True)
            payload = status_local(f"nuvem inacessivel: {erro}")

    with _trava_cache:
        _cache["quando"] = time.monotonic()
        _cache["payload"] = payload
    return payload


def laco_leitura_nuvem():
    while True:
        ler_nuvem_uma_vez()
        time.sleep(CACHE_STATUS_S)


def iniciar_leitura_nuvem():
    """Le a nuvem numa thread propria, para o dashboard nunca esperar a internet.

    Pelo hotspot do celular, cada leitura da nuvem levava de 2 a 5 s e as vezes
    estourava o timeout de 8 s. Com o /api/status esperando a nuvem, o dashboard
    congelava bem durante um alerta curto e nunca mostrava "Inadequada" (teste
    de 24/09). Agora o /api/status so devolve a ultima leitura pronta.
    """
    leitor_nuvem["ativo"] = True
    thread = threading.Thread(target=laco_leitura_nuvem, daemon=True)
    thread.start()


def estado_ao_vivo():
    """Conexao e postura atuais, lidas do SQLite.

    O ESP32 grava primeiro aqui, entao este e o estado mais novo que existe. A
    nuvem recebe o mesmo evento segundos depois; esperar por ela so atrasaria
    o cartao "Postura agora".
    """
    with closing(conectar()) as conexao:
        ultimo = conexao.execute(
            "SELECT * FROM eventos ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if ultimo is None:
        return {}

    atual = montar_resposta("local", ultimo, {"total": 0, "segundos": 0}, [], 0)
    campos = ("conectado", "dispositivo", "postura_ok",
              "inclinado_frente", "inclinado_lateral", "visto_em")
    return {campo: atual[campo] for campo in campos}


@app.get("/api/status")
def status():
    """Estado atual e estatisticas das ultimas 24 horas.

    Estatisticas e historico vem da nuvem. Se a nuvem nao responder, caem para o
    banco local e o campo "origem" diz isso -- o dashboard mostra a diferenca em
    vez de fingir que esta tudo bem. Conexao e postura atuais vem sempre do
    banco local (ver estado_ao_vivo).
    """
    with _trava_cache:
        payload = _cache["payload"]
        vencido = time.monotonic() - _cache["quando"] >= CACHE_STATUS_S

    if payload is None or (vencido and not leitor_nuvem["ativo"]):
        payload = ler_nuvem_uma_vez()

    payload = dict(payload, **estado_ao_vivo())

    # Fora do montar_resposta de proposito: o estado dos avisos nao vem do
    # banco nem da nuvem, e vale igual nos dois caminhos (nuvem e local).
    payload["notificacao"] = resumo_notificacao()

    return jsonify(payload)


@app.get("/api/nuvem")
def diagnostico_nuvem():
    """Diagnostico da integracao com a nuvem.

    Serve para conferir a configuracao sem abrir o dashboard, e e uma das
    evidencias da entrega: mostra o projeto Supabase respondendo, quantas
    linhas existem na tabela e quantas ainda estao na fila local.
    """
    ok, mensagem = nuvem.testar_conexao()

    return jsonify({
        "servico": "Supabase (PostgreSQL gerenciado)",
        "comunicacao": "HTTPS / REST (PostgREST)",
        "projeto_url": nuvem.url_base() or None,
        "tabela": nuvem.TABELA,
        "configurada": nuvem.configurada(),
        "conexao_ok": ok,
        "mensagem": mensagem,
        "eventos_na_nuvem": nuvem.total_de_linhas() if ok else 0,
        "pendentes_envio": contar_pendentes(),
        "ultimo_envio": estado_nuvem["ultimo_envio"],
        "ultimo_erro": estado_nuvem["ultimo_erro"],
        "enviados_na_sessao": estado_nuvem["enviados_na_sessao"],
    }), (200 if ok else 503)


@app.get("/api/notificacao")
def diagnostico_notificacao():
    """Diagnostico do aviso no celular.

    Nao devolve o token nem o chat de destino: o token e credencial e o chat
    identifica a pessoa que recebe as mensagens. A resposta e evidencia da
    entrega, e vai parar em arquivo de log.
    """
    ok, mensagem = notifica.testar_conexao()

    return jsonify({
        "servico": "Telegram (Bot API)",
        "comunicacao": "HTTPS / REST",
        "configurada": notifica.configurada(),
        "conexao_ok": ok,
        "mensagem": mensagem,
        "avisos_ligados": estado_notificacao["ligada"],
        "intervalo_minimo_s": intervalo_notificacao_s(),
        "enviadas_na_sessao": estado_notificacao["enviadas_na_sessao"],
        "ignoradas_por_intervalo": estado_notificacao["ignoradas_por_intervalo"],
        "na_fila": fila_notificacoes.qsize(),
        "ultimo_envio": estado_notificacao["ultimo_envio"],
        "ultimo_erro": estado_notificacao["ultimo_erro"],
    }), (200 if ok else 503)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

PAGINA = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de Postura</title>
<style>
  body { font-family: system-ui, sans-serif; background: #12151c; color: #e8eaf0;
         margin: 0; padding: 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #8b93a7; font-size: 13px; margin-bottom: 16px; }
  .fonte { display: inline-flex; align-items: center; gap: 8px; font-size: 13px;
           background: #1a1f2b; border: 1px solid #2a3040; border-radius: 999px;
           padding: 6px 14px; margin-bottom: 24px; }
  .bolinha { width: 8px; height: 8px; border-radius: 50%; background: #8b93a7; }
  .bolinha.nuvem { background: #4ade80; } .bolinha.local { background: #fbbf24; }
  .cartoes { display: grid; gap: 12px;
             grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
             margin-bottom: 24px; }
  .cartao { background: #1a1f2b; border-radius: 10px; padding: 16px; }
  .rotulo { color: #8b93a7; font-size: 12px; text-transform: uppercase;
            letter-spacing: .5px; }
  .valor { font-size: 26px; font-weight: 600; margin-top: 6px; }
  .nota { color: #8b93a7; font-size: 11px; margin-top: 4px; min-height: 14px; }
  .ok { color: #4ade80; } .ruim { color: #f87171; } .off { color: #8b93a7; }
  .alerta { color: #fbbf24; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; color: #8b93a7; font-weight: 500; padding: 8px;
       border-bottom: 1px solid #2a3040; font-size: 12px; text-transform: uppercase; }
  td { padding: 8px; border-bottom: 1px solid #1f242f; }
  .vazio { color: #8b93a7; padding: 24px 8px; }
</style>
</head>
<body>
  <h1>Monitor de Postura</h1>
  <div class="sub">Projeto de extensao &middot; ESP32 + sensores SW-520D</div>

  <div class="fonte">
    <span class="bolinha" id="bolinha"></span>
    <span id="fonte">Verificando a origem dos dados...</span>
  </div>

  <div class="cartoes">
    <div class="cartao">
      <div class="rotulo">Dispositivo</div>
      <div class="valor" id="conexao">--</div>
      <div class="nota" id="visto"></div>
    </div>
    <div class="cartao">
      <div class="rotulo">Postura agora</div>
      <div class="valor" id="postura">--</div>
      <div class="nota" id="eixo_atual"></div>
    </div>
    <div class="cartao">
      <div class="rotulo">Alertas (24h)</div>
      <div class="valor" id="alertas">--</div>
      <div class="nota" id="onde_somou">--</div>
    </div>
    <div class="cartao">
      <div class="rotulo">Tempo torto (24h)</div>
      <div class="valor" id="tempo">--</div>
      <div class="nota">soma das duracoes</div>
    </div>
    <div class="cartao">
      <div class="rotulo">Eventos na nuvem</div>
      <div class="valor" id="nanuvem">--</div>
      <div class="nota" id="fila"></div>
    </div>
    <div class="cartao">
      <div class="rotulo">Avisos no celular</div>
      <div class="valor" id="avisos">--</div>
      <div class="nota" id="avisos_nota"></div>
    </div>
  </div>

  <table>
    <thead>
      <tr><th>Horario</th><th>Evento</th><th>Eixo</th><th>Duracao</th></tr>
    </thead>
    <tbody id="corpo">
      <tr><td colspan="4" class="vazio">Aguardando dados do ESP32...</td></tr>
    </tbody>
  </table>

<script>
function eixo(e) {
  if (e.inclinado_frente && e.inclinado_lateral) return "frente + lateral";
  if (e.inclinado_frente) return "frente";
  if (e.inclinado_lateral) return "lateral";
  return "--";
}

// Deixa explicito na tela de onde vem cada numero mostrado: da nuvem ou do
// banco local. E o jeito mais honesto de apresentar o fallback.
function mostrarFonte(d) {
  const bolinha = document.getElementById("bolinha");
  const texto = document.getElementById("fonte");

  const onde = document.getElementById("onde_somou");

  if (d.origem === "nuvem") {
    bolinha.className = "bolinha nuvem";
    texto.textContent = "Dados lidos da nuvem \\u2014 Supabase / PostgreSQL via HTTPS";
    onde.textContent = "somado no Postgres, na nuvem";
  } else {
    bolinha.className = "bolinha local";
    texto.textContent = "Dados do banco local \\u2014 " + (d.nuvem_detalhe || "nuvem indisponivel");
    onde.textContent = "somado no SQLite local";
  }
}

async function atualizar() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();

    mostrarFonte(d);

    const conexao = document.getElementById("conexao");
    conexao.textContent = d.conectado ? "Online" : "Offline";
    conexao.className = "valor " + (d.conectado ? "ok" : "off");
    document.getElementById("visto").textContent =
      d.visto_em ? "visto em " + d.visto_em.replace("T", " ") : "";

    const postura = document.getElementById("postura");
    if (!d.conectado || d.postura_ok === null) {
      postura.textContent = "--";
      postura.className = "valor off";
      document.getElementById("eixo_atual").textContent = "";
    } else {
      postura.textContent = d.postura_ok ? "Correta" : "Inadequada";
      postura.className = "valor " + (d.postura_ok ? "ok" : "ruim");
      document.getElementById("eixo_atual").textContent =
        d.postura_ok ? "" : "eixo: " + eixo(d);
    }

    document.getElementById("alertas").textContent = d.alertas_24h;
    document.getElementById("tempo").textContent = d.tempo_ruim_24h_min + " min";

    const nanuvem = document.getElementById("nanuvem");
    nanuvem.textContent = d.origem === "nuvem" ? d.eventos_na_nuvem : "--";
    nanuvem.className = "valor " + (d.origem === "nuvem" ? "ok" : "off");

    const fila = document.getElementById("fila");
    if (d.pendentes_envio > 0) {
      fila.textContent = d.pendentes_envio + " na fila de envio";
      fila.className = "nota alerta";
    } else {
      fila.textContent = "fila local vazia";
      fila.className = "nota";
    }

    const avisos = document.getElementById("avisos");
    const avisosNota = document.getElementById("avisos_nota");
    const n = d.notificacao || {};
    if (!n.ligada) {
      avisos.textContent = "--";
      avisos.className = "valor off";
      avisosNota.textContent = "Telegram nao configurado";
    } else {
      avisos.textContent = n.enviadas;
      avisos.className = "valor " + (n.ok === false ? "ruim" : "ok");
      avisosNota.textContent =
        n.ok === false ? "falha no ultimo envio" : "enviados por Telegram";
    }

    const corpo = document.getElementById("corpo");
    if (!d.eventos.length) {
      corpo.innerHTML = '<tr><td colspan="4" class="vazio">Nenhum evento ainda.</td></tr>';
      return;
    }
    corpo.innerHTML = d.eventos.map(e => `
      <tr>
        <td>${e.recebido_em.replace("T", " ")}</td>
        <td>${e.tipo === "alerta_postura" ? "Alerta" : "Postura corrigida"}</td>
        <td>${eixo(e)}</td>
        <td>${e.duracao_s}s</td>
      </tr>`).join("");
  } catch (err) {
    console.error("falha ao buscar status", err);
  }
}

atualizar();
setInterval(atualizar, 2000);
</script>
</body>
</html>
"""


@app.get("/")
def dashboard():
    return PAGINA


if __name__ == "__main__":
    nuvem.carregar_env()
    criar_tabelas()

    print(f"Banco local (fila): {BANCO}")

    if nuvem.configurada():
        ok, mensagem = nuvem.testar_conexao()
        print(f"Nuvem: {nuvem.url_base()}")
        print(f"       {'OK' if ok else 'FALHA'} -- {mensagem}")
        if not ok:
            print("       O servidor sobe de qualquer jeito: os eventos ficam na")
            print("       fila local e sobem quando a nuvem responder.")

    if notifica.configurada():
        ok, mensagem = notifica.testar_conexao()
        print(f"Celular: Telegram -- {'OK' if ok else 'FALHA'} -- {mensagem}")

    iniciar_sincronizacao()
    iniciar_notificacoes()
    iniciar_leitura_nuvem()

    print("Dashboard: http://localhost:5000")
    # host 0.0.0.0 e obrigatorio para o ESP32 conseguir alcancar o servidor.
    app.run(host="0.0.0.0", port=5000, debug=False)
