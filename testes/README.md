# Testes

Testes que rodam no PC, sem precisar do ESP32 montado.

## Lógica do firmware

Compila o firmware de verdade (`02_monitor_postura.ino`) usando mocks das
funções do Arduino e simula uma sessão de uso: postura correta, inclinação
curta, inclinação longa, correção e sensor tremendo.

```bash
cd testes
g++ -std=c++17 -I mocks -I ../firmware/02_monitor_postura \
    -x c++ teste_firmware.cpp -o teste_firmware
./teste_firmware
```

O caso mais importante é o último: a esfera do SW-520D treme muito, e o teste
confirma que o filtro de janela não deixa isso virar alerta falso.

## Consultas do servidor

Testa o esquema do banco e as agregações do dashboard direto no SQLite, sem
precisar do Flask instalado.

```bash
python3 testes/teste_sql.py
```

## Integração com a nuvem

Testa o caminho até o Supabase **sem precisar de internet**. O teste sobe um
servidor HTTP local que imita o PostgREST do Supabase e aponta o `SUPABASE_URL`
para ele.

```bash
python testes/teste_nuvem.py
```

Precisa do `flask` e do `requests` instalados (`servidor/requirements.txt`).

Verifica o formato exato do JSON que sai para a nuvem, os cabeçalhos de
autenticação, as consultas que o dashboard faz e três comportamentos que só
aparecem quando algo dá errado:

- **nuvem fora do ar** — o ESP32 ainda recebe `201` e o evento fica na fila;
- **nuvem de volta** — o pendente sobe sozinho, nada se perde;
- **reenvio do mesmo evento** — o upsert atualiza a linha em vez de duplicar.

## Aviso no celular

Testa o caminho até o Telegram **sem precisar de internet nem de um bot**. O
teste sobe um servidor HTTP local que imita a Bot API e aponta o
`TELEGRAM_API_URL` para ele.

```bash
python testes/teste_notificacao.py
```

Precisa do `flask` e do `requests` instalados (`servidor/requirements.txt`).

Mostra no log o texto exato que chega no celular e verifica cinco
comportamentos que decidem se a notificação é útil ou vira incômodo:

- **só alerta notifica** — correção de postura e heartbeat não viram mensagem;
- **intervalo mínimo** — dois alertas seguidos viram uma mensagem só;
- **Telegram fora do ar** — o ESP32 continua recebendo `201`, o evento é
  gravado e a falha fica registrada no gateway;
- **recusa com HTTP 200** — a Bot API responde `{"ok": false}` quando o chat
  está errado ou o bot foi bloqueado, e isso precisa contar como falha;
- **o token não vaza** — nem para o navegador, nem para o `/api/status`, nem
  para as mensagens de erro que vão parar no log da entrega.

Os quatro retornam código de saída 0 quando passam.
