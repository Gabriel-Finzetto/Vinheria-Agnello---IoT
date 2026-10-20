"""Exportacao somente leitura do historico real do device096 no STH-Comet."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import json
import re
from threading import Lock
from time import monotonic

import requests

BASE_URL = "http://3.135.234.101:8666"
ENTITY_ID = "urn:ngsi-ld:device:096"
HEADERS = {"fiware-service": "smart", "fiware-servicepath": "/"}
BRASILIA = timezone(timedelta(hours=-3))
SENSORES = {
    "t": ("Temperatura", "°C"),
    "h": ("Umidade relativa", "%"),
    "p": ("Luminosidade relativa", "%"),
}
_CACHE_TTL_SEGUNDOS = 60
_cache_lock = Lock()
_cache_disponibilidade = None
_cache_atualizado_em = 0.0


def _data(texto, campo):
    if not isinstance(texto, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        raise ValueError(f"{campo}: use uma data no formato AAAA-MM-DD.")
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{campo}: data invalida ({texto}).") from exc


def _instante_utc(texto, campo):
    if not isinstance(texto, str):
        raise ValueError(f"{campo}: informe um instante ISO 8601 com fuso horario.")
    try:
        instante = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        if instante.tzinfo is None or instante.utcoffset() is None:
            raise ValueError("o instante esta sem fuso horario")
        return instante.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{campo}: instante ISO 8601 invalido ou sem fuso horario.") from exc


def _iso_utc(instante):
    # Precisao uniforme mantem a ordenacao textual e evita ambiguidades de formato.
    return instante.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validar_page_size(page_size):
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("page_size precisa ser um inteiro entre 1 e 100.")


def _valores_pagina(resposta, atributo):
    """Valida o envelope NGSIv1, inclusive erros internos com HTTP 200."""
    try:
        contextos = resposta["contextResponses"]
        if not isinstance(contextos, list) or len(contextos) != 1:
            raise ValueError("quantidade inesperada de contextResponses")
        contexto = contextos[0]
        status = contexto.get("statusCode", {})
        if "code" in status and str(status["code"]) != "200":
            raise ValueError(f"STH retornou codigo {status['code']}")
        entidade = contexto["contextElement"]
        if entidade["id"] != ENTITY_ID:
            raise ValueError("a resposta pertence a outra entidade")
        if entidade.get("type", "device") != "device":
            raise ValueError("tipo de entidade diferente de device")
        atributos = entidade["attributes"]
        if not isinstance(atributos, list) or len(atributos) != 1:
            raise ValueError("quantidade inesperada de atributos")
        if atributos[0]["name"] != atributo:
            raise ValueError("a resposta pertence a outro atributo")
        valores = atributos[0]["values"]
        if not isinstance(valores, list):
            raise ValueError("values precisa ser uma lista")
        return valores
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise RuntimeError(f"Resposta invalida do historico de {atributo}: {exc}.") from exc


def _registro(entrada, atributo, primeiro_instante, ultimo_instante, posicao):
    try:
        if not isinstance(entrada, dict):
            raise ValueError("o registro nao e um objeto")
        if entrada.get("attrName", atributo) != atributo:
            raise ValueError("attrName diferente do atributo solicitado")
        instante_texto = entrada["recvTime"]
        if not isinstance(instante_texto, str):
            raise ValueError("recvTime precisa ser texto ISO 8601")
        instante = datetime.fromisoformat(instante_texto.replace("Z", "+00:00"))
        if instante.tzinfo is None or instante.utcoffset() is None:
            raise ValueError("recvTime esta sem fuso horario")
        instante = instante.astimezone(timezone.utc)
        if ((primeiro_instante is not None and instante < primeiro_instante)
                or (ultimo_instante is not None and instante > ultimo_instante)):
            raise ValueError("instante fora do periodo solicitado")
        valor = entrada["attrValue"]
        if isinstance(valor, bool) or not isinstance(valor, (str, int, float)):
            raise ValueError("attrValue precisa ser numerico")
        numero = Decimal(str(valor))
        if not numero.is_finite():
            raise ValueError("attrValue nao e finito")
        numero_csv = format(numero, "f").replace(".", ",")
        identificador = None
        if "_id" in entrada:
            raw_id = entrada["_id"]
            if raw_id is None or raw_id == "":
                raise ValueError("_id presente, mas vazio")
            identificador = raw_id if isinstance(raw_id, str) else json.dumps(
                raw_id, ensure_ascii=False, sort_keys=True, allow_nan=False
            )
        return instante, atributo, numero_csv, identificador
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as exc:
        raise RuntimeError(
            f"Registro invalido em {atributo}, posicao {posicao}: {exc}. "
            "A exportacao foi cancelada; nenhum CSV parcial foi gerado."
        ) from exc


def _consultar_historico(primeiro=None, ultimo=None, *, page_size=100):
    """Busca todas as paginas; sem limites, consulta todo o historico existente."""
    _validar_page_size(page_size)
    registros = []
    contagens = {atributo: 0 for atributo in SENSORES}

    with requests.Session() as sessao:
        for atributo in SENSORES:
            url = (f"{BASE_URL}/STH/v1/contextEntities/type/device/id/"
                   f"{ENTITY_ID}/attributes/{atributo}")
            offset, paginas_vistas, ids_vistos = 0, set(), set()
            while True:
                parametros = {"hLimit": page_size, "hOffset": offset}
                if primeiro is not None:
                    parametros["dateFrom"] = _iso_utc(primeiro)
                if ultimo is not None:
                    parametros["dateTo"] = _iso_utc(ultimo)
                try:
                    resposta = sessao.get(url, headers=HEADERS, params=parametros, timeout=(3, 10))
                    resposta.raise_for_status()
                    dados = resposta.json()
                except (requests.RequestException, ValueError) as exc:
                    raise RuntimeError(
                        f"Falha ao consultar {atributo}, offset {offset}: {exc}. "
                        "Nenhum CSV parcial foi gerado."
                    ) from exc
                valores = _valores_pagina(dados, atributo)
                if len(valores) > page_size:
                    raise RuntimeError(f"O STH ignorou hLimit ao consultar {atributo}.")
                if not valores:
                    break
                pagina = [_registro(item, atributo, primeiro, ultimo, offset + i + 1)
                          for i, item in enumerate(valores)]
                assinatura = hashlib.sha256(repr(sorted(
                    (item[0].isoformat(), item[2], item[3] or "") for item in pagina
                )).encode("utf-8")).digest()
                if assinatura in paginas_vistas:
                    raise RuntimeError(
                        f"Pagina repetida em {atributo}, offset {offset}; "
                        "o servidor pode estar ignorando hOffset. Exportacao cancelada."
                    )
                paginas_vistas.add(assinatura)
                for registro in pagina:
                    identificador = registro[3]
                    if identificador is not None:
                        if identificador in ids_vistos:
                            raise RuntimeError(f"Registro _id repetido em {atributo}; exportacao cancelada.")
                        ids_vistos.add(identificador)
                registros.extend(pagina)
                contagens[atributo] += len(pagina)
                if len(valores) < page_size:
                    break
                offset += len(valores)

    return registros, contagens


def consultar_disponibilidade(*, atualizar=False):
    """Lista os instantes UTC realmente existentes na uniao de t, h e p.

    Consulta todas as paginas e valida todos os registros no recarregamento.
    O cache em memoria dura 60 segundos e devolve copias independentes da lista.
    atualizar=True forca uma nova consulta completa; falhas nunca retornam catalogo parcial.
    """
    global _cache_disponibilidade, _cache_atualizado_em
    if not isinstance(atualizar, bool):
        raise ValueError("atualizar precisa ser True ou False.")
    with _cache_lock:
        if (_cache_disponibilidade is not None and not atualizar
                and monotonic() - _cache_atualizado_em < _CACHE_TTL_SEGUNDOS):
            return list(_cache_disponibilidade)
        try:
            registros, _ = _consultar_historico()
            catalogo = [_iso_utc(instante) for instante in sorted({item[0] for item in registros})]
        except Exception:
            _cache_disponibilidade = None
            _cache_atualizado_em = 0.0
            raise
        _cache_disponibilidade = catalogo
        _cache_atualizado_em = monotonic()
        return list(catalogo)


def _gerar_csv(registros, contagens):

    registros.sort(key=lambda item: (item[0], item[1], item[3] or ""))
    tem_id = any(item[3] is not None for item in registros)
    destino = io.StringIO(newline="")
    escritor = csv.writer(destino, delimiter=";", lineterminator="\r\n")
    cabecalho = ["timestamp_utc", "data_brasilia", "hora_brasilia", "atributo", "sensor", "valor", "unidade"]
    escritor.writerow(cabecalho + (["id_registro"] if tem_id else []))
    for instante, atributo, valor, identificador in registros:
        local = instante.astimezone(BRASILIA)
        sensor, unidade = SENSORES[atributo]
        linha = [instante.isoformat().replace("+00:00", "Z"), local.strftime("%d/%m/%Y"),
                 local.strftime("%H:%M:%S"), atributo, sensor, valor, unidade]
        escritor.writerow(linha + ([identificador or ""] if tem_id else []))
    return "\ufeff" + destino.getvalue(), contagens


def exportar_intervalo(inicio_iso: str, fim_iso: str, *, page_size=100):
    """Exporta instantes inclusivos, ambos obrigatoriamente presentes no historico."""
    primeiro = _instante_utc(inicio_iso, "Inicio")
    ultimo = _instante_utc(fim_iso, "Fim")
    _validar_page_size(page_size)
    if ultimo < primeiro:
        raise ValueError("O instante final precisa ser igual ou posterior ao instante inicial.")
    catalogo = set(consultar_disponibilidade())
    if not catalogo:
        raise ValueError("Nao ha horarios disponiveis no historico para exportar.")
    for nome, instante in (("inicial", primeiro), ("final", ultimo)):
        if _iso_utc(instante) not in catalogo:
            raise ValueError(
                f"O instante {nome} nao existe no historico disponivel. "
                "Clique em Atualizar horarios e selecione uma das opcoes existentes."
            )
    registros, contagens = _consultar_historico(primeiro, ultimo, page_size=page_size)
    instantes_exportados = {item[0] for item in registros}
    if primeiro not in instantes_exportados or ultimo not in instantes_exportados:
        raise RuntimeError(
            "Um dos horarios selecionados nao esta mais no historico. "
            "Atualize os horarios e tente novamente; nenhum CSV parcial foi gerado."
        )
    return _gerar_csv(registros, contagens)


def exportar_periodo(inicio: str, fim: str, *, page_size=100):
    """API compativel: exporta dias inteiros inclusivos em Brasilia (UTC-3)."""
    dia_inicio, dia_fim = _data(inicio, "Inicio"), _data(fim, "Fim")
    if dia_fim < dia_inicio:
        raise ValueError("A data final precisa ser igual ou posterior a data inicial.")
    _validar_page_size(page_size)
    try:
        primeiro = datetime.combine(dia_inicio, time.min, BRASILIA).astimezone(timezone.utc)
        limite_exclusivo = datetime.combine(
            dia_fim + timedelta(days=1), time.min, BRASILIA
        ).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Periodo fora do intervalo de datas suportado.") from exc
    ultimo = limite_exclusivo - timedelta(milliseconds=1)
    agora = datetime.now(timezone.utc)
    agora = agora.replace(microsecond=(agora.microsecond // 1000) * 1000)
    ultimo = min(ultimo, agora)
    if primeiro > ultimo:
        raise ValueError("Escolha um periodo que ja comecou; nao ha medicoes futuras para exportar.")
    registros, contagens = _consultar_historico(primeiro, ultimo, page_size=page_size)
    return _gerar_csv(registros, contagens)
