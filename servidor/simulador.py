"""
Simulador do ESP32.

Serve para testar o servidor e o dashboard sem precisar da placa montada.
Gera eventos falsos de postura, como se um ESP32 real estivesse enviando.

Como usar (em dois terminais):
    terminal 1:  python app.py
    terminal 2:  python simulador.py

Com a nuvem configurada, os eventos gerados aqui sobem para o Supabase igual
aos de um ESP32 de verdade -- e assim que as evidencias da Etapa 4 sao geradas
sem a placa montada.
"""

import os
import random
import time

import requests

# Configuraveis por variavel de ambiente para o script de evidencias poder usar
# um nome de dispositivo diferente em cada execucao. Isso importa por causa da
# nuvem: a tabela tem UNIQUE (dispositivo, id_local), entao repetir o mesmo
# nome com um banco local recriado sobrescreveria as linhas da rodada anterior
# em vez de acrescentar novas.
URL = os.environ.get("SIMULADOR_URL", "http://localhost:5000/api/eventos")
DISPOSITIVO = os.environ.get("SIMULADOR_DISPOSITIVO", "esp32-simulado")


def enviar(tipo, frente=False, lateral=False, duracao=0, alertas=0, uptime=0):
    corpo = {
        "dispositivo": DISPOSITIVO,
        "tipo": tipo,
        "inclinado_frente": frente,
        "inclinado_lateral": lateral,
        "duracao_s": duracao,
        "total_alertas": alertas,
        "uptime_s": uptime,
    }
    try:
        resposta = requests.post(URL, json=corpo, timeout=3)
        print(f"enviado {tipo:20} -> HTTP {resposta.status_code}")
    except requests.RequestException as erro:
        print(f"falha ao enviar: {erro}")
        print("O servidor esta rodando? (python app.py)")


def main():
    print("Simulando um ESP32. Ctrl+C para parar.\n")

    alertas = 0
    inicio = time.time()

    while True:
        uptime = int(time.time() - inicio)

        # A cada ciclo, 40% de chance de a "pessoa" torcer a postura.
        if random.random() < 0.4:
            # Todo alerta tem ao menos um eixo inclinado, como no firmware real.
            frente, lateral = random.choice(
                [(True, False), (True, False), (False, True), (True, True)]
            )
            duracao = random.randint(6, 45)
            alertas += 1

            enviar("alerta_postura", frente, lateral, duracao, alertas, uptime)
            time.sleep(4)

            enviar("postura_corrigida", False, False, duracao, alertas, uptime)
        else:
            enviar("status", False, False, 0, alertas, uptime)

        time.sleep(6)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSimulador encerrado.")
