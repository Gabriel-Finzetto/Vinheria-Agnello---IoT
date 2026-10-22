import math
import re
import time
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from uuid import uuid4

import paho.mqtt.client as mqtt
import requests

from configurar_smart_data_models import configurar_fiware


IP_ADDRESS = "3.135.234.101"
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60
PORT_ORION = 1026
FIWARE_SVC = "smart"
FIWARE_SSP = "/"
DEVICE_NUMBER = "096"
DEVICE_ID = f"device{DEVICE_NUMBER}"
ENTITY_ID = f"urn:ngsi-ld:device:{DEVICE_NUMBER}"
MQTT_TOPIC = f"/TEF/{DEVICE_ID}/attrs"
MQTT_CLIENT_ID = f"sdm_{DEVICE_NUMBER}_{uuid4().hex[:8]}"
URL_ORION = f"http://{IP_ADDRESS}:{PORT_ORION}"
TIMEOUT_HTTP = (3, 5)
MAX_TENTATIVAS = 3
INTERVALO_REENVIO = 2
MAX_LEITURAS_PENDENTES = 120

HEADERS = {
    "fiware-service": FIWARE_SVC,
    "fiware-servicepath": FIWARE_SSP,
    "Content-Type": "application/json",
}

ATRIBUTOS = {
    "t": ("temperature", "CEL"),
    "h": ("relativeHumidity", "P1"),
    "p": ("relativeLightLevel", "P1"),
}


def normalizar_data(texto):
    formato = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
    if not re.fullmatch(formato, texto):
        raise ValueError("A leitura precisa incluir data e hora ISO 8601 com fuso.")
    data = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    return data.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def interpretar_mensagem(payload):
    texto = payload.decode("utf-8").strip()
    if texto in ("s|on", "s|off"):
        return None

    campos = texto.split("|")
    if len(campos) != 7:
        raise ValueError("Formato esperado: timestamp|p|valor|t|valor|h|valor.")

    data_utc = normalizar_data(campos[0])
    valores = {}
    for i in range(1, len(campos), 2):
        atributo = campos[i]
        if atributo not in ATRIBUTOS or atributo in valores:
            raise ValueError("A leitura deve conter p, t e h uma unica vez.")
        valor = float(campos[i + 1])
        if not math.isfinite(valor):
            raise ValueError(f"Valor invalido para {atributo}.")
        if atributo in ("p", "h") and not 0 <= valor <= 100:
            raise ValueError(f"O atributo {atributo} precisa estar entre 0 e 100.")
        valores[atributo] = valor

    return data_utc, valores


def montar_entidades(data_utc, valores):
    entidades = []
    for atributo, (propriedade, unidade) in ATRIBUTOS.items():
        entidades.append({
            "id": f"urn:ngsi-ld:DeviceMeasurement:{DEVICE_NUMBER}:{propriedade}",
            "type": "DeviceMeasurement",
            "controlledProperty": {"type": "Text", "value": propriedade},
            "numValue": {
                "type": "Number",
                "value": valores[atributo],
                "metadata": {
                    "TimeInstant": {"type": "DateTime", "value": data_utc},
                },
            },
            "unit": {"type": "Text", "value": unidade},
            "dateObserved": {"type": "DateTime", "value": data_utc},
            "refDevice": {"type": "Text", "value": ENTITY_ID},
        })
    return entidades


def publicar_entidades(sessao, entidades):
    mensagem = {"actionType": "append", "entities": entidades}
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resposta = sessao.post(
                f"{URL_ORION}/v2/op/update",
                json=mensagem,
                headers=HEADERS,
                timeout=TIMEOUT_HTTP,
            )
            if resposta.status_code == 204:
                return True
            print(f"[FIWARE] HTTP {resposta.status_code}: {resposta.text[:300]}")
            if resposta.status_code != 429 and resposta.status_code < 500:
                return False
        except requests.RequestException as erro:
            print(f"[FIWARE] Falha na tentativa {tentativa}: {erro}")

        if tentativa < MAX_TENTATIVAS:
            time.sleep(INTERVALO_REENVIO)
    return False


def enviar_leitura(sessao, data_utc, valores, ultima_data):
    data_utc = normalizar_data(data_utc)
    data = datetime.fromisoformat(data_utc.replace("Z", "+00:00"))
    if ultima_data is not None:
        anterior = datetime.fromisoformat(ultima_data.replace("Z", "+00:00"))
        if data <= anterior:
            print(f"[MQTT] Leitura repetida ou anterior ignorada: {data_utc}")
            return ultima_data

    if publicar_entidades(sessao, montar_entidades(data_utc, valores)):
        print(
            f"[FIWARE] {data_utc} | Temperatura: {valores['t']:.1f} C"
            f" | Umidade: {valores['h']:.1f}%"
            f" | Luz relativa: {valores['p']:.1f}% | 3 entidades atualizadas."
        )
        return data_utc

    print(f"[FIWARE] Leitura nao enviada apos as tentativas: {data_utc}")
    return ultima_data


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[MQTT] Conexao recusada: {reason_code}")
        return
    resultado, _ = client.subscribe(MQTT_TOPIC, qos=1)
    if resultado == mqtt.MQTT_ERR_SUCCESS:
        print(f"[MQTT] Conectado. Escutando {MQTT_TOPIC}")
    else:
        print(f"[MQTT] Falha ao assinar o topico: {resultado}")


def on_connect_fail(client, userdata):
    print("[MQTT] Servidor indisponivel. Tentando reconectar automaticamente.")


def on_disconnect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[MQTT] Conexao interrompida: {reason_code}. Aguardando reconexao.")


def on_message(client, userdata, msg):
    if msg.retain:
        return
    try:
        leitura = interpretar_mensagem(msg.payload)
        if leitura is not None:
            userdata.put_nowait(leitura)
    except (UnicodeDecodeError, ValueError) as erro:
        print(f"[MQTT] Mensagem ignorada: {erro}")
    except Full:
        print("[FIWARE] Fila cheia. Leitura descartada; confira o servidor.")


def main():
    fila = Queue(maxsize=MAX_LEITURAS_PENDENTES)
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_ID,
        userdata=fila,
    )
    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    ultima_data = None

    print(f"[MQTT] Conectando a {IP_ADDRESS}:{MQTT_PORT}")
    print(f"[FIWARE] Destino: {URL_ORION} | Modelo: DeviceMeasurement")
    print("Mantenha esta janela aberta e execute o Wokwi. Para encerrar: Ctrl+C.")

    try:
        with requests.Session() as sessao:
            for tentativa in range(1, MAX_TENTATIVAS + 1):
                try:
                    configurar_fiware(sessao)
                    print("[FIWARE] Historico DeviceMeasurement configurado. Endpoint antigo desativado.")
                    break
                except (requests.RequestException, RuntimeError, ValueError) as erro:
                    print(f"[FIWARE] Falha ao configurar, tentativa {tentativa}: {erro}")
                    if tentativa == MAX_TENTATIVAS:
                        return
                    time.sleep(INTERVALO_REENVIO)
            client.connect_async(IP_ADDRESS, MQTT_PORT, MQTT_KEEPALIVE)
            client.loop_start()
            while True:
                try:
                    data_utc, valores = fila.get(timeout=1)
                except Empty:
                    continue
                ultima_data = enviar_leitura(sessao, data_utc, valores, ultima_data)
    except KeyboardInterrupt:
        print("\nAdaptador encerrado.")
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
