"""
Testa o aviso no celular (Etapa 5) sem precisar de internet nem de um bot.

O teste sobe um servidor HTTP local que imita a Bot API do Telegram e aponta o
TELEGRAM_API_URL para ele. Assim conseguimos verificar de verdade:

  - que um alerta de postura vira mensagem no celular, num texto que a pessoa
    entende sem saber nada do sistema (o que esta errado, ha quanto tempo e o
    que fazer) e sem jargao de manutencao;
  - que correcao de postura e heartbeat NAO viram mensagem;
  - o intervalo minimo entre avisos, que impede a enxurrada de mensagens;
  - que o ESP32 continua recebendo 201 mesmo com o Telegram fora do ar;
  - que o token nao vaza: nem no dashboard, nem no /api/status, nem nas
    mensagens de erro que vao para o log.

Rodar:
    python testes/teste_notificacao.py

Precisa do flask e do requests instalados (servidor/requirements.txt).
"""

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "servidor"))

# Token de mentira, no mesmo formato do que o @BotFather entrega. Vale como
# agulha no palheiro: no fim do teste procuramos por ele nas respostas que o
# servidor da para o navegador.
TOKEN = "123456789:AAtoken-de-teste-nao-serve-para-nada"
CHAT_ID = "987654321"


# ---------------------------------------------------------------------------
# Bot API de mentira
# ---------------------------------------------------------------------------

class TelegramFalso:
    """Guarda as mensagens "entregues" e o historico das requisicoes."""

    def __init__(self):
        self.mensagens = []           # o que chegaria no celular
        self.requisicoes = []         # (caminho, corpo)
        self.modo = "normal"          # normal | fora_do_ar | recusa


class Manipulador(BaseHTTPRequestHandler):
    telegram = None

    def log_message(self, *_):
        pass                          # silencia o log padrao do http.server

    def responder(self, codigo, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        caminho = urlparse(self.path).path
        tamanho = int(self.headers.get("Content-Length", 0))
        corpo = json.loads(self.rfile.read(tamanho) or b"{}")
        self.telegram.requisicoes.append((caminho, corpo))

        if self.telegram.modo == "fora_do_ar":
            return self.responder(503, {"ok": False, "description": "servico indisponivel"})

        # O token vai dentro da URL, e e assim que a Bot API autentica.
        if not caminho.startswith(f"/bot{TOKEN}/"):
            return self.responder(401, {"ok": False, "description": "token invalido"})

        metodo = caminho.rsplit("/", 1)[-1]

        if metodo == "getMe":
            return self.responder(200, {
                "ok": True,
                "result": {"id": 123456789, "is_bot": True, "username": "postura_teste_bot"},
            })

        if metodo != "sendMessage":
            return self.responder(404, {"ok": False, "description": f"metodo {metodo}"})

        # A Bot API responde 200 com ok=false quando o chat nao existe ou o bot
        # foi bloqueado -- o caso mais facil de tratar errado.
        if self.telegram.modo == "recusa":
            return self.responder(200, {"ok": False, "description": "chat not found"})

        self.telegram.mensagens.append(corpo)
        return self.responder(200, {"ok": True, "result": {"message_id": len(self.telegram.mensagens)}})


def subir_telegram_falso():
    estado = TelegramFalso()
    Manipulador.telegram = estado

    servidor = HTTPServer(("127.0.0.1", 0), Manipulador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    porta = servidor.server_address[1]
    return estado, servidor, f"http://127.0.0.1:{porta}"


# ---------------------------------------------------------------------------
# Verificacoes
# ---------------------------------------------------------------------------

falhas = []


def check(nome, obtido, esperado):
    ok = obtido == esperado
    print(f"  {'PASS' if ok else 'FALHA'}  {nome}: {obtido!r}"
          + ("" if ok else f" (esperado {esperado!r})"))
    if not ok:
        falhas.append(nome)


def esperar(condicao, limite_s=5):
    """Espera a thread de notificacao trabalhar, sem sleep fixo no teste."""
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return False


def enviar_evento(cliente, tipo, frente=False, lateral=False, duracao=0, alertas=1):
    return cliente.post("/api/eventos", json={
        "dispositivo": "esp32-teste",
        "tipo": tipo,
        "inclinado_frente": frente,
        "inclinado_lateral": lateral,
        "duracao_s": duracao,
        "total_alertas": alertas,
        "uptime_s": 300,
    })


def main():
    estado, servidor, url = subir_telegram_falso()

    banco = Path(tempfile.mkdtemp()) / "teste.db"
    os.environ["POSTURA_DB"] = str(banco)
    os.environ["TELEGRAM_API_URL"] = url
    os.environ["TELEGRAM_TOKEN"] = TOKEN
    os.environ["TELEGRAM_CHAT_ID"] = CHAT_ID
    os.environ["INTERVALO_NOTIFICACAO_S"] = "60"
    # Sem nuvem: este teste e sobre o celular. O dashboard cai para o SQLite.
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_KEY", None)

    import app                                    # noqa: PLC0415

    app.criar_tabelas()
    app.iniciar_notificacoes()
    cliente = app.app.test_client()

    print(f"Telegram simulado (Bot API) em {url}")
    print(f"Banco local temporario em {banco}\n")

    # -----------------------------------------------------------------------
    print("1. Um alerta de postura vira mensagem no celular")
    resposta = enviar_evento(cliente, "alerta_postura", frente=True, duracao=12, alertas=3)
    check("HTTP do POST /api/eventos", resposta.status_code, 201)
    check("mensagem entregue pelo Telegram", esperar(lambda: len(estado.mensagens) == 1), True)

    caminho, _ = estado.requisicoes[-1]
    check("metodo chamado na Bot API", caminho, f"/bot{TOKEN}/sendMessage")

    enviada = estado.mensagens[0]
    check("destinatario da mensagem", enviada["chat_id"], CHAT_ID)

    texto = enviada["text"]
    print(f"\n    --- mensagem como chega no celular ---\n"
          + "\n".join("    | " + l for l in texto.splitlines()) + "\n")
    check("assunto da mensagem", texto.splitlines()[0], "Hora de ajustar a postura!")
    check("eixo descrito em portugues", "inclinado para frente" in texto, True)
    check("tempo em palavras, nao em segundos crus", "ha 12 segundos" in texto, True)
    check("a mensagem diz o que fazer", "Endireite as costas" in texto, True)
    ultima_linha = texto.splitlines()[-1]
    check("hora do aviso, sem data nem segundos",
          ultima_linha.startswith("Aviso das ")
          and len(ultima_linha) == len("Aviso das 17:06"),
          True)
    # O aviso e para a pessoa, nao para quem mantem o sistema: identificador do
    # dispositivo e contagem de alertas ficam no painel e no banco.
    check("sem jargao do sistema no celular", "esp32-teste" in texto, False)
    check("sem contagem de alertas no celular", "Alertas" in texto, False)
    check("aviso curto", len(texto.splitlines()), 6)

    # -----------------------------------------------------------------------
    print("\n2. Correcao de postura e heartbeat nao viram mensagem")
    app._ultimo_aviso = 0.0                       # zera o intervalo minimo
    enviar_evento(cliente, "postura_corrigida", duracao=12)
    enviar_evento(cliente, "status")
    time.sleep(0.3)                               # tempo de sobra para a thread
    check("nenhuma mensagem nova", len(estado.mensagens), 1)

    # -----------------------------------------------------------------------
    print("\n3. Intervalo minimo: dois alertas seguidos nao viram duas mensagens")
    app._ultimo_aviso = 0.0
    enviar_evento(cliente, "alerta_postura", frente=True, duracao=8)
    check("o primeiro passa", esperar(lambda: len(estado.mensagens) == 2), True)

    enviar_evento(cliente, "alerta_postura", lateral=True, duracao=9)
    time.sleep(0.3)
    check("o segundo e segurado", len(estado.mensagens), 2)
    check(
        "e o gateway registra que segurou",
        app.estado_notificacao["ignoradas_por_intervalo"],
        1,
    )
    check("mas o evento foi gravado assim mesmo", app.contar_pendentes() >= 1, True)

    # -----------------------------------------------------------------------
    print("\n4. Telegram fora do ar: o ESP32 nao fica sabendo")
    estado.modo = "fora_do_ar"
    app._ultimo_aviso = 0.0
    resposta = enviar_evento(cliente, "alerta_postura", frente=True, duracao=20)
    check("ESP32 ainda recebe 201", resposta.status_code, 201)
    check(
        "a falha fica registrada no gateway",
        esperar(lambda: app.estado_notificacao["ok"] is False),
        True,
    )
    check("nada foi entregue", len(estado.mensagens), 2)
    check(
        "e o token nao aparece na mensagem de erro",
        TOKEN not in (app.estado_notificacao["ultimo_erro"] or ""),
        True,
    )

    # -----------------------------------------------------------------------
    print("\n5. Telegram responde 200 recusando (chat errado, bot bloqueado)")
    estado.modo = "recusa"
    app._ultimo_aviso = 0.0
    app.estado_notificacao["ok"] = None
    enviar_evento(cliente, "alerta_postura", lateral=True, duracao=25)
    check(
        "recusa com HTTP 200 tambem conta como falha",
        esperar(lambda: app.estado_notificacao["ok"] is False),
        True,
    )
    check("motivo devolvido pelo Telegram", "chat not found" in (app.estado_notificacao["ultimo_erro"] or ""), True)

    # -----------------------------------------------------------------------
    print("\n6. A fila volta a andar quando o Telegram responde de novo")
    estado.modo = "normal"
    app._ultimo_aviso = 0.0
    enviar_evento(cliente, "alerta_postura", frente=True, lateral=True, duracao=30)
    check("mensagem entregue", esperar(lambda: len(estado.mensagens) == 3), True)
    check(
        "os dois eixos aparecem no texto",
        "inclinado para frente e para o lado" in estado.mensagens[-1]["text"],
        True,
    )
    check("gateway voltou para ok", app.estado_notificacao["ok"], True)

    # -----------------------------------------------------------------------
    print("\n7. Diagnostico e dashboard")
    diagnostico = cliente.get("/api/notificacao").get_json()
    check("servico declarado", diagnostico["servico"], "Telegram (Bot API)")
    check("conexao com a Bot API", diagnostico["conexao_ok"], True)
    check("bot identificado pelo getMe", diagnostico["mensagem"], "conectado como @postura_teste_bot")
    check("avisos enviados na sessao", diagnostico["enviadas_na_sessao"], 3)
    check("avisos segurados pelo intervalo", diagnostico["ignoradas_por_intervalo"], 1)

    app._cache["payload"] = None                  # o cache do status e de 2s
    status = cliente.get("/api/status").get_json()
    check("dashboard sabe que os avisos estao ligados", status["notificacao"]["ligada"], True)
    check("dashboard mostra quantos foram enviados", status["notificacao"]["enviadas"], 3)

    # -----------------------------------------------------------------------
    print("\n8. O token fica no servidor: nao vai para o navegador")
    check(
        "token ausente do HTML do dashboard",
        TOKEN not in cliente.get("/").get_data(as_text=True),
        True,
    )
    check(
        "token ausente do /api/status",
        TOKEN not in cliente.get("/api/status").get_data(as_text=True),
        True,
    )
    check(
        "token ausente do /api/notificacao",
        TOKEN not in cliente.get("/api/notificacao").get_data(as_text=True),
        True,
    )
    check(
        "chat de destino ausente do /api/notificacao",
        CHAT_ID not in cliente.get("/api/notificacao").get_data(as_text=True),
        True,
    )

    servidor.shutdown()

    print()
    if falhas:
        print(f"{len(falhas)} verificacao(oes) falharam: {', '.join(falhas)}")
        return 1

    print("Todas as verificacoes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
