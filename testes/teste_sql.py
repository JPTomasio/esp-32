"""Testa o esquema e as queries do app.py sem precisar do Flask."""
import sqlite3, sys
from datetime import datetime, timedelta

con = sqlite3.connect(":memory:")
con.row_factory = sqlite3.Row
con.execute("""
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispositivo TEXT NOT NULL, tipo TEXT NOT NULL,
    inclinado_frente INTEGER NOT NULL DEFAULT 0,
    inclinado_lateral INTEGER NOT NULL DEFAULT 0,
    duracao_s INTEGER NOT NULL DEFAULT 0,
    total_alertas INTEGER NOT NULL DEFAULT 0,
    uptime_s INTEGER NOT NULL DEFAULT 0,
    recebido_em TEXT NOT NULL)""")
con.execute("CREATE INDEX IF NOT EXISTS idx_recebido ON eventos(recebido_em)")

agora = datetime.now()
linhas = [
    # evento antigo primeiro, como chegaria de verdade (id cresce com o tempo).
    # Fica fora da janela de 24h.
    ("esp32-01", "alerta_postura",    1,1, 99, 9,  1, (agora-timedelta(hours=30)).isoformat(timespec="seconds")),
    ("esp32-01", "status",            0,0,  0, 0, 10, (agora-timedelta(minutes=5)).isoformat(timespec="seconds")),
    ("esp32-01", "alerta_postura",    1,0, 12, 1, 40, (agora-timedelta(minutes=4)).isoformat(timespec="seconds")),
    ("esp32-01", "postura_corrigida", 0,0, 12, 1, 55, (agora-timedelta(minutes=3)).isoformat(timespec="seconds")),
    ("esp32-01", "alerta_postura",    0,1, 30, 2, 90, (agora-timedelta(minutes=2)).isoformat(timespec="seconds")),
    ("esp32-01", "status",            0,1,  8, 2,120, agora.isoformat(timespec="seconds")),
]
con.executemany("""INSERT INTO eventos (dispositivo,tipo,inclinado_frente,inclinado_lateral,
                   duracao_s,total_alertas,uptime_s,recebido_em) VALUES (?,?,?,?,?,?,?,?)""", linhas)

limite = (agora - timedelta(hours=24)).isoformat(timespec="seconds")
ultimo = con.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT 1").fetchone()
alertas = con.execute("""SELECT COUNT(*) AS total, COALESCE(SUM(duracao_s),0) AS segundos
                         FROM eventos WHERE tipo='alerta_postura' AND recebido_em >= ?""",(limite,)).fetchone()
recentes = con.execute("""SELECT tipo,inclinado_frente,inclinado_lateral,duracao_s,recebido_em
                          FROM eventos WHERE tipo != 'status' ORDER BY id DESC LIMIT 20""").fetchall()

visto = datetime.fromisoformat(ultimo["recebido_em"])
conectado = (datetime.now() - visto).total_seconds() < 90
torto = bool(ultimo["inclinado_frente"] or ultimo["inclinado_lateral"])

falhas = []
def check(nome, obtido, esperado):
    ok = obtido == esperado
    print(f"  {'PASS' if ok else 'FALHA'}  {nome}: {obtido!r}" + ("" if ok else f" (esperado {esperado!r})"))
    if not ok: falhas.append(nome)

print("Resultados:")
check("conectado (ultimo evento agora)", conectado, True)
check("postura_ok", not torto, False)
check("alertas nas ultimas 24h", alertas["total"], 2)
check("tempo torto 24h (min)", round(alertas["segundos"]/60, 1), 0.7)
check("eventos na tabela (sem 'status')", len(recentes), 4)
check("evento mais recente da lista", recentes[0]["tipo"], "alerta_postura")

sys.exit(1 if falhas else 0)
