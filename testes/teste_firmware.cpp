// Testa a maquina de estados do firmware real, incluindo o .ino de verdade.
#include "Arduino.h"
#include <vector>

unsigned long g_millis = 0;
std::map<int,int> g_pinos;
SerialMock Serial;
WiFiMock WiFi;
int g_escritas_buzzer_high = 0;
int g_pino_buzzer_observado = 26;

// Inclui o firmware entregue, sem alterar nada nele.
#include "02_monitor_postura.ino"

static int falhas = 0;
void check(const char* nome, long obtido, long esperado){
  bool ok = obtido == esperado;
  printf("  %s  %s: %ld%s\n", ok?"PASS":"FALHA", nome, obtido,
         ok?"":(" (esperado " + std::to_string(esperado) + ")").c_str());
  if(!ok) falhas++;
}

// Avanca o tempo simulado rodando loop(), com os sensores na posicao dada.
void avancar(unsigned long ms, bool frente, bool lateral){
  g_pinos[PINO_SENSOR_FRENTE]  = frente  ? LOW : HIGH;  // LOW = switch fechado
  g_pinos[PINO_SENSOR_LATERAL] = lateral ? LOW : HIGH;
  unsigned long fim = g_millis + ms;
  while(g_millis < fim){ g_millis += 5; loop(); }
}

int main(){
  setup();
  printf("Simulando uma sessao de uso (TEMPO_PARA_ALERTAR_MS=%d):\n",
         TEMPO_PARA_ALERTAR_MS);

  // 1. Postura correta por 10s: nada deve acontecer.
  avancar(10000, false, false);
  check("sem alerta com postura correta", totalAlertas, 0);
  check("LED apagado", g_pinos[PINO_LED], LOW);

  // 2. Curva para frente por 3s (abaixo do limite de 5s): nao deve alertar.
  avancar(3000, true, false);
  check("nao alerta antes do tempo limite", totalAlertas, 0);
  avancar(2000, false, false);  // corrige a tempo
  check("continua sem alerta apos corrigir", totalAlertas, 0);

  // 3. Curva para frente e fica 8s: DEVE alertar.
  g_escritas_buzzer_high = 0;
  avancar(8000, true, false);
  check("alertou apos passar do limite", totalAlertas, 1);
  check("LED aceso durante o alerta", g_pinos[PINO_LED], HIGH);
  check("eixo detectado e o da frente", inclinadoFrente, 1);
  check("eixo lateral nao disparou", inclinadoLateral, 0);

  // 4. Continua torto por mais 7s: deve repetir o bipe (intervalo de 3s),
  //    mas sem contar um novo alerta.
  int bipesAntes = g_escritas_buzzer_high;
  avancar(7000, true, false);
  check("nao conta alerta duplicado enquanto segue torto", totalAlertas, 1);
  bool repetiu = g_escritas_buzzer_high > bipesAntes;
  check("bipe repetiu durante o alerta", repetiu, 1);

  // 5. Endireita: alerta desliga.
  avancar(3000, false, false);
  check("LED apaga ao corrigir a postura", g_pinos[PINO_LED], LOW);
  check("alertaAtivo zerado", alertaAtivo, 0);

  // 6. Pende para o lado por 8s: alerta pelo eixo lateral.
  avancar(8000, false, true);
  check("segundo alerta, pelo eixo lateral", totalAlertas, 2);
  check("eixo lateral detectado", inclinadoLateral, 1);
  check("eixo frente nao disparou", inclinadoFrente, 0);
  avancar(2000, false, false);

  // 7. Ruido: sensor tremendo (alterna a cada 100ms) nao deve alertar,
  //    porque o filtro exige 70% das amostras da janela.
  int alertasAntes = totalAlertas;
  for(int i = 0; i < 60; i++) avancar(100, i % 2 == 0, false);
  check("ruido do sensor nao gera alerta falso", totalAlertas, alertasAntes);

  // 8. Contabilidade do tempo total torto (nao conta o ruido filtrado).
  bool tempoRegistrado = tempoTotalPosturaRuimMs > 0;
  check("tempo torto acumulado foi registrado", tempoRegistrado, 1);

  printf("\n%s\n", falhas ? "HOUVE FALHAS" : "Todos os testes passaram.");
  return falhas ? 1 : 0;
}
