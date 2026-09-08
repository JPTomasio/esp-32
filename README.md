# Monitor de Postura — ESP32 + SW-520D

Projeto de extensão. Detecta quando o usuário fica em inclinação inadequada
por tempo prolongado, alerta com buzzer e registra os eventos em um servidor
Python com dashboard.

## Componentes

- 1x ESP32 (DevKit v1 ou similar)
- 2x módulo sensor de inclinação/vibração SW-520D
- 1x buzzer (ativo, de preferência)
- Protoboard + jumpers
- Cinta/elástico para prender os sensores no corpo

## Como o SW-520D funciona (leia antes de montar)

O SW-520D **não mede ângulo**. Ele é um interruptor de esfera: dois contatos
que fecham ou abrem conforme a orientação física do sensor. A saída é digital,
0 ou 1 — não sai número em graus.

Por isso o "limite de inclinação" do projeto é definido **pela posição em que
você cola o sensor**, e não por software:

- **Sensor da frente** — colado inclinado para frente nas costas. Gire-o até
  que ele mude de estado exatamente quando a pessoa curva o tronco no ângulo
  que o grupo escolheu como limite.
- **Sensor lateral** — mesma ideia, girado 90°, para detectar o corpo pendendo
  para o lado.

A esfera dentro do sensor treme muito ao menor movimento. O firmware trata isso
com um filtro: amostra a cada 20 ms e só considera "inclinado" quando 70% das
amostras de 1 segundo concordam. Sem esse filtro, o buzzer apitaria sem parar.

> Se depois o grupo quiser inclinação em graus de verdade, o componente certo é
> o **MPU-6050** (acelerômetro + giroscópio, ~R$15). O código está organizado
> para trocar só a parte da leitura, sem reescrever o resto.

## Ligação

```
   ESP32                Sensor frente      Sensor lateral      Buzzer
   -----                -------------      --------------      ------
   3V3   ------------->  VCC          ---->  VCC
   GND   ------------->  GND          ---->  GND          ---->  (-)
   GPIO 14 <-----------  DO
   GPIO 27 <--------------------------------  DO
   GPIO 26 ------------------------------------------------->   (+)
```

O LED de alerta usa o LED azul embutido na placa (GPIO 2), não precisa ligar nada.

## Ordem de execução

### Etapa 1 — testar os sensores

```
firmware/01_teste_sensores/01_teste_sensores.ino
```

Grave, abra o Monitor Serial em **115200** e incline cada sensor com a mão.
Os percentuais devem ir de ~0% para ~100%. É aqui que você descobre se algum
sensor está com a lógica invertida.

Se os valores aparecerem trocados, mude `SENSOR_LOGICA_INVERTIDA` para `true`
no `config.h` da Etapa 2.

### Etapa 2 — firmware principal

```
firmware/02_monitor_postura/
├── 02_monitor_postura.ino
├── config.exemplo.h      <- modelo versionado
└── config.h              <- crie a partir do modelo e edite
```

**Antes de gravar, crie o seu `config.h`:**

```bash
cp firmware/02_monitor_postura/config.exemplo.h \
   firmware/02_monitor_postura/config.h
```

O `config.h` está no `.gitignore` de propósito: ele guarda a senha do WiFi e
**este repositório é público**. Nunca versione esse arquivo. Se precisar mudar
algum parâmetro para o grupo todo, altere o `config.exemplo.h`.

No `config.h`, preencha:

- `WIFI_SSID` e `WIFI_SENHA` — o ESP32 **só funciona em rede 2.4 GHz**, não pega 5 GHz
- `SERVIDOR_URL` — IP do computador que vai rodar o servidor Python
- `TEMPO_PARA_ALERTAR_MS` — está em 5 s para facilitar o teste; para uso real,
  algo entre 15 s e 30 s

O sistema **funciona sem WiFi**. Se a rede não conectar, o buzzer e o LED
continuam alertando normalmente; só o envio de dados fica desativado. Dá para
apresentar o protótipo mesmo sem internet no laboratório.

### Etapa 3 — servidor Python

```bash
cd servidor
pip install -r requirements.txt
python app.py
```

Abre em <http://localhost:5000>. Recebe os eventos do ESP32, grava num SQLite
(`postura.db`, criado sozinho) e mostra um dashboard que atualiza a cada 2 s.

**Para testar sem a placa montada**, em outro terminal:

```bash
python simulador.py
```

Ele gera eventos falsos como se fosse um ESP32 real — útil para desenvolver o
dashboard enquanto a parte física não está pronta.

### Descobrir o IP do computador

```bash
ip a          # Linux — procure algo como 192.168.x.x
ipconfig      # Windows
```

O ESP32 e o computador precisam estar **na mesma rede WiFi**. Se o dashboard
não receber nada, é quase sempre firewall bloqueando a porta 5000.

## Estrutura

```
esp32/
├── firmware/
│   ├── 01_teste_sensores/     validação dos sensores, sem WiFi
│   └── 02_monitor_postura/    firmware completo + config.h
├── servidor/
│   ├── app.py                 API Flask + SQLite + dashboard
│   ├── simulador.py           gera eventos falsos para teste
│   └── requirements.txt
├── testes/                    testes que rodam no PC, sem a placa
└── README.md
```

Os testes em `testes/` validam a lógica do firmware e as consultas do servidor
sem precisar do hardware montado. Veja `testes/README.md`.

## O que ainda falta

- Notificação no celular (Telegram/WhatsApp). O ponto de entrada já está
  marcado em `app.py`, na função `receber_evento`.
- Alimentação por bateria, para o protótipo não ficar preso ao cabo USB.
- Definir e documentar o ângulo-limite escolhido pelo grupo, com foto da
  montagem — vale como evidência na entrega.
