"""
ETAPA 3 - Servidor Python do monitor de postura.

Recebe os eventos que o ESP32 envia por HTTP, guarda em um banco SQLite
e mostra um dashboard simples no navegador.

Como rodar:
    pip install -r requirements.txt
    python app.py

Depois abra http://localhost:5000 no navegador.

O ESP32 precisa apontar para o IP deste computador no arquivo config.h.
Descubra o IP com "ip a" (Linux) ou "ipconfig" (Windows).
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

BANCO = Path(__file__).parent / "postura.db"


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def conectar():
    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    return conexao


def criar_tabelas():
    with conectar() as conexao:
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
                recebido_em       TEXT    NOT NULL
            )
            """
        )
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_recebido ON eventos(recebido_em)"
        )


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

    with conectar() as conexao:
        conexao.execute(
            """
            INSERT INTO eventos (dispositivo, tipo, inclinado_frente,
                                 inclinado_lateral, duracao_s, total_alertas,
                                 uptime_s, recebido_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            evento,
        )

    print(f"[{evento[7]}] {evento[0]} -> {evento[1]} (duracao={evento[4]}s)")

    # Aqui entra a notificacao para o celular (Telegram/WhatsApp), quando o
    # grupo chegar nessa parte. Exemplo comentado em notificador.py.
    if dados.get("tipo") == "alerta_postura":
        print("  >> ALERTA: usuario com postura inadequada")

    return jsonify({"ok": True}), 201


@app.get("/api/status")
def status():
    """Devolve o estado atual e as estatisticas das ultimas 24 horas."""
    limite = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")

    with conectar() as conexao:
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
        return jsonify({
            "conectado": False,
            "postura_ok": None,
            "alertas_24h": 0,
            "tempo_ruim_24h_min": 0,
            "eventos": [],
        })

    # Consideramos o dispositivo online se deu sinal nos ultimos 90 segundos
    # (o heartbeat do firmware e de 30s).
    visto_em = datetime.fromisoformat(ultimo["recebido_em"])
    conectado = (datetime.now() - visto_em).total_seconds() < 90

    torto = bool(ultimo["inclinado_frente"] or ultimo["inclinado_lateral"])

    return jsonify({
        "conectado": conectado,
        "dispositivo": ultimo["dispositivo"],
        "postura_ok": not torto,
        "inclinado_frente": bool(ultimo["inclinado_frente"]),
        "inclinado_lateral": bool(ultimo["inclinado_lateral"]),
        "visto_em": ultimo["recebido_em"],
        "alertas_24h": alertas["total"],
        "tempo_ruim_24h_min": round(alertas["segundos"] / 60, 1),
        "eventos": [dict(linha) for linha in recentes],
    })


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
  .sub { color: #8b93a7; font-size: 13px; margin-bottom: 24px; }
  .cartoes { display: grid; gap: 12px;
             grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
             margin-bottom: 24px; }
  .cartao { background: #1a1f2b; border-radius: 10px; padding: 16px; }
  .rotulo { color: #8b93a7; font-size: 12px; text-transform: uppercase;
            letter-spacing: .5px; }
  .valor { font-size: 26px; font-weight: 600; margin-top: 6px; }
  .ok { color: #4ade80; } .ruim { color: #f87171; } .off { color: #8b93a7; }
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

  <div class="cartoes">
    <div class="cartao">
      <div class="rotulo">Dispositivo</div>
      <div class="valor" id="conexao">--</div>
    </div>
    <div class="cartao">
      <div class="rotulo">Postura agora</div>
      <div class="valor" id="postura">--</div>
    </div>
    <div class="cartao">
      <div class="rotulo">Alertas (24h)</div>
      <div class="valor" id="alertas">--</div>
    </div>
    <div class="cartao">
      <div class="rotulo">Tempo torto (24h)</div>
      <div class="valor" id="tempo">--</div>
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

async function atualizar() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();

    const conexao = document.getElementById("conexao");
    conexao.textContent = d.conectado ? "Online" : "Offline";
    conexao.className = "valor " + (d.conectado ? "ok" : "off");

    const postura = document.getElementById("postura");
    if (!d.conectado || d.postura_ok === null) {
      postura.textContent = "--";
      postura.className = "valor off";
    } else {
      postura.textContent = d.postura_ok ? "Correta" : "Inadequada";
      postura.className = "valor " + (d.postura_ok ? "ok" : "ruim");
    }

    document.getElementById("alertas").textContent = d.alertas_24h;
    document.getElementById("tempo").textContent = d.tempo_ruim_24h_min + " min";

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
    criar_tabelas()
    print(f"Banco de dados: {BANCO}")
    print("Dashboard: http://localhost:5000")
    # host 0.0.0.0 e obrigatorio para o ESP32 conseguir alcancar o servidor.
    app.run(host="0.0.0.0", port=5000, debug=False)
