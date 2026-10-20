"""Adaptacao do web_requests_Fiware.py fornecido pelo professor.

Consulta o mesmo STH-Comet, com dados separados por sensor e horario de Brasilia.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import math

import dash
from dash import dcc, html, no_update, ctx
from dash.dependencies import Input, Output, State
import plotly.graph_objs as go
import requests

from exportar_historico import consultar_disponibilidade, exportar_intervalo

# Configuracoes editaveis
IP_ADDRESS = "3.135.234.101"
PORT_STH = 8666
FIWARE_SVC = "smart"
FIWARE_SSP = "/"
ENTITY_ID = "urn:ngsi-ld:device:096"
LAST_N = 100
MAX_POINTS = 500
REFRESH_MS = 2_000  # Consulta o servidor mesmo quando os valores nao mudam.
JANELA_AO_VIVO_MINUTOS = 5
MAX_SILENCIO_SEGUNDOS = 10  # Acima disso, sinaliza ausencia de novas medicoes.
DASH_HOST = "127.0.0.1"
DASH_PORT = 8050
TZ_BRASILIA = timezone(timedelta(hours=-3))

SENSORS = {
    "t": {"label": "Temperatura", "unit": "°C", "color": "#d9480f"},
    "h": {"label": "Umidade relativa", "unit": "%", "color": "#2563eb"},
    "p": {"label": "Luminosidade relativa", "unit": "%", "color": "#9a6700"},
}


def get_attr_values(attr, last_n):
    """Retorna amostras UTC e eventual erro, sem inventar medicoes."""
    url = (f"http://{IP_ADDRESS}:{PORT_STH}/STH/v1/contextEntities/"
           f"type/device/id/{ENTITY_ID}/attributes/{attr}")
    headers = {"fiware-service": FIWARE_SVC, "fiware-servicepath": FIWARE_SSP}
    try:
        response = requests.get(url, params={"lastN": last_n}, headers=headers, timeout=(2, 3))
        response.raise_for_status()
        values = response.json()["contextResponses"][0]["contextElement"]["attributes"][0]["values"]
        out = []
        for entry in values:
            try:
                instant = datetime.fromisoformat(entry["recvTime"].replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=timezone.utc)
                value = float(entry["attrValue"])
                if math.isfinite(value):
                    out.append((instant.astimezone(timezone.utc).isoformat(timespec="microseconds"), value))
            except (KeyError, TypeError, ValueError):
                continue
        return out, "" if out else "Sem amostras na resposta do histórico."
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        return [], f"Falha na consulta: {exc}"


def local_time(iso_utc):
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(TZ_BRASILIA)


app = dash.Dash(__name__)
app.title = "CP05 — device096"


def campo_periodo(prefixo, titulo, numero):
    return html.Div([
        html.Div([html.Span(numero, className="step-number"), html.H3(titulo)], className="range-title"),
        html.Div([
            html.Div([html.Label("Data", htmlFor=f"{prefixo}-data"),
                      dcc.Dropdown(id=f"{prefixo}-data", options=[], clearable=False, disabled=True,
                                   placeholder="Carregando datas…", className="period-dropdown")]),
            html.Div([html.Label("Hora", htmlFor=f"{prefixo}-hora"),
                      dcc.Dropdown(id=f"{prefixo}-hora", options=[], clearable=False, disabled=True,
                                   placeholder="Carregando horários…", className="period-dropdown")]),
        ], className="field-grid"),
    ], className="range-card")


app.layout = lambda: html.Div(
    [
        html.Header([
            html.Div([html.P("VINHERIA AGNELLO · CP05", className="eyebrow"),
                      html.H1("Histórico dos sensores"),
                      html.P("Temperatura, umidade e luminosidade do ambiente.", className="subtitle")]),
            html.Span("device096", className="device-badge"),
        ], className="page-header"),
        html.Section(
            [
                html.Div([
                    html.Div([html.H2("Baixar histórico"),
                              html.P("Escolha o início e o fim. Todos os horários estão em Brasília (UTC−3).", className="section-description")]),
                    html.Button("Atualizar horários", id="atualizar-horarios", n_clicks=0, className="button-secondary"),
                ], className="section-heading"),
                dcc.Loading(html.Div(id="disponibilidade-status", children="Consultando os registros disponíveis…", className="availability")),
                html.Div([campo_periodo("inicio", "A partir de", "1"),
                          campo_periodo("fim", "Até", "2")], className="range-grid"),
                html.P("As listas mostram apenas datas e horários com medições. O fim acompanha o início escolhido.", className="field-help"),
                html.Div([
                    html.Div(id="selecao-resumo", children="Aguarde o carregamento dos horários.", className="selection-summary"),
                    html.Div([html.Button("Todo o histórico", id="todo-historico", n_clicks=0, className="button-secondary"),
                              html.Button("Baixar CSV", id="exportar-csv", n_clicks=0, disabled=True, className="button-primary")], className="download-actions"),
                ], className="download-footer"),
                dcc.Loading(html.Div(id="exportacao-status", className="download-status", role="status")),
                dcc.Download(id="download-csv"),
            ],
            className="export-card",
        ),
        html.Div([html.H2("Monitoramento em tempo real"),
                  html.P("Novas leituras aparecem mesmo com valores iguais. Atualização automática a cada 2 segundos.", className="section-description"),
                  html.P(id="consulta-status", children="Consultando o histórico…", className="section-description"),
                  dcc.RadioItems(id="modo-grafico", value="ao-vivo", inline=True, className="chart-mode",
                      options=[{"label": f"Ao vivo · últimos {JANELA_AO_VIVO_MINUTOS} minutos", "value": "ao-vivo"},
                               {"label": "Histórico carregado", "value": "historico"}]),
                  ], className="charts-heading"),
        *[
            html.Section(
                [
                    html.Div([html.Span(className=f"sensor-dot sensor-{attr}"), html.H3(sensor["label"])], className="sensor-heading"),
                    html.P(id=f"status-{attr}", children="Aguardando histórico…", className="sensor-status"),
                    dcc.Graph(id=f"fig-{attr}", config={"displaylogo": False, "responsive": True,
                              "modeBarButtonsToRemove": ["lasso2d", "select2d"]}),
                ],
                className="chart-card",
            )
            for attr, sensor in SENSORS.items()
        ],
        dcc.Store(id="store", data={attr: {"ts": [], "y": [], "error": ""} for attr in SENSORS}),
        dcc.Store(id="catalogo-horarios", data={"instantes": [], "erro": ""}),
        dcc.Store(id="intervalo-escolhido"),
        dcc.Store(id="exportacao-ocupada", data=False),
        dcc.Store(id="exportacao-resultado"),
        dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),
        dcc.Interval(id="tick-disponibilidade", interval=60_000, n_intervals=0),
    ],
    className="page-container",
)


def horarios_catalogo(catalogo):
    return (catalogo or {}).get("instantes", [])


def dias_catalogo(instantes):
    return sorted({local_time(instant).date().isoformat() for instant in instantes})


def opcoes_datas(dias):
    return [{"label": datetime.fromisoformat(dia).strftime("%d/%m/%Y"), "value": dia} for dia in dias]


def opcoes_horas(instantes):
    return [{"label": local_time(instant).strftime("%H:%M:%S") +
             (f".{local_time(instant).microsecond:06d}".rstrip("0") if local_time(instant).microsecond else ""),
             "value": instant} for instant in instantes]


@app.callback(
    Output("catalogo-horarios", "data"), Output("disponibilidade-status", "children"),
    Input("tick-disponibilidade", "n_intervals"), Input("atualizar-horarios", "n_clicks"),
    running=[(Output("atualizar-horarios", "disabled"), True, False)],
)
def carregar_horarios(_, __):
    try:
        instantes = consultar_disponibilidade(atualizar=ctx.triggered_id == "atualizar-horarios")
        if not instantes:
            return {"instantes": [], "erro": ""}, "Ainda não há medições salvas. Inicie o Wokwi e atualize os horários."
        primeiro, ultimo = [local_time(instant).strftime("%d/%m/%Y às %H:%M:%S") for instant in (instantes[0], instantes[-1])]
        return {"instantes": instantes, "erro": ""}, html.Div([
            html.Strong("Histórico disponível"),
            html.Span(f"De {primeiro} até {ultimo}"),
            html.Small(f"{len(dias_catalogo(instantes))} {'dia' if len(dias_catalogo(instantes)) == 1 else 'dias'} com medições · somente horários registrados"),
        ], className="availability-content")
    except (ValueError, RuntimeError) as exc:
        return {"instantes": [], "erro": str(exc)}, f"Não foi possível conferir os horários. Clique em Atualizar horários. Detalhe: {exc}"


@app.callback(
    Output("inicio-data", "options"), Output("inicio-data", "value"), Output("inicio-data", "disabled"),
    Input("catalogo-horarios", "data"), Input("todo-historico", "n_clicks"), State("inicio-data", "value"),
)
def selecionar_data_inicio(catalogo, _, atual):
    dias = dias_catalogo(horarios_catalogo(catalogo))
    valor = atual if atual in dias and ctx.triggered_id != "todo-historico" else (dias[0] if dias else None)
    return opcoes_datas(dias), valor, not dias


@app.callback(
    Output("inicio-hora", "options"), Output("inicio-hora", "value"), Output("inicio-hora", "disabled"),
    Input("inicio-data", "value"), Input("catalogo-horarios", "data"), Input("todo-historico", "n_clicks"),
    State("inicio-hora", "value"),
)
def selecionar_hora_inicio(dia, catalogo, _, atual):
    instantes = [instant for instant in horarios_catalogo(catalogo) if local_time(instant).date().isoformat() == dia]
    valor = atual if atual in instantes and ctx.triggered_id != "todo-historico" else (instantes[0] if instantes else None)
    return opcoes_horas(instantes), valor, not instantes


@app.callback(
    Output("fim-data", "options"), Output("fim-data", "value"), Output("fim-data", "disabled"),
    Input("inicio-hora", "value"), Input("catalogo-horarios", "data"), Input("todo-historico", "n_clicks"),
    State("fim-data", "value"),
)
def selecionar_data_fim(inicio, catalogo, _, atual):
    instantes = horarios_catalogo(catalogo)
    validos = [instant for instant in instantes if instant >= inicio] if inicio in instantes else []
    dias = dias_catalogo(validos)
    valor = atual if atual in dias and ctx.triggered_id != "todo-historico" else (dias[-1] if dias else None)
    return opcoes_datas(dias), valor, not dias


@app.callback(
    Output("fim-hora", "options"), Output("fim-hora", "value"), Output("fim-hora", "disabled"),
    Input("fim-data", "value"), Input("inicio-hora", "value"), Input("catalogo-horarios", "data"),
    Input("todo-historico", "n_clicks"), State("fim-hora", "value"),
)
def selecionar_hora_fim(dia, inicio, catalogo, _, atual):
    instantes = horarios_catalogo(catalogo)
    validos = [instant for instant in instantes if instant >= inicio
               and local_time(instant).date().isoformat() == dia] if inicio in instantes else []
    valor = atual if atual in validos and ctx.triggered_id != "todo-historico" else (validos[-1] if validos else None)
    return opcoes_horas(validos), valor, not validos


@app.callback(
    Output("intervalo-escolhido", "data"), Output("selecao-resumo", "children"), Output("exportar-csv", "disabled"),
    Input("inicio-data", "value"), Input("inicio-hora", "value"), Input("fim-data", "value"), Input("fim-hora", "value"),
    Input("catalogo-horarios", "data"), Input("exportacao-ocupada", "data"),
)
def resumir_intervalo(dia_inicio, inicio, dia_fim, fim, catalogo, ocupada):
    instantes = horarios_catalogo(catalogo)
    if (inicio not in instantes or fim not in instantes or inicio > fim
            or local_time(inicio).date().isoformat() != dia_inicio or local_time(fim).date().isoformat() != dia_fim):
        return None, "Selecione um início e um fim entre as medições disponíveis.", True
    quantidade = sum(inicio <= instant <= fim for instant in instantes)
    de, ate = [local_time(instant).strftime("%d/%m/%Y · %H:%M:%S") for instant in (inicio, fim)]
    return {"inicio": inicio, "fim": fim}, [html.Small("PERÍODO DO ARQUIVO"),
        html.Strong(f"De {de} até {ate}"), html.Span(f"{quantidade} {'horário' if quantidade == 1 else 'horários'} com medições · três sensores em CSV")], bool(ocupada)


@app.callback(
    Output("download-csv", "data"),
    Output("exportacao-resultado", "data"),
    Input("exportar-csv", "n_clicks"),
    State("intervalo-escolhido", "data"),
    prevent_initial_call=True,
    running=[(Output("exportacao-ocupada", "data"), True, False)],
)
def baixar_csv(_, intervalo):
    try:
        if not intervalo:
            raise ValueError("Escolha um período com medições salvas.")
        inicio, fim = intervalo["inicio"], intervalo["fim"]
        conteudo, contagens = exportar_intervalo(inicio, fim)
        if not sum(contagens.values()):
            return no_update, {"intervalo": intervalo, "erro": True, "mensagem": "Não há registros nesse período. Atualize os horários."}
        resumo = " • ".join(f"{SENSORS[attr]['label']}: {contagens[attr]}" for attr in SENSORS)
        return {
            "content": conteudo,
            "filename": f"device096_{local_time(inicio):%Y-%m-%d_%H-%M-%S}_a_{local_time(fim):%Y-%m-%d_%H-%M-%S}.csv",
            "type": "text/csv;charset=utf-8",
        }, {"intervalo": intervalo, "erro": False, "mensagem": f"✓ CSV pronto: {sum(contagens.values())} medições. {resumo}."}
    except (ValueError, RuntimeError) as exc:
        return no_update, {"intervalo": intervalo, "erro": True, "mensagem": f"Não foi possível exportar: {exc}"}


@app.callback(
    Output("exportacao-status", "children"), Input("exportacao-resultado", "data"),
    Input("intervalo-escolhido", "data"), Input("exportacao-ocupada", "data"),
)
def mostrar_resultado_exportacao(resultado, intervalo, ocupada):
    if ocupada:
        return "Preparando o arquivo com as medições do período…"
    if not resultado or resultado.get("intervalo") != intervalo:
        return None
    return html.Span(resultado["mensagem"], className="message-error" if resultado["erro"] else "message-success")


@app.callback(
    Output("store", "data"),
    Output("consulta-status", "children"),
    Input("tick", "n_intervals"),
    State("store", "data"),
    running=[(Output("tick", "disabled"), True, False)],
)
def on_tick(_, store):
    store = store or {}
    # As tres consultas em paralelo evitam somar os tempos de espera.
    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(lambda attr: get_attr_values(attr, LAST_N), SENSORS))

    updated = {}
    for attr, (batch, error) in zip(SENSORS, batches):
        previous = store.get(attr, {"ts": [], "y": []})
        # Cada atributo possui sua propria serie: nunca mistura as unidades.
        readings = dict(zip(previous["ts"], previous["y"]))
        readings.update(batch)
        readings = sorted(readings.items())[-MAX_POINTS:]
        updated[attr] = {
            "ts": [ts for ts, _ in readings],
            "y": [value for _, value in readings],
            "error": error,
        }

    # A consulta sempre atualiza este relogio, mesmo sem novas amostras no STH.
    # Ele move o eixo de tempo, sem criar pontos que o sensor nao enviou.
    agora = datetime.now(timezone.utc)
    updated["_consulta_utc"] = agora.isoformat(timespec="microseconds")
    now = agora.astimezone(TZ_BRASILIA).strftime("Data: %d/%m/%Y | Hora: %H:%M:%S")
    status = f"Painel atualizado — {now} (Brasília)."
    if any(updated[attr]["error"] for attr in SENSORS):
        status += " Confira a mensagem do sensor; os pontos anteriores foram mantidos."
    return updated, status


@app.callback(
    [Output(f"fig-{attr}", "figure") for attr in SENSORS]
    + [Output(f"status-{attr}", "children") for attr in SENSORS],
    Input("store", "data"),
    Input("modo-grafico", "value"),
)
def draw_figures(store, modo="ao-vivo"):
    figures, statuses = [], []
    agora_texto = (store or {}).get("_consulta_utc")
    agora = local_time(agora_texto) if agora_texto else datetime.now(TZ_BRASILIA)
    limite_inicial = agora - timedelta(minutes=JANELA_AO_VIVO_MINUTOS)
    limite_final = agora + timedelta(seconds=1)
    for attr, sensor in SENSORS.items():
        series = (store or {}).get(attr, {"ts": [], "y": [], "error": ""})
        ts, values = series["ts"], series["y"]
        pontos = list(zip(ts, values))
        if modo == "ao-vivo":
            pontos = [(instant, value) for instant, value in pontos
                      if limite_inicial <= local_time(instant) <= limite_final]
        # Retira o offset somente na exibicao para o grafico manter a hora local.
        local_x = [local_time(instant).strftime("%Y-%m-%dT%H:%M:%S.%f") for instant, _ in pontos]
        fig = go.Figure(go.Scatter(
            x=local_x, y=[value for _, value in pontos], mode="lines+markers", name=sensor["label"],
            line={"color": sensor["color"], "width": 4},
            marker={"size": 6, "line": {"color": "white", "width": 1}},
            hovertemplate=f"%{{x|%d/%m/%Y %H:%M:%S}}<br>%{{y:.1f}} {sensor['unit']}<extra></extra>",
        ))
        fig.update_layout(
            xaxis_title="Data e hora — Brasília (UTC−3)",
            yaxis_title=f"{sensor['label']} ({sensor['unit']})",
            template="plotly_white", height=325,
            font={"family": "Arial, sans-serif", "size": 14, "color": "#34445c"},
            margin={"l": 70, "r": 25, "t": 15, "b": 65},
            hovermode="x unified", uirevision=f"sensor-{attr}-{modo}",
        )
        fig.update_xaxes(type="date", tickformat="%d/%m<br>%H:%M:%S", showspikes=True,
                         gridcolor="#e4eaf2", gridwidth=1.2, showline=True, linecolor="#afbdce", linewidth=1.5)
        fig.update_yaxes(gridcolor="#e4eaf2", gridwidth=1.2, zerolinecolor="#afbdce", zerolinewidth=1.5)
        if modo == "ao-vivo":
            # A janela acompanha o relogio, independentemente da mudanca de valor.
            fig.update_xaxes(autorange=False, range=[
                limite_inicial.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                limite_final.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            ])
            fig.add_shape(type="line", x0=agora.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                          x1=agora.strftime("%Y-%m-%dT%H:%M:%S.%f"), y0=0, y1=1, yref="paper",
                          line={"color": "#8395ac", "width": 1.5, "dash": "dot"})
            fig.add_annotation(x=1, y=1.03, xref="paper", yref="paper", xanchor="right", showarrow=False,
                               text=f"Agora: {agora:%H:%M:%S}", font={"size": 11, "color": "#60718a"})
            if not pontos:
                fig.add_annotation(x=.5, y=.55, xref="paper", yref="paper", showarrow=False,
                    text=("Sem medições nos últimos " + str(JANELA_AO_VIVO_MINUTOS) + " minutos.<br>"
                          "Deixe a simulação do Wokwi rodando para receber novos pontos."),
                    font={"size": 14, "color": "#60718a"}, bgcolor="white", borderpad=10)
        figures.append(fig)

        if ts:
            when = local_time(ts[-1]).strftime("%d/%m/%Y às %H:%M:%S")
            idade = max(0, int((agora - local_time(ts[-1])).total_seconds()))
            if series.get("error"):
                estado, classe = "Falha na consulta", "reading-error"
            elif idade > MAX_SILENCIO_SEGUNDOS:
                atraso = f"{idade}s" if idade < 60 else f"{idade // 60}min {idade % 60:02d}s"
                estado, classe = f"Sem novas leituras há {atraso}", "reading-stale"
            else:
                estado, classe = "Recebendo leituras", "reading-live"
            text = (f"Última medição: {values[-1]:.1f} {sensor['unit']} • "
                    f"Data e hora: {when} • {len(ts)} pontos carregados.")
        else:
            estado, classe = "Aguardando leituras", "reading-stale"
            text = "Sem registros carregados. Inicie o Wokwi e confira a assinatura no STH-Comet."
        if series.get("error"):
            text += " " + series["error"]
        statuses.append([html.Span(estado, className=f"reading-badge {classe}"), html.Span(text)])
    return figures + statuses


if __name__ == "__main__":
    print("Abra http://127.0.0.1:8050 no navegador. Para encerrar, use Ctrl+C.")
    app.run(debug=False, host=DASH_HOST, port=DASH_PORT)
