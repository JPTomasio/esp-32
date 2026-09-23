"""
ETAPA 5 - Notificacao no celular (Telegram).

Quando o ESP32 avisa que a postura ficou inadequada, o buzzer apita em quem
esta usando a cinta -- mas ninguem mais fica sabendo. Este modulo fecha essa
lacuna: manda a mesma informacao para o celular, onde ela pode ser vista
depois, guardada e mostrada para outra pessoa.

    SW-520D -> ESP32 -> HTTP/JSON -> app.py -> SQLite local (fila)
                                            -> HTTPS/REST -> Supabase
                                            -> HTTPS/REST -> [ ESTE ARQUIVO ]
                                                                    |
                                                          Telegram -> celular

Por que Telegram e nao WhatsApp: o WhatsApp so permite envio automatico pela
API oficial (Meta Cloud API), que exige conta comercial verificada, numero
dedicado e mensagens em modelos aprovados antes -- nada disso cabe num projeto
de extensao. O Telegram entrega um bot gratuito em dois minutos e o envio e um
POST HTTPS, exatamente o que o projeto ja faz com o Supabase.

Configuracao (servidor/.env, fora do Git):

    TELEGRAM_TOKEN=123456789:AA...
    TELEGRAM_CHAT_ID=987654321

O token da controle total do bot, entao ele fica somente no servidor Python:
nunca vai para o navegador nem para o firmware do ESP32. Por precaucao, as
mensagens de erro deste modulo passam por sem_token() antes de subir -- o
token aparece dentro da URL da API e apareceria nos logs sem esse cuidado.

Como todo o resto do projeto, isto e opcional: sem as duas variaveis o sistema
roda igual, so nao avisa o celular.
"""

import os
from datetime import datetime

import requests

# Endereco da API do Telegram. E variavel de ambiente para o teste automatizado
# poder apontar para um servidor local e rodar sem internet.
API_PADRAO = "https://api.telegram.org"

# Tempo maximo de espera. Curto de proposito: a notificacao nunca pode segurar
# o caminho do evento: se o Telegram demorar, o alerta perde a graca de qualquer
# jeito e o evento ja esta gravado no banco e na nuvem.
TIMEOUT_S = 8


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

def url_api():
    return (os.environ.get("TELEGRAM_API_URL") or API_PADRAO).rstrip("/")


def token():
    return os.environ.get("TELEGRAM_TOKEN") or ""


def destino():
    return os.environ.get("TELEGRAM_CHAT_ID") or ""


def configurada():
    """True quando ha bot e destinatario para notificar."""
    return bool(token() and destino())


def endpoint(metodo):
    return f"{url_api()}/bot{token()}/{metodo}"


def sem_token(texto):
    """Troca o token por um marcador.

    A URL da API do Telegram carrega o token no caminho, entao ele aparece em
    qualquer mensagem de erro do requests. Como os logs do servidor viram
    evidencia da entrega e este repositorio e publico, o token e apagado aqui,
    antes de a mensagem chegar ao app.py.
    """
    atual = token()
    return texto.replace(atual, "[token-omitido]") if atual else texto


# ---------------------------------------------------------------------------
# Chamadas a API
# ---------------------------------------------------------------------------

def chamar(metodo, parametros=None):
    """Chama um metodo da Bot API e devolve o campo "result" da resposta.

    Levanta requests.RequestException quando nao consegue falar com o Telegram
    ou quando a API recusa a chamada -- quem chama decide o que fazer.
    """
    if not token():
        raise RuntimeError("TELEGRAM_TOKEN nao configurado")

    try:
        resposta = requests.post(
            endpoint(metodo), json=parametros or {}, timeout=TIMEOUT_S
        )
    except requests.RequestException as erro:
        # "from None" corta o encadeamento de proposito: o traceback original
        # traz a URL completa, com o token dentro.
        raise requests.RequestException(sem_token(str(erro))) from None

    try:
        corpo = resposta.json()
    except ValueError:
        corpo = {}

    # A Bot API responde 200 com {"ok": false, "description": "..."} em varios
    # erros de uso (chat_id errado, bot bloqueado pelo usuario), entao conferir
    # so o codigo HTTP nao basta.
    if not resposta.ok or not corpo.get("ok"):
        detalhe = corpo.get("description") or resposta.text[:200]
        raise requests.HTTPError(
            sem_token(f"Telegram recusou {metodo}: HTTP {resposta.status_code} -- {detalhe}"),
            response=resposta,
        )

    return corpo.get("result")


def enviar(texto):
    """Manda uma mensagem de texto para o chat configurado."""
    if not configurada():
        raise RuntimeError("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID nao configurados")

    return chamar("sendMessage", {
        "chat_id": destino(),
        "text": texto,
        # Sem preview de link e sem markup: a mensagem e texto puro, e assim
        # nenhum caractere do conteudo precisa ser escapado.
        "disable_web_page_preview": True,
    })


# ---------------------------------------------------------------------------
# Texto da mensagem
# ---------------------------------------------------------------------------

def descrever_eixos(frente, lateral):
    """Como a pessoa esta torta, nas palavras que ela usaria."""
    if frente and lateral:
        return "inclinado para frente e para o lado"
    if frente:
        return "inclinado para frente"
    if lateral:
        return "inclinado para o lado"
    return "fora da posicao certa"


def descrever_tempo(segundos):
    """Segundos viram tempo de relogio: "40 segundos", "1 minuto e 30 segundos".

    O firmware conta em segundos porque e assim que ele mede, mas "ha 180
    segundos" obriga quem le a fazer conta. No celular vale o numero que a
    pessoa entende sem pensar.
    """
    segundos = int(segundos or 0)
    if segundos < 60:
        return "1 segundo" if segundos == 1 else f"{segundos} segundos"

    minutos, resto = divmod(segundos, 60)
    texto = "1 minuto" if minutos == 1 else f"{minutos} minutos"
    if resto:
        texto += " e 1 segundo" if resto == 1 else f" e {resto} segundos"
    return texto


def o_que_fazer(frente, lateral):
    """A unica coisa que o aviso pede da pessoa: o movimento de correcao."""
    if frente and lateral:
        return "Endireite as costas e alinhe os ombros."
    if lateral:
        return "Alinhe os ombros: o peso esta todo de um lado so."
    return "Endireite as costas e encoste bem no fundo da cadeira."


def hora_curta(quando):
    """So a hora e o minuto do "2026-09-22T17:06:16" que o app.py carimba.

    Segundos e data completa nao dizem nada para quem esta lendo no celular
    minutos depois -- o que importa e "foi agora" ou "foi mais cedo".
    """
    relogio = str(quando).replace("T", " ").split(" ")[-1]
    return relogio[:5] if relogio[2:3] == ":" else str(quando)


def mensagem(evento):
    """Monta o aviso que chega no celular.

    Quem le e a pessoa que esta com a cinta, no meio de outra coisa -- aula,
    trabalho, estudo. Entao o texto responde so as tres perguntas dela: o que
    esta errado, ha quanto tempo, o que fazer agora -- e a hora, porque o aviso
    pode ser lido bem depois de chegar. Nome do dispositivo e contagem de
    alertas saem daqui de proposito: isso e informacao de manutencao do
    sistema, e continua inteira no painel, no banco local e na nuvem, que e
    onde ela serve para alguma coisa.

    Sem acento e sem emoji, pelo mesmo motivo do resto do projeto: o texto
    passa por log, terminal e arquivo de evidencia antes de chegar no celular.
    """
    frente = evento.get("inclinado_frente")
    lateral = evento.get("inclinado_lateral")
    # Sem "recebido_em" o aviso nao veio pelo caminho normal (app.py sempre
    # carimba): a hora do envio e a melhor aproximacao que sobra.
    quando = evento.get("recebido_em") or datetime.now().isoformat(timespec="seconds")

    return "\n".join([
        "Hora de ajustar a postura!",
        "",
        f"Voce esta {descrever_eixos(frente, lateral)}"
        f" ha {descrever_tempo(evento.get('duracao_s'))}.",
        o_que_fazer(frente, lateral),
        "",
        f"Aviso das {hora_curta(quando)}",
    ])


def notificar_alerta(evento):
    return enviar(mensagem(evento))


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------

def testar_conexao():
    """Confere token e conectividade sem mandar mensagem para ninguem.

    Devolve (ok, mensagem) em vez de levantar excecao: o app.py usa isso apenas
    para avisar na tela, nunca para impedir a inicializacao.
    """
    if not configurada():
        return False, "TELEGRAM_TOKEN/TELEGRAM_CHAT_ID ausentes (servidor/.env)"

    try:
        eu = chamar("getMe") or {}
    except (requests.RequestException, RuntimeError) as erro:
        return False, str(erro)

    nome = eu.get("username") or eu.get("first_name") or "?"
    return True, f"conectado como @{nome}"

