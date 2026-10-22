import requests


IP_ADDRESS = "3.135.234.101"
PORT_ORION = 1026
PORT_STH = 8666
PORT_IOT_AGENT = 4041
FIWARE_SVC = "smart"
FIWARE_SSP = "/"
DEVICE_NUMBER = "096"
DEVICE_ID = f"device{DEVICE_NUMBER}"
ENTITY_ID_ANTIGO = f"urn:ngsi-ld:device:{DEVICE_NUMBER}"
ENTITY_TYPE_ANTIGO = "device"
ENTITY_TYPE = "DeviceMeasurement"
PROPRIEDADES = ("temperature", "relativeHumidity", "relativeLightLevel")
ENTITY_IDS = tuple(
    f"urn:ngsi-ld:{ENTITY_TYPE}:{DEVICE_NUMBER}:{propriedade}"
    for propriedade in PROPRIEDADES
)
URL_ORION = f"http://{IP_ADDRESS}:{PORT_ORION}"
URL_STH = f"http://{IP_ADDRESS}:{PORT_STH}"
URL_IOT_AGENT = f"http://{IP_ADDRESS}:{PORT_IOT_AGENT}"
DESCRICAO_ASSINATURA = f"CP05 - Historico DeviceMeasurement do {DEVICE_ID}"
TIMEOUT_HTTP = (3, 8)
TAMANHO_PAGINA = 100
HEADERS = {"fiware-service": FIWARE_SVC, "fiware-servicepath": FIWARE_SSP}


def conferir_resposta(resposta, permitidos):
    resposta.raise_for_status()
    if resposta.status_code not in permitidos:
        raise RuntimeError(
            f"Resposta inesperada do FIWARE: HTTP {resposta.status_code} "
            f"em {resposta.url}: {resposta.text[:300]}"
        )


def listar_assinaturas(sessao):
    assinaturas = []
    identificadores = set()
    offset = 0
    while True:
        resposta = sessao.get(
            f"{URL_ORION}/v2/subscriptions",
            params={"limit": TAMANHO_PAGINA, "offset": offset},
            headers=HEADERS,
            timeout=TIMEOUT_HTTP,
        )
        conferir_resposta(resposta, (200,))
        pagina = resposta.json()
        if not isinstance(pagina, list):
            raise RuntimeError("O Orion nao retornou uma lista de assinaturas.")
        for assinatura in pagina:
            identificador = assinatura.get("id")
            if not identificador or identificador in identificadores:
                raise RuntimeError("A paginacao das assinaturas retornou IDs invalidos ou repetidos.")
            identificadores.add(identificador)
            assinaturas.append(assinatura)
        if len(pagina) < TAMANHO_PAGINA:
            return assinaturas
        offset += len(pagina)


def entidades_exatas(assinatura, identificadores, tipo):
    entidades = assinatura.get("subject", {}).get("entities", [])
    if len(entidades) != len(identificadores):
        return False
    return (
        {entidade.get("id") for entidade in entidades} == set(identificadores)
        and all(
            entidade.get("type") == tipo
            and not entidade.get("idPattern")
            and not entidade.get("typePattern")
            for entidade in entidades
        )
    )


def corpo_assinatura():
    return {
        "description": DESCRICAO_ASSINATURA,
        "status": "active",
        "subject": {
            "entities": [{"id": identificador, "type": ENTITY_TYPE} for identificador in ENTITY_IDS],
            "condition": {"attrs": ["numValue", "dateObserved"], "notifyOnMetadataChange": True},
        },
        "notification": {
            "http": {"url": f"{URL_STH}/notify"},
            "attrs": ["numValue"],
            "attrsFormat": "legacy",
            "onlyChangedAttrs": False,
        },
    }


def assinatura_equivalente(assinatura):
    condicao = assinatura.get("subject", {}).get("condition", {})
    notificacao = assinatura.get("notification", {})
    return (
        entidades_exatas(assinatura, ENTITY_IDS, ENTITY_TYPE)
        and "dateObserved" in condicao.get("attrs", [])
        and notificacao.get("attrs") == ["numValue"]
        and notificacao.get("attrsFormat") == "legacy"
        and notificacao.get("http", {}).get("url") == f"{URL_STH}/notify"
        and not notificacao.get("onlyChangedAttrs", False)
        and not assinatura.get("throttling")
    )


def garantir_assinatura(sessao, assinaturas):
    for assinatura in assinaturas:
        if assinatura.get("status") == "active" and assinatura_equivalente(assinatura):
            return

    for assinatura in assinaturas:
        if (
            assinatura.get("description") == DESCRICAO_ASSINATURA
            and entidades_exatas(assinatura, ENTITY_IDS, ENTITY_TYPE)
        ):
            resposta = sessao.patch(
                f"{URL_ORION}/v2/subscriptions/{assinatura['id']}",
                json=corpo_assinatura(),
                headers=HEADERS,
                timeout=TIMEOUT_HTTP,
            )
            conferir_resposta(resposta, (204,))
            return

    resposta = sessao.post(
        f"{URL_ORION}/v2/subscriptions",
        json=corpo_assinatura(),
        headers=HEADERS,
        timeout=TIMEOUT_HTTP,
    )
    conferir_resposta(resposta, (201,))


def bloquear_atualizacoes_antigas(sessao):
    url = f"{URL_IOT_AGENT}/iot/devices/{DEVICE_ID}"
    resposta = sessao.get(url, headers=HEADERS, timeout=TIMEOUT_HTTP)
    if resposta.status_code == 404:
        raise RuntimeError(
            f"O cadastro {DEVICE_ID} nao existe no IoT Agent. "
            "A entidade antiga foi preservada para evitar recriacao automatica sem bloqueio."
        )
    conferir_resposta(resposta, (200,))
    dispositivo = resposta.json()
    if dispositivo.get("entity_name") != ENTITY_ID_ANTIGO:
        raise RuntimeError("O cadastro do IoT Agent aponta para uma entidade diferente da esperada.")
    if (dispositivo.get("explicitAttrs") == "[]" and dispositivo.get("timestamp") is False
            and not dispositivo.get("commands")):
        return
    resposta = sessao.put(
        url,
        json={"attributes": dispositivo.get("attributes", []), "explicitAttrs": "[]",
              "timestamp": False, "commands": []},
        headers=HEADERS,
        timeout=TIMEOUT_HTTP,
    )
    conferir_resposta(resposta, (200, 204))
    resposta = sessao.get(url, headers=HEADERS, timeout=TIMEOUT_HTTP)
    conferir_resposta(resposta, (200,))
    dispositivo = resposta.json()
    if (dispositivo.get("explicitAttrs") != "[]" or dispositivo.get("timestamp") is not False
            or dispositivo.get("commands")):
        raise RuntimeError("O IoT Agent nao confirmou o bloqueio das atualizacoes antigas.")


def remover_entidade_antiga(sessao):
    url = f"{URL_ORION}/v2/entities/{ENTITY_ID_ANTIGO}"
    parametros = {"type": ENTITY_TYPE_ANTIGO}
    resposta = sessao.get(url, params=parametros, headers=HEADERS, timeout=TIMEOUT_HTTP)
    if resposta.status_code == 404:
        return
    conferir_resposta(resposta, (200,))
    resposta = sessao.delete(url, params=parametros, headers=HEADERS, timeout=TIMEOUT_HTTP)
    if resposta.status_code != 404:
        conferir_resposta(resposta, (204,))


def remover_assinatura_antiga(sessao, assinaturas):
    atributos = {"p", "t", "h"}
    for assinatura in assinaturas:
        condicao = assinatura.get("subject", {}).get("condition", {})
        notificacao = assinatura.get("notification", {})
        if not (
            entidades_exatas(assinatura, (ENTITY_ID_ANTIGO,), ENTITY_TYPE_ANTIGO)
            and set(condicao.get("attrs", [])) == atributos
            and set(notificacao.get("attrs", [])) == atributos
            and notificacao.get("http", {}).get("url") == f"{URL_STH}/notify"
        ):
            continue
        resposta = sessao.delete(
            f"{URL_ORION}/v2/subscriptions/{assinatura['id']}",
            headers=HEADERS,
            timeout=TIMEOUT_HTTP,
        )
        if resposta.status_code != 404:
            conferir_resposta(resposta, (204,))


def configurar_fiware(sessao: requests.Session) -> None:
    assinaturas = listar_assinaturas(sessao)
    garantir_assinatura(sessao, assinaturas)
    bloquear_atualizacoes_antigas(sessao)
    remover_entidade_antiga(sessao)
    remover_assinatura_antiga(sessao, assinaturas)
