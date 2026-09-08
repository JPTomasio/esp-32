/*
 * Configuracoes do projeto.
 * Edite este arquivo com os dados da sua rede antes de gravar no ESP32.
 */

#ifndef CONFIG_H
#define CONFIG_H

// ---------- WiFi ----------
// O ESP32 so enxerga redes de 2.4 GHz. Nao funciona em rede 5 GHz.
#define WIFI_SSID     "NOME_DA_SUA_REDE"
#define WIFI_SENHA    "SENHA_DA_REDE"

// ---------- Servidor ----------
// IP do computador que esta rodando o servidor Python (servidor/app.py).
// Descubra com "ip a" no Linux ou "ipconfig" no Windows.
// O ESP32 e o computador precisam estar na MESMA rede WiFi.
#define SERVIDOR_URL  "http://192.168.0.100:5000/api/eventos"

// Identificador deste dispositivo. Util se o grupo montar mais de um.
#define DISPOSITIVO_ID "esp32-postura-01"

// ---------- Pinos ----------
#define PINO_SENSOR_FRENTE   14   // sensor colado inclinado para frente
#define PINO_SENSOR_LATERAL  27   // sensor colado na lateral do corpo
#define PINO_BUZZER          26
#define PINO_LED             2    // LED azul embutido na maioria das placas ESP32

// Alguns modulos SW-520D tem a saida invertida (comparador LM393).
// Se no teste da Etapa 1 os valores aparecerem trocados, mude para true.
#define SENSOR_LOGICA_INVERTIDA false

// Coloque false se o seu buzzer for PASSIVO (nao apita sozinho com 3V3).
// Buzzer ativo = so ligar na energia e ele apita. E o mais comum em kits.
#define BUZZER_ATIVO true

// ---------- Parametros da deteccao ----------

// Quanto tempo a postura precisa ficar ruim antes de alertar.
// 5 segundos e bom para testar. Na pratica, use algo entre 15000 e 30000.
#define TEMPO_PARA_ALERTAR_MS 5000

// Janela de filtragem: quantas amostras de 20ms formam uma decisao.
#define TOTAL_AMOSTRAS 50   // 50 x 20ms = 1 segundo

// Percentual de amostras "inclinado" na janela para considerar postura ruim.
// A esfera do SW-520D treme muito, entao exigimos maioria folgada.
#define LIMIAR_PERCENTUAL 70

// Intervalo entre bipes enquanto a postura continua ruim.
#define INTERVALO_ALERTA_MS 3000

// Envio periodico de status para o servidor, mesmo sem mudanca de estado.
#define INTERVALO_HEARTBEAT_MS 30000

#endif
