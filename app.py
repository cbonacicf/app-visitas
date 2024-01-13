import polars as pl
import pyarrow
from datetime import date, time, datetime, timedelta
import pytz
import os
import io
import json
import sqalchemy

import dash
from dash import dcc
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash_extensions.enrich import Input, Output, State, DashProxy, MultiplexerTransform, html
from dash.exceptions import PreventUpdate

### Parámetros
# período de duración de las visitas

fecha_inicial = date(2024, 3, 1)
fecha_final = date(2024, 11, 30)

# inicialización de usuario

usuario = 0


### Lectura de datos
# datos de visitas programadas y propuestas

repo_programadas = './data/programadas.parquet'
repo_propuestas = './data/propuestas.parquet'

def lectura(repositorio):
    df = pl.read_parquet(repositorio)
    return df.to_dicts(), df.schema

programadas, schema_programada = lectura(repo_programadas)
propuestas, schema_propuesta = lectura(repo_propuestas)

# crea diccionario rbd->nombre y rbd->codigo_comuna

colegios = dict(
    pl.read_parquet('./data/colegios.parquet')
    .select(['rbd', 'nombre'])
    .rows()
)

colegios_comuna = dict(
    pl.read_parquet('./data/colegios.parquet')
    .select(['rbd', 'cod_com'])
    .rows()
)

# crea listado con los feriados y fines de semana

feriados = (
    pl.read_parquet('./data/feriados2024.parquet')
    .to_series()
    .to_list()
)

# crea diccionario codigo->comuna

comunas = dict(
    pl.read_parquet('./data/comunas.parquet')
    .sort('comuna')
    .rows()
)

# crea diccionario con la división horaria de la jornada

horas_15 = dict(
    pl.read_parquet('./data/div_horas.parquet')
    .rows()
)


# universidades que participan en las visitas

universidades = {
    1: 'Universidad Gabriela Mistral',
    2: 'Universidad Finis Terrae',
    4: 'Universidad Central de Chile',
    9: 'Universidad del Alba',
    11: 'Universidad Academia de Humanismo Cristiano',
    13: 'Universidad Santo Tomás',
    17: 'Universidad SEK',
    19: 'Universidad de Las Américas',
    26: 'Universidad de Artes, Ciencias y Comunicación UNIACC',
    31: 'Universidad Autónoma de Chile',
    39: 'Universidad San Sebastián',
    42: 'Universidad Católica Cardenal Raúl Silva Henríquez',
    50: "Universidad Bernardo O'Higgins",
    68: 'Universidad Miguel de Cervantes'
}

usuarios = {0: 'Visita'} | universidades
universidades_inv = {v: k for k, v in universidades.items()}

### Funciones
# registra la fecha actual (instantánea) en Santiago

ahora = lambda: datetime.now(pytz.timezone('America/Santiago')).date()

# función que crea id

def crea_id(variable_entorno):
    id_nuevo = int(os.environ[variable_entorno]) + 1
    os.environ[variable_entorno] = str(id_nuevo)
    return id_nuevo


def crea_id2(tipo):
    file = f'./data/{tipo}.json'

    with open(file, 'r') as f:
        id_nuevo = json.load(f)[tipo] + 1

    with open(file, 'w') as w:
        json.dump({tipo: id_nuevo}, w)

    return id_nuevo


# función que crea las opciones de visualización de meses

lista_meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

def opciones_meses():
    i = fecha_inicial.month
    j = fecha_final.month
    map_meses = {0: 'Todos'} | {k: v for k, v in enumerate(lista_meses[i-1:j], start=i) if k >= i and k <= j}
    return opciones(map_meses)


# función que cambia el nombre de la pestaña de edición

def label_pestana(usuario):
    if usuario:
        return 'Edición de información'
    else:
        return 'Acreditación / Edición de información'

# función que convierte hora cuando es null

def convierte_hora(hora):
    if hora is None:
        hora = '00:00:00'

    return datetime.strptime(hora, '%H:%M:%S').time()

# función que convierte columnas datetime a str

def convierte_a_str(df):
    df = (df
        .with_columns([
            pl.col('fecha').dt.strftime('%Y-%m-%d'),  # si se decide cambiar el formato de las fechas
            pl.col('hora_ini').dt.strftime('%H:%M:%S'),
            pl.col('hora_fin').dt.strftime('%H:%M:%S'),
            pl.col('hora_ins').dt.strftime('%H:%M:%S'),
        ])
    )
    return df

# función que convierte diccionario en formato de opciones para dropdown

def opciones(dic):
    return [{'label': v, 'value': k} for k, v in dic.items()]


#### Programadas
# función que crea datos de visualización (insumo: lista de diccionarios)

actualiza = {
    'fecha': pl.Utf8,
    'hora_ini': pl.Utf8,
    'hora_fin': pl.Utf8,
    'hora_ins': pl.Utf8,
}

schema_programada_lectura = schema_programada.copy()
schema_programada_lectura.update(actualiza)

orden = ['id', 'fecha', 'rbd', 'nombre', 'id_organizador', 'organizador', 'estatus']

map_orden_todas = {
    'id': 'ID',
    'fecha': 'Fecha',
    'rbd': 'RBD',
    'nombre': 'Colegio',
    'id_organizador': 'Código',
    'organizador': 'Universidad',
    'direccion': 'Dirección',
    'id_comuna': 'Comuna',
    'hora_ini': 'Inicio',
    'hora_fin': 'Término',
    'hora_ins': 'Instalación',
    'contacto': 'Contacto',
    'tel_contacto': 'Teléfono contacto',
    'mail_contacto': 'Correo contacto',
    'cargo_contacto': 'Cargo contacto',
    'orientador': 'Orientador',
    'tel_orientador': 'Teléfono orientador',
    'mail_orientador': 'Correo orientador',
    'estatus': 'Estatus',
    'observaciones': 'Observaciones',
}

map_orden = {k: map_orden_todas[k] for k in orden}

def programadas_vista(datos, mes=0):
    df = (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .select(orden)
        .with_columns(
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
        )
    )
    if mes != 0:
        df = df.filter(pl.col('fecha').dt.month() == mes)

    return df.sort(['fecha', 'id']).to_dicts()


def programadas_fecha(datos, fecha):
    return (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .select(orden)
        .filter(pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d') == fecha)
        .with_columns([
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
            pl.arange(1, pl.count()+1).alias('orden'),
        ])
        .sort(['fecha', 'id'])
    ).to_dicts()


def programadas_usuario(datos, usuario, hoy):
    return (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .select(orden)
        .filter((pl.col('id_organizador') == usuario) & (pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d') >= hoy))
        .with_columns([
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
        ])
        .sort(['fecha', 'id'])
        .with_columns([
            pl.arange(1, pl.count()+1).alias('orden'),
        ])
        .to_dicts()
    )


def exporta_programada(datos, mes, usuario):
    output = io.BytesIO()
    df = (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .with_columns([
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
            pl.col('id_comuna').replace(comunas),
        ])
        .select({0: orden}.get(usuario, list(map_orden_todas.keys())))
        .rename({0: map_orden}.get(usuario, map_orden_todas))
    )
    if mes != 0:
        df = df.filter(pl.col('Fecha').dt.month() == mes)
    df.sort(['Fecha', 'ID']).write_excel(workbook=output, autofilter=False)

    return output.getvalue()


# funciones que agregan, modifican y eliminan una programada

def nueva_programada(dic):
    df = pl.read_parquet(repo_programadas)
#    dic = {'id': crea_id('ID_PROGRAMADA')} | dic
    dic = {'id': crea_id2('programadas')} | dic
    df = pl.concat([df, pl.from_dict(dic, schema=schema_programada)], how='diagonal')
    df.write_parquet(repo_programadas)
    return df.to_dicts()


def modifica_programada(dic, consume=False, id=None):
    df = pl.read_parquet(repo_programadas).filter(pl.col('id') != dic['id'])
    if id == None:
#        dic['id'] = crea_id('ID_PROGRAMADA')
        dic['id'] = crea_id2('programadas')
    df = pl.concat([df, pl.from_dict(dic, schema=schema_programada)], how='diagonal')
    df.write_parquet(repo_programadas)
    if consume:
        df = convierte_a_str(df)
    return df.to_dicts()


def elimina_programada(id, consume=False):
    df = pl.read_parquet(repo_programadas).filter(pl.col('id') != id)
    df.write_parquet(repo_programadas)
    if consume:
        df = convierte_a_str(df)
    return df.to_dicts()


#### Propuestas
# función que selecciona datos para la visualización y edición del listado de propuestas de colegios

def propuesta_vista(datos, usuario=None):
    if usuario:
        return (
            pl.DataFrame(datos, schema=schema_propuesta)
            .filter(pl.col('id_organizador') == usuario)
            .select(['id', 'id_organizador', 'rbd', 'nombre']).to_dicts()
        )
    else:
        return (
            pl.DataFrame(datos, schema=schema_propuesta)
            .sort(['id_organizador'])
            .select(['rbd', 'nombre', 'organizador']).to_dicts()
        )


def exporta_propuesta(datos):
    output = io.BytesIO()
    (
        pl.DataFrame(datos, schema=schema_propuesta)
        .sort(['id_organizador'])
        .select(['rbd', 'nombre', 'organizador'])
        .rename({'rbd': 'RBD', 'nombre': 'Colegio', 'organizador': 'Proponente'})
    ).write_excel(workbook=output, autofilter=False)
    return output.getvalue()

# funciones que agregan y eliminan una propuesta

def nueva_propuesta(dic):
    df = pl.read_parquet(repo_propuestas)
#    dic = {'id': crea_id('ID_PROPUESTA')} | dic
    dic = {'id': crea_id2('propuestas')} | dic
    df = pl.concat([df, pl.from_dict(dic, schema=schema_propuesta)])
    df.write_parquet(repo_propuestas)
    return df.to_dicts()


def elimina_propuesta(id):
    df = pl.read_parquet(repo_propuestas).filter(pl.col('id') != id)
    df.write_parquet(repo_propuestas)
    return df.to_dicts()


### Construcción de la aplicación
# color azul de tab, botones, footer, etc.

color = '#2FA4E7'

linea = html.Hr(style={'borderWidth': '0.3vh', 'width': '100%', 'color': '#104e8b'})
espacio = html.Br()

fecha_sel = max(ahora(), fecha_inicial)
mes_sel = fecha_sel.month


#### Encabezado
# encabezado

encabezado = html.Div(
    dbc.Row([
        dbc.Col(
            html.Img(src='/assets/cup-logo-1.svg', style={'width': '140%', 'height': '140%'}),
            width=2,
        ),
        dbc.Col(
            html.H1(['Programación de Visitas a Colegios'], style={'textAlign': 'center'}),
            width=7
        ),
    ], align='center'),
    style={'marginTop': 15}
)


#### Usuario
# usuario actual

def usuario_actual(usuario):
    return html.Div(
        dbc.Row(
            dbc.Col([
                html.H4([f'Usuario: {usuarios[usuario]}'], style={'textAlign': 'center'}),    
            ], width={'size': 7, 'offset': 2},)
        ),
        style={'marginTop': 5},
    )


#### Pestañas de inicio
# estilo de las pestañas de inicio

tab_style = {
    'borderBottom': '1px solid #d6d6d6',
    'border': '1px solid #d6d6d6',
    'padding': '6px',
    'fontSize': '20px',
}

tab_selected_style = {
    'borderTop': '1px solid #d6d6d6',
    'borderBottom': '1px solid #d6d6d6',
    'backgroundColor': color,
    'color': 'white',
    'padding': '6px',
    'fontSize': '20px',    
}

custom_tabs_container = {
    'width': '70%',
}

# pestañas de inicio

def tabs_inicio(usuario):
    return html.Div([
        dcc.Tabs(id='tabs-inicio', value='tab-in1', children=[
            dcc.Tab(
                id='tab01',
                label='Visualización de información',
                value='tab-in1',
                style=tab_style,
                selected_style=tab_selected_style,
            ),
            dcc.Tab(
                id='tab02',
                label=label_pestana(usuario),
                value='tab-in2',
                style=tab_style,
                selected_style=tab_selected_style,
            ),
        ], style=custom_tabs_container),
        html.Div(id='contenido-inicio'), # pestañas de segundo nivel
    ])


#### Pestañas de visualización

def tabs_visual(tab):
    return html.Div([
        html.H4(['Opciones de visualización:'], style={'marginLeft': 10, 'marginBottom': 5, 'marginTop': 20}),
        dcc.Tabs(id='tabs-visual', value=tab, children=[
            dcc.Tab(
                id='tab-viz01',
                label='Colegios propuestos por institución',
                value='tabviz1',
                style=tab_style,
                selected_style=tab_selected_style,
            ),
            dcc.Tab(
                id='tab-viz02',
                label='Colegios programados',
                value='tabviz2',
                style=tab_style,
                selected_style=tab_selected_style,
            ),
        ], style=custom_tabs_container),
        html.Div(id='contenido-visual'), # <- recibe el contenido de las pestañas de visualización
    ])


#### Viz colegios propuestos
# visualización de colegios propuestos

getRowStyle = {
    'styleConditions': [{
        'condition': 'params.rowIndex % 2 === 0',
        'style': {'backgroundColor': 'rgb(47, 164, 231, 0.1)'},
    }],
}

columnDefs_viz_prop = [
    {'field': 'rbd', 'headerName': 'RBD', 'cellStyle': {'textAlign': 'center'}, 'filter': True},
    {'field': 'nombre', 'width': 450, 'filter': True},
    {'field': 'organizador', 'headerName': 'Proponente', 'width': 450, 'filter': True, 'sortable': True},
]

def vista_propuestos_gral(datos):
    return html.Div(
        dag.AgGrid(
            id='viz-col-prop-gral',
            rowData=propuesta_vista(datos),
            defaultColDef={'resizable': True},    
            columnDefs=columnDefs_viz_prop,
            dashGridOptions = {
                'rowSelection': 'single',
            },
            columnSize='sizeToFit',
            getRowStyle=getRowStyle,
            style={'height': '800px', 'width': '1000px'},
        )
    )

# botón que exporta selección a excel
btn_exp_prop = html.Div(
    dbc.Row([
        html.Button('Exportar a Excel', id='exporta-prop', className='btn btn-outline-primary',
                    style={'width': '15%', 'marginRight': 10, 'marginTop': 15, 'padding': '6px 20px'}),
        dcc.Download(id='exporta-prop-archivo'),
    ], justify='end',)
)


def form_vista_propuestos_gral(datos):
    return dbc.Form([
        html.H3(['Colegios propuestos por universidad'], style={'marginLeft': 15, 'marginBottom': 12, 'marginTop': 10}),
        vista_propuestos_gral(datos),
        btn_exp_prop,
    ], id='form-viz-col-prop-gral')


#### Viz colegios programados
# selección de mes de visualización de visitas programadas

op_meses = opciones_meses()

def botones_mes(mes):
    return html.Div([
        dbc.Row([
            html.H5('Filtro según mes:', style={'width': '14%', 'display': 'flex'}),
            dcc.RadioItems(
                id = 'selec-mes',
                options = op_meses,
                value = mes,
                style = {'textAlign': 'left', 'width': '50%', 'display': 'flex', 'marginTop': '3px'},
                labelStyle = {'display': 'inline-block', 'fontSize': '14px', 'fontWeight': 'normal'},
                inputStyle = {'marginRight': '5px', 'marginLeft': '20px'},
            )
        ]),
    ], style={'marginLeft': 10, 'marginBottom': 1, 'marginTop': 20})

# formato de la tabla de visualización

columnDefs = [
    {'field': 'fecha', 'cellStyle': {'textAlign': 'center'}},
    {'field': 'rbd', 'headerName': 'RBD', 'cellStyle': {'textAlign': 'center'}, 'filter': True},
    {'field': 'nombre', 'width': 450, 'filter': True},
    {'field': 'organizador', 'width': 450, 'filter': True, 'sortable': True},
    {'field': 'estatus', 'filter': True, 'sortable': True},
]

def grid_programadas(datos, mes):
    return dag.AgGrid(
        id='viz-ferias',
        rowData=programadas_vista(datos, mes),
        defaultColDef={'resizable': True},    
        columnDefs=columnDefs,
        dashGridOptions = {
            'rowSelection': 'single',
        },
        columnSize='sizeToFit',
        getRowStyle=getRowStyle,
        style={'height': '800px', 'width': 1250},
    )

# botón que exporta selección a excel
btn_exp_visitas = dbc.Row([
    html.Button('Exportar a Excel', id='exporta-visitas', className='btn btn-outline-primary',
                style={'width': '15%', 'marginRight': 10, 'marginTop': 15, 'padding': '6px 20px'}),
    dcc.Download(id='exporta-visitas-archivo'),
], justify='end',)

btn_exp_ejemplo = dbc.Row([
    html.Button('Exporta ejemplo', id='exporta-ejemplo', className='btn btn-outline-primary',
                style={'width': '15%', 'marginRight': 10, 'marginTop': 15, 'padding': '6px 20px'}),
    dcc.Download(id='exporta-ejemplo-archivo'),
], justify='end',)

def form_visualiza(datos, mes):
    return dbc.Form([
        html.H3(['Visitas Programadas'], style={'marginLeft': 15, 'marginBottom': 12, 'marginTop': 10}),
        html.Div(botones_mes(mes)),
        html.Div(grid_programadas(datos, mes)),
        html.Div(btn_exp_visitas),
    ], id='form-visualiza')


#### Edición
# estilo de los tab de edición

tab2_style = {
    'borderBottom': '1px solid #d6d6d6',
    'padding': '6px',
    'fontSize': '18px',
}

tab2_selected_style = {
    'borderTop': '1px solid #d6d6d6',
    'borderBottom': '1px solid #d6d6d6',
    'backgroundColor': color, #'#119DFF',
    'color': 'white',
    'padding': '6px',
    'fontSize': '18px',    
}

# contenido de la pestaña de edición

def contenido_edicion(tab):  
    return html.Div([
        html.H4(['Acciones:'], style={'marginLeft': 15, 'marginBottom': 5, 'marginTop': 20}),
        dcc.Tabs(id='tabs-edicion', value=tab, children=[
            dcc.Tab(
                label='Crear lista de colegios',
                value='tab-ed1',
                style=tab2_style,
                selected_style=tab2_selected_style,
            ),
            dcc.Tab(
                label='Crear nueva visita',
                value='tab-ed2',
                style=tab2_style,
                selected_style=tab2_selected_style,
            ),
            dcc.Tab(
                label='Modificar / Eliminar visita',
                value='tab-ed3',
                style=tab2_style,
                selected_style=tab2_selected_style,
            ),
        ], style={'width': '70%'}),
        html.Div(id='contenido-edicion')],
    )


#### Edición propuestas
# visualización del listado de colegios propuestos

columnDefs_listado = [
    {'field': 'id', 'hide': True},
    {'field': 'id_organizador', 'hide': True},
    {'field': 'rbd', 'headerName': 'RBD', 'cellStyle': {'textAlign': 'center'}, 'filter': True},
    {'field': 'nombre', 'width': 550, 'filter': True},
]

def viz_colegios_prop(datos, usr):
    return dag.AgGrid(
        id='viz-col-prop',
        rowData=propuesta_vista(datos, usuario=usr),  # función que provee los datos
        defaultColDef={'resizable': True},    
        columnDefs=columnDefs_listado,
        columnSize='sizeToFit',
        dashGridOptions = {
            'rowSelection': 'single',
        },
        getRowStyle=getRowStyle,
        style={'height': '600px', 'width': '650px'},
    )

# elementos: selector de colegio, botones agregar y eliminar, listado de colegios seleccionados -> form_list_colegios

def colegio2():
    return html.Div([
        html.H5('Selector de colegio: ', style={'display': 'inline-block', 'vertical-align': 'top', 'width': '19%'}),
        dbc.Col([
            html.H5('Según RBD'),
            dbc.Input(type='number', id='in-rbd-prop', placeholder='Ingrese RBD', debounce=True, style={'width': '80%'},
                      persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '15%'}),
        dbc.Col([
            html.H5('Según nombre'),
            dcc.Dropdown(op_colegios, id='in-nom-prop', placeholder='Seleccione de la lista / Ingrese palabra(s) del nombre', style={'width': '98%'},
                        persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '65%'}),
        dbc.Row([
            html.Button('Limpiar selección', id='btn-limpia-prop', n_clicks=0, className='btn btn-outline-primary', style={'display': 'inline-block', 'width': '16%', 'marginLeft': 15}),
            html.Button('Agregar colegio', id='btn-ag-prop', n_clicks=0, className='btn btn-outline-primary', style={'display': 'inline-block', 'width': '16%', 'marginLeft': 15}),
        ], style={'marginTop': 15})
    ], style={'marginTop': 10})


boton_elimina_prop = html.Div([
        dbc.Row(html.Button('Eliminar', id='btn-elimina-prop', className='btn btn-outline-primary', style={'marginTop': 50, 'padding': '6px 15px'})),
    ], style={'width': '15%', 'display': 'inline-block', 'vertical-align': 'center'}
)


def form_colegios_prop(datos, usuario):
    return html.Div([
        linea,
        colegio2(),
        linea,
        html.H4(['Listado de colegios propuestos'], style={'marginLeft': 15, 'marginBottom': 10, 'marginTop': 15}),
        dbc.Row([
            html.Div(viz_colegios_prop(datos, usuario), style={'width': 'auto', 'display': 'inline-block', 'vertical-align': 'top'}),    # id: viz-col-prop
            boton_elimina_prop,
        ])
    ], id='form-col-prop')


#### Ingresa

op_horas = opciones({'00:00:00': 'En blanco'} | horas_15)
op_colegios = opciones(colegios)
op_comunas = opciones(comunas)

# forma de pestaña de agregar visita
# selector de fecha de nueva visita
columnDefs_ing = [
    {'field': 'orden', 'headerName': 'N', 'width': 70, 'cellStyle': {'textAlign': 'center'}},
    {'field': 'rbd', 'headerName': 'RBD', 'width': 150, 'cellStyle': {'textAlign': 'center'}, 'filter': True},
    {'field': 'nombre', 'width': 450, 'filter': True},
    {'field': 'organizador', 'width': 450, 'filter': True, 'sortable': True},
    {'field': 'estatus', 'filter': True, 'sortable': True},
]

def fecha_visita():
    return html.Div([
        dbc.Col([
            html.H5('Seleccione una fecha:'),
            dcc.DatePickerSingle(
                id='sel-fecha',
                min_date_allowed=fecha_sel,
                max_date_allowed=fecha_final,
                disabled_days=feriados,
                first_day_of_week=1,
                initial_visible_month=str(mes_sel),
                date=fecha_sel,
                display_format='D MMM YYYY',
                stay_open_on_select=False, # MANTIENE ABIERTO EL SELECTOR DE FECHA
                show_outside_days=False,
                persistence=True,
                persistence_type='memory',
            )  
        ], style={'width': '18%', 'display': 'inline-block', 'vertical-align': 'top'}),
        dbc.Col([
            html.H5('Visitas programadas para dicha fecha'),
            dag.AgGrid(
                id='ferias-prg',
                defaultColDef={'resizable': True},
                columnDefs=columnDefs_ing,
                columnSize='sizeToFit',
                dashGridOptions = {
                    'domLayout': 'autoHeight',
                    'rowSelection': 'single',
                },
                style={'width': '1030px', 'height': None, 'margin': 5},
                persistence=True,
                persistence_type='memory',
            ),
        ], style={'width': '80%', 'display': 'inline-block', 'vertical-align': 'top'})
    ])

# selector de colegio
def colegio():
    return html.Div([
        html.H5('Seleccione un colegio: ', style={'display': 'inline-block', 'vertical-align': 'top', 'width': '19%'}),
        dbc.Col([
            html.H5('Según RBD'),
            dbc.Input(type='number', id='sel-rbd', placeholder='Ingrese RBD', debounce=True, style={'width': '80%'}, persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '15%'}),
        dbc.Col([
            html.H5('Según nombre'),
            dcc.Dropdown(op_colegios, id='sel-nombre', placeholder='Seleccione de la lista / Ingrese palabra(s) del nombre', style={'width': '98%'},
                        persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '65%'}),
        dbc.Row([
            html.Button('Limpiar selección', id='limpiar-sel', n_clicks=0, className='btn btn-outline-primary', style={'width': '16%', 'marginLeft': 15}),
        ])
    ], style={'marginTop': 10})


# dirección del colegio
def direccion():
    return html.Div([
        dbc.Row([
            dbc.Col(
                html.H5(['Dirección del establecimiento:']),
                width=3),
            dbc.Col([
                html.H5(['Dirección']),
                dbc.Input(type='text', id='id-direccion', placeholder='Dirección', debounce=True, persistence=True, persistence_type='memory'),
            ], width=6),
            dbc.Col([
                html.H5(['Comuna']),
                dcc.Dropdown(op_comunas, id='id-comuna', persistence=True, persistence_type='memory', style={'width': '110%'}),
            ], width=2),        
        ])
    ])

def horario():
    return html.Div([
        html.H5(['Especifique el horario:'], style={'display': 'inline-block', 'width': '20%'}),
        dbc.Col([
            html.H5(['De inicio']),
            dcc.Dropdown(op_horas, id='hr-inicio', style={'margin-right': '50px'}, persistence=True, persistence_type='memory')
        ], style={'display': 'inline-block', 'width': '15%'}),
        dbc.Col([
            html.H5(['De término']),
            dcc.Dropdown(op_horas, id='hr-termino', style={'margin-right': '50px'}, persistence=True, persistence_type='memory')
        ], style={'display': 'inline-block', 'width': '15%'}),
        dbc.Col([
            html.H5(['De instalación']),
            dcc.Dropdown(op_horas, id='hr-instala', style={'margin-right': '50px'}, persistence=True, persistence_type='memory')
        ], style={'display': 'inline-block', 'width': '15%'}),
    ])

def contacto():
    return html.Div([
        html.H5(['Añada información de contacto:'], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '26%'}),
        dbc.Col([
            html.H5(['Contacto']),
            dbc.Input(type='text', id='contacto-nom', placeholder='Nombre', debounce=True, persistence=True, persistence_type='memory'),
            dbc.Input(type='text', id='contacto-cel', placeholder='Celular', debounce=True, persistence=True, persistence_type='memory'),
            dbc.Input(type='text', id='contacto-mail', placeholder='Correo', debounce=True, persistence=True, persistence_type='memory'),
            dbc.Input(type='text', id='contacto-cargo', placeholder='Cargo', debounce=True, persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block','margin-right': '20px', 'width': '36%'}),
        dbc.Col([
            html.H5(['Orientador']),
            dbc.Input(type='text', id='orienta-nom', placeholder='Nombre', debounce=True, persistence=True, persistence_type='memory'),
            dbc.Input(type='text', id='orienta-cel', placeholder='Celular', debounce=True, persistence=True, persistence_type='memory'),
            dbc.Input(type='text', id='orienta-mail', placeholder='Correo', debounce=True, persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '36%'}),
    ])

lista_estatus = ['Confirmada', 'Por confirmar', 'Realizada', 'Suspendida']

def estatus():
    return html.Div([
        dbc.Col([
            html.H5(['Indique el estatus:']),
            dcc.Dropdown(lista_estatus, id='def-estatus', style={'width': '90%'}, persistence=True, persistence_type='memory')
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '20%'}),
        dbc.Col([
            html.H5(['Observaciones:']),
            dcc.Textarea(id='obs-texto', placeholder=' ¡Añada las observaciones que estime conveniente!', style={'color': '#8a8a8a', 'width': '95%', 'height': 100},
                        persistence=True, persistence_type='memory'),
        ], style={'display': 'inline-block', 'vertical-align': 'top', 'width': '79%'}),
    ])


acepta = html.Div([
    html.Button('Agregar visita', id='ag-visita', n_clicks=0, className='btn btn-outline-primary', style={'width': '16%', 'marginLeft': 15}),
])

# forma
def form_agrega():
    return dbc.Form([
        linea,
        fecha_visita(),
        linea,
        colegio(),
        linea,
        direccion(),
        linea,
        horario(),
        linea,
        contacto(),
        linea,
        estatus(),
        linea,
        acepta,
        linea
    ], style={'marginTop': 0, 'padding': '10px'})


#### Modifica
# modifica / elimina una visita existente

columnDefs_mod = [
    {'field': 'orden', 'headerName': 'N', 'width': 50, 'cellStyle': {'textAlign': 'center'}},
    {'field': 'rbd', 'headerName': 'RBD', 'width': 110, 'cellStyle': {'textAlign': 'center'}, 'filter': True},
    {'field': 'nombre', 'width': 450, 'filter': True},
    {'field': 'fecha', 'cellStyle': {'textAlign': 'center'}},
]

def viz_modifica(datos, usuario):
    return html.Div([
        dbc.Col([
            dag.AgGrid(
                id='ferias-prg',
                rowData=programadas_usuario(datos, usuario, ahora()),
                defaultColDef={'resizable': True},
                columnDefs=columnDefs_mod,
                columnSize='sizeToFit',
                dashGridOptions = {
                    'domLayout': 'autoHeight',
                    'rowSelection': 'single',
                },
                style={'height': '600px', 'width': '850px', 'height': None, 'margin': 5},
                persistence=True,
                persistence_type='memory',
            ),
        ], style={'width': '80%', 'display': 'inline-block', 'vertical-align': 'top'})
    ])

botones_modifica = html.Div([
        dbc.Row(html.Button('Modificar', id='btn-mod-visita', className='btn btn-outline-primary', n_clicks=0, style={'marginTop': 55, 'padding': '6px 15px'})),
        dbc.Row(html.Button('Eliminar', id='btn-elim-visita', className='btn btn-outline-primary', n_clicks=0, style={'marginTop': 10, 'padding': '6px 15px'})),
    ], style={'width': '15%', 'display': 'inline-block', 'vertical-align': 'center'}
)

def form_modifica(datos, usuario):
    return dbc.Form([
        html.H5(['Seleccione la visita que desea modificar o eliminar:'], style={'marginLeft': 15, 'marginTop': 20}),
        dbc.Row([
            html.Div(viz_modifica(datos, usuario), style={'width': 'auto', 'display': 'inline-block', 'vertical-align': 'top'}),
            botones_modifica,
        ])
    ], id='form-modifica')

# cambio de visualización de None

viz_none = lambda x: '()' if x == None else f'({x})'

# forma para la modificación específica de datos
# colegio: no se contempla modificar esta información
def identificacion(nom, rbd):
    return html.Div(
        f'Colegio: {nom}\nRBD: {rbd}',
        style={'whiteSpace': 'pre-line', 'background': '#f7f7f7', 'padding': '12px 25px'},
    )

# estatus
def mod_estatus(estatus):
    return html.Div([
        dbc.Row([
            dbc.Col(html.H5(['Estatus:'], style={'width': 'auto', 'marginTop': 10}), width=1),
            dbc.Col(dcc.Dropdown(lista_estatus, value=estatus, id='mod-estatus'), width=2),
            dbc.Col(html.P(viz_none(estatus), style={'marginTop': 10, 'color': 'red'}), width=2),
        ], align='center'),
    ])


# dirección
def mod_direccion(direccion, comuna):
    return html.Div([
        dbc.Row([
            dbc.Col([
                html.H5(['Dirección']),
                dbc.Input(type='text', id='mod-id-direccion', value=direccion, debounce=True, persistence=True, persistence_type='memory'),
                html.P(viz_none(direccion), style={'color': 'red'}),
            ], width=6),
            dbc.Col([
                html.H5(['Comuna']),
                dcc.Dropdown(op_comunas, id='mod-id-comuna', value=comuna, persistence=True, persistence_type='memory', style={'width': '110%'}),
                html.P(viz_none(comunas[comuna]), style={'color': 'red'}),
            ], width=2),        
        ])
    ])


# fecha
def mod_fecha(datos, fecha):
    return html.Div(
        dbc.Row([
            dbc.Col([
                html.H5(['Fecha:']),
                dcc.DatePickerSingle(
                    id='mod-fecha',
                    min_date_allowed=fecha_sel,
                    max_date_allowed=fecha_final,
                    disabled_days=feriados,
                    first_day_of_week=1,
                    initial_visible_month=str(fecha.month),
                    date=fecha,
                    display_format='D MMM YYYY',
                    stay_open_on_select=False,
                    show_outside_days=False,
                ),
                html.P('('+fecha.strftime('%d/%m/%Y')+')', style={'color': 'red'}),
            ], width=2, align='start'),    
            dbc.Col([
                html.H5('Visitas programadas para dicha fecha'),
                dag.AgGrid(
                    id='mod-ferias-prg',
                    rowData=programadas_fecha(datos, fecha),
                    defaultColDef={'resizable': True},
                    columnDefs=columnDefs_ing,
                    columnSize='sizeToFit',
                    dashGridOptions = {
                        'domLayout': 'autoHeight',
                        'rowSelection': 'single',
                    },
                    style={'width': '1030px', 'height': None, 'margin': 5},
                ),
            ], width=7, align='start')
        ])
    )

# horario
def mod_horario(inicio, termino, instal):
    return html.Div(
        dbc.Row([
            dbc.Col(html.H5(['Horario:']), width=1, align='start'),
            dbc.Col([
                html.H5(['De inicio']),
                dcc.Dropdown(op_horas, id='mod-hr-inicio', value=inicio, style={'margin-right': '50px'}),
                html.P(viz_none(inicio), style={'color': 'red'}),
            ], width=2, align='start'),
            dbc.Col([
                html.H5(['De término']),
                dcc.Dropdown(op_horas, id='mod-hr-termino', value=termino, style={'margin-right': '50px'}),
                html.P(viz_none(termino), style={'color': 'red'}),
            ], width=2, align='start'),
            dbc.Col([
                html.H5(['De instalación']),
                dcc.Dropdown(op_horas, id='mod-hr-instala', value=instal, style={'margin-right': '50px'}),
                html.P(viz_none(instal), style={'color': 'red'}),
            ], width=2, align='start'),
        ])
    )

# información de contacto
def mod_contacto(ctto, tel_ctto, mail_ctto, cargo_ctto, ori, tel_ori, mail_ori):
    return html.Div(
        dbc.Row([
            dbc.Col(html.H5(['Contacto:']), width=1, align='start'),
            dbc.Col([
                html.H5(['Contacto']),
                dbc.Input(type='text', id='mod-contacto-nom', value=ctto,  placeholder='Nombre', debounce=True),
                html.P(viz_none(ctto), style={'color': 'red'}),
                dbc.Input(type='text', id='mod-contacto-cel', value=tel_ctto,  placeholder='Celular', debounce=True),
                html.P(viz_none(tel_ctto), style={'color': 'red'}),
                dbc.Input(type='text', id='mod-contacto-mail', value=mail_ctto,  placeholder='Correo', debounce=True),
                html.P(viz_none(mail_ctto), style={'color': 'red'}),
                dbc.Input(type='text', id='mod-contacto-cargo', value=cargo_ctto,  placeholder='Cargo', debounce=True),
                html.P(viz_none(cargo_ctto), style={'color': 'red'}),
            ], width=4, align='start'),
            dbc.Col([
                html.H5(['Orientador']),
                dbc.Input(type='text', id='mod-orienta-nom', value=ori,  placeholder='Nombre', debounce=True),
                html.P(viz_none(ori), style={'color': 'red'}),
                dbc.Input(type='text', id='mod-orienta-cel', value=tel_ori,  placeholder='Celular', debounce=True),
                html.P(viz_none(tel_ori), style={'color': 'red'}),
                dbc.Input(type='text', id='mod-orienta-mail', value=mail_ori,  placeholder='Correo', debounce=True),
                html.P(viz_none(mail_ori), style={'color': 'red'}),
            ], width=4, align='start'),
        ])
    )

# observaciones
def mod_observaciones(obs):
    return html.Div(
        dbc.Row([
            dbc.Col([
                html.H5(['Observaciones:']),
                dcc.Textarea(id='mod-obs-texto', value=obs, style={'color': '#8a8a8a', 'width': '100%', 'height': 100}),
            ], width=6),
            dbc.Col(
                html.P(viz_none(obs), style={'color': 'red', 'marginTop': 30}),
                width=6,
            )
        ]),
    )

# boton volver
botones_acepta_modifica = html.Div([
        dbc.Row([
            html.Button('Volver', id='btn-mod-volver', className='btn btn-outline-primary', n_clicks=0,
                        style={'width': '15%','marginLeft': 10, 'display': 'inline-block', 'padding': '6px 15px'}),
            html.Button('Aplicar cambios', id='btn-mod-aplica', className='btn btn-outline-primary', n_clicks=0,
                        style={'width': '15%','marginLeft': 10, 'display': 'inline-block', 'padding': '6px 15px'}),
        ]),
    ], style={'marginTop': 10}
)


def form_modifica_visita(datos, original):
    return dbc.Form([
        html.H5(['Modificación de datos de visita'], style={'marginLeft': 15, 'marginTop': 20}),
        linea,
        identificacion(original['nombre'], original['rbd']),
        linea,
        mod_estatus(original['estatus']),
        linea,
        mod_direccion(original['direccion'], original['id_comuna']),
        linea,
        mod_fecha(datos, original['fecha']),
        linea,
        mod_horario(original['hora_ini'], original['hora_fin'], original['hora_ins']),
        linea,
        mod_contacto(original['contacto'], original['tel_contacto'], original['mail_contacto'], original['cargo_contacto'],
                     original['orientador'], original['tel_orientador'], original['mail_orientador']),
        linea,
        mod_observaciones(original['observaciones']),
        linea,
        botones_acepta_modifica,
    ])


#### Acreditación

op_universidades = opciones(universidades)

# control de acceso

seccion_acceso = html.Div([
    dbc.Row([
        dbc.Label('Universidad:', html_for='ingreso-univ', width=2, style={'align': 'right'}),
        dbc.Col(
            dcc.Dropdown(op_universidades, id='ingreso-univ', placeholder='Seleccione una universidad'),
            width=10,
        ),
    ], style={'width': '60%'}),
    dbc.Row([
        dbc.Label('Password:', html_for='ingreso-pw', width=2),
        dbc.Col(
            dbc.Input(type='password', id='ingreso-pw', placeholder='Ingrese password', debounce=True),
            width=4,
        ),
    ], style={'width': '60%'}),
    dbc.Row([
        dbc.Col(
            html.Button('Ingresar', id='boton-ingresar', n_clicks=0, className='btn btn-outline-primary', style={'marginTop': 15, 'padding': '6px 20px'}),
            width=4,
        )
    ], style={'width': '60%'}),
])


def form_ingreso(usuario):
    return dbc.Form([
        html.H5(['Para editar el contenido debe ingresar como usuario autorizado']),
        linea,
        seccion_acceso,
        linea
    ], style={'marginTop': 15, 'padding': '10px'})


#### Pie

def form_footer():
    return html.Div(
        html.Footer(
            ['2024:  Corporación de Universidades Privadas'],
            style={
                'display': 'flex',
                'background': color,
                'color': 'white',
                'padding': '30px',
                'fontSize': '18px',
                'marginTop': 25,
            }
        )
    )


parametros_iniciales = {
    'user': usuario,
    'mes': mes_sel,
    'tab_visual': 'tabviz2',
    'tab_edit': 'tab-ed2',
    'rbd_propuesta': None,
    'id_modifica': None,
}


#### Layout

app = DashProxy(__name__, transforms=[MultiplexerTransform()], external_stylesheets=[dbc.themes.CERULEAN])
server = app.server

app.config.suppress_callback_exceptions = True

# layout de la aplicación
app.layout = dbc.Container([
    encabezado,
    html.Div(usuario_actual(usuario), id='contenido-usuario'),    
    tabs_inicio(usuario),  # id='contenido-inicio'
    form_footer(),

    dcc.Store(id='datos-programadas', data=programadas),
    dcc.Store(id='datos-propuestas', data=propuestas),
    dcc.Store(id='parametros', data=parametros_iniciales),
])

# CALLBACK
# TAB: ventana inicial
@app.callback(
    Output('contenido-inicio', 'children'),
    Input('tabs-inicio', 'value'),
    State('datos-programadas', 'data'),
    State('parametros', 'data'),
)
def crea_contenido_inicio(tab, datos, param):
    usuario = param['user']
    if tab == 'tab-in1':
        return tabs_visual(param['tab_visual'])
    elif tab == 'tab-in2':
        if usuario == 0:
            return html.Div(form_ingreso(usuario))
        else:
            return html.Div([contenido_edicion(param['tab_edit'])])
        

# BOTON: ingreso como usuario / acceso a edición
@app.callback(
    Output('contenido-inicio', 'children'),
    Output('contenido-usuario', 'children'),
    Output('parametros', 'data'),  # cambio del id del usuario
    Output('ingreso-pw', 'value'),
    Output('tab02', 'label'),
    Input('boton-ingresar', 'n_clicks'),
    State('ingreso-univ', 'value'),
    State('ingreso-pw', 'value'),
    State('parametros', 'data'),
)
def ingreso_edicion(click, universidad, pw, param):
    if click == 0:
        raise PreventUpdate
    else:
        if (universidad != None) & (pw != None) & (pw != ''):
            if os.environ[f'U{universidad}'] == pw:
                param['user'] = universidad
                return html.Div([contenido_edicion(param['tab_edit'])]), usuario_actual(universidad), param, pw, label_pestana(universidad)
            else:
                return dash.no_update, dash.no_update, dash.no_update, None, dash.no_update
        else:
            return dash.no_update, dash.no_update, dash.no_update, None, dash.no_update


# TAB: despliegue de las opciones de visualización
@app.callback(
    Output('contenido-visual', 'children'),
    Output('parametros', 'data'),
    Input('tabs-visual', 'value'),
    State('datos-programadas', 'data'),
    State('datos-propuestas', 'data'),
    State('parametros', 'data'),
)
def crea_contenido_visualizacion(tab, datos, datos_prop, param):
    if tab == 'tabviz1':
        param['tab_visual'] = tab
        return html.Div(form_vista_propuestos_gral(datos_prop)), param
    elif tab == 'tabviz2':
        param['tab_visual'] = tab
        return html.Div(form_visualiza(datos, param['mes'])), param


# TAB: despliegue de las opciones de edición
@app.callback(
    Output('contenido-edicion', 'children'),
    Output('parametros', 'data'),
    Input('tabs-edicion', 'value'),
    State('datos-programadas', 'data'),
    State('datos-propuestas', 'data'),
    State('parametros', 'data'),
)
def crea_contenido_edicion(tab, datos, datos_prop, param):
    if tab == 'tab-ed1':
        param['tab_edit'] = tab
        return form_colegios_prop(datos_prop, param['user']), param
    elif tab == 'tab-ed2':
        param['tab_edit'] = tab
        return form_agrega(), param
    elif tab == 'tab-ed3':
        param['tab_edit'] = tab
        return form_modifica(datos, param['user']), param


# BOTON RADIO: modifica la visualización de las visitas programadas
@app.callback(
    Output('parametros', 'data'),
    Output('viz-ferias', 'rowData'),
    Input('selec-mes', 'value'),
    State('datos-programadas', 'data'),
    State('parametros', 'data'),
    prevent_initial_call=True,
)
def mod_visualizacion_mes(mes, datos, param):
    param['mes'] = mes
    return param, programadas_vista(datos, mes)


# SELECTOR: cambio de día
@app.callback(
    Output('ferias-prg', 'rowData'),
    Input('sel-fecha', 'date'),
    State('datos-programadas', 'data'),
)
def ferias_programadas_fecha(fecha, datos):
    return programadas_fecha(datos, datetime.strptime(fecha, '%Y-%m-%d').date())


# INPUT: sleccición de RBD y nombre
@app.callback(
    Output('sel-rbd', 'value'),
    Output('sel-nombre', 'value'),
    Output('id-comuna', 'value'),
    Input('sel-rbd', 'value'),
    Input('sel-nombre', 'value'),
    Input('limpiar-sel', 'n_clicks'),
    prevent_initial_call=True,
)
def completa_rbd_y_nombre(rbd, nombre, click):

    disparador = dash.ctx.triggered_id

    if disparador == 'sel-rbd':
        try:
            return dash.no_update, rbd, colegios_comuna[rbd]
        except:
            return None, dash.no_update, dash.no_update
    elif disparador == 'sel-nombre':
        return nombre, dash.no_update, colegios_comuna[nombre]
    elif disparador == 'limpiar-sel':
        return None, None, None


# sleccición de RBD y nombre en página de colegios propuestos
@app.callback(
    Output('in-rbd-prop', 'value'),
    Output('in-nom-prop', 'value'),
    Output('parametros',  'data'),
    Input('in-rbd-prop', 'value'),
    Input('in-nom-prop', 'value'),
    Input('btn-limpia-prop', 'n_clicks'),
    State('parametros',  'data'),
    prevent_initial_call=True,
)
def completa_rbd_y_nombre2(rbd, nombre, click, param):

    disparador = dash.ctx.triggered_id

    if disparador == 'in-rbd-prop':
        try:
            param['rbd_propuesta'] = rbd
            return dash.no_update, rbd, param
        except:
            param['rbd_propuesta'] = None
            return None, dash.no_update, param
    elif disparador == 'in-nom-prop':
        param['rbd_propuesta'] = nombre
        return nombre, dash.no_update, param
    elif disparador == 'btn-limpia-prop':
        param['rbd_propuesta'] = None
        return None, None, param


# añade visita a base de datos / faltan observaciones
@app.callback([
        Output('datos-programadas', 'data'),
        Output('contenido-edicion', 'children'),
        Output('sel-rbd', 'value'),
        Output('sel-nombre', 'value'),
        Output('id-direccion', 'value'),
        Output('id-comuna', 'value'),
        Output('hr-inicio', 'value'),
        Output('hr-termino', 'value'),
        Output('hr-instala', 'value'),
        Output('contacto-nom', 'value'),
        Output('contacto-cel', 'value'),
        Output('contacto-mail', 'value'),
        Output('contacto-cargo', 'value'),
        Output('orienta-nom', 'value'),
        Output('orienta-cel', 'value'),
        Output('orienta-mail', 'value'),
        Output('def-estatus', 'value'),
        Output('obs-texto', 'value'),
    ], [
        Input('ag-visita', 'n_clicks')
    ], [
        State('parametros', 'data'),    # id usuario
        State('sel-fecha', 'date'),     # fecha
        State('sel-rbd', 'value'),      # rbd
        State('id-direccion', 'value'), # dirección
        State('id-comuna', 'value'),    # comuna
        State('hr-inicio', 'value'),    # inicio
        State('hr-termino', 'value'),   # término
        State('hr-instala', 'value'),   # instalación
        State('contacto-nom', 'value'), # contacto
        State('contacto-cel', 'value'),
        State('contacto-mail', 'value'),
        State('contacto-cargo', 'value'),
        State('orienta-nom', 'value'),  # orientador
        State('orienta-cel', 'value'),
        State('orienta-mail', 'value'),
        State('def-estatus', 'value'),  # estatus
        State('obs-texto', 'value'),    # observaciones
    ],
    prevent_initial_call=True,
)
def agrega_feria(click, param, fecha, rbd, direc, comuna, hr_ini, hr_fin, hr_ins, ct, ct_tel, ct_mail, ct_cargo, ori, ori_tel, ori_mail, est, obs):

    if click == 0:
        raise PreventUpdate
    else:
        dic_datos = {}
        dic_observaciones = {}

        dic_datos['id_organizador'] = param['user']
        dic_datos['organizador'] = universidades[param['user']]
        dic_datos['fecha'] = datetime.strptime(fecha, '%Y-%m-%d')
        dic_datos['rbd'] = rbd
        dic_datos['nombre'] = colegios[rbd]
        dic_datos['direccion'] = direc
        dic_datos['id_comuna'] = comuna
        dic_datos['hora_ini'] = convierte_hora(hr_ini)
        dic_datos['hora_fin'] = convierte_hora(hr_fin)
        dic_datos['hora_ins'] = convierte_hora(hr_ins)
        dic_datos['contacto'] = ct
        dic_datos['tel_contacto'] = ct_tel
        dic_datos['mail_contacto'] = ct_mail
        dic_datos['cargo_contacto'] = ct_cargo
        dic_datos['orientador'] = ori
        dic_datos['tel_orientador'] = ori_tel
        dic_datos['mail_orientador'] = ori_mail
        dic_datos['estatus'] = est
        dic_datos['observaciones'] = obs
    
        nuevos_datos = nueva_programada(dic_datos)
    
        return nuevos_datos, form_agrega(), None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None


# agrega colegio a listado de colegios propuestos
@app.callback(
    Output('datos-propuestas', 'data'),
    Output('contenido-edicion', 'children'),
    Input('btn-ag-prop', 'n_clicks'),
    State('parametros', 'data'),
    State('in-rbd-prop', 'value'),
    State('in-nom-prop', 'label'),
    prevent_initial_call=True,
)
def arega_propuesta(click, param, rbd, nombre):
    if click == 0:
        raise PreventUpdate
    else:
        dic = {
            'id_organizador': param['user'],
            'organizador': universidades[param['user']],
            'rbd': rbd,
            'nombre': colegios[rbd],
        }
        df = nueva_propuesta(dic)
        datos_grid = propuesta_vista(df, usuario=param['user']) # datos para grid
        return df, form_colegios_prop(df, param['user'])


# exporta visitas programadas a excel
@app.callback(
    Output('exporta-visitas-archivo', 'data'),
    Input('exporta-visitas', 'n_clicks'),
    State('datos-programadas', 'data'),
    State('parametros', 'data'),
    prevent_initial_call=True,
)
def exporta_visitas_excel(click, datos, param):
    df = exporta_programada(datos, param['mes'], param['user'])
    return dcc.send_bytes(df, 'visitas.xlsx')


# exporta colegios propuestos a excel
@app.callback(
    Output('exporta-prop-archivo', 'data'),
    Input('exporta-prop', 'n_clicks'),
    State('datos-propuestas', 'data'),
    prevent_initial_call=True,
)
def exporta_propuestas_excel(click, datos):
    df = exporta_propuesta(datos)
    return dcc.send_bytes(df, 'propuestas.xlsx')

# elimina selección de listado de colegios propuestos
@app.callback(
    Output('datos-propuestas', 'data'),
    Output('contenido-edicion', 'children'),
    Input('btn-elimina-prop', 'n_clicks'),
    State('viz-col-prop', 'selectedRows'),
    prevent_initial_call=True,
)
def elimina_colegio_propuesto(click, filas):
    if click == 0:
        raise PreventUpdate
    else:
        if filas:
            id = filas[0]['id']
            usuario = filas[0]['id_organizador']
            df = elimina_propuesta(id)
            return df, form_colegios_prop(df, usuario)
        else:
            return dash.no_update, dash.no_update,


# BOTON elimina selección de listado de colegios programados
@app.callback(
    Output('datos-programadas', 'data'),
    Output('contenido-edicion', 'children'), # recibe la misma forma actualizada: form_modifica
    Input('btn-elim-visita', 'n_clicks'),
    State('ferias-prg', 'selectedRows'),
    prevent_initial_call=True,
)
def elimina_colegio_programado(click, filas):
    if click == 0:
        raise PreventUpdate
    else:
        if filas:
            id = filas[0]['id']
            usuario = filas[0]['id_organizador']
            df = elimina_programada(id, consume=True)
            return df, form_modifica(df, usuario)
        else:
            return dash.no_update, dash.no_update


# modifica colegio programado
@app.callback(
    Output('contenido-edicion', 'children'),
    Output('parametros', 'data'),
    Input('btn-mod-visita', 'n_clicks'),
    State('datos-programadas', 'data'),
    State('ferias-prg', 'selectedRows'),
    State('parametros', 'data'),
    prevent_initial_call=True,
)
def modifica_colegio_programado(click, datos, filas, param):
    if click == 0:
        raise PreventUpdate
    else:
        if filas:
            id = filas[0]['id']
            dic_original = (
                pl.read_parquet('./data/programadas.parquet')
                .filter(pl.col('id') == id)
                .to_dicts()
            )[0]
            param['id_modifica'] = id
            return form_modifica_visita(datos, dic_original), param
        else:
            return dash.no_update, dash.no_update


# vuelve de página de modificaciones sin cambio
@app.callback(
    Output('contenido-edicion', 'children'), 
    Input('btn-mod-volver', 'n_clicks'),
    State('datos-programadas', 'data'),
    State('parametros', 'data'),
    prevent_initial_call=True,
)
def vuelve_sin_modificacion(click, datos, param):
    if click == 0:
        raise PreventUpdate
    else:
        return form_modifica(datos, param['user'])


# cambio de día en ventana de modificación
@app.callback(
    Output('mod-ferias-prg', 'rowData'),
    Input('mod-fecha', 'date'),
    State('datos-programadas', 'data'),
)
def mod_ferias_programadas_fecha(fecha, datos):
    return programadas_fecha(datos, datetime.strptime(fecha, '%Y-%m-%d').date())


# aplicar cambios en ventana de modificaciones
@app.callback(
    Output('datos-programadas', 'data'), # datos
    Output('contenido-edicion', 'children'), # cambia forma: form_modifica
    Output('parametros', 'data'), # actualiza id_modifica a None
    Input('btn-mod-aplica', 'n_clicks'),
    State('parametros', 'data'),
    State('mod-id-direccion', 'value'),
    State('mod-id-comuna', 'value'),
    State('mod-fecha', 'date'),
    State('mod-hr-inicio', 'value'),
    State('mod-hr-termino', 'value'),
    State('mod-hr-instala', 'value'),
    State('mod-contacto-nom', 'value'),
    State('mod-contacto-cel', 'value'),
    State('mod-contacto-mail', 'value'),
    State('mod-contacto-cargo', 'value'),
    State('mod-orienta-nom', 'value'),
    State('mod-orienta-cel', 'value'),
    State('mod-orienta-mail', 'value'),
    State('mod-estatus', 'value'),
    State('mod-obs-texto', 'value'),
    prevent_initial_call=True,
)
def aplica_cambios(click, param, direc, comuna, fecha, hr_ini, hr_fin, hr_ins, ct, ct_tel, ct_mail, ct_cargo, ori, ori_tel, ori_mail, est, obs):
    if click == 0:
        raise PreventUpdate
    else:
        id_visita = param['id_modifica']
        dic_original = (
            pl.read_parquet('./data/programadas.parquet')
            .filter(pl.col('id') == id_visita)
            .to_dicts()
        )[0]
        id = dic_original['id']
        nueva_fecha = datetime.strptime(fecha, '%Y-%m-%d').date()
        if dic_original['fecha'] != nueva_fecha:
            id = None

        dic_original['direccion'] = direc
        dic_original['id_comuna'] = comuna
        dic_original['fecha'] = nueva_fecha
        dic_original['hora_ini'] = convierte_hora(hr_ini)
        dic_original['hora_fin'] = convierte_hora(hr_fin)
        dic_original['hora_ins'] = convierte_hora(hr_ins)
        dic_original['contacto'] = ct
        dic_original['tel_contacto'] = ct_tel
        dic_original['mail_contacto'] = ct_mail
        dic_original['cargo_contacto'] = ct_cargo
        dic_original['orientador'] = ori
        dic_original['tel_orientador'] = ori_tel
        dic_original['mail_orientador'] = ori_mail
        dic_original['estatus'] = est
        dic_original['observaciones'] = obs

        nuevos_datos = modifica_programada(dic_original, consume=True, id=id)
        param['id_modifica'] = None
    
        return nuevos_datos, form_modifica(nuevos_datos, param['user']), param


# ejecución de la aplicación
if __name__ == '__main__':
    app.run_server(debug=False) #True, port=8050)

