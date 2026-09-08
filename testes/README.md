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

Os dois retornam código de saída 0 quando passam.
