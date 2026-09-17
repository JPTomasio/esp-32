"""
Testa a integracao com a nuvem (Etapa 4) sem precisar de internet.

O teste sobe um servidor HTTP local que imita o PostgREST do Supabase e aponta
o SUPABASE_URL para ele. Assim conseguimos verificar de verdade:

  - o formato exato do JSON que sai daqui para a nuvem;
  - os cabecalhos de autenticacao;
  - as consultas que o dashboard faz (filtros e ordenacao do PostgREST);
  - a fila de reenvio: nuvem fora do ar nao perde evento, e o que ficou
    pendente sobe quando ela volta;
  - o fallback do dashboard para o banco local quando a nuvem nao responde.

Rodar:
    python testes/teste_nuvem.py

Precisa do flask e do requests instalados (servidor/requirements.txt).
"""

import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "servidor"))


# ---------------------------------------------------------------------------
# PostgREST de mentira
# ---------------------------------------------------------------------------

class NuvemFalsa:
    """Guarda o estado do "banco na nuvem" e o historico das requisicoes."""

    def __init__(self):
        self.linhas = []              # como se fosse a tabela public.eventos
        self.requisicoes = []         # (metodo, caminho, parametros, cabecalhos)
        self.fora_do_ar = False       # liga/desliga para testar a fila
        self.proximo_id = 1

    def gravar(self, corpo):
        gravadas = []

        for evento in corpo:
            chave = (evento["dispositivo"], evento["id_local"])

            # Imita o UNIQUE (dispositivo, id_local) + merge-duplicates:
            # reenvio atualiza a linha em vez de duplicar.
            existente = next(
                (l for l in self.linhas
                 if (l["dispositivo"], l["id_local"]) == chave),
                None,
            )

            if existente:
                existente.update(evento)
                gravadas.append(existente)
                continue

            linha = dict(
                evento,
                id=self.proximo_id,
                gravado_em=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            self.proximo_id += 1
            self.linhas.append(linha)
            gravadas.append(linha)

        return gravadas


class Manipulador(BaseHTTPRequestHandler):
    nuvem_falsa = None

    def log_message(self, *_):
        pass                          # silencia o log padrao do http.server

    def responder(self, codigo, corpo, cabecalhos=None):
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        for chave, valor in (cabecalhos or {}).items():
            self.send_header(chave, valor)
        self.end_headers()
        self.wfile.write(dados)

    def registrar(self, metodo):
        partes = urlparse(self.path)
        parametros = {k: v[0] for k, v in parse_qs(partes.query).items()}
        self.nuvem_falsa.requisicoes.append(
            (metodo, partes.path, parametros, dict(self.headers))
        )
        return partes.path, parametros

    def autenticado(self):
        return (
            self.headers.get("apikey") == "chave-de-teste"
            and self.headers.get("Authorization") == "Bearer chave-de-teste"
        )

    def do_POST(self):
        caminho, _ = self.registrar("POST")

        if self.nuvem_falsa.fora_do_ar:
            return self.responder(503, {"message": "nuvem simulada fora do ar"})

        if not self.autenticado():
            return self.responder(401, {"message": "chave invalida"})

        tamanho = int(self.headers.get("Content-Length", 0))
        corpo = json.loads(self.rfile.read(tamanho) or b"[]")

        if caminho != "/rest/v1/eventos":
            return self.responder(404, {"message": f"tabela desconhecida: {caminho}"})

        return self.responder(201, self.nuvem_falsa.gravar(corpo))

    def do_GET(self):
        caminho, parametros = self.registrar("GET")

        if self.nuvem_falsa.fora_do_ar:
            return self.responder(503, {"message": "nuvem simulada fora do ar"})

        if not self.autenticado():
            return self.responder(401, {"message": "chave invalida"})

        if caminho == "/rest/v1/estatisticas_24h":
            # Mesma conta da view em esquema_supabase.sql.
            limite = datetime.now().astimezone() - timedelta(hours=24)
            alertas = [
                l for l in self.nuvem_falsa.linhas
                if l["tipo"] == "alerta_postura"
                and datetime.fromisoformat(l["recebido_em"]) >= limite
            ]
            return self.responder(200, [{
                "alertas_24h": len(alertas),
                "segundos_24h": sum(l["duracao_s"] for l in alertas),
            }])

        if caminho != "/rest/v1/eventos":
            return self.responder(404, {"message": f"tabela desconhecida: {caminho}"})

        linhas = list(self.nuvem_falsa.linhas)

        # Suporta o unico filtro que o projeto usa: tipo=neq.status
        if parametros.get("tipo") == "neq.status":
            linhas = [l for l in linhas if l["tipo"] != "status"]

        if "desc" in parametros.get("order", ""):
            linhas.sort(key=lambda l: (l["recebido_em"], l["id"]), reverse=True)

        if "limit" in parametros:
            recorte = linhas[: int(parametros["limit"])]
        else:
            recorte = linhas

        cabecalhos = {}
        if "count=exact" in (self.headers.get("Prefer") or ""):
            cabecalhos["Content-Range"] = f"0-{max(len(recorte) - 1, 0)}/{len(linhas)}"

        return self.responder(200, recorte, cabecalhos)


def subir_nuvem_falsa():
    estado = NuvemFalsa()
    Manipulador.nuvem_falsa = estado

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


def enviar_evento(cliente, tipo, frente=False, lateral=False, duracao=0):
    return cliente.post("/api/eventos", json={
        "dispositivo": "esp32-teste",
        "tipo": tipo,
        "inclinado_frente": frente,
        "inclinado_lateral": lateral,
        "duracao_s": duracao,
        "total_alertas": 1,
        "uptime_s": 120,
    })


def main():
    estado, servidor, url = subir_nuvem_falsa()

    banco = Path(tempfile.mkdtemp()) / "teste.db"
    os.environ["POSTURA_DB"] = str(banco)
    os.environ["SUPABASE_URL"] = url
    os.environ["SUPABASE_KEY"] = "chave-de-teste"

    import app                                    # noqa: PLC0415

    app.criar_tabelas()
    cliente = app.app.test_client()

    print(f"Nuvem simulada (PostgREST) em {url}")
    print(f"Banco local temporario em {banco}\n")

    # -----------------------------------------------------------------------
    print("1. O gateway responde ao ESP32 sem esperar a nuvem")
    resposta = enviar_evento(cliente, "alerta_postura", frente=True, duracao=12)
    check("HTTP do POST /api/eventos", resposta.status_code, 201)
    check("id local devolvido ao firmware", resposta.get_json()["id_local"], 1)
    check("nada foi para a nuvem ainda", len(estado.linhas), 0)
    check("evento ficou na fila local", app.contar_pendentes(), 1)

    # -----------------------------------------------------------------------
    print("\n2. A sincronizacao leva a fila para a nuvem")
    check("eventos confirmados pela nuvem", app.sincronizar_uma_vez(), 1)
    check("linhas na tabela da nuvem", len(estado.linhas), 1)
    check("fila local esvaziou", app.contar_pendentes(), 0)

    gravado = estado.linhas[0]
    check("dispositivo gravado na nuvem", gravado["dispositivo"], "esp32-teste")
    check("tipo gravado na nuvem", gravado["tipo"], "alerta_postura")
    check("sensor da frente virou boolean", gravado["inclinado_frente"], True)
    check("duracao preservada", gravado["duracao_s"], 12)
    check("correlacao com a linha do SQLite", gravado["id_local"], 1)
    check(
        "recebido_em foi enviado com fuso horario",
        datetime.fromisoformat(gravado["recebido_em"]).tzinfo is not None,
        True,
    )

    metodo, caminho, _, cabecalhos = estado.requisicoes[0]
    check("metodo da escrita", f"{metodo} {caminho}", "POST /rest/v1/eventos")
    check("chave enviada no cabecalho apikey", cabecalhos.get("apikey"), "chave-de-teste")
    check(
        "upsert pedido no cabecalho Prefer",
        "resolution=merge-duplicates" in cabecalhos.get("Prefer", ""),
        True,
    )

    # -----------------------------------------------------------------------
    print("\n3. O dashboard le da nuvem, nao do banco local")
    enviar_evento(cliente, "postura_corrigida", duracao=12)
    enviar_evento(cliente, "status")
    app.sincronizar_uma_vez()
    app._cache["payload"] = None                  # o cache e de 2s

    dados = cliente.get("/api/status").get_json()
    check("origem dos dados", dados["origem"], "nuvem")
    check("total de linhas contado na nuvem", dados["eventos_na_nuvem"], 3)
    check("alertas 24h (view do Postgres)", dados["alertas_24h"], 1)
    check("tempo torto 24h em minutos", dados["tempo_ruim_24h_min"], 0.2)
    check("heartbeats 'status' fora da tabela", len(dados["eventos"]), 2)
    check("evento mais recente primeiro", dados["eventos"][0]["tipo"], "postura_corrigida")
    check(
        "horario devolvido sem fuso, para o dashboard",
        "+" not in dados["eventos"][0]["recebido_em"],
        True,
    )

    consultas = {c for _, c, _, _ in estado.requisicoes if c.startswith("/rest")}
    check(
        "a view de estatisticas foi consultada na nuvem",
        "/rest/v1/estatisticas_24h" in consultas,
        True,
    )

    # -----------------------------------------------------------------------
    print("\n4. Nuvem fora do ar: nada se perde")
    estado.fora_do_ar = True

    resposta = enviar_evento(cliente, "alerta_postura", lateral=True, duracao=30)
    check("ESP32 ainda recebe 201", resposta.status_code, 201)
    check("tentativa de envio nao confirma nada", app.sincronizar_uma_vez(), 0)
    check("evento aguardando na fila", app.contar_pendentes(), 1)

    app._cache["payload"] = None
    dados = cliente.get("/api/status").get_json()
    check("dashboard cai para o banco local", dados["origem"], "local")
    check("dashboard avisa quantos faltam subir", dados["pendentes_envio"], 1)
    check("e continua mostrando a postura atual", dados["postura_ok"], False)

    # -----------------------------------------------------------------------
    print("\n5. Nuvem de volta: a fila sobe sozinha")
    estado.fora_do_ar = False
    check("pendente enviado na retomada", app.sincronizar_uma_vez(), 1)
    check("fila local vazia", app.contar_pendentes(), 0)
    check("linhas na nuvem", len(estado.linhas), 4)

    # -----------------------------------------------------------------------
    print("\n6. Reenvio do mesmo evento nao duplica na nuvem")
    with app.closing(app.conectar()) as conexao, conexao:
        conexao.execute("UPDATE eventos SET enviado_nuvem = 0 WHERE id = 4")

    check("reenvio aceito pela nuvem", app.sincronizar_uma_vez(), 1)
    check("tabela na nuvem continua com 4 linhas", len(estado.linhas), 4)

    # -----------------------------------------------------------------------
    print("\n7. Diagnostico da integracao")
    diagnostico = cliente.get("/api/nuvem").get_json()
    check("servico declarado", diagnostico["servico"], "Supabase (PostgreSQL gerenciado)")
    check("metodo de comunicacao", diagnostico["comunicacao"], "HTTPS / REST (PostgREST)")
    check("conexao com a nuvem", diagnostico["conexao_ok"], True)
    check("eventos contabilizados na nuvem", diagnostico["eventos_na_nuvem"], 4)
    check("nada pendente", diagnostico["pendentes_envio"], 0)

    servidor.shutdown()

    print()
    if falhas:
        print(f"{len(falhas)} verificacao(oes) falharam: {', '.join(falhas)}")
        return 1

    print("Todas as verificacoes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
