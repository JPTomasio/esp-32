# Monitor de Postura — ESP32 + SW-520D

Projeto de extensão. Detecta quando o usuário fica em inclinação inadequada
por tempo prolongado, alerta com buzzer, avisa no celular e registra os eventos
na nuvem, com dashboard em tempo real.

```
SW-520D  →  ESP32  →  HTTP/JSON (Wi-Fi)  →  Python (gateway Flask)
                                               ├──→ SQLite local  (fila de envio)
                                               ├──→ HTTPS/REST  →  Supabase / PostgreSQL
                                               │                        ↓
                                               │                    Dashboard
                                               └──→ HTTPS/REST  →  Telegram  →  celular
```

O documento da entrega da integração com cloud (`docs/Entrega_4_Resposta.md`)
não é versionado — veja **Documentos das entregas**, no fim deste arquivo.

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
(`postura.db`, criado sozinho), replica para a nuvem e mostra um dashboard que
atualiza a cada 2 s.

**Para testar sem a placa montada**, em outro terminal:

```bash
python simulador.py
```

Ele gera eventos falsos como se fosse um ESP32 real — útil para desenvolver o
dashboard enquanto a parte física não está pronta. Com a nuvem configurada,
esses eventos sobem para o Supabase igual aos de uma placa de verdade.

### Etapa 4 — nuvem (Supabase)

O servidor Python deixa de ser o destino dos dados e passa a ser um **gateway**:
grava no SQLite local e replica cada evento para um PostgreSQL na nuvem. O
dashboard então lê da nuvem.

**1. Criar o projeto**

<https://supabase.com> → *Start your project* → login com GitHub → *New project*.
Região **South America (São Paulo)**. Plano gratuito, não pede cartão.

**2. Criar a estrutura do banco**

No painel do projeto: *SQL Editor* → *New query* → cole o conteúdo de
`servidor/esquema_supabase.sql` → *Run*. Isso cria a tabela `eventos`, os
índices, a view `estatisticas_24h` e liga o RLS.

**3. Configurar as credenciais**

```bash
cp servidor/.env.exemplo servidor/.env
```

Os valores estão em *Project Settings → API*: **Project URL** e a chave
**`service_role`**.

O `.env` está no `.gitignore` de propósito, junto com o `config.h`: a chave
`service_role` dá acesso total ao banco e **este repositório é público**.

**4. Rodar**

```bash
cd servidor && python app.py
```

O servidor imprime na subida se conseguiu falar com a nuvem. Para conferir
depois: <http://localhost:5000/api/nuvem>.

**O sistema funciona sem nuvem.** Sem o `.env`, tudo roda local como na Etapa 3
— o dashboard mostra um selo amarelo avisando que os dados vieram do banco
local. E com a nuvem configurada, se a internet cair no meio do uso, o buzzer
continua alertando e os eventos ficam numa fila no SQLite (coluna
`enviado_nuvem = 0`), que sobe sozinha quando a conexão volta. Nada se perde.

O dashboard mostra sempre de onde vieram os números na tela, em vez de fingir
que está tudo bem.

### Etapa 5 — aviso no celular (Telegram)

O buzzer avisa quem está usando a cinta. Esta etapa manda a mesma informação
para o celular, onde ela pode ser vista depois e mostrada para outra pessoa.
É opcional: sem configurar, o resto do sistema roda igual.

**Por que Telegram e não WhatsApp:** o WhatsApp só permite envio automático
pela API oficial da Meta, que exige conta comercial verificada, número
dedicado e mensagens em modelos aprovados antes. Nada disso cabe num projeto
de extensão. O bot do Telegram sai em dois minutos e o envio é um POST HTTPS,
exatamente o que o projeto já faz com o Supabase.

**1. Criar o bot**

No Telegram, fale com o **@BotFather** → `/newbot` → escolha um nome. Ele
responde com o token, algo como `123456789:AAH...`.

**2. Descobrir para quem mandar**

Mande qualquer mensagem para o bot que você acabou de criar — o Telegram não
deixa um bot escrever primeiro para alguém. Depois abra no navegador,
trocando `<token>` pelo seu:

```
https://api.telegram.org/bot<token>/getUpdates
```

Procure `"chat":{"id":987654321` — esse número é o destino. Para avisar o
grupo todo, crie um grupo, coloque o bot dentro e use o id do grupo (vem
negativo, é normal).

**3. Preencher o `.env`**

No mesmo `servidor/.env` do Supabase:

```
TELEGRAM_TOKEN=123456789:AAH...
TELEGRAM_CHAT_ID=987654321
```

O token dá controle total do bot, então ele fica **só no servidor Python**:
nunca vai para o navegador nem para o firmware. O `.env` está no `.gitignore`.

**4. Rodar**

```bash
cd servidor && python app.py
```

O servidor imprime na subida se conseguiu falar com o bot. Para conferir
depois: <http://localhost:5000/api/notificacao>. O dashboard ganha um cartão
mostrando quantos avisos já saíram.

Só **alerta de postura** vira mensagem: correção de postura é boa notícia e o
heartbeat é de 30 em 30 segundos — notificar os dois só ensinaria a pessoa a
ignorar o aviso. E existe um intervalo mínimo entre mensagens (padrão 120 s,
ajustável em `INTERVALO_NOTIFICACAO_S`), porque quem está com a cinta entorta
e endireita várias vezes seguidas.

O envio acontece numa thread separada, pelo mesmo motivo do envio para a
nuvem: o ESP32 recebe a resposta na hora, sem ficar preso esperando a
internet. **Se o Telegram estiver fora do ar, o aviso é descartado** — ao
contrário dos eventos, que ficam na fila do SQLite. Um aviso de postura que
chega meia hora depois não serve para nada; o evento em si não se perde,
continua no banco e na nuvem.

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
│   ├── app.py                 gateway Flask + fila SQLite + dashboard
│   ├── nuvem.py               integração com o Supabase (única parte que
│   │                          fala com a nuvem)
│   ├── notifica.py            aviso no celular (única parte que fala com o
│   │                          Telegram)
│   ├── esquema_supabase.sql   tabela, índices, view e RLS da nuvem
│   ├── .env.exemplo           modelo das credenciais (o .env não é versionado)
│   ├── simulador.py           gera eventos falsos para teste
│   └── requirements.txt
├── testes/                    testes que rodam no PC, sem a placa
├── evidencias/                script que gera as provas de funcionamento
├── ferramentas/               conversor dos documentos para PDF
├── docs/                      documentos das entregas (não versionado)
└── README.md
```

Os testes em `testes/` validam a lógica do firmware, as consultas do servidor,
a integração com a nuvem e o aviso no celular sem precisar do hardware montado
nem de internet. Veja `testes/README.md`.

## Evidencias de funcionamento

Para a entrega, existe um script que gera automaticamente as provas de que o
sistema funciona, sem precisar da placa montada:

```bash
uv venv .venv-evidencias
uv pip install --python .venv-evidencias -r servidor/requirements.txt
bash evidencias/gera_evidencias.sh
```

> Sem o `uv`, um venv comum resolve:
> `python3 -m venv .venv-evidencias && .venv-evidencias/bin/pip install -r servidor/requirements.txt`

Em cerca de um minuto ele roda os quatro testes, sobe o servidor num banco
separado (nao encosta no `postura.db` do grupo), grava as chamadas HTTP que o
ESP32 faz, roda o simulador por 45 s, captura o dashboard nos estados de alerta
e de postura correta, despeja o conteudo do banco local, **consulta o Supabase
com `curl` para provar que os dados chegaram na nuvem** e registra o **aviso
enviado para o celular**. Tudo vai para `evidencias/saida/`, com um `RESUMO.md`
explicando o que cada arquivo prova.

Se `servidor/.env` nao estiver configurado, as etapas que dependem de internet
sao puladas com uma explicacao no log e o resto roda normalmente -- inclusive o
teste da notificacao, que usa um Telegram simulado.

A pasta `evidencias/saida/` **nao e versionada** — o script e regenera em um
minuto, e a saida inclui um banco binario. Rode o script antes da entrega e
anexe a pasta, ou gere na hora da apresentacao.

Antes de fechar, o script limpa os logs: IPs da rede local viram
`[ip-local-omitido]`, caminhos absolutos viram relativos, os codigos de cor do
Flask sao removidos e **a chave do Supabase e o token do bot sao apagados**.
Assim a pasta pode ser entregue ou versionada sem levar junto o IP da sua
maquina, o caminho da sua pasta pessoal nem as credenciais. No final o script
confere de novo se algum segredo vazou em algum arquivo e aborta se encontrar.

O que o script **nao** cobre: o print do painel do Supabase (exige login), o
print da conversa no celular (exige a tela do aparelho) e as evidencias que
dependem do hardware montado -- log do Monitor Serial, video do buzzer
disparando e a foto da montagem com o angulo-limite. O `RESUMO.md`
gerado lista essas pendencias com instrucoes, inclusive o nome do dispositivo
para filtrar no painel da nuvem.

## Documentos das entregas

Os documentos ficam em `docs/`, que **não é versionado**: os enunciados em PDF
são material do professor, e o documento do grupo traz nome completo e matrícula
dos seis integrantes — este repositório é público.

Quem for editar precisa da pasta compartilhada pelo grupo. O documento é escrito
em Markdown e convertido para PDF por `ferramentas/md_para_pdf.py`:

```bash
uv pip install --python .venv-evidencias markdown weasyprint
.venv-evidencias/bin/python ferramentas/md_para_pdf.py docs/Entrega_4_Resposta.md
```

O Markdown é a fonte: edite o `.md` e gere o PDF de novo, nunca o contrário.

## O que ainda falta

- Alimentação por bateria, para o protótipo não ficar preso ao cabo USB.
- Definir e documentar o ângulo-limite escolhido pelo grupo, com foto da
  montagem — é a única documentação possível da calibragem, já que o SW-520D
  não mede ângulo. Vale como evidência na entrega.
