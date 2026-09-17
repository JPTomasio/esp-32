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

Os três retornam código de saída 0 quando passam.
