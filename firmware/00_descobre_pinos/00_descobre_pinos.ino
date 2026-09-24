/*
 * ETAPA 0 - Descobrir em quais pinos os sensores estao ligados
 *
 * Objetivo: quando nao se sabe ao certo em qual GPIO cada fio de sinal (DO)
 * foi espetado, este sketch le todos os pinos seguros da placa ao mesmo tempo
 * e mostra qual deles muda quando o sensor e inclinado.
 * Nao tem WiFi, nao tem logica de postura. So le e imprime.
 *
 * Como usar:
 *   1. Grave este sketch no ESP32
 *   2. Abra o Monitor Serial em 115200
 *   3. Incline UM sensor de cada vez, devagar, varias vezes
 *   4. Anote o GPIO que aparece mudando para cada sensor
 *   5. Coloque esses numeros no config.h (Etapa 2) e no teste da Etapa 1
 *
 * Pinos que NAO sao lidos (e por que):
 *   0, 2, 12, 15  -> pinos de boot (strapping); o 2 e o LED azul da placa
 *   1, 3          -> TX/RX da Serial, usados pelo proprio Monitor Serial
 *   6 a 11        -> ligados a memoria flash, mexer neles trava a placa
 */

// Pinos com pull-up interno: entrada normal.
const int PINOS_PULLUP[] = {4, 5, 13, 14, 16, 17, 18, 19, 21, 22, 23,
                            25, 26, 27, 32, 33};

// Pinos 34, 35, 36 (VP) e 39 (VN) sao so de entrada e nao tem pull-up interno.
// O modulo SW-520D de 3 pinos ja tem resistor de pull-up proprio, entao
// funciona. Sem nada ligado, esses pinos ficam "flutuando" e trocam de valor
// a cada leitura por causa do ruido -- o filtro de estabilidade abaixo e o que
// impede que isso encha a tela.
const int PINOS_SO_ENTRADA[] = {34, 35, 36, 39};

const int QTD_PULLUP = sizeof(PINOS_PULLUP) / sizeof(PINOS_PULLUP[0]);
const int QTD_SO_ENTRADA = sizeof(PINOS_SO_ENTRADA) / sizeof(PINOS_SO_ENTRADA[0]);
const int QTD_PINOS = QTD_PULLUP + QTD_SO_ENTRADA;

const unsigned long INTERVALO_AMOSTRA_MS = 20;
const unsigned long INTERVALO_RESUMO_MS = 5000;

// So conta uma mudanca quando o pino fica no valor novo por 5 leituras
// seguidas (100 ms). Pino solto oscila a cada leitura e nunca passa disso;
// sensor inclinado de verdade passa.
const int LEITURAS_ESTAVEIS = 5;

int pinos[QTD_PINOS];
int ultimoValor[QTD_PINOS];
int mudancas[QTD_PINOS];
int leiturasNovas[QTD_PINOS];  // leituras seguidas no valor novo, ainda nao confirmado

unsigned long ultimaAmostra = 0;
unsigned long ultimoResumo = 0;

void setup() {
  Serial.begin(115200);
  delay(500);

  int i = 0;
  for (int k = 0; k < QTD_PULLUP; k++) {
    pinos[i] = PINOS_PULLUP[k];
    pinMode(pinos[i], INPUT_PULLUP);
    i++;
  }
  for (int k = 0; k < QTD_SO_ENTRADA; k++) {
    pinos[i] = PINOS_SO_ENTRADA[k];
    pinMode(pinos[i], INPUT);
    i++;
  }

  delay(50);  // deixa os pull-ups estabilizarem antes da primeira leitura

  for (int k = 0; k < QTD_PINOS; k++) {
    ultimoValor[k] = digitalRead(pinos[k]);
    mudancas[k] = 0;
    leiturasNovas[k] = 0;
  }

  Serial.println();
  Serial.println("=================================================");
  Serial.println(" DESCOBRIR OS PINOS DOS SENSORES");
  Serial.println(" Incline UM sensor de cada vez e veja qual GPIO muda.");
  Serial.println("=================================================");
  Serial.println();
}

void loop() {
  unsigned long agora = millis();

  if (agora - ultimaAmostra >= INTERVALO_AMOSTRA_MS) {
    ultimaAmostra = agora;

    for (int k = 0; k < QTD_PINOS; k++) {
      int valor = digitalRead(pinos[k]);
      if (valor == ultimoValor[k]) {
        leiturasNovas[k] = 0;
        continue;
      }
      leiturasNovas[k]++;
      if (leiturasNovas[k] >= LEITURAS_ESTAVEIS) {
        leiturasNovas[k] = 0;
        mudancas[k]++;
        Serial.printf("GPIO %d mudou: %d -> %d (mudancas: %d)\n",
                      pinos[k], ultimoValor[k], valor, mudancas[k]);
        ultimoValor[k] = valor;
      }
    }
  }

  // Resumo periodico: a esfera treme e gera muitas linhas, entao de tempos
  // em tempos mostramos so quem mudou e quantas vezes.
  if (agora - ultimoResumo >= INTERVALO_RESUMO_MS) {
    ultimoResumo = agora;

    Serial.print("--- resumo: ");
    bool algum = false;
    for (int k = 0; k < QTD_PINOS; k++) {
      if (mudancas[k] > 0) {
        Serial.printf("GPIO %d = %d mudancas; ", pinos[k], mudancas[k]);
        algum = true;
      }
    }
    if (!algum) {
      Serial.print("nenhum pino mudou ainda, incline um sensor");
    }
    Serial.println(" ---");
  }
}
