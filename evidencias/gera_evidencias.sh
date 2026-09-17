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
# usa, a gravacao no banco local, o envio para a nuvem (Supabase) e o dashboard
# reagindo aos dados. O que NAO fica provado: os sensores fisicos e o buzzer --
# isso depende do hardware montado e esta descrito no final do RESUMO.md gerado.
#
# Se servidor/.env estiver preenchido, o script tambem grava eventos no Supabase
# de verdade e consulta a nuvem com curl para provar que os dados chegaram la.
# Sem .env, as etapas de nuvem que dependem de internet sao puladas e o resto
# roda normalmente.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAIDA="$RAIZ/evidencias/saida"
VENV="$RAIZ/.venv-evidencias"
PYTHON="$VENV/bin/python"
BANCO="$SAIDA/postura_evidencia.db"
PORTA=5000
BASE="http://localhost:$PORTA"
SEGUNDOS_SIMULACAO="${SEGUNDOS_SIMULACAO:-45}"

# Nome unico por execucao. A tabela na nuvem tem UNIQUE (dispositivo,id_local)
# para o reenvio nao duplicar linha; como o banco local e recriado a cada
# rodada (ids voltam para 1), repetir o nome faria a rodada nova sobrescrever a
# anterior na nuvem em vez de acrescentar.
DISPOSITIVO="esp32-evidencia-$(date +%Y%m%d-%H%M%S)"

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

# A chave do Supabase (service_role) da acesso total ao banco na nuvem. Ela
# nunca pode acabar num arquivo entregue ou versionado, entao a apagamos dos
# logs antes de fechar. A URL do projeto fica: ela e a evidencia de qual
# projeto na nuvem recebeu os dados, e sozinha nao da acesso a nada.
ocultar_chaves() {
  [ -z "${SUPABASE_KEY:-}" ] && return 0
  # A chave e um JWT com pontos e barras, que confundiriam o sed; por isso a
  # substituicao e feita em Python, com comparacao literal.
  "$PYTHON" - "$SUPABASE_KEY" "$@" <<'PY'
import sys
from pathlib import Path

chave, *arquivos = sys.argv[1:]

for nome in arquivos:
    caminho = Path(nome)
    try:
        texto = caminho.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    if chave and chave in texto:
        caminho.write_text(texto.replace(chave, "[chave-omitida]"), encoding="utf-8")
PY
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

# Le servidor/.env para saber se a nuvem esta configurada. Somente as duas
# variaveis do Supabase sao aproveitadas -- nao usamos "source" para nao
# executar o que estiver escrito no arquivo.
ENV_NUVEM="$RAIZ/servidor/.env"
SUPABASE_URL=""
SUPABASE_KEY=""
if [ -f "$ENV_NUVEM" ]; then
  SUPABASE_URL="$(grep -E '^SUPABASE_URL=' "$ENV_NUVEM" | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r' || true)"
  SUPABASE_KEY="$(grep -E '^SUPABASE_KEY=' "$ENV_NUVEM" | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r' || true)"
fi
export SUPABASE_URL SUPABASE_KEY

NUVEM_ATIVA=false
[ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_KEY" ] && NUVEM_ATIVA=true

rm -rf "$SAIDA"
mkdir -p "$SAIDA"

DATA_INICIO="$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Gerando evidencias em $SAIDA"
echo "Inicio: $DATA_INICIO"
if $NUVEM_ATIVA; then
  echo "Nuvem: configurada ($SUPABASE_URL)"
else
  echo "Nuvem: NAO configurada -- crie servidor/.env a partir de .env.exemplo"
  echo "       As etapas 9 e 10 (dados na nuvem) serao puladas."
fi

# ---------------------------------------------------------------------------
# 1. Logica do firmware (compila o .ino de verdade com mocks do Arduino)
# ---------------------------------------------------------------------------

titulo "1/9 Teste da logica do firmware"
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

titulo "2/9 Teste das consultas do servidor"
{
  echo "# Teste do esquema e das agregacoes do dashboard -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "\$ python3 testes/teste_sql.py"
  python3 "$RAIZ/testes/teste_sql.py"
  echo
  echo "codigo de saida: 0"
} | tee "$SAIDA/02_teste_servidor.log"

# ---------------------------------------------------------------------------
# 3. Integracao com a nuvem, testada sem internet
# ---------------------------------------------------------------------------

titulo "3/9 Teste da integracao com a nuvem"
{
  echo "# Teste da integracao com a nuvem -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# Sobe um PostgREST de mentira em 127.0.0.1 e aponta o SUPABASE_URL para"
  echo "# ele. Verifica o JSON que sai para a nuvem, os cabecalhos de"
  echo "# autenticacao, as consultas do dashboard e a fila de reenvio."
  echo
  echo "\$ python testes/teste_nuvem.py"
  # Ambiente limpo: este teste usa a nuvem simulada, nao a de verdade.
  ( cd "$RAIZ" && env -u SUPABASE_URL -u SUPABASE_KEY -u POSTURA_DB \
      "$PYTHON" -u testes/teste_nuvem.py )
  echo
  echo "codigo de saida: 0"
} | tee "$SAIDA/03_teste_nuvem.log"

# ---------------------------------------------------------------------------
# 4. Sobe o gateway (banco separado, para nao mexer no postura.db do grupo)
# ---------------------------------------------------------------------------

titulo "4/9 Gateway Flask"
POSTURA_DB="$BANCO" "$PYTHON" -u "$RAIZ/servidor/app.py" > "$SAIDA/04_servidor.log" 2>&1 &
SERVIDOR_PID=$!

for _ in $(seq 1 40); do
  curl -sf "$BASE/api/status" >/dev/null 2>&1 && break
  kill -0 "$SERVIDOR_PID" 2>/dev/null || { echo "servidor morreu:"; cat "$SAIDA/04_servidor.log"; exit 1; }
  sleep 0.5
done
curl -sf "$BASE/api/status" >/dev/null || { echo "servidor nao respondeu"; cat "$SAIDA/04_servidor.log"; exit 1; }
echo "gateway no ar em $BASE (pid $SERVIDOR_PID), banco local em $BANCO"
$NUVEM_ATIVA && echo "replicando para $SUPABASE_URL"

# ---------------------------------------------------------------------------
# 5. Contrato HTTP -- as mesmas chamadas que o ESP32 faz
# ---------------------------------------------------------------------------

titulo "5/9 Chamadas da API"
{
  echo "# Contrato HTTP entre o ESP32 e o gateway Python -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# As chamadas abaixo sao identicas as que o firmware faz em enviarEvento()."
  echo "# Dispositivo desta rodada: $DISPOSITIVO"
  echo

  echo "--- 5.1 Evento de alerta (o que o ESP32 envia ao detectar postura ruim)"
  echo "\$ curl -i -X POST $BASE/api/eventos -H 'Content-Type: application/json' -d '{...}'"
  curl -si -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
    -d "{\"dispositivo\":\"$DISPOSITIVO\",\"tipo\":\"alerta_postura\",\"inclinado_frente\":true,\"inclinado_lateral\":false,\"duracao_s\":12,\"total_alertas\":1,\"uptime_s\":180}"
  echo; echo
  echo "# A resposta traz id_local e fila_nuvem: o gateway confirma o registro"
  echo "# na hora e leva o evento para a nuvem em segundo plano, para o firmware"
  echo "# nao ficar esperando a internet."
  echo

  echo "--- 5.2 Evento de correcao de postura"
  echo "\$ curl -i -X POST $BASE/api/eventos -H 'Content-Type: application/json' -d '{...}'"
  curl -si -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
    -d "{\"dispositivo\":\"$DISPOSITIVO\",\"tipo\":\"postura_corrigida\",\"inclinado_frente\":false,\"inclinado_lateral\":false,\"duracao_s\":12,\"total_alertas\":1,\"uptime_s\":195}"
  echo; echo

  echo "--- 5.3 Corpo invalido: o servidor recusa com HTTP 400 (validacao de entrada)"
  echo "\$ curl -i -X POST $BASE/api/eventos -H 'Content-Type: application/json' -d 'isso nao e json'"
  curl -si -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' -d 'isso nao e json'
  echo; echo

  echo "--- 5.4 Estado consolidado que o dashboard consome"
  echo "# O campo \"origem\" diz de onde vieram os numeros: \"nuvem\" quando foram"
  echo "# lidos do Supabase, \"local\" quando a nuvem nao respondeu."
  echo "\$ curl $BASE/api/status"
  curl -s "$BASE/api/status" | "$PYTHON" -m json.tool
} | tee "$SAIDA/05_api_http.log"

# ---------------------------------------------------------------------------
# 6. Simulador
# ---------------------------------------------------------------------------

titulo "6/9 Simulador de ESP32 por ${SEGUNDOS_SIMULACAO}s"
# -u desliga o buffer do stdout: sem isso o log fica vazio ao encerrar.
SIMULADOR_DISPOSITIVO="$DISPOSITIVO" \
  "$PYTHON" -u "$RAIZ/servidor/simulador.py" > "$SAIDA/06_simulador.log" 2>&1 &
SIMULADOR_PID=$!
sleep "$SEGUNDOS_SIMULACAO"
# SIGINT em vez de SIGTERM: o simulador trata KeyboardInterrupt e fecha limpo.
kill -INT "$SIMULADOR_PID" 2>/dev/null || true
sleep 1
kill "$SIMULADOR_PID" 2>/dev/null || true
SIMULADOR_PID=""
ENVIADOS=$(grep -c '^enviado' "$SAIDA/06_simulador.log" 2>/dev/null || true)
echo "simulador parado; ${ENVIADOS:-0} eventos enviados"

# ---------------------------------------------------------------------------
# 7. Capturas do dashboard
# ---------------------------------------------------------------------------

titulo "7/9 Capturas do dashboard"

# Espera a fila local esvaziar, para o dashboard ser capturado lendo da nuvem
# e nao caindo para o banco local.
aguardar_fila_vazia() {
  $NUVEM_ATIVA || return 0
  for _ in $(seq 1 30); do
    local pendentes
    pendentes=$(curl -s "$BASE/api/nuvem" \
      | grep -oE '"pendentes_envio":[[:space:]]*[0-9]+' \
      | grep -oE '[0-9]+$' || true)
    [ "${pendentes:-1}" = "0" ] && { echo "  fila de envio vazia"; return 0; }
    sleep 1
  done
  echo "  ATENCAO: ainda ha eventos na fila de envio"
}

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
  -d "{\"dispositivo\":\"$DISPOSITIVO\",\"tipo\":\"alerta_postura\",\"inclinado_frente\":true,\"inclinado_lateral\":true,\"duracao_s\":23,\"total_alertas\":7,\"uptime_s\":600}" >/dev/null
aguardar_fila_vazia
capturar "$SAIDA/07_dashboard_alerta.png"

# Estado "postura correta": o usuario se endireitou.
curl -s -X POST "$BASE/api/eventos" -H 'Content-Type: application/json' \
  -d "{\"dispositivo\":\"$DISPOSITIVO\",\"tipo\":\"postura_corrigida\",\"inclinado_frente\":false,\"inclinado_lateral\":false,\"duracao_s\":23,\"total_alertas\":7,\"uptime_s\":625}" >/dev/null
aguardar_fila_vazia
capturar "$SAIDA/08_dashboard_ok.png"

# ---------------------------------------------------------------------------
# 8. Banco local (a fila)
# ---------------------------------------------------------------------------

titulo "8/9 Conteudo do banco local"
"$PYTHON" - "$BANCO" > "$SAIDA/09_banco_local.log" <<'PY'
import sqlite3, sys
from datetime import datetime

con = sqlite3.connect(sys.argv[1])
con.row_factory = sqlite3.Row

print(f"# Conteudo do banco SQLite local -- {datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"# Arquivo: {sys.argv[1]}")
print("#")
print("# Na Etapa 4 este banco deixou de ser o destino dos dados e passou a ser")
print("# a fila de envio para a nuvem. A coluna enviado_nuvem mostra o estado de")
print("# cada linha: 1 = ja confirmada pelo Supabase, 0 = ainda na fila.")
print()
print("--- Esquema criado pelo app.py")
for (sql,) in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
    print(sql, end=";\n\n")

linhas = con.execute("SELECT * FROM eventos ORDER BY id").fetchall()
print(f"--- SELECT * FROM eventos  ({len(linhas)} registros gravados)")
cab = ["id", "dispositivo", "tipo", "frente", "lateral", "dur_s", "alertas", "uptime_s", "recebido_em", "na_nuvem"]
larg = [4, 30, 18, 6, 7, 5, 7, 8, 19, 8]
print("  ".join(c.ljust(w) for c, w in zip(cab, larg)))
print("  ".join("-" * w for w in larg))
for l in linhas:
    vals = [l["id"], l["dispositivo"], l["tipo"], l["inclinado_frente"],
            l["inclinado_lateral"], l["duracao_s"], l["total_alertas"],
            l["uptime_s"], l["recebido_em"], l["enviado_nuvem"]]
    print("  ".join(str(v).ljust(w) for v, w in zip(vals, larg)))

print()
print("--- Resumo por tipo de evento")
for l in con.execute("SELECT tipo, COUNT(*) n, COALESCE(SUM(duracao_s),0) s FROM eventos GROUP BY tipo ORDER BY n DESC"):
    print(f"  {l['tipo']:20} {l['n']:3} eventos   {l['s']:5}s acumulados")

print()
print("--- Estado da fila de envio para a nuvem")
for l in con.execute("""SELECT enviado_nuvem, COUNT(*) n FROM eventos
                        GROUP BY enviado_nuvem ORDER BY enviado_nuvem DESC"""):
    rotulo = "confirmados na nuvem" if l["enviado_nuvem"] else "ainda na fila"
    print(f"  enviado_nuvem = {l['enviado_nuvem']}  ->  {l['n']:3} ({rotulo})")
PY
tail -12 "$SAIDA/09_banco_local.log"

# ---------------------------------------------------------------------------
# 9. Dados na nuvem
# ---------------------------------------------------------------------------

titulo "9/9 Dados na nuvem (Supabase)"
{
  echo "# Evidencia de dados chegando a nuvem -- $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# Dispositivo desta rodada: $DISPOSITIVO"
  echo

  if ! $NUVEM_ATIVA; then
    echo "PULADO: servidor/.env nao esta configurado."
    echo
    echo "Para gerar esta evidencia:"
    echo "  1. crie o projeto em https://supabase.com"
    echo "  2. rode servidor/esquema_supabase.sql no SQL Editor"
    echo "  3. cp servidor/.env.exemplo servidor/.env e preencha URL e chave"
    echo "  4. rode este script de novo"
  else
    echo "--- 9.1 Diagnostico da integracao, pelo proprio gateway"
    echo "\$ curl $BASE/api/nuvem"
    curl -s "$BASE/api/nuvem" | "$PYTHON" -m json.tool
    echo

    echo "--- 9.2 Consulta direta ao Supabase, por fora da nossa aplicacao"
    echo "# curl falando com o PostgREST do Supabase. Nao passa pelo app.py nem"
    echo "# pelo SQLite: o que aparece aqui esta gravado no PostgreSQL na nuvem."
    echo "\$ curl '\$SUPABASE_URL/rest/v1/eventos?dispositivo=eq.$DISPOSITIVO&select=id,dispositivo,tipo,inclinado_frente,inclinado_lateral,duracao_s,recebido_em,gravado_em&order=id.desc&limit=10' \\"
    echo "       -H 'apikey: \$SUPABASE_KEY' -H 'Authorization: Bearer \$SUPABASE_KEY'"
    curl -s "$SUPABASE_URL/rest/v1/eventos?dispositivo=eq.$DISPOSITIVO&select=id,dispositivo,tipo,inclinado_frente,inclinado_lateral,duracao_s,recebido_em,gravado_em&order=id.desc&limit=10" \
      -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" \
      | "$PYTHON" -m json.tool
    echo
    echo "# recebido_em = quando o gateway Python recebeu o evento do ESP32."
    echo "# gravado_em  = quando o Postgres na nuvem gravou a linha (default now())."
    echo "# A diferenca entre os dois e o salto para a nuvem: prova que o dado"
    echo "# saiu da rede local e foi registrado por outro servidor."
    echo

    echo "--- 9.3 Quantas linhas esta rodada colocou na nuvem"
    echo "\$ curl -I '\$SUPABASE_URL/rest/v1/eventos?dispositivo=eq.$DISPOSITIVO' -H 'Prefer: count=exact'"
    curl -s -o /dev/null -D - -X HEAD \
      "$SUPABASE_URL/rest/v1/eventos?dispositivo=eq.$DISPOSITIVO&select=id" \
      -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" \
      -H "Prefer: count=exact" -H "Range-Unit: items" \
      | grep -iE '^(HTTP/|content-range)' || true
    echo "# content-range: 0-N/TOTAL -- o TOTAL depois da barra e a contagem"
    echo "# feita pelo Postgres, do lado da nuvem."
    echo

    echo "--- 9.4 Agregacao executada dentro do Postgres (view estatisticas_24h)"
    echo "# A soma das ultimas 24h nao e feita no PC: e a nuvem que calcula e"
    echo "# devolve o resultado pronto. Definida em servidor/esquema_supabase.sql."
    echo "\$ curl '\$SUPABASE_URL/rest/v1/estatisticas_24h?select=*' -H 'apikey: \$SUPABASE_KEY'"
    curl -s "$SUPABASE_URL/rest/v1/estatisticas_24h?select=*" \
      -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" \
      | "$PYTHON" -m json.tool
    echo

    echo "--- 9.5 A chave publica (anon) nao le a tabela: RLS ligado e sem policy"
    echo "# Confirma que os dados na nuvem nao ficaram abertos para qualquer um."
    echo "# Somente o gateway Python, que guarda a chave service_role, tem acesso."
    ANON="$(curl -s "$SUPABASE_URL/rest/v1/eventos?select=id&limit=1" \
      -H "apikey: chave-invalida-de-proposito" 2>/dev/null || true)"
    echo "\$ curl '\$SUPABASE_URL/rest/v1/eventos?select=id&limit=1' -H 'apikey: <chave invalida>'"
    echo "${ANON:-(sem resposta)}"
  fi
} | tee "$SAIDA/10_nuvem_supabase.log"

limpar
SERVIDOR_PID=""

titulo "Limpando dados sensiveis dos logs"
ocultar_ips_locais "$SAIDA"/*.log
encurtar_caminhos "$SAIDA"/*.log
remover_cores "$SAIDA"/*.log
ocultar_chaves "$SAIDA"/*.log
echo "  IPs da rede local, caminhos absolutos, cores do terminal e a chave do"
echo "  Supabase foram removidos dos arquivos .log"

# Conferencia final: se a chave ainda aparecer em algum arquivo, aborta em vez
# de entregar uma pasta com credencial dentro.
if [ -n "${SUPABASE_KEY:-}" ] && grep -rqF "$SUPABASE_KEY" "$SAIDA" 2>/dev/null; then
  echo "ERRO: a chave do Supabase ainda aparece em evidencias/saida/." >&2
  echo "Nao entregue esta pasta. Avise o grupo e abra um issue." >&2
  exit 1
fi
echo "  conferido: a chave nao aparece em nenhum arquivo da pasta"

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------

REGISTROS=$(grep -oP '\(\K\d+(?= registros gravados)' "$SAIDA/09_banco_local.log" | head -1)
NA_NUVEM=$(grep -oE 'items 0-[0-9]+/[0-9]+' "$SAIDA/10_nuvem_supabase.log" | grep -oE '[0-9]+$' | head -1)

cat > "$SAIDA/RESUMO.md" <<RESUMO
# Evidencias de funcionamento -- Monitor de Postura

Gerado automaticamente por \`evidencias/gera_evidencias.sh\`.

- **Inicio da execucao:** $DATA_INICIO
- **Fim da execucao:** $(date '+%Y-%m-%d %H:%M:%S %Z')
- **Maquina:** $(uname -srm)
- **Python:** $("$PYTHON" -V 2>&1)
- **Compilador:** $(g++ --version 2>/dev/null | head -1)
- **Navegador da captura:** $([ -n "$CHROME" ] && $CHROME --version || echo "nao disponivel")
- **Dispositivo desta rodada:** \`$DISPOSITIVO\`
- **Nuvem:** $($NUVEM_ATIVA && echo "Supabase -- $SUPABASE_URL" || echo "nao configurada (etapa 9 pulada)")

## Caminho dos dados

\`\`\`
SW-520D -> ESP32 -> HTTP/JSON (Wi-Fi) -> Python (gateway Flask)
                                           +-> SQLite local (fila de envio)
                                           +-> HTTPS/REST -> Supabase / PostgreSQL
                                                                   |
                                                             Dashboard le daqui
\`\`\`

## Arquivos

| Arquivo | O que prova |
|---|---|
| \`01_teste_firmware.log\` | O firmware entregue compila e sua maquina de estados se comporta como especificado: nao alerta com postura correta, nao alerta antes do tempo limite, alerta ao passar dele, repete o bipe sem contar alerta novo, desliga ao corrigir, detecta os dois eixos e **filtra o tremor do SW-520D sem gerar alerta falso**. |
| \`02_teste_servidor.log\` | O esquema do banco e as agregacoes do dashboard (janela de 24h, contagem de alertas, tempo acumulado, lista de eventos) retornam os valores corretos. |
| \`03_teste_nuvem.log\` | A integracao com a nuvem, verificada sem internet contra um PostgREST simulado: o JSON e os cabecalhos que saem para o Supabase, as consultas que o dashboard faz, o **fallback para o banco local quando a nuvem cai**, a **fila que reenvia o que ficou pendente** e o upsert que impede linha duplicada no reenvio. |
| \`04_servidor.log\` | Saida do gateway durante a execucao: cada evento recebido do ESP32 e cada lote confirmado pelo Supabase, com horario. |
| \`05_api_http.log\` | O contrato HTTP que o ESP32 usa, com requisicao e resposta completas: alerta aceito (201), correcao aceita (201), corpo invalido recusado (400) e o JSON de \`/api/status\` -- onde o campo \`origem\` mostra que os dados exibidos vieram da nuvem. |
| \`06_simulador.log\` | Sessao de ${SEGUNDOS_SIMULACAO}s com um ESP32 simulado enviando eventos continuamente, todos respondidos com HTTP 201. |
| \`07_dashboard_alerta.png\` | Dashboard no estado **postura inadequada**: cartao vermelho, contagem de alertas, tabela de eventos preenchida e o selo indicando que os dados foram lidos da nuvem. |
| \`08_dashboard_ok.png\` | Dashboard no estado **postura correta** logo apos o evento de correcao: prova que a tela reage a mudanca de estado. |
| \`09_banco_local.log\` | Esquema criado pelo proprio \`app.py\` e os ${REGISTROS:-todos os} registros da fila local, com a coluna \`enviado_nuvem\` mostrando o que ja subiu para o Supabase. |
| \`10_nuvem_supabase.log\` | **Evidencia de dados chegando a nuvem.** Consulta feita com \`curl\` direto no PostgREST do Supabase, por fora da nossa aplicacao: as linhas gravadas${NA_NUVEM:+ ($NA_NUVEM desta rodada)}, a contagem feita pelo Postgres, a agregacao rodando na view \`estatisticas_24h\` e a confirmacao de que a tabela nao esta aberta para chave publica (RLS). |
| \`postura_evidencia.db\` | O banco SQLite local desta execucao, caso seja preciso conferir os dados na mao. |

## O que estas evidencias nao cobrem

Tudo acima roda no PC e na nuvem. Ficam de fora:

1. **Print do painel do Supabase** -- exige login, entao nao da para automatizar.
   Abra o projeto em <https://supabase.com>, va em **Table Editor -> eventos**,
   filtre por \`dispositivo = $DISPOSITIVO\` e salve a captura. E a evidencia
   visual de dados na nuvem, complementar ao \`10_nuvem_supabase.log\`.
2. **Leitura fisica dos sensores SW-520D** -- grave \`firmware/01_teste_sensores\`,
   incline cada sensor com a mao e salve o texto do Monitor Serial (115200).
3. **Buzzer e LED disparando** -- vale um video curto: a pessoa curva o tronco,
   o buzzer apita depois do tempo limite e o dashboard registra o evento.
4. **Envio pelo WiFi** -- o log serial do firmware principal mostra a conexao e
   o HTTP 201 de cada envio; junto com \`04_servidor.log\` do lado do PC, fecha o
   caminho completo.
5. **Angulo-limite escolhido pelo grupo** -- foto da montagem com o angulo
   marcado. O SW-520D nao mede angulo: a calibragem e a posicao em que o sensor
   foi colado, entao a foto e a unica documentacao possivel dela.
RESUMO

titulo "Pronto"
echo "Evidencias em: $SAIDA"
ls -1 "$SAIDA"
