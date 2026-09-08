/*
 * ETAPA 2 - Monitor de postura
 *
 * Le dois sensores de inclinacao SW-520D, decide se a postura esta ruim,
 * apita quando ela fica ruim por tempo demais e envia os eventos para o
 * servidor Python.
 *
 * O sistema funciona MESMO SEM WIFI: o buzzer e o LED continuam alertando,
 * so os dados nao sobem para o servidor.
 *
 * LIGACAO
 *
 *   Sensor frente     Sensor lateral    Buzzer
 *   VCC -> 3V3        VCC -> 3V3        (+) -> GPIO 26
 *   GND -> GND        GND -> GND        (-) -> GND
 *   DO  -> GPIO 14    DO  -> GPIO 27
 *
 * MONTAGEM NO CORPO
 *   Os sensores vao presos na cinta, na altura das costas.
 *   - Sensor "frente": gire ate ele desarmar quando voce curvar o tronco
 *     para frente no angulo que o grupo definir como limite.
 *   - Sensor "lateral": mesma ideia, mas para o corpo pendendo de lado.
 *   A calibragem e fisica: gire o sensor ate acertar o ponto de disparo.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include "config.h"

// ---------------------------------------------------------------------------
// Prototipos das funcoes
// ---------------------------------------------------------------------------

bool lerSensor(int pino);
void amostrarSensores();
void fecharJanela();
void avaliarPostura(bool ruimAgora);
const char* descreverEixo();
void dispararBipe();
void atualizarBuzzer();
void conectarWiFi();
void verificarHeartbeat();
void enviarEvento(const char* tipo, unsigned long duracaoSegundos);

// ---------------------------------------------------------------------------
// Estado global
// ---------------------------------------------------------------------------

// Contadores da janela de amostragem
int amostrasFrente = 0;
int amostrasLateral = 0;
int totalAmostras = 0;
unsigned long ultimaAmostra = 0;

// Resultado filtrado da ultima janela
bool inclinadoFrente = false;
bool inclinadoLateral = false;

// Controle da postura
bool posturaRuim = false;          // decisao filtrada, atualizada a cada janela
bool alertaAtivo = false;          // ja passou do tempo limite?
unsigned long inicioPosturaRuim = 0;
unsigned long ultimoBipe = 0;
unsigned long ultimoHeartbeat = 0;

// Controle do bipe nao bloqueante
bool buzzerLigado = false;
unsigned long inicioBipe = 0;
const unsigned long DURACAO_BIPE_MS = 250;

// Estatistica simples da sessao
unsigned long tempoTotalPosturaRuimMs = 0;
int totalAlertas = 0;

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PINO_SENSOR_FRENTE, INPUT_PULLUP);
  pinMode(PINO_SENSOR_LATERAL, INPUT_PULLUP);
  pinMode(PINO_BUZZER, OUTPUT);
  pinMode(PINO_LED, OUTPUT);

  digitalWrite(PINO_BUZZER, LOW);
  digitalWrite(PINO_LED, LOW);

  Serial.println();
  Serial.println("=================================================");
  Serial.println(" MONITOR DE POSTURA - ESP32");
  Serial.printf(" Dispositivo: %s\n", DISPOSITIVO_ID);
  Serial.println("=================================================");

  conectarWiFi();

  Serial.println("Monitorando. Sente-se ereto para calibrar a posicao neutra.");
}

// ---------------------------------------------------------------------------
// Loop principal
// ---------------------------------------------------------------------------

void loop() {
  amostrarSensores();
  atualizarBuzzer();
  verificarHeartbeat();
}

// ---------------------------------------------------------------------------
// Leitura e filtragem dos sensores
// ---------------------------------------------------------------------------

// Le o pino ja tratando a inversao configurada no config.h.
// Retorna true quando o sensor esta na posicao que consideramos "inclinado".
bool lerSensor(int pino) {
  bool fechado = (digitalRead(pino) == LOW);
  return SENSOR_LOGICA_INVERTIDA ? !fechado : fechado;
}

void amostrarSensores() {
  unsigned long agora = millis();

  if (agora - ultimaAmostra < 20) {
    return;
  }
  ultimaAmostra = agora;

  if (lerSensor(PINO_SENSOR_FRENTE))  amostrasFrente++;
  if (lerSensor(PINO_SENSOR_LATERAL)) amostrasLateral++;
  totalAmostras++;

  if (totalAmostras >= TOTAL_AMOSTRAS) {
    fecharJanela();
  }
}

// Chamada uma vez por segundo. Transforma o ruido da esfera em uma decisao.
void fecharJanela() {
  int percentualFrente  = (amostrasFrente  * 100) / TOTAL_AMOSTRAS;
  int percentualLateral = (amostrasLateral * 100) / TOTAL_AMOSTRAS;

  inclinadoFrente  = (percentualFrente  >= LIMIAR_PERCENTUAL);
  inclinadoLateral = (percentualLateral >= LIMIAR_PERCENTUAL);

  // Postura ruim se qualquer um dos eixos passou do limite.
  bool ruimAgora = inclinadoFrente || inclinadoLateral;

  avaliarPostura(ruimAgora);

  amostrasFrente = 0;
  amostrasLateral = 0;
  totalAmostras = 0;
}

// ---------------------------------------------------------------------------
// Logica da postura
// ---------------------------------------------------------------------------

void avaliarPostura(bool ruimAgora) {
  unsigned long agora = millis();

  if (ruimAgora && !posturaRuim) {
    // Acabou de entrar em postura ruim: comeca a contar o tempo.
    posturaRuim = true;
    inicioPosturaRuim = agora;
    Serial.println("[postura] inclinacao detectada, contando tempo...");

  } else if (!ruimAgora && posturaRuim) {
    // Voltou para a posicao correta.
    unsigned long duracao = agora - inicioPosturaRuim;
    tempoTotalPosturaRuimMs += duracao;
    posturaRuim = false;

    if (alertaAtivo) {
      alertaAtivo = false;
      digitalWrite(PINO_LED, LOW);
      Serial.printf("[postura] CORRIGIDA apos %lu s\n", duracao / 1000);
      enviarEvento("postura_corrigida", duracao / 1000);
    } else {
      Serial.println("[postura] voltou ao normal antes do alerta");
    }

  } else if (ruimAgora && posturaRuim) {
    // Continua ruim. Ja passou do tempo limite?
    unsigned long duracao = agora - inicioPosturaRuim;

    if (!alertaAtivo && duracao >= TEMPO_PARA_ALERTAR_MS) {
      alertaAtivo = true;
      totalAlertas++;
      digitalWrite(PINO_LED, HIGH);

      const char* eixo = descreverEixo();
      Serial.printf("[ALERTA] postura ruim ha %lu s (%s)\n", duracao / 1000, eixo);

      dispararBipe();
      ultimoBipe = agora;
      enviarEvento("alerta_postura", duracao / 1000);

    } else if (alertaAtivo && agora - ultimoBipe >= INTERVALO_ALERTA_MS) {
      // Continua torto: lembra a pessoa de novo.
      dispararBipe();
      ultimoBipe = agora;
    }
  }
}

const char* descreverEixo() {
  if (inclinadoFrente && inclinadoLateral) return "frente e lateral";
  if (inclinadoFrente)  return "frente";
  if (inclinadoLateral) return "lateral";
  return "nenhum";
}

// ---------------------------------------------------------------------------
// Buzzer (nao bloqueante, para nao travar a leitura dos sensores)
// ---------------------------------------------------------------------------

void dispararBipe() {
  buzzerLigado = true;
  inicioBipe = millis();

  if (BUZZER_ATIVO) {
    digitalWrite(PINO_BUZZER, HIGH);
  }
}

void atualizarBuzzer() {
  if (!buzzerLigado) {
    return;
  }

  if (millis() - inicioBipe >= DURACAO_BIPE_MS) {
    buzzerLigado = false;
    digitalWrite(PINO_BUZZER, LOW);
    return;
  }

  if (!BUZZER_ATIVO) {
    // Buzzer passivo nao apita com tensao constante: precisa de onda quadrada.
    // Alternamos o pino a ~2 kHz enquanto o bipe estiver ativo.
    digitalWrite(PINO_BUZZER, !digitalRead(PINO_BUZZER));
    delayMicroseconds(250);
  }
}

// ---------------------------------------------------------------------------
// Rede
// ---------------------------------------------------------------------------

void conectarWiFi() {
  Serial.printf("Conectando em \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);

  // Tenta por 15 segundos. Se falhar, segue offline (buzzer continua funcionando).
  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi conectado. IP do ESP32: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi NAO conectou. Rodando em modo offline.");
    Serial.println("(o alerta sonoro continua funcionando normalmente)");
  }
}

void verificarHeartbeat() {
  unsigned long agora = millis();

  if (agora - ultimoHeartbeat < INTERVALO_HEARTBEAT_MS) {
    return;
  }
  ultimoHeartbeat = agora;

  // Reconecta se a rede caiu.
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    return;
  }

  unsigned long duracaoAtual = posturaRuim ? (agora - inicioPosturaRuim) / 1000 : 0;
  enviarEvento("status", duracaoAtual);
}

void enviarEvento(const char* tipo, unsigned long duracaoSegundos) {
  if (WiFi.status() != WL_CONNECTED) {
    return;  // offline: apenas ignora o envio
  }

  // Monta o JSON na mao para nao depender de biblioteca externa.
  String json = "{";
  json += "\"dispositivo\":\"" + String(DISPOSITIVO_ID) + "\",";
  json += "\"tipo\":\"" + String(tipo) + "\",";
  json += "\"inclinado_frente\":"  + String(inclinadoFrente  ? "true" : "false") + ",";
  json += "\"inclinado_lateral\":" + String(inclinadoLateral ? "true" : "false") + ",";
  json += "\"duracao_s\":" + String(duracaoSegundos) + ",";
  json += "\"total_alertas\":" + String(totalAlertas) + ",";
  json += "\"uptime_s\":" + String(millis() / 1000);
  json += "}";

  HTTPClient http;
  http.begin(SERVIDOR_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(3000);

  int codigo = http.POST(json);

  if (codigo > 0) {
    Serial.printf("[http] %s enviado (HTTP %d)\n", tipo, codigo);
  } else {
    Serial.printf("[http] falha ao enviar %s: %s\n",
                  tipo, http.errorToString(codigo).c_str());
  }

  http.end();
}
