import polars as pl
from datetime import date, time, datetime, timedelta
import pytz
import os
import io
import psycopg2

from sqlalchemy import create_engine, URL, text
from sqlalchemy.orm import Session
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.pool import NullPool

import dash
from dash import dcc
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash_extensions.enrich import Input, Output, State, DashProxy, MultiplexerTransform, html
from dash.exceptions import PreventUpdate

from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.colors import HexColor

### Parámetros
# período de duración de las visitas

fecha_inicial = date(2024, 3, 1)
fecha_final = date(2024, 11, 30)

# inicialización de usuario

usuario = 0

### Conexión
# parámetros de conexión

objeto_url = URL.create(
    'postgresql+psycopg2',
    username = os.environ['PGUSER'],
    password = os.environ['PGPASSWORD'],
    host = os.environ['PGHOST'],
    port = os.environ['PGPORT'],
    database = os.environ['PGDATABASE'],
)

# string_conn = os.environ['DATABASE_PRIVATE_URL'] + '?connect_timeout=300'

engine = create_engine(objeto_url, pool_pre_ping=True, poolclass=NullPool)

# creación de clases de las bases de datos

Base = automap_base()
Base.prepare(autoload_with=engine)

Propuesta = Base.classes.propuestas
Programada = Base.classes.programadas
Asiste = Base.classes.asisten

### Lectura de datos
# función que convierte columnas datetime a str

def convierte_a_str(df):
    df = (df
        .with_columns([
            pl.col('fecha').dt.strftime('%Y-%m-%d'),
            pl.col('hora_ini').dt.strftime('%H:%M:%S'),
            pl.col('hora_fin').dt.strftime('%H:%M:%S'),
            pl.col('hora_ins').dt.strftime('%H:%M:%S'),
        ])
    )
    return df

# datos de visitas programadas y propuestas

def lectura(db, consume=False):
    df = pl.read_database(
        query = f'SELECT * FROM {db}',
        connection = engine,
    )
    if consume:
        df = convierte_a_str(df)
    return df.to_dicts(), df.schema


programadas, schema_programada = lectura('programadas')
propuestas, schema_propuesta = lectura('propuestas')


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
dia_laboral = lambda: min({ahora() + timedelta(days=i) for i in range(14)}.difference(feriados))

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

# función que convierte diccionario en formato de opciones para dropdown

def opciones(dic):
    return [{'label': v, 'value': k} for k, v in dic.items()]


# MODIFICACIONES PARA RESTRINGIR CANTIDAD DE FERIAS POR DÍA

# función que crea lista de fechas bloqueadas (local)
def bloqueados_local():
    return list(
        pl.DataFrame(programadas)
        .group_by('fecha')
        .agg(
            pl.count('prog_id').alias('cantidad')
        )
        .filter(pl.col('cantidad') >= 3)
        .sort('fecha')
        .get_column('fecha')
    )

# función que verifica fechas bloqueadas (base)
def verifica_bloqueados():
    sql = text("SELECT * FROM bloqueados")
    with Session(engine) as session:
        resultados = session.execute(sql).all()
    return [item[0] for item in resultados]

def chk_bloqueado(fecha, fn, excluye=None):
    bloqueados = fn()
    if excluye in bloqueados:
        bloqueados.remove(excluye)
    return (fecha in bloqueados)

# =================

#### Programadas
# función que crea datos de visualización (insumo: lista de diccionarios) (no lee datos externos)

actualiza = {
    'fecha': pl.Utf8,
    'hora_ini': pl.Utf8,
    'hora_fin': pl.Utf8,
    'hora_ins': pl.Utf8,
}

schema_programada_lectura = schema_programada.copy()
schema_programada_lectura.update(actualiza)

orden = ['prog_id', 'fecha', 'rbd', 'nombre', 'organizador_id', 'organizador', 'estatus']

map_orden_todas = {
    'prog_id': 'ID',
    'fecha': 'Fecha',
    'rbd': 'RBD',
    'nombre': 'Colegio',
    'organizador_id': 'Código',
    'organizador': 'Universidad',
    'direccion': 'Dirección',
    'comuna_id': 'Comuna',
    'hora_ini': 'Inicio',
    'hora_fin': 'Término',
    'hora_ins': 'Instalación',
    'contacto': 'Contacto',
    'contacto_tel': 'Teléfono contacto',
    'contacto_mail': 'Correo contacto',
    'contacto_cargo': 'Cargo contacto',
    'orientador': 'Orientador',
    'orientador_tel': 'Teléfono orientador',
    'orientador_mail': 'Correo orientador',
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

    return df.sort(['fecha', 'prog_id']).to_dicts()


def programadas_fecha(datos, fecha):
    return (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .select(orden)
        .filter(pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d') == fecha)
        .with_columns([
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
            pl.arange(1, pl.len()+1).alias('orden'),
        ])
        .sort(['fecha', 'prog_id'])
    ).to_dicts()


def programadas_usuario(datos, usuario, hoy):
    return (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .select(orden)
        .filter((pl.col('organizador_id') == usuario) & (pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d') >= hoy))
        .with_columns([
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
        ])
        .sort(['fecha', 'prog_id'])
        .with_columns([
            pl.arange(1, pl.len()+1).alias('orden'),
        ])
        .to_dicts()
    )


def exporta_programada(datos, mes, usuario):
    output = io.BytesIO()
    df = (
        pl.DataFrame(datos, schema=schema_programada_lectura)
        .with_columns([
            pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
            pl.col('comuna_id').replace(comunas),
        ])
        .select({0: orden}.get(usuario, list(map_orden_todas.keys())))
        .rename({0: map_orden}.get(usuario, map_orden_todas))
    )
    if mes != 0:
        df = df.filter(pl.col('Fecha').dt.month() == mes)
    df.sort(['Fecha', 'ID']).write_excel(workbook=output, autofilter=False)

    return output.getvalue()


# funciones para la descarga de información detallada de visitas

univ = {str(k): v for k, v in universidades.items()}

def asisten_todas():
    return (
        pl.read_database(
            query = 'SELECT * FROM asisten',
            connection = engine,
        )
        .rename({'programada_id': 'prog_id'})
        .with_columns(
            pl.col('asiste').replace({0: 'No', 1: 'Sí'})
        )
        .pivot(
            index='prog_id',
            columns='organizador_id',
            values='asiste',
        )
        .rename(univ)
        .select(['prog_id'] + list(universidades.values()))
    )

orden2 = ['fecha', 'prog_id', 'organizador_id', 'organizador', 'nombre', 'rbd', 'direccion', 'comuna_id', 'hora_ins', 'hora_ini', 'hora_fin',
          'contacto', 'contacto_tel', 'contacto_mail', 'contacto_cargo', 'orientador', 'orientador_tel', 'orientador_mail', 'estatus', 'observaciones']

def exporta_programada_detalle(mes=0):
    sql = 'SELECT * FROM programadas'
    if mes:
        sql = f'SELECT * FROM programadas WHERE EXTRACT(MONTH FROM fecha) = {mes}'

    output = io.BytesIO()
    df = (
        pl.read_database(query = sql, connection = engine)
        .with_columns(
            pl.col('comuna_id').replace(comunas)
        )
        .select(orden2)
        .join(asisten_todas(), how='left', on='prog_id')
        .rename(map_orden_todas)
        .sort(['Fecha', 'ID'])
        .drop(['Código'])
        .write_excel(workbook=output, autofilter=False)
    )
    return output.getvalue()


# función que lee universidades que asisten a visita

def dic_asisten(id_prog):
    return dict(
        pl.read_database(
            query = f'SELECT * FROM asisten WHERE programada_id = {id_prog} ORDER BY organizador_id',
            connection = engine,
        )
        .select(['organizador_id', 'asiste'])
        .iter_rows()
    )

# funciones que agregan, modifican y eliminan una programada (toman datos externos)

def ob_prog(dic):
    return Programada(
        organizador_id = dic['organizador_id'],
        organizador = dic['organizador'],
        fecha = dic['fecha'],
        rbd = dic['rbd'],
        nombre = dic['nombre'],
        direccion = dic['direccion'],
        comuna_id = dic['comuna_id'],
        hora_ini = dic['hora_ini'],
        hora_fin = dic['hora_fin'],
        hora_ins = dic['hora_ins'],
        contacto = dic['contacto'],
        contacto_tel = dic['contacto_tel'],
        contacto_mail = dic['contacto_mail'],
        contacto_cargo = dic['contacto_cargo'],
        orientador = dic['orientador'],
        orientador_tel = dic['orientador_tel'],
        orientador_mail = dic['orientador_mail'],
        estatus = dic['estatus'],
        observaciones = dic['observaciones'],
    )


def nueva_programada(dic):
    programada = ob_prog(dic)

    with Session(engine) as session:
        session.add(programada)
        session.commit()

    return lectura('programadas')[0]


def modifica_programada(id_prog, dic, cambia_fecha=False, consume=False):

    with Session(engine) as session:
        visita = session.query(Programada).filter(Programada.prog_id == id_prog).first()
        if cambia_fecha:
            session.delete(visita)
            agrega = ob_prog(dic)
            session.add(agrega)
        else:
            visita.fecha = dic['fecha'],
            visita.direccion = dic['direccion'],
            visita.comuna_id = dic['comuna_id'],
            visita.hora_ini = dic['hora_ini'],
            visita.hora_fin = dic['hora_fin'],
            visita.hora_ins = dic['hora_ins'],
            visita.contacto = dic['contacto'],
            visita.contacto_tel = dic['contacto_tel'],
            visita.contacto_mail = dic['contacto_mail'],
            visita.contacto_cargo = dic['contacto_cargo'],
            visita.orientador = dic['orientador'],
            visita.orientador_tel = dic['orientador_tel'],
            visita.orientador_mail = dic['orientador_mail'],
            visita.estatus = dic['estatus'],
            visita.observaciones = dic['observaciones'],

        session.commit()

    return lectura('programadas', consume=consume)[0]


def elimina_programada(id, consume=False):
    with Session(engine) as session:
        elimina = session.query(Programada).filter(Programada.prog_id == id).first()
        session.delete(elimina)
        session.commit()

    return lectura('programadas', consume=consume)[0]

# modifica condición de asistente

def cambia_asiste(usuario, programada, asiste):
    with Session(engine) as session:
        modifica = session.query(Asiste).filter(Asiste.organizador_id == usuario).filter(Asiste.programada_id == programada).first()
        modifica.asiste = asiste,
        session.commit()
    
# reporte

orden_reporte = ['organizador', 'nombre', 'rbd', 'fecha', 'direccion', 'comuna_id', 'hora_ins', 'hora_ini', 'hora_fin', 'orientador']
map_orden_reporte = map_orden_todas.copy()
map_orden_reporte['organizador'] = 'Organizador'

def def_asisten(id_prog):
    return (
        pl.read_database(
            query = 'SELECT * FROM asisten',
            connection = engine,
        )
        .filter((pl.col('asiste') == 1) & (pl.col('programada_id') == id_prog))
        .sort('organizador_id')
        .to_dicts()
    )
    
def exporta_reporte(visita, asisten):
    output = io.BytesIO()

    cm = 72. / 2.54
    top = 792. - (1.3 * cm) - 24
    step = 19
    canvas = Canvas(output, pagesize=(612., 792.))

    canvas.setFont('Helvetica', 24)
    canvas.setFillColor(HexColor('#1b81e5'))
    canvas.drawCentredString(306, top, 'Programa de Visitas a Colegios 2024')
    top -= (step + 14)
    
    canvas.setFont('Helvetica', 16)
    canvas.drawString((2 * cm), top, 'Antecedentes de la Visita')
    top -= (step + 4)
    
    canvas.setFont('Helvetica', 12)
    canvas.setFillColor(HexColor('#596a6d'))
    for item in orden_reporte:
        canvas.drawString((2.5 * cm), top, f'{map_orden_reporte[item]}')
        canvas.drawString((2.5 * cm)*2.02, top, ':')
        canvas.drawString((2.5 * cm)*2.17, top, f'{formato_items.get(item, lambda x: x)(visita[item])}')
        top -= step
    
    top -= step
    canvas.setFont('Helvetica', 16)
    canvas.setFillColor(HexColor('#1b81e5'))
    canvas.drawString((2 * cm), top, 'Universidades Participantes')
    top -= (step + 4)
    
    canvas.setFont('Helvetica', 12)
    canvas.setFillColor(HexColor('#596a6d'))
    for asiste in asisten:
        canvas.drawString((2.5 * cm), top, f"{universidades[asiste['organizador_id']]}")
        top -= step

    canvas.save()

    retorna = output.getvalue()
    output.close()

    return retorna


#### Propuestas
# función que selecciona datos para la visualización y edición del listado de propuestas de colegios

def propuesta_vista(datos, usuario=None):
    if usuario:
        return (
            pl.DataFrame(datos, schema=schema_propuesta)
            .filter(pl.col('organizador_id') == usuario)
            .select(['prop_id', 'organizador_id', 'rbd', 'nombre']).to_dicts()
        )
    else:
        return (
            pl.DataFrame(datos, schema=schema_propuesta)
            .sort(['organizador_id'])
            .select(['rbd', 'nombre', 'organizador']).to_dicts()
        )


def exporta_propuesta(datos):
    output = io.BytesIO()
    (
        pl.DataFrame(datos, schema=schema_propuesta)
        .sort(['organizador_id'])
        .select(['rbd', 'nombre', 'organizador'])
        .rename({'rbd': 'RBD', 'nombre': 'Colegio', 'organizador': 'Proponente'})
    ).write_excel(workbook=output, autofilter=False)
    return output.getvalue()

# funciones que agregan y eliminan una propuesta (de base PostgreSQL)

def nueva_propuesta(dic):
    propuesta = Propuesta(
        organizador_id=dic['organizador_id'],
        organizador=dic['organizador'],
        rbd=dic['rbd'],
        nombre=dic['nombre'],
    )

    with Session(engine) as session:
        session.add(propuesta)
        session.commit()

    return lectura('propuestas')[0]


def elimina_propuesta(id):
    with Session(engine) as session:
        elimina = session.query(Propuesta).filter(Propuesta.prop_id == id).first()
        session.delete(elimina)
        session.commit()

    return lectura('propuestas')[0]


### Construcción de la aplicación
# color azul de tab, botones, footer, etc.

color = '#2FA4E7'

linea = html.Hr(style={'borderWidth': '0.3vh', 'width': '100%', 'color': '#104e8b'})
espacio = html.Br()

# fecha_sel = max(ahora(), fecha_inicial)
fecha_sel = max(dia_laboral(), fecha_inicial)
mes_sel = fecha_sel.month


# #### Encabezado
# encabezado

encabezado = html.Div(
    dbc.Row([
        dbc.Col(
            html.Img(src='./assets/cup-logo-1.svg', style={'width': '140%', 'height': '140%'}),
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
        html.Div(id='contenido-visual'),
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
            style={'height': '800px', 'width': '1000px'}
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
        style={'height': '800px', 'width': 1250}
    )

# botón que exporta selección a excel

btn_exp_visitas = dbc.Row([
    html.Button('Exportar a Excel', id='exporta-visitas', className='btn btn-outline-primary',
                style={'width': '15%', 'marginRight': 10, 'marginTop': 15, 'padding': '6px 20px'}),
    dcc.Download(id='exporta-visitas-archivo'),
], justify='end',)

# modal con la información de la visita

reporte_programada = html.Div(
    dbc.Modal(
        [
            dbc.ModalHeader(html.H4('Información de la visita'), close_button=False),
            dbc.ModalBody(id='reporte-prog-contenido'),
            dbc.ModalFooter(
                html.Div([
                    dbc.Button('Descargar reporte', id='descarga-reporte', outline=True, color="primary", className='me-2'),
                    dbc.Button('Cerrar', id='btn-cerrar-reporte-prog', outline=True, color="primary", className='me-2'),
                    dcc.Download(id='descarga-reporte-archivo'),
                ])
            ),
        ],
        id='modal-reporte-prog',
        size='lg',
        keyboard=False,
        backdrop="static",
   ),
)

# forma
def form_visualiza(datos, mes):
    return dbc.Form([
        html.H3(['Visitas Programadas'], style={'marginLeft': 15, 'marginBottom': 12, 'marginTop': 10}),
        html.Div(botones_mes(mes)),
        html.Div(grid_programadas(datos, mes)),
        html.Div(reporte_programada),
        html.Div(btn_exp_visitas),
    ], id='form-visualiza')


#### Reporte visita
# diccionario que da formato

fto_hora = lambda x: x[:-3] + ' hrs.' if x != '00:00:00' else ''
fto_blanco = lambda x: '' if x == None else x

formato_items = {
    'fecha': lambda x: f'{datetime.strptime(x, "%Y-%m-%d").date():%A, %d %B %Y}',
    'direccion': fto_blanco,
    'comuna_id': lambda x: comunas[x],
    'hora_ins': fto_hora,
    'hora_ini': fto_hora,
    'hora_fin': fto_hora,
    'orientador': fto_blanco,
}

def da_formato(dic):
    return {k: formato_items.get(k, lambda x: x)(dic[k]) for k in dic.keys()}

# parte general del modal

items_reporte = ['organizador', 'nombre', 'rbd', 'fecha', 'direccion', 'comuna_id', 'hora_ins', 'hora_ini', 'hora_fin', 'orientador']
items_reporte_label = ['Organizador', 'Nombre', 'RBD', 'Fecha', 'Dirección', 'Comuna', 'Hora instalación', 'Hora inicio', 'Hora término', 'Orientador']
items_reporte_dic = dict(zip(items_reporte, items_reporte_label))

def info_gral(item, dic):
    return html.Div([
        html.P(items_reporte_dic[item], style={'width': '18%', 'fontSize': '16px', 'marginLeft': 20, 'marginBottom': -2, 'display': 'inline-block'}),
        html.P(': ' + str(dic[item]), style={'width': '75%', 'fontSize': '16px', 'marginBottom': -2, 'display': 'inline-block'}),
    ])

def seccion_info_gral(dic):
    dic_reducido = {k: dic[k] for k in items_reporte}
    dic_reducido_fto = da_formato(dic_reducido)
    return html.Div(
        [html.H6('Información general:', style={'fontSize': '17px'})] +
        [info_gral(item, dic_reducido_fto) for item in items_reporte]
    )

# observaciones

def seccion_observaciones(dic):
    valor = '' if dic['observaciones'] == None else dic['observaciones']
    return html.Div(
        dbc.Row([
            html.P('Observaciones', style={'width': '18%', 'fontSize': '16px', 'marginLeft': 20, 'marginBottom': -2, 'display': 'inline-block'}),
            dcc.Textarea(value=valor, style={'fontSize': '15px', 'width': '70%', 'height': 80, 'display': 'inline-block'}),
        ]),
        style={'marginTop': 20}
    )

# listado de universidades asistentes

def universidad_asiste(n, usuario):
    return html.Div(
        html.P(str(n) + ') ' + universidades[usuario], style={'marginLeft': 20, 'marginBottom': -2, 'fontSize': '16px', 'fontWeight': 'normal'})
    )

def seccion_universidades_asisten(dic, crt=1):
    dic_crt = {k: v for k, v in dic.items() if v == crt}
    return html.Div([
        html.H6('Universidades que asisten:' if crt == 1 else 'Universidades que no asisten:', style={'fontSize': '17px'}),
        dbc.Col(
            [universidad_asiste(n, usuario) for n, usuario in enumerate(dic_crt.keys(), start=1)]
        )
    ])

# selector de participación

def selector_asiste(usuario, dic):
    return html.Div([
        linea,
        html.H6('Asistencia a visita:', style={'fontSize': '17px'}),
        dbc.Row([
            html.P(f'{universidades[usuario]}:', style={'width': '60%', 'fontSize': '16px', 'marginTop': 2, 'marginLeft': 20, 'display': 'inline-block'}),
            dcc.RadioItems(
                id = 'selector-asiste',
                options=[
                   {'label': 'Sí', 'value': 1},
                   {'label': 'No', 'value': 0},
                ],
                value = dic[usuario],
                style = {'textAlign': 'start', 'width': '20%', 'display': 'inline-block'},
                labelStyle = {'display': 'inline-block', 'fontSize': '16px', 'fontWeight': 'normal'},
                inputStyle = {'marginRight': '5px', 'marginLeft': '20px'},
            )
        ]),
    ], style={'marginLeft': 10, 'marginBottom': 1, 'marginTop': 20})


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
        style={'height': '600px', 'width': '650px'}
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
                min_date_allowed=dia_laboral(),
                max_date_allowed=fecha_final,
                disabled_days=feriados,
                first_day_of_week=1,
                initial_visible_month=str(mes_sel),
                date=dia_laboral(),
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
    html.Button('Agregar visita', id='ag-visita', n_clicks=0, className='btn btn-outline-primary', style={'width': '16%', 'marginLeft': 15},
                 disabled=chk_bloqueado(dia_laboral(), bloqueados_local)),
])

# modal que informa que fecha no está disponible
fecha_no_disponible = html.Div(
    dbc.Modal(
        [
            dbc.ModalHeader(html.H4('No es posible agregar visita')),
            dbc.ModalBody(html.Div('La fecha escogida ya no está disponible para agregar una nueva visita. Algún otro usuario la ocupó en el intertanto.')),
            dbc.ModalFooter(dbc.Button('Cerrar', id='cerrar-fecha-no-disponible')),
        ],
        id='modal-fecha-no-disponible',
        size='lg',
        centered=True,
    ),
)

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
        linea,
        fecha_no_disponible,
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
                id='ferias-prg-usr',
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


# fecha: cambiar la fecha debiera ser equivalente a crear una nueva visita
def mod_fecha(datos, fecha):
    return html.Div(
        dbc.Row([
            dbc.Col([
                html.H5(['Fecha:']),
                dcc.DatePickerSingle(
                    id='mod-fecha',
                    min_date_allowed=dia_laboral(),
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

# modal que informa que fecha no está disponible
fecha_no_disponible2 = html.Div(
    dbc.Modal(
        [
            dbc.ModalHeader(html.H4('No es posible agregar visita')),
            dbc.ModalBody(html.Div('La fecha escogida ya no está disponible para agregar una nueva visita. Algún otro usuario la ocupó en el intertanto.')),
            dbc.ModalFooter(dbc.Button('Cerrar', id='cerrar-fecha-no-disponible2')),
        ],
        id='modal-fecha-no-disponible2',
        size='lg',
        centered=True,
    )
)

def form_modifica_visita(datos, original):
    return dbc.Form([
        html.H5(['Modificación de datos de visita'], style={'marginLeft': 15, 'marginTop': 20}),
        linea,
        identificacion(original['nombre'], original['rbd']),
        linea,
        mod_estatus(original['estatus']),
        linea,
        mod_direccion(original['direccion'], original['comuna_id']),
        linea,
        mod_fecha(datos, original['fecha']),
        linea,
        mod_horario(original['hora_ini'], original['hora_fin'], original['hora_ins']),
        linea,
        mod_contacto(original['contacto'], original['contacto_tel'], original['contacto_mail'], original['contacto_cargo'],
                     original['orientador'], original['orientador_tel'], original['orientador_mail']),
        linea,
        mod_observaciones(original['observaciones']),
        linea,
        botones_acepta_modifica,
        fecha_no_disponible2,
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
    'mes': -1,
    'fecha_ori': fecha_inicial,
    'tab_visual': 'tabviz2',
    'tab_edit': 'tab-ed2',
    'rbd_propuesta': None,
    'id_modifica': None,
}


#### Layout

app = DashProxy(__name__, transforms=[MultiplexerTransform()], external_stylesheets=[dbc.themes.CERULEAN])

app.config.suppress_callback_exceptions = True

# layout de la aplicación
def serve_layout():
    return dbc.Container([
        encabezado,
        html.Div(usuario_actual(usuario), id='contenido-usuario'),    
        tabs_inicio(usuario),
        form_footer(),

        dcc.Store(id='datos-programadas', data=lectura('programadas')[0]),
        dcc.Store(id='datos-propuestas', data=lectura('propuestas')[0]),
        dcc.Store(id='parametros', data=parametros_iniciales),
    ])

app.layout = serve_layout

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


# 3.1 despliegue de las opciones de visualización
@app.callback(
    Output('contenido-visual', 'children'),
    Output('parametros', 'data'),
    Input('tabs-visual', 'value'),
    State('datos-programadas', 'data'),
    State('datos-propuestas', 'data'),
    State('parametros', 'data'),
)
def crea_contenido_visualizacion(tab, datos, datos_prop, param):
    if param['mes'] == -1:
        param['mes'] = max(dia_laboral(), fecha_inicial).month
    if tab == 'tabviz1':
        param['tab_visual'] = tab
        return html.Div(form_vista_propuestos_gral(datos_prop)), param  # <= ***
    elif tab == 'tabviz2':
        param['tab_visual'] = tab
        return html.Div(form_visualiza(datos, param['mes'])), param


# 3.2 despliegue de las opciones de edición
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


# RADIO: modifica la visualización de las visitas programadas
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


# cambio de día
@app.callback(
    Output('ferias-prg', 'rowData'),
    Input('sel-fecha', 'date'),
    State('datos-programadas', 'data'),
)
def ferias_programadas_fecha(fecha, datos):
    return programadas_fecha(datos, datetime.strptime(fecha, '%Y-%m-%d').date())


# sleccición de RBD y nombre
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

# ====================================================================

# añade visita a base de datos / faltan observaciones
@app.callback(
    Output('modal-fecha-no-disponible', 'is_open'),  # modal con advertencia que no es posible agregar visita
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

    Input('ag-visita', 'n_clicks'),
    Input('cerrar-fecha-no-disponible', 'n_clicks'),  # botón que cierra modal

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
    prevent_initial_call=True,
)
def agrega_feria(click, click2, param, fecha_str, rbd, direc, comuna, hr_ini, hr_fin, hr_ins, ct, ct_tel, ct_mail, ct_cargo, ori, ori_tel, ori_mail, est, obs):
    if click == 0:
        raise PreventUpdate
    else:
        disparador = dash.ctx.triggered_id
    
        if disparador == 'cerrar-fecha-no-disponible':
            return False, dash.no_update, dash.no_update, *[dash.no_update]*16
    
        elif disparador == 'ag-visita':
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()       
            if chk_bloqueado(fecha, verifica_bloqueados):
                return True, dash.no_update, dash.no_update, *[dash.no_update]*16
            else:
                dic_datos = {}
        
                dic_datos['organizador_id'] = param['user']
                dic_datos['organizador'] = universidades[param['user']]
                dic_datos['fecha'] = fecha
                dic_datos['rbd'] = rbd
                dic_datos['nombre'] = colegios[rbd]
                dic_datos['direccion'] = direc
                dic_datos['comuna_id'] = comuna
                dic_datos['hora_ini'] = convierte_hora(hr_ini)
                dic_datos['hora_fin'] = convierte_hora(hr_fin)
                dic_datos['hora_ins'] = convierte_hora(hr_ins)
                dic_datos['contacto'] = ct
                dic_datos['contacto_tel'] = ct_tel
                dic_datos['contacto_mail'] = ct_mail
                dic_datos['contacto_cargo'] = ct_cargo
                dic_datos['orientador'] = ori
                dic_datos['orientador_tel'] = ori_tel
                dic_datos['orientador_mail'] = ori_mail
                dic_datos['estatus'] = est
                dic_datos['observaciones'] = obs
            
                nuevos_datos = nueva_programada(dic_datos)
            
                return False, nuevos_datos, form_agrega(), *[None]*16

# ====================================================================

# agrega colegio a listado de colegios propuestos
@app.callback(
    Output('datos-propuestas', 'data'),
    Output('contenido-edicion', 'children'),
    Input('btn-ag-prop', 'n_clicks'),
    State('parametros', 'data'),
    State('in-rbd-prop', 'value'),
#    State('in-nom-prop', 'label'),
    prevent_initial_call=True,
)
def arega_propuesta(click, param, rbd):
    if click == 0:
        raise PreventUpdate
    else:
        dic = {
            'organizador_id': param['user'],
            'organizador': universidades[param['user']],
            'rbd': rbd,
            'nombre': colegios[rbd],
        }
        df = nueva_propuesta(dic)
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
    if param['user'] == 0:
        df = exporta_programada(datos, param['mes'], param['user'])
        return dcc.send_bytes(df, 'visitas.xlsx')
    else:
        df = exporta_programada_detalle(param['mes'])
        return dcc.send_bytes(df, 'visitas_detalle.xlsx')


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
            id_el = filas[0]['prop_id']
            usuario = filas[0]['organizador_id']
            df = elimina_propuesta(id_el)
            return df, form_colegios_prop(df, usuario)
        else:
            return dash.no_update, dash.no_update,


# BOTON elimina selección de listado de colegios programados
@app.callback(
    Output('datos-programadas', 'data'),
    Output('contenido-edicion', 'children'), # recibe la misma forma actualizada: form_modifica
    Input('btn-elim-visita', 'n_clicks'),
    State('ferias-prg-usr', 'selectedRows'),
    prevent_initial_call=True,
)
def elimina_colegio_programado(click, filas):
    if click == 0:
        raise PreventUpdate
    else:
        if filas:
            id_el = filas[0]['prog_id']
            usuario = filas[0]['organizador_id']
            df = elimina_programada(id_el, consume=True)
            return df, form_modifica(df, usuario)
        else:
            return dash.no_update, dash.no_update


# modifica colegio programado
@app.callback(
    Output('contenido-edicion', 'children'),
    Output('parametros', 'data'),
    Input('btn-mod-visita', 'n_clicks'),
    State('datos-programadas', 'data'),
    State('ferias-prg-usr', 'selectedRows'),
    State('parametros', 'data'),
    prevent_initial_call=True,
)
def modifica_colegio_programado(click, datos, filas, param):
    if click == 0:
        raise PreventUpdate
    else:
        if filas:
            id_mod = filas[0]['prog_id']
            dic_original = (
                pl.DataFrame(datos)
                .filter(pl.col('prog_id') == id_mod)
                .with_columns([
                    pl.col('fecha').str.strptime(pl.Date, '%Y-%m-%d'),
                    pl.col('hora_ini').str.strptime(pl.Time, '%H:%M:%S'),
                    pl.col('hora_fin').str.strptime(pl.Time, '%H:%M:%S'),
                    pl.col('hora_ins').str.strptime(pl.Time, '%H:%M:%S'),
                ])
                .to_dicts()
            )[0]
            param['id_modifica'] = id_mod
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

# ====================================================================

# aplicar cambios en ventana de modificaciones
@app.callback(
    Output('modal-fecha-no-disponible2', 'is_open'),
    Output('datos-programadas', 'data'),
    Output('contenido-edicion', 'children'),
    Output('parametros', 'data'),

    Input('btn-mod-aplica', 'n_clicks'),
    Input('cerrar-fecha-no-disponible2', 'n_clicks'),

    State('datos-programadas', 'data'),
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
def aplica_cambios(click, click2, datos, param, direc, comuna, fecha_str, hr_ini, hr_fin, hr_ins, ct, ct_tel, ct_mail, ct_cargo, ori, ori_tel, ori_mail, est, obs):
    if click == 0:
        raise PreventUpdate
    else:
        disparador = dash.ctx.triggered_id
    
        if disparador == 'cerrar-fecha-no-disponible2':
            return False, dash.no_update, dash.no_update, dash.no_update
    
        elif disparador == 'btn-mod-aplica':
            nueva_fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()       
            fecha_original = datetime.strptime(param['fecha_ori'], '%Y-%m-%d').date()
            if chk_bloqueado(nueva_fecha, verifica_bloqueados, excluye=fecha_original):
                return True, dash.no_update, dash.no_update, dash.no_update
            else:
                id_visita = param['id_modifica']
                dic_original = next(item for item in datos if item['prog_id'] == id_visita)
        
                cambia_fecha = False
                if fecha_original != nueva_fecha:
                    cambia_fecha = True
        
                dic_original['fecha'] = nueva_fecha
                dic_original['direccion'] = direc
                dic_original['comuna_id'] = comuna
                dic_original['hora_ini'] = convierte_hora(hr_ini)
                dic_original['hora_fin'] = convierte_hora(hr_fin)
                dic_original['hora_ins'] = convierte_hora(hr_ins)
                dic_original['contacto'] = ct
                dic_original['contacto_tel'] = ct_tel
                dic_original['contacto_mail'] = ct_mail
                dic_original['contacto_cargo'] = ct_cargo
                dic_original['orientador'] = ori
                dic_original['orientador_tel'] = ori_tel
                dic_original['orientador_mail'] = ori_mail
                dic_original['estatus'] = est
                dic_original['observaciones'] = obs
        
                nuevos_datos = modifica_programada(id_visita, dic_original, cambia_fecha=cambia_fecha, consume=True)
                param['id_modifica'] = None
            
                return False, nuevos_datos, form_modifica(nuevos_datos, param['user']), param

# ====================================================================

# abre modal con la información de la visita programada
@app.callback(
    Output('modal-reporte-prog', 'is_open'),
    Output('reporte-prog-contenido', 'children'),
    Output('viz-ferias', 'selectedRows'),
    Input('viz-ferias', 'selectedRows'),
    Input('btn-cerrar-reporte-prog', 'n_clicks'),
    State('datos-programadas', 'data'),
    State('parametros', 'data')
)
def abre_modal_reporte(visita, _, datos, param):
    disparador = dash.ctx.triggered_id

    if disparador == 'btn-cerrar-reporte-prog':
        return False, dash.no_update, []

    if visita:
        id_sel = visita[0]['prog_id']
        asiste_dic = dic_asisten(id_sel)
        datos = next(item for item in datos if item['prog_id'] == id_sel)
        if param['user'] == 0:
            return True, html.Div([
                seccion_info_gral(datos),
                linea,
                seccion_universidades_asisten(asiste_dic),
            ]), dash.no_update
        else:
            return True, html.Div([
                seccion_info_gral(datos),
                seccion_observaciones(datos),
                linea,
                seccion_universidades_asisten(asiste_dic),
                espacio,
                seccion_universidades_asisten(asiste_dic, crt=0),
                selector_asiste(param['user'], asiste_dic),
            ]), dash.no_update

    return dash.no_update, dash.no_update, dash.no_update

# cambia la condición de asistente a visita
@app.callback(
    Output('parametros', 'data'),
    Input('selector-asiste', 'value'),
    State('viz-ferias', 'selectedRows'),
    State('parametros', 'data'),
#    prevent_initial_call=True,
)
def cambia_condicion_asiste(asiste, visita, param):
    id_sel = visita[0]['prog_id']
    cambia_asiste(param['user'], id_sel, asiste)
    return param

# descarga reporte de la visita en formato pdf
@app.callback(
    Output('descarga-reporte-archivo', 'data'),
    Input('descarga-reporte', 'n_clicks'),
    State('viz-ferias', 'selectedRows'),
    State('datos-programadas', 'data'),
    prevent_initial_call=True,
)
def descarga_reporte_pdf(_, filas, datos):
    id_rep = filas[0]['prog_id']
    visita = next(item for item in datos if item['prog_id'] == id_rep)
    asisten = def_asisten(id_rep)
    doc = exporta_reporte(visita, asisten)
    return dcc.send_bytes(doc, f"reporte_{str(visita['rbd'])}.pdf")


# restringe visibilidad de boton que agrega visita
@app.callback(
    Output('ag-visita', 'disabled'),
    Input('sel-fecha', 'date')
)
def evalua_fecha_bloqueada(fecha_str):
    fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    return chk_bloqueado(fecha, bloqueados_local)


# restringe visibilidad de boton que modifica visita
@app.callback(
    Output('btn-mod-aplica', 'disabled'),
    Input('mod-fecha', 'date'),
    State('parametros', 'data'),
)
def evalua_fecha_bloqueada_2(fecha_str, param):
    fecha_original = datetime.strptime(param['fecha_ori'], '%Y-%m-%d').date()
    fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    return chk_bloqueado(fecha, bloqueados_local, excluye=fecha_original)


# rescata fecha de visita a modificar
@app.callback(
    Output('parametros', 'data'),
    Input('ferias-prg-usr', 'selectedRows'),    
    State('parametros', 'data'),
    prevent_initial_call=True,
)
def guarda_fecha_original(filas, param):
    if filas == []:
        raise PreventUpdate
    else:
        param['fecha_ori'] = datetime.strptime(filas[0]['fecha'], '%Y-%m-%d').date()
        return param


# ejecución de la aplicación
if __name__ == '__main__':
    app.run_server(debug=False)  #True, mode='inline', port=8050)
