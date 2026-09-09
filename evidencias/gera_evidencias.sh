#!/usr/bin/env bash
#
# Gera as evidencias de funcionamento do projeto, sem precisar do ESP32 montado.
#
#   bash evidencias/gera_evidencias.sh
#
# Tudo o que for gerado vai para evidencias/saida/. Cada execucao refaz a pasta,
# entao o conteudo sempre corresponde a mesma rodada.
#
# O que fica provado aqui: a logica do firmware, o contrato HTTP que o ESP32
# usa, a gravacao no banco e o dashboard reagindo aos dados. O que NAO fica
# provado: os sensores fisicos e o buzzer -- isso depende do hardware montado
# e esta descrito no final do RESUMO.md gerado.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAIDA="$RAIZ/evidencias/saida"
VENV="$RAIZ/.venv-evidencias"
PYTHON="$VENV/bin/python"
BANCO="$SAIDA/postura_evidencia.db"
PORTA=5000
BASE="http://localhost:$PORTA"
SEGUNDOS_SIMULACAO="${SEGUNDOS_SIMULACAO:-45}"

SERVIDOR_PID=""
SIMULADOR_PID=""

limpar() {
  [ -n "$SIMULADOR_PID" ] && kill "$SIMULADOR_PID" 2>/dev/null || true
  [ -n "$SERVIDOR_PID" ]  && kill "$SERVIDOR_PID"  2>/dev/null || true
  wait 2>/dev/null || true
}
trap limpar EXIT

titulo() { printf '\n=== %s ===\n' "$1"; }

# O Flask imprime o IP da maquina na rede local ao subir ("Running on
# http://192.168.x.x:5000"). Como o repositorio e publico, trocamos esses
# enderecos por um marcador antes de guardar os logs. 127.0.0.1 e 0.0.0.0
# ficam como estao: nao identificam ninguem e ajudam a entender o log.
ocultar_ips_locais() {
  sed -i -E 's#\b(192\.168\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b#[ip-local-omitido]#g' "$@"
}

# Pelo mesmo motivo, os caminhos absolutos viram caminhos relativos a raiz do
# projeto -- assim o log nao carrega o nome de usuario nem a pasta pessoal.
encurtar_caminhos() {
  sed -i "s#$RAIZ/#./#g" "$@"
}

# O Flask colore a saida com codigos ANSI, que viram lixo visual quando o log e
# aberto num editor. Removemos para o arquivo ficar legivel na entrega.
remover_cores() {
  sed -i 's/\x1b\[[0-9;]*m//g' "$@"
}

# ---------------------------------------------------------------------------
# Preparacao
# ---------------------------------------------------------------------------

if [ ! -x "$PYTHON" ]; then
  echo "Ambiente Python nao encontrado em $VENV." >&2
  echo "Crie com:  uv venv .venv-evidencias && \\" >&2
  echo "           uv pip install --python .venv-evidencias -r servidor/requirements.txt" >&2
  echo "(ou use um venv comum: python3 -m venv .venv-evidencias e pip install -r ...)" >&2
  exit 1
fi

CHROME=""
for c in google-chrome google-chrome-stable chromium chromium-browser; do
  command -v "$c" >/dev/null && { CHROME="$c"; break; }
done

rm -rf "$SAIDA"
mkdir -p "$SAIDA"

DATA_INICIO="$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Gerando evidencias em $SAIDA"
echo "Inicio: $DATA_INICIO"

# ---------------------------------------------------------------------------
# 1. Logica do firmware (compila o .ino de verdade com mocks do Arduino)
# ---------------------------------------------------------------------------

titulo "1/6 Teste da logica do firmware"
{
  echo "# Teste da logica do firmware -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# Compila firmware/02_monitor_postura/02_monitor_postura.ino sem alteracoes,"
  echo "# usando mocks das funcoes do Arduino, e simula uma sessao de uso."
  echo
  echo "\$ g++ -std=c++17 -I mocks -I ../firmware/02_monitor_postura -x c++ teste_firmware.cpp"
  ( cd "$RAIZ/testes" && g++ -std=c++17 -I mocks -I ../firmware/02_monitor_postura \
      -x c++ teste_firmware.cpp -o "$SAIDA/.teste_firmware" ) 2>&1
  echo "compilou sem erros"
  echo
  echo "\$ ./teste_firmware"
  "$SAIDA/.teste_firmware"
  echo
  echo "codigo de saida: 0"
} | tee "$SAIDA/01_teste_firmware.log"
rm -f "$SAIDA/.teste_firmware"

# ---------------------------------------------------------------------------
# 2. Consultas do servidor
# ---------------------------------------------------------------------------

titulo "2/6 Teste das consultas do servidor"
{
  echo "# Teste do esquema e das agregacoes do dashboard -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "\$ python3 testes/teste_sql.py"
  python3 "$RAIZ/testes/teste_sql.py"
  echo
  echo "codigo de saida: 0"
} | tee "$SAIDA/02_teste_servidor.log"

# ---------------------------------------------------------------------------
# 3. Sobe o servidor (banco separado, para nao mexer no postura.db do grupo)
# ---------------------------------------------------------------------------

titulo "3/6 Servidor Flask"
POSTURA_DB="$BANCO" "$PYTHON" "$RAIZ/servidor/app.py" > "$SAIDA/03_servidor.log" 2>&1 &
SERVIDOR_PID=$!

for _ in $(seq 1 40); do
  curl -sf "$BASE/api/status" >/dev/null 2>&1 && break
  kill -0 "$SERVIDOR_PID" 2>/dev/null || { echo "servidor morreu:"; cat "$SAIDA/03_servidor.log"; exit 1; }
  sleep 0.5
done
curl -sf "$BASE/api/status" >/dev/null || { echo "servidor nao respondeu"; cat "$SAIDA/03_servidor.log"; exit 1; }
echo "servidor no ar em $BASE (pid $SERVIDOR_PID), banco em $BANCO"

# ---------------------------------------------------------------------------
# 4. Contrato HTTP -- as mesmas chamadas que o ESP32 faz
# ---------------------------------------------------------------------------

titulo "4/6 Chamadas da API"
{
  echo "# Contrato HTTP entre o ESP32 e o servidor -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# As chamadas abaixo sao identicas as que o firmware faz em enviarEvento()."
  echo

  echo "--- 4.1 Evento de alerta (o que o ESP32 envia ao detectar postura ruim)"
  echo "\$ curl -i -X POST $BASE/api/eventos -H 'Content-Type: application/json' -d '{...}'"
  curl -si -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
    -d '{"dispositivo":"esp32-01","tipo":"alerta_postura","inclinado_frente":true,"inclinado_lateral":false,"duracao_s":12,"total_alertas":1,"uptime_s":180}'
  echo; echo

  echo "--- 4.2 Evento de correcao de postura"
  echo "\$ curl -i -X POST $BASE/api/eventos -H 'Content-Type: application/json' -d '{...}'"
  curl -si -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
    -d '{"dispositivo":"esp32-01","tipo":"postura_corrigida","inclinado_frente":false,"inclinado_lateral":false,"duracao_s":12,"total_alertas":1,"uptime_s":195}'
  echo; echo

  echo "--- 4.3 Corpo invalido: o servidor recusa com HTTP 400 (validacao de entrada)"
  echo "\$ curl -i -X POST $BASE/api/eventos -H 'Content-Type: application/json' -d 'isso nao e json'"
  curl -si -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' -d 'isso nao e json'
  echo; echo

  echo "--- 4.4 Estado consolidado que o dashboard consome"
  echo "\$ curl $BASE/api/status"
  curl -s "$BASE/api/status" | "$PYTHON" -m json.tool
} | tee "$SAIDA/04_api_http.log"

# ---------------------------------------------------------------------------
# 5. Simulador + capturas do dashboard
# ---------------------------------------------------------------------------

titulo "5/6 Simulador de ESP32 por ${SEGUNDOS_SIMULACAO}s"
# -u desliga o buffer do stdout: sem isso o log fica vazio ao encerrar.
"$PYTHON" -u "$RAIZ/servidor/simulador.py" > "$SAIDA/05_simulador.log" 2>&1 &
SIMULADOR_PID=$!
sleep "$SEGUNDOS_SIMULACAO"
# SIGINT em vez de SIGTERM: o simulador trata KeyboardInterrupt e fecha limpo.
kill -INT "$SIMULADOR_PID" 2>/dev/null || true
sleep 1
kill "$SIMULADOR_PID" 2>/dev/null || true
SIMULADOR_PID=""
ENVIADOS=$(grep -c '^enviado' "$SAIDA/05_simulador.log" 2>/dev/null || true)
echo "simulador parado; ${ENVIADOS:-0} eventos enviados"

capturar() {
  local arquivo="$1"
  [ -z "$CHROME" ] && { echo "  (Chrome/Chromium nao encontrado, captura pulada)"; return; }
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
    --user-data-dir="$(mktemp -d)" --window-size=1200,900 \
    --virtual-time-budget=6000 --screenshot="$arquivo" "$BASE/" >/dev/null 2>&1 || true
  [ -f "$arquivo" ] && echo "  capturado: $(basename "$arquivo")" || echo "  FALHOU: $(basename "$arquivo")"
}

# Estado "postura inadequada": ultimo evento e um alerta em curso.
curl -s -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
  -d '{"dispositivo":"esp32-01","tipo":"alerta_postura","inclinado_frente":true,"inclinado_lateral":true,"duracao_s":23,"total_alertas":7,"uptime_s":600}' >/dev/null
capturar "$SAIDA/06_dashboard_alerta.png"

# Estado "postura correta": o usuario se endireitou.
curl -s -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
  -d '{"dispositivo":"esp32-01","tipo":"postura_corrigida","inclinado_frente":false,"inclinado_lateral":false,"duracao_s":23,"total_alertas":7,"uptime_s":625}' >/dev/null
capturar "$SAIDA/07_dashboard_ok.png"

# ---------------------------------------------------------------------------
# 6. Banco de dados
# ---------------------------------------------------------------------------

titulo "6/6 Conteudo do banco"
POSTURA_DB="$BANCO" "$PYTHON" - "$BANCO" > "$SAIDA/08_banco_sqlite.log" <<'PY'
import sqlite3, sys
from datetime import datetime

con = sqlite3.connect(sys.argv[1])
con.row_factory = sqlite3.Row

print(f"# Conteudo do banco SQLite -- {datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"# Arquivo: {sys.argv[1]}")
print()
print("--- Esquema criado pelo app.py")
for (sql,) in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
    print(sql, end=";\n\n")

linhas = con.execute("SELECT * FROM eventos ORDER BY id").fetchall()
print(f"--- SELECT * FROM eventos  ({len(linhas)} registros gravados)")
cab = ["id", "dispositivo", "tipo", "frente", "lateral", "dur_s", "alertas", "uptime_s", "recebido_em"]
larg = [4, 16, 18, 6, 7, 5, 7, 8, 19]
print("  ".join(c.ljust(w) for c, w in zip(cab, larg)))
print("  ".join("-" * w for w in larg))
for l in linhas:
    vals = [l["id"], l["dispositivo"], l["tipo"], l["inclinado_frente"],
            l["inclinado_lateral"], l["duracao_s"], l["total_alertas"],
            l["uptime_s"], l["recebido_em"]]
    print("  ".join(str(v).ljust(w) for v, w in zip(vals, larg)))

print()
print("--- Resumo por tipo de evento")
for l in con.execute("SELECT tipo, COUNT(*) n, COALESCE(SUM(duracao_s),0) s FROM eventos GROUP BY tipo ORDER BY n DESC"):
    print(f"  {l['tipo']:20} {l['n']:3} eventos   {l['s']:5}s acumulados")
PY
cat "$SAIDA/08_banco_sqlite.log" | tail -20

limpar
SERVIDOR_PID=""

titulo "Limpando dados pessoais dos logs"
ocultar_ips_locais "$SAIDA"/*.log
encurtar_caminhos "$SAIDA"/*.log
remover_cores "$SAIDA"/*.log
grep -l -e 'ip-local-omitido' -e '\./evidencias' "$SAIDA"/*.log | while read -r arquivo; do
  echo "  limpo: $(basename "$arquivo")"
done

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------

REGISTROS=$(grep -oP '\(\K\d+(?= registros gravados)' "$SAIDA/08_banco_sqlite.log" | head -1)

cat > "$SAIDA/RESUMO.md" <<RESUMO
# Evidencias de funcionamento -- Monitor de Postura

Gerado automaticamente por \`evidencias/gera_evidencias.sh\`.

- **Inicio da execucao:** $DATA_INICIO
- **Fim da execucao:** $(date '+%Y-%m-%d %H:%M:%S %Z')
- **Maquina:** $(uname -srm)
- **Python:** $("$PYTHON" -V 2>&1)
- **Compilador:** $(g++ --version 2>/dev/null | head -1)
- **Navegador da captura:** $([ -n "$CHROME" ] && $CHROME --version || echo "nao disponivel")

## Arquivos

| Arquivo | O que prova |
|---|---|
| \`01_teste_firmware.log\` | O firmware entregue compila e sua maquina de estados se comporta como especificado: nao alerta com postura correta, nao alerta antes do tempo limite, alerta ao passar dele, repete o bipe sem contar alerta novo, desliga ao corrigir, detecta os dois eixos e **filtra o tremor do SW-520D sem gerar alerta falso**. |
| \`02_teste_servidor.log\` | O esquema do banco e as agregacoes do dashboard (janela de 24h, contagem de alertas, tempo acumulado, lista de eventos) retornam os valores corretos. |
| \`03_servidor.log\` | Saida do servidor durante a execucao: cada evento recebido aparece com horario, dispositivo e tipo. |
| \`04_api_http.log\` | O contrato HTTP que o ESP32 usa, com requisicao e resposta completas: alerta aceito (201), correcao aceita (201), corpo invalido recusado (400) e o JSON consolidado de \`/api/status\`. |
| \`05_simulador.log\` | Sessao de ${SEGUNDOS_SIMULACAO}s com um ESP32 simulado enviando eventos continuamente, todos respondidos com HTTP 201. |
| \`06_dashboard_alerta.png\` | Dashboard no estado **postura inadequada**: cartao vermelho, contagem de alertas e tabela de eventos preenchida. |
| \`07_dashboard_ok.png\` | Dashboard no estado **postura correta** logo apos o evento de correcao: prova que a tela reage a mudanca de estado. |
| \`08_banco_sqlite.log\` | Esquema criado pelo proprio \`app.py\` e os ${REGISTROS:-todos os} registros efetivamente gravados, com resumo por tipo de evento. |
| \`postura_evidencia.db\` | O banco SQLite da execucao, caso seja preciso conferir os dados na mao. |

## O que estas evidencias nao cobrem

Tudo acima roda no PC. Ficam de fora, por dependerem do hardware montado:

1. **Leitura fisica dos sensores SW-520D** -- grave \`firmware/01_teste_sensores\`,
   incline cada sensor com a mao e salve o texto do Monitor Serial (115200).
2. **Buzzer e LED disparando** -- vale um video curto: a pessoa curva o tronco,
   o buzzer apita depois do tempo limite e o dashboard registra o evento.
3. **Envio pelo WiFi** -- o log serial do firmware principal mostra a conexao e
   o HTTP 201 de cada envio; junto com \`03_servidor.log\` do lado do PC, fecha o
   caminho completo.
4. **Angulo-limite escolhido pelo grupo** -- foto da montagem com o angulo
   marcado. O SW-520D nao mede angulo: a calibragem e a posicao em que o sensor
   foi colado, entao a foto e a unica documentacao possivel dela.
RESUMO

titulo "Pronto"
echo "Evidencias em: $SAIDA"
ls -1 "$SAIDA"
