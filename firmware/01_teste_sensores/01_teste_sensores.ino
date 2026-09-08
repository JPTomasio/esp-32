/*
 * ETAPA 1 - Teste dos sensores SW-520D
 *
 * Objetivo: confirmar que os dois sensores estao ligados certo e respondendo.
 * Nao tem WiFi, nao tem logica de postura. So le e imprime.
 *
 * Como usar:
 *   1. Grave este sketch no ESP32
 *   2. Abra o Monitor Serial em 115200
 *   3. Incline cada sensor com a mao e veja os valores mudarem de 0 para 1
 *
 * LIGACAO (modulo SW-520D de 3 pinos: VCC / GND / DO):
 *
 *   Sensor A (frente/costas)      Sensor B (lateral)
 *   VCC -> 3V3                    VCC -> 3V3
 *   GND -> GND                    GND -> GND
 *   DO  -> GPIO 14                DO  -> GPIO 27
 *
 * Se o seu modulo tiver 4 pinos (VCC/GND/DO/AO), ignore o AO.
 */

const int PINO_SENSOR_A = 14;
const int PINO_SENSOR_B = 27;

// A esfera do SW-520D balanca muito. Amostramos rapido e contamos quantas
// leituras deram "inclinado" no ultimo segundo, em vez de confiar em 1 leitura.
const unsigned long INTERVALO_AMOSTRA_MS = 20;
const int TOTAL_AMOSTRAS = 50;  // 50 x 20ms = janela de 1 segundo

int amostrasAtivasA = 0;
int amostrasAtivasB = 0;
int contadorAmostras = 0;
unsigned long ultimaAmostra = 0;

void setup() {
  Serial.begin(115200);
  delay(500);

  // INPUT_PULLUP: o pino fica em HIGH quando o circuito esta aberto.
  // Com o switch fechado, o pino e puxado para LOW.
  pinMode(PINO_SENSOR_A, INPUT_PULLUP);
  pinMode(PINO_SENSOR_B, INPUT_PULLUP);

  Serial.println();
  Serial.println("=================================================");
  Serial.println(" TESTE DOS SENSORES SW-520D");
  Serial.println(" Incline cada sensor com a mao e observe a saida.");
  Serial.println("=================================================");
  Serial.println();
  Serial.println("bruto_A | bruto_B | ativo_A% | ativo_B%");
}

void loop() {
  unsigned long agora = millis();

  if (agora - ultimaAmostra < INTERVALO_AMOSTRA_MS) {
    return;
  }
  ultimaAmostra = agora;

  // Leitura crua. LOW (0) = switch fechado.
  int brutoA = digitalRead(PINO_SENSOR_A);
  int brutoB = digitalRead(PINO_SENSOR_B);

  // Consideramos "ativo" quando o switch esta fechado (LOW).
  if (brutoA == LOW) amostrasAtivasA++;
  if (brutoB == LOW) amostrasAtivasB++;
  contadorAmostras++;

  // A cada janela cheia (1 segundo), imprime o resumo.
  if (contadorAmostras >= TOTAL_AMOSTRAS) {
    int percentualA = (amostrasAtivasA * 100) / TOTAL_AMOSTRAS;
    int percentualB = (amostrasAtivasB * 100) / TOTAL_AMOSTRAS;

    Serial.printf("   %d    |    %d    |   %3d%%   |   %3d%%\n",
                  brutoA, brutoB, percentualA, percentualB);

    amostrasAtivasA = 0;
    amostrasAtivasB = 0;
    contadorAmostras = 0;
  }
}
