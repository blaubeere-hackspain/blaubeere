"""Grafica HTML autonoma para healthscore_v3 (solo healthscore_v3).

Lee assessments.parquet (una fila por empresa y mes, esquema cerrado) y summary.json
desde el directorio de entrada (flag --input-dir, por defecto reports/score_v3/).
NO genera ni modifica el parquet: si falta, falla con un mensaje claro que
menciona la ruta usada.

Tres vistas en un unico HTML sin dependencias externas:
  1. Evolucion mensual por empresa (A vs B) con confidence visible por punto
     (opacidad/relleno) y sin interpolar meses sin nota.
  2. Histograma de la cartera en un corte (bins de 5 puntos, apilado por
     confidence) con barra aparte para empresas sin nota, etiquetada por motivo.
  3. Ranking de empresas en ese corte, ordenable y filtrable, con orden inicial
     confidence descendente + health_score descendente (nunca por nota alta).

La cabecera (model_version, formula, k, alpha, beta, data_workspace, total de
empresas) sale del parquet y de summary.json, nunca de constantes escritas a
mano aqui. La serializacion embebida usa json.dumps(..., allow_nan=False) y el
resultado es determinista. Sin datos reales no se inventa nada: no se muestrean
empresas y, si el payload supera el tope de tamano, se recortan los meses
embebidos (los mas antiguos primero) y se avisa en pantalla.
"""

import argparse
import copy
import html
import json
import math
from pathlib import Path

import duckdb

from xray import paths
from xray.scoring_v3 import CONFIDENCE_LEVELS, LIMITACIONES, VERSION

SCORE_V3_DIR = paths.ROOT / 'reports' / 'score_v3'
ASSESSMENTS_NAME = 'assessments.parquet'
SUMMARY_NAME = 'summary.json'
MISSING_MESSAGE = (f'falta reports/score_v3/{ASSESSMENTS_NAME}, '
                   'ejecuta antes xray.scoring_io_v3')
DEMO_TITLE = 'DEMO SINTETICA · Blaubeere · Healthscore v3 · notas y confianza'
DEFAULT_TITLE = 'Blaubeere · Healthscore v3 · notas y confianza'
DEMO_BANNER = (
    '<div id="demo-sintetica-banner" role="alert" '
    'style="position:sticky;top:0;z-index:1000;background:#8f1d1d;color:#ffffff;'
    'padding:14px 22px;font-weight:700;font-size:15px;text-align:center;'
    'letter-spacing:.3px;box-shadow:0 2px 10px rgba(0,0,0,.35)">'
    '&#9888; DEMOSTRACIÓN CON DATOS SINTÉTICOS GENERADOS ALEATORIAMENTE. '
    'Ninguna cifra de esta página corresponde a una empresa real. '
    'Sirve únicamente para revisar el diseño de la gráfica.</div>')
MAX_BYTES = 25 * 1024 * 1024
TARGET_BYTES = 23 * 1024 * 1024
SCHEMA = (
    ('version', 'VARCHAR'), ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'),
    ('month', 'DATE'), ('as_of', 'DATE'), ('health_score', 'DOUBLE'),
    ('confidence', 'VARCHAR'), ('n_meses_ventana', 'INTEGER'),
    ('n_meses_con_actividad', 'INTEGER'), ('ventana_parcial', 'BOOLEAN'),
    ('c6', 'DOUBLE'), ('t6', 'DOUBLE'), ('p6', 'DOUBLE'), ('d6', 'DOUBLE'),
    ('r_hist', 'DOUBLE'), ('colchon_bruto', 'DOUBLE'),
    ('colchon_aplicable', 'DOUBLE'), ('mora_ratio', 'DOUBLE'),
    ('h_antes_de_mora', 'DOUBLE'), ('penalizacion_mora_puntos', 'DOUBLE'),
    ('volumen_ambiguo_eur', 'DOUBLE'), ('volumen_ambiguo_pct', 'DOUBLE'),
    ('k', 'DOUBLE'), ('alpha', 'DOUBLE'), ('beta', 'DOUBLE'),
    ('reasons', 'VARCHAR[]'),
)


def _clean(value):
    """ float finito o None: NaN/inf no se serializan ni se inventan datos."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _reasons_text(reasons):
    return ' · '.join(str(reason) for reason in reasons) if reasons else 'sin motivo registrado'


def _missing_assessments_message(directory):
    """Mensaje de fichero ausente; con --input-dir propio menciona la ruta usada."""
    if directory.name == 'score_v3' and directory.parent.name == 'reports':
        # Directorio por defecto (el modulo resuelve paths.ROOT en cada llamada,
        # tambien en tests que lo parchean): mensaje literal de siempre.
        return MISSING_MESSAGE
    return (f'falta {directory / ASSESSMENTS_NAME}, ejecuta antes xray.scoring_io_v3 '
            f'(directorio de entrada: {directory})')


def load_assessments(parquet_path):
    """Lee el parquet, valida version y parametros unicos, sanea no finitos."""
    if not parquet_path.exists():
        raise SystemExit(_missing_assessments_message(parquet_path.parent))
    con = duckdb.connect(':memory:')
    try:
        cursor = con.execute('SELECT * FROM read_parquet(?)', [str(parquet_path)])
        columns = [column[0] for column in cursor.description]
        missing = [name for name, _ in SCHEMA if name not in columns]
        if missing:
            raise ValueError(f'assessments.parquet sin columnas esperadas: {", ".join(missing)}')
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        con.close()
    if not raw:
        raise ValueError('assessments.parquet esta vacio')
    versions = {row['version'] for row in raw}
    if len(versions) != 1 or versions.pop() != VERSION:
        raise ValueError('la grafica solo admite healthscore_v3; no se mezclan versiones')
    for field in ('k', 'alpha', 'beta'):
        values = {_clean(row[field]) for row in raw}
        if len(values) != 1 or next(iter(values)) is None:
            raise ValueError(f'el parquet debe declarar un unico {field} finito')
    rows = []
    for row in raw:
        reasons = tuple(str(reason) for reason in (row['reasons'] or []))
        rows.append({
            'company_id': str(row['company_id']), 'group_id': row['group_id'],
            'month': row['month'], 'health_score': _clean(row['health_score']),
            'confidence': row['confidence'] if row['confidence'] in CONFIDENCE_LEVELS else 'ninguna',
            'ventana_parcial': bool(row['ventana_parcial']),
            'c6': _clean(row['c6']), 't6': _clean(row['t6']), 'r_hist': _clean(row['r_hist']),
            'colchon_aplicable': _clean(row['colchon_aplicable']),
            'mora_ratio': _clean(row['mora_ratio']),
            'penalizacion_mora_puntos': _clean(row['penalizacion_mora_puntos']),
            'volumen_ambiguo_pct': _clean(row['volumen_ambiguo_pct']),
            'k': _clean(row['k']), 'alpha': _clean(row['alpha']), 'beta': _clean(row['beta']),
            'reasons': reasons,
        })
    return rows


def _summary_get(summary, keys):
    """Primer valor presente en summary.json (nivel superior o anidado)."""
    if not isinstance(summary, dict):
        return None
    for key in keys:
        if summary.get(key) is not None:
            return summary[key]
    for value in summary.values():
        if isinstance(value, dict):
            for key in keys:
                if value.get(key) is not None:
                    return value[key]
    return None


def _formula_text(summary):
    formula = _summary_get(summary, ('formula', 'formulas', 'formula_text'))
    if formula is None:
        # Fallback: formula cerrada del motor (xray/scoring_v3.py). No es un
        # parametro calibrado: es la definicion del indice.
        formula = ('H = 100 * (C6 + colchon_aplicable + k * R_hist) / '
                   '(C6 + colchon_aplicable + T6 + k); '
                   'H_final = H * (1 - beta * mora_ratio)')
    return str(formula)


def score_segments(scores):
    """Trazos de la serie: cada mes sin nota parte la linea, nunca se une."""
    segments = []
    current = []
    for index, score in enumerate(scores):
        if score is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append([index, score])
    if current:
        segments.append(current)
    return segments


def build_payload(parquet_path, summary_path):
    rows = load_assessments(parquet_path)
    summary = {}
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
    months = sorted({row['month'] for row in rows})
    month_index = {month: index for index, month in enumerate(months)}
    params = {field: rows[0][field] for field in ('k', 'alpha', 'beta')}
    companies = {}
    cuts = {month: {'total': 0, 'confidence': {level: 0 for level in CONFIDENCE_LEVELS},
                    'bins': [{level: 0 for level in CONFIDENCE_LEVELS} for _ in range(20)],
                    'null_motivos': {}} for month in months}
    for row in rows:
        company = companies.setdefault(row['company_id'], {
            'id': row['company_id'], 'group_id': row['group_id'], 'points': {}})
        if company['group_id'] != row['group_id']:
            raise ValueError(f"el grupo de {company['id']} cambio entre meses")
        company['points'][row['month']] = row
        cut = cuts[row['month']]
        cut['total'] += 1
        cut['confidence'][row['confidence']] += 1
        score = row['health_score']
        if score is None:
            motivo = _reasons_text(row['reasons'])
            cut['null_motivos'][motivo] = cut['null_motivos'].get(motivo, 0) + 1
        else:
            cut['bins'][min(19, int(score // 5))][row['confidence']] += 1
    company_rows = []
    for company_id in sorted(companies, key=lambda key: companies[key]['id']):
        company = companies[company_id]
        points = []
        for month in months:
            row = company['points'].get(month)
            points.append({} if row is None else {
                'score': row['health_score'], 'conf': row['confidence'],
                'c6': row['c6'], 't6': row['t6'], 'r_hist': row['r_hist'],
                'colchon': row['colchon_aplicable'], 'mora': row['mora_ratio'],
                'pen': row['penalizacion_mora_puntos'], 'amb': row['volumen_ambiguo_pct'],
                'parcial': row['ventana_parcial'], 'reasons': list(row['reasons']),
            })
        scores = [point.get('score') for point in points]
        company_rows.append({
            'id': company['id'], 'group_id': company['group_id'],
            'scored_months': sum(score is not None for score in scores),
            'segments': score_segments(scores), 'points': points,
        })
    company_rows.sort(key=lambda row: (-row['scored_months'], row['id']))
    return {
        'model_version': str(_summary_get(summary, ('model_version', 'version')) or VERSION),
        'formula': _formula_text(summary),
        'data_workspace': str(_summary_get(summary, ('data_workspace', 'workspace')) or 'desconocido'),
        'k': rows[0]['k'], 'alpha': rows[0]['alpha'], 'beta': rows[0]['beta'],
        'total_companies': len(company_rows),
        'limitations': list(_summary_get(summary, ('limitaciones', 'limitations',
                                                   'supuestos_y_limitaciones')) or LIMITACIONES),
        'months': [month.isoformat() for month in months],
        'companies': company_rows,
        'cuts': {month.isoformat(): cut for month, cut in cuts.items()},
        'reduced_months': False,
        'es_demo_sintetica': bool(_summary_get(summary, ('es_demo_sintetica',
                                                         'demo_sintetica'))),
        'banner_texto': _summary_get(summary, ('banner_texto', 'aviso_banner')),
    }


def _embed(payload):
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return data.replace('<', '\\u003c')


def _aviso_banner(texto):
    texto = html.escape(str(texto), quote=False)
    return ('<div id="aviso-banner" role="alert" '
            'style="position:sticky;top:0;z-index:1000;background:#8f1d1d;color:#ffffff;'
            'padding:14px 22px;font-weight:700;font-size:15px;text-align:center;'
            'letter-spacing:.3px;box-shadow:0 2px 10px rgba(0,0,0,.35)">'
            f'&#9888; {texto}</div>')


def render_chart(payload):
    page = PAGE.replace('__PAYLOAD_DATA__', _embed(payload))
    if payload.get('es_demo_sintetica'):
        # Banner fijo y en lo alto solo cuando los datos son sinteticos: una
        # grafica con numeros inventados sin marcar es peor que no tener grafica.
        page = page.replace(f'<title>{DEFAULT_TITLE}</title>', f'<title>{DEMO_TITLE}</title>')
        page = page.replace('<body>\n', '<body>\n' + DEMO_BANNER + '\n', 1)
    elif payload.get('banner_texto'):
        # Mismo mecanismo de banner fijo, con texto configurable desde summary.json
        # (campo banner_texto): para avisos sobre datos reales, p.ej. defectos conocidos.
        page = page.replace('<body>\n', '<body>\n' + _aviso_banner(payload['banner_texto']) + '\n', 1)
    return page


def _reduced_payload(parquet_path, summary_path, limit=TARGET_BYTES):
    """Payload completo; si supera el tope, recorta meses antiguos (nunca empresas)."""
    payload = build_payload(parquet_path, summary_path)
    if len(_embed(payload).encode('utf-8')) <= limit:
        return payload
    total_months = len(payload['months'])
    keep = total_months
    while keep > 1:
        keep = max(1, keep // 2)
        offset = total_months - keep
        trimmed = copy.deepcopy(payload)
        trimmed['months'] = trimmed['months'][offset:]
        kept = set(trimmed['months'])
        for company in trimmed['companies']:
            company['points'] = company['points'][offset:]
            company['segments'] = [[[index - offset, score] for index, score in segment]
                                    for segment in company['segments'] if segment[0][0] >= offset]
            company['scored_months'] = sum(point is not None and point.get('score') is not None
                                           for point in company['points'])
        trimmed['cuts'] = {month: cut for month, cut in trimmed['cuts'].items() if month in kept}
        trimmed['reduced_months'] = True
        if len(_embed(trimmed).encode('utf-8')) <= limit:
            return trimmed
    raise ValueError('el payload no cabe ni con un solo mes; no se muestrean empresas')


PAGE = r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blaubeere · Healthscore v3 · notas y confianza</title>
<style>
:root{color-scheme:light;--ink:#142c42;--muted:#607183;--blue:#186c98;--orange:#b85517;--line:#dce5eb;--bg:#f3f6f8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,sans-serif}main{max-width:1280px;margin:0 auto;padding:36px 28px}h1{font-size:clamp(26px,4vw,38px);letter-spacing:-1px;margin:6px 0}h2{font-size:18px;margin:0 0 10px}p{margin:6px 0}.eyebrow{font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--blue)}.muted{color:var(--muted)}.panel{background:white;border:1px solid var(--line);border-radius:16px;padding:24px;margin-top:22px}.controls{display:grid;grid-template-columns:1fr 1fr .9fr;gap:18px}label{font-size:13px;font-weight:650;display:block}select,input{display:block;width:100%;font:inherit;color:var(--ink);border:1px solid #c9d6df;border-radius:8px;background:#fff;margin-top:6px;padding:10px}select:focus,input:focus{outline:3px solid #8dcae9;outline-offset:2px}.legend{display:flex;gap:20px;flex-wrap:wrap;margin:18px 0 4px;font-weight:650;font-size:13px}.swatch{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:7px;vertical-align:-1px}.chart-scroll{overflow-x:auto}svg{display:block;width:100%;min-width:640px;height:auto}svg text{font-family:inherit}.note{background:#eef5f9;border-radius:8px;padding:12px 16px;font-size:13px;margin-top:14px}.warning{background:#fff4df;color:#744915;border:1px solid #eed3a1}.slider-row{display:grid;grid-template-columns:220px 1fr;align-items:center;gap:20px;margin:20px 0 12px}input[type=range]{width:100%;margin-top:0;accent-color:var(--blue)}.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{border:1px solid var(--line);border-top:3px solid var(--blue);border-radius:10px;padding:18px;min-width:0}.score{font-size:32px;line-height:1.2;font-weight:700;margin:8px 0}.small{font-size:12px}.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.pill{background:var(--bg);padding:5px 9px;border-radius:5px;font-size:12px}.histo-grid{display:grid;grid-template-columns:1.7fr 1fr;gap:20px;align-items:start}.table-scroll{overflow:auto;max-height:520px}table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{background:#f1f5f8;position:sticky;top:0}td.n,th.n{text-align:right}.filters{display:flex;gap:14px;flex-wrap:wrap;align-items:center;padding-top:22px}.filters label{display:flex;align-items:center;gap:6px;font-weight:600}.filters input{width:auto;margin-top:0;accent-color:var(--blue)}.pager{display:flex;gap:14px;align-items:center;margin-top:14px}.pager button{font:inherit;font-weight:650;border:1px solid #c9d6df;background:#fff;border-radius:8px;padding:8px 16px;cursor:pointer}.pager button:disabled{opacity:.4;cursor:default}.footer-note{font-size:12px;color:var(--muted);margin-top:22px;overflow-wrap:anywhere}.source,code{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}.formula{background:var(--bg);padding:10px 12px;border-radius:8px;font-size:13px}.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#fff}
@media(max-width:760px){main{padding:20px 12px}.panel{padding:16px}.controls,.cards,.histo-grid{grid-template-columns:1fr}.slider-row{grid-template-columns:1fr;gap:6px}}
</style>
</head>
<body>
<main>
<header>
<div class="eyebrow">Blaubeere · Estado observado, no predicción</div>
<h1>Healthscore de flujo mensual · v3</h1>
<p class="muted">Una nota por empresa y mes, con su nivel de confianza: distingue de un vistazo lo medido de lo inferido.</p>
<div class="formula" id="formula"></div>
</header>
<div class="note warning" role="note"><strong>Aviso permanente:</strong> los parámetros k, alpha y beta <strong>NO están calibrados</strong> y esta nota <strong>no es una calificación crediticia</strong>. Limitaciones declaradas por el generador:<ul id="limitations" class="small" style="margin:8px 0 0;padding-left:18px"></ul></div>

<section class="panel" aria-label="Evolución mensual">
<h2>1 · Evolución mensual por empresa</h2>
<div class="controls">
<label>Empresa A<select id="company-a"></select></label>
<label>Comparar con<select id="company-b"><option value="">Sin comparación</option></select></label>
<div class="formula"><strong>Nota única por mes · sin intervalos</strong><br><span class="small muted">v3 no produce rangos: cada mes es un número y un nivel de confianza.</span></div>
</div>
<div id="legend" class="legend"></div>
<div class="chart-scroll"><svg id="chart" viewBox="0 0 1100 440" role="img" aria-label="Evolución mensual del healthscore entre cero y cien; los meses sin nota quedan vacíos y sin unir"></svg></div>
<p class="small muted">Un punto por mes y empresa. 50 = equilibrio (línea marcada). Punto sólido: confianza alta · semitransparente: media · hueco: baja · gris: ninguna. Un mes sin nota se deja vacío y la línea no lo cruza: no se interpola. La selección inicial se ordena por nº de meses con nota, nunca por nota alta.</p>
<div class="slider-row"><label for="inspect">Ficha del mes: <span id="inspect-label"></span></label><input id="inspect" type="range" min="0" step="1" aria-label="Mes para la ficha de empresa"></div>
<div id="cards" class="cards" aria-live="polite"></div>
</section>

<section class="panel" aria-label="Distribución del corte">
<h2>2 · Distribución de la cartera en un corte</h2>
<div class="controls" style="grid-template-columns:1fr"><label>Corte (compartido con el ranking)<select id="cut"></select></label></div>
<div class="histo-grid">
<div>
<div class="chart-scroll"><svg id="histo" viewBox="0 0 800 390" role="img" aria-label="Histograma del healthscore en bins de 5 puntos, apilado por nivel de confianza, con barra aparte para empresas sin nota"></svg></div>
<div id="histo-legend" class="legend"></div>
</div>
<div><p class="small muted" style="margin-top:0">Recuentos por confianza en el corte (cuadran con el universo de empresas del corte):</p><table id="cut-counts"></table></div>
</div>
<p class="small muted">Bins de 5 puntos sobre 0..100, apilados por confianza: importa cuánta de la distribución es medición (alta) y cuánta inferencia. La barra separada de la derecha, fuera del eje 0..100, cuenta las empresas del corte sin nota, etiquetadas por su motivo. Nunca se omiten.</p>
</section>

<section class="panel" aria-label="Ranking del corte">
<h2>3 · Comparación entre empresas en el corte</h2>
<p class="small muted">Orden inicial: confianza descendente y luego health_score descendente. La ventana inicial no se elige por nota alta. Se listan las empresas presentes en el corte seleccionado.</p>
<div class="controls rank-controls">
<label>Buscar por empresa o grupo<input id="rank-search" type="search" placeholder="company_id o group_id…"></label>
<label>Ordenar por<select id="rank-sort">
<option value="rank">Confianza ↓ y nota ↓ (inicial)</option>
<option value="score">health_score ↓</option>
<option value="amb">volumen_ambiguo_pct ↓</option>
<option value="mora">mora_ratio ↓</option>
<option value="colchon">colchon_aplicable ↓</option>
<option value="id">company_id A→Z</option>
</select></label>
<div class="filters" id="rank-filters"></div>
</div>
<div class="table-scroll"><table id="rank-table"></table></div>
<div class="pager"><button id="rank-prev" type="button">← Anterior</button><span id="rank-page" class="small muted"></span><button id="rank-next" type="button">Siguiente →</button></div>
</section>

<footer class="footer-note"><strong>Salud del flujo observado; no es score crediticio, ni probabilidad de impago.</strong> Esta gráfica solo lee healthscore_v3: está prohibido mezclar series de versiones distintas.<p id="source" class="source"></p></footer>
<noscript><p class="note warning">Activa JavaScript para usar los selectores y las vistas.</p></noscript>
</main>
<script id="payload-data" type="application/json">__PAYLOAD_DATA__</script>
<script id="chart-code">
function init(){
  var data=JSON.parse(document.getElementById('payload-data').textContent);
  var NS='http:'+'//www.w3.org/2000/svg';
  var confs=['alta','media','baja','ninguna'];
  var confNames={alta:'alta · medida',media:'media',baja:'baja · inferida',ninguna:'ninguna'};
  var confColor={alta:'#186c98',media:'#5d95b8',baja:'#b85517',ninguna:'#9fb5c4'};
  var slotColors=['#186c98','#b85517'];
  var confRank=function(c){return confs.indexOf(c);};
  var element=function(tag,text,cls){var n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  var number=function(v,d){d=d===undefined?1:d;return v===null||v===undefined?'—':Number(v).toLocaleString('es-ES',{minimumFractionDigits:d,maximumFractionDigits:d});};
  var money=function(v){return v===null||v===undefined?'—':Number(v).toLocaleString('es-ES',{style:'currency',currency:'EUR'});};
  var percent=function(v){return v===null||v===undefined?'—':number(v)+'%';};
  var monthDates=data.months.map(function(m){return new Date(m+'T12:00:00Z');});
  var shortDate=function(i){return monthDates[i].toLocaleDateString('es-ES',{month:'short',year:'2-digit',timeZone:'UTC'});};
  var longDate=function(i){return monthDates[i].toLocaleDateString('es-ES',{month:'long',year:'numeric',timeZone:'UTC'});};
  var byId=new Map(data.companies.map(function(c){return [c.id,c];}));

  // --- Cabecera y limitaciones (todo desde el fichero / summary.json) ---
  var formula=document.getElementById('formula');
  formula.appendChild(element('strong',data.model_version+' · '+data.total_companies+' empresas · '+data.months.length+' meses'));
  formula.appendChild(document.createElement('br'));
  formula.appendChild(element('code',data.formula));
  formula.appendChild(document.createElement('br'));
  formula.appendChild(element('code','k = '+number(data.k,2)+' · alpha = '+number(data.alpha,2)+' · beta = '+number(data.beta,2)+' · '+data.data_workspace));
  data.limitations.forEach(function(text){var li=document.createElement('li');li.textContent=text;document.getElementById('limitations').appendChild(li);});
  document.getElementById('source').textContent=data.model_version+' · '+data.data_workspace;

  // --- Controles comunes ---
  var a=document.getElementById('company-a'),b=document.getElementById('company-b');
  data.companies.forEach(function(company){
    var label=company.id+' · '+(company.group_id||'Grupo desconocido')+' · '+company.scored_months+' notas';
    a.appendChild(new Option(label,company.id));b.appendChild(new Option(label,company.id));
  });
  a.value=data.companies.length?data.companies[0].id:'';
  b.value=data.companies.length>1?data.companies[1].id:'';
  var cut=document.getElementById('cut');
  for(var m=data.months.length-1;m>=0;m--) cut.appendChild(new Option(longDate(m),data.months[m]));
  cut.value=data.months[data.months.length-1];
  var inspect=document.getElementById('inspect');
  inspect.max=data.months.length-1;inspect.value=data.months.length-1;

  // Leyenda fija de confianza (vista 1)
  var legend=document.getElementById('legend');
  legend.appendChild(legendItem(slotColors[0],'Empresa A'));
  legend.appendChild(legendItem(slotColors[1],'Empresa B'));
  confs.forEach(function(conf){
    var item=element('span');
    var sw=element('span','','swatch');
    if(conf==='baja'){sw.style.background='#fff';sw.style.border='2px solid '+confColor[conf];}
    else{sw.style.background=confColor[conf];if(conf==='media')sw.style.opacity=0.55;}
    item.appendChild(sw);item.appendChild(document.createTextNode('Confianza '+confNames[conf]));
    legend.appendChild(item);
  });
  function legendItem(color,text){
    var item=element('span'),sw=element('span','','swatch');
    sw.style.background=color;sw.style.borderRadius='3px';item.appendChild(sw);
    item.appendChild(document.createTextNode(text));return item;
  }
  var histoLegend=document.getElementById('histo-legend');
  confs.slice().reverse().forEach(function(conf){
    var item=element('span'),sw=element('span','','swatch');
    sw.style.background=confColor[conf];sw.style.borderRadius='2px';
    item.appendChild(sw);item.appendChild(document.createTextNode('Confianza '+confNames[conf]));
    histoLegend.appendChild(item);
  });

  function svgInto(parent){
    return function(tag,attrs,text){
      var n=document.createElementNS(NS,tag);
      for(var key in attrs)n.setAttribute(key,attrs[key]);
      if(text!==undefined)n.textContent=text;
      parent.appendChild(n);return n;
    };
  }

  // ===================== VISTA 1 =====================
  var chart=document.getElementById('chart');
  function renderSeries(){
    if(b.value===a.value)b.value='';
    var companies=[byId.get(a.value),byId.get(b.value)].filter(Boolean);
    var selected=Number(inspect.value);
    document.getElementById('inspect-label').textContent=longDate(selected);
    chart.replaceChildren();
    var x=function(i){return 62+i*1006/Math.max(1,monthDates.length-1);};
    var y=function(s){return 330-s*3;};
    for(var v=0;v<=100;v+=25){
      svgInto(chart)('line',{x1:62,x2:1068,y1:y(v),y2:y(v),stroke:v===50?'#9fb5c4':'#dce5eb','stroke-width':v===50?2:1});
      svgInto(chart)('text',{x:48,y:y(v)+4,'text-anchor':'end',fill:'#607183','font-size':12},v);
    }
    svgInto(chart)('text',{x:1072,y:y(50)-6,'text-anchor':'end',fill:'#607183','font-size':11},'50 · equilibrio');
    monthDates.forEach(function(_,i){
      if(i%2===0||i===monthDates.length-1)
        svgInto(chart)('text',{x:x(i),y:395,'text-anchor':'end',transform:'rotate(-35 '+x(i)+' 395)',fill:'#607183','font-size':12},shortDate(i));
    });
    svgInto(chart)('line',{x1:x(selected),x2:x(selected),y1:20,y2:368,stroke:'#a9b9c5','stroke-dasharray':'4 5'});
    companies.forEach(function(company,slot){
      var color=slotColors[slot];
      company.segments.forEach(function(segment){
        if(segment.length>1)
          svgInto(chart)('polyline',{points:segment.map(function(pt){return x(pt[0])+','+y(pt[1]);}).join(' '),fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round'});
      });
      company.points.forEach(function(point,i){
        if(!point||point.score===null||point.score===undefined)return;
        var conf=point.conf,rank=confRank(conf);
        var label=company.id+' · '+longDate(i)+' · nota '+number(point.score)+' · confianza '+conf;
        var mark;
        if(conf==='baja'){
          mark=svgInto(chart)('circle',{cx:x(i),cy:y(point.score),r:5,fill:'#ffffff',stroke:color,'stroke-width':2,tabindex:0,role:'button','aria-label':label});
        }else{
          mark=svgInto(chart)('circle',{cx:x(i),cy:y(point.score),r:5,fill:rank===3?'#9fb5c4':color,'fill-opacity':conf==='media'?0.55:1,stroke:'#ffffff','stroke-width':1.5,tabindex:0,role:'button','aria-label':label});
        }
        var title=document.createElementNS(NS,'title');
        title.textContent=label+'\nc6 '+money(point.c6)+' · t6 '+money(point.t6)+' · r_hist '+number(point.r_hist,3);
        mark.appendChild(title);
        var goTo=function(){inspect.value=i;renderSeries();};
        mark.addEventListener('click',goTo);
        mark.addEventListener('keydown',function(event){if(event.key==='Enter'||event.key===' '){event.preventDefault();goTo();}});
      });
    });
    var cards=document.getElementById('cards');cards.replaceChildren();
    companies.forEach(function(company,slot){
      var point=company.points[selected];
      var card=element('article',undefined,'card');card.style.borderTopColor=slotColors[slot];
      card.appendChild(element('h2',company.id));
      card.appendChild(element('p',(company.group_id||'Grupo desconocido')+' · '+longDate(selected),'small muted'));
      if(!point||point.score===null||point.score===undefined){
        card.appendChild(element('p','Sin nota este mes','score muted'));
        card.appendChild(element('p','El mes queda vacío en la serie: no se interpola entre los adyacentes.','note'));
      }else{
        card.appendChild(element('p','Confianza '+confNames[point.conf],'small muted'));
        card.appendChild(element('div',number(point.score)+' / 100','score'));
        var pills=element('div',undefined,'pills');
        [['c6',money(point.c6)],['t6',money(point.t6)],['r_hist',number(point.r_hist,3)],
         ['colchon_aplicable',money(point.colchon)],['mora_ratio',number(point.mora,3)],
         ['penalizacion_mora_puntos',number(point.pen,2)],['volumen_ambiguo_pct',percent(point.amb)]]
          .forEach(function(pair){pills.appendChild(element('span',pair[0]+': '+pair[1],'pill'));});
        card.appendChild(pills);
        if(point.parcial)card.appendChild(element('p','Ventana parcial: menos meses de los objetivo.','note warning'));
      }
      if(point&&point.reasons&&point.reasons.length)
        card.appendChild(element('p','Motivos: '+point.reasons.join(' · '),'small muted'));
      cards.appendChild(card);
    });
  }
  a.addEventListener('change',renderSeries);b.addEventListener('change',renderSeries);
  inspect.addEventListener('input',renderSeries);

  // ===================== VISTAS 2 Y 3 =====================
  var pageSize=100,page=0;
  var search=document.getElementById('rank-search'),sortSel=document.getElementById('rank-sort');
  var filters=document.getElementById('rank-filters');
  confs.forEach(function(conf){
    var box=document.createElement('input');box.type='checkbox';box.id='rank-conf-'+conf;box.checked=true;
    var label=document.createElement('label');label.htmlFor=box.id;
    label.appendChild(box);label.appendChild(document.createTextNode(conf));
    filters.appendChild(label);
    box.addEventListener('change',function(){page=0;renderRank();});
  });

  function cutRows(){
    var index=data.months.indexOf(cut.value),info=data.cuts[cut.value];
    if(!info)return [];
    var query=search.value.trim().toLowerCase();
    var active=confs.map(function(conf){return document.getElementById('rank-conf-'+conf).checked;});
    var list=[];
    data.companies.forEach(function(company){
      var point=company.points[index];
      if(!point)return;
      if(query&&((company.id+' '+(company.group_id||'')).toLowerCase().indexOf(query)<0))return;
      if(!active[confRank(point.conf)])return;
      list.push({id:company.id,group:company.group_id,conf:point.conf,point:point});
    });
    var comparator=sorters[sortSel.value]||sorters.rank;
    list.sort(comparator);
    return list;
  }
  var descending=function(key){
    return function(x,y){
      var a=key(x),c=key(y);
      if(a===null&&c===null)return 0;
      if(a===null)return 1;
      if(c===null)return -1;
      return c-a;
    };
  };
  var byIdAsc=function(x,y){return x.id<y.id?-1:x.id>y.id?1:0;};
  var scoreOf=function(row){return row.point.score===undefined?null:row.point.score;};
  var sorters={
    rank:function(x,y){
      return confRank(x.conf)-confRank(y.conf)||descending(scoreOf)(x,y)||byIdAsc(x,y);
    },
    score:function(x,y){return descending(scoreOf)(x,y)||byIdAsc(x,y);},
    amb:function(x,y){return descending(function(r){return r.point.amb;})(x,y)||byIdAsc(x,y);},
    mora:function(x,y){return descending(function(r){return r.point.mora;})(x,y)||byIdAsc(x,y);},
    colchon:function(x,y){return descending(function(r){return r.point.colchon;})(x,y)||byIdAsc(x,y);},
    id:byIdAsc
  };

  var rankTable=document.getElementById('rank-table');
  function renderRank(){
    var rows=cutRows();
    var pages=Math.max(1,Math.ceil(rows.length/pageSize));
    if(page>=pages)page=pages-1;
    var start=page*pageSize,visible=rows.slice(start,start+pageSize);
    rankTable.replaceChildren();
    var head=document.createElement('thead'),tr=document.createElement('tr');
    ['#','Empresa','Grupo','Nota / 100','Confianza','vol. ambiguo %','mora','colchón EUR'].forEach(function(text,ci){
      var th=document.createElement('th');th.textContent=text;if(ci===0||ci>=4)th.className='n';
      tr.appendChild(th);
    });
    head.appendChild(tr);
    rankTable.appendChild(head);
    var body=document.createElement('tbody');
    visible.forEach(function(row,i){
      var rowEl=document.createElement('tr');
      [String(start+i+1),row.id,row.group||'—',number(scoreOf(row)),row.conf,
       percent(row.point.amb),number(row.point.mora,3),money(row.point.colchon)].forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=text;
        if(ci===0||ci>=4)td.className='n';
        rowEl.appendChild(td);
      });
      body.appendChild(rowEl);
    });
    rankTable.appendChild(body);
    document.getElementById('rank-page').textContent='Página '+(page+1)+' de '+pages+' · empresas en el filtro: '+rows.length+' de '+data.cuts[cut.value].total;
    document.getElementById('rank-prev').disabled=page===0;
    document.getElementById('rank-next').disabled=page>=pages-1;
  }
  document.getElementById('rank-prev').addEventListener('click',function(){page--;renderRank();});
  document.getElementById('rank-next').addEventListener('click',function(){page++;renderRank();});
  search.addEventListener('input',function(){page=0;renderRank();});
  sortSel.addEventListener('change',function(){page=0;renderRank();});

  // ===================== VISTA 2: histograma =====================
  var histo=document.getElementById('histo');
  function renderHisto(){
    var info=data.cuts[cut.value];
    if(!info)return;
    histo.replaceChildren();
    var draw=svgInto(histo);
    var bins=info.bins,nullTotal=0;
    Object.keys(info.null_motivos).forEach(function(key){nullTotal+=info.null_motivos[key];});
    var binTotals=bins.map(function(bin){return confs.reduce(function(sum,conf){return sum+bin[conf];},0);});
    var maxBar=Math.max(1,Math.max.apply(null,binTotals),nullTotal);
    var x0=46,binW=28,plotW=20*binW,nullX=x0+plotW+40,nullW=90;
    var y=function(v){return 330-v*290/maxBar;};
    var scale=function(v){return v*290/maxBar;};
    for(var v=0;v<=100;v+=25){
      var gx=x0+v/100*plotW;
      draw('line',{x1:gx,x2:gx,y1:30,y2:330,stroke:v===50?'#9fb5c4':'#dce5eb','stroke-width':v===50?2:1});
      draw('text',{x:gx,y:348,'text-anchor':'middle',fill:'#607183','font-size':12},v);
    }
    draw('text',{x:x0+25/100*plotW,y:342,'text-anchor':'middle',fill:'#607183','font-size':11},'50 · equilibrio');
    bins.forEach(function(bin,bi){
      var bottom=330,top;
      confs.forEach(function(conf){
        var count=bin[conf];
        if(!count)return;
        var height=scale(count);
        top=bottom-height;
        draw('rect',{x:x0+bi*binW+2,y:top,width:binW-4,height:height,fill:confColor[conf],stroke:'#ffffff','stroke-width':0.5});
        bottom=top;
      });
      if(binTotals[bi]>0)
        draw('text',{x:x0+bi*binW+binW/2,y:top-4,'text-anchor':'middle',fill:'#607183','font-size':10},binTotals[bi]);
    });
    // Barra aparte, fuera del eje 0..100: empresas sin nota, por motivo.
    draw('line',{x1:nullX-8,x2:nullX-8,y1:30,y2:330,stroke:'#dce5eb'});
    var motivos=Object.keys(info.null_motivos).sort(function(a,c){return info.null_motivos[c]-info.null_motivos[a];});
    var slotW=nullW/Math.max(1,motivos.length);
    motivos.forEach(function(motivo,mi){
      var count=info.null_motivos[motivo],height=scale(count),top=330-height;
      draw('rect',{x:nullX+mi*slotW+2,y:top,width:Math.max(4,slotW-6),height:height,fill:'#9fb5c4',stroke:'#ffffff','stroke-width':0.5});
      draw('text',{x:nullX+mi*slotW+slotW/2,y:top-4,'text-anchor':'middle',fill:'#607183','font-size':10},count);
      var short=motivo.length>34?motivo.slice(0,33)+'…':motivo;
      var text=draw('text',{x:nullX+mi*slotW+slotW/2,y:368,'text-anchor':'end',fill:'#607183','font-size':10,transform:'rotate(-35 '+(nullX+mi*slotW+slotW/2)+' 368)'},short);
      var title=document.createElementNS(NS,'title');title.textContent=motivo;text.appendChild(title);
    });
    draw('text',{x:nullX+nullW/2,y:30,'text-anchor':'middle',fill:'#607183','font-size':11},'sin nota');
    var counts=document.getElementById('cut-counts');counts.replaceChildren();
    var head=document.createElement('thead'),hrow=document.createElement('tr');
    ['Confianza','Empresas','%'].forEach(function(text){var th=document.createElement('th');th.textContent=text;hrow.appendChild(th);});
    head.appendChild(hrow);
    counts.appendChild(head);
    var body=document.createElement('tbody'),total=info.total;
    confs.forEach(function(conf){
      var row=document.createElement('tr');
      [confNames[conf],info.confidence[conf],percent(info.confidence[conf]/total*100)].forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=text;if(ci)td.className='n';row.appendChild(td);
      });
      body.appendChild(row);
    });
    var nullRow=document.createElement('tr');
    ['Sin nota (barra aparte)',nullTotal,percent(nullTotal/total*100)].forEach(function(text,ci){
      var td=document.createElement('td');td.textContent=text;if(ci)td.className='n';nullRow.appendChild(td);
    });
    body.appendChild(nullRow);
    var totalRow=document.createElement('tr');
    ['Total del corte',total,'100%'].forEach(function(text,ci){
      var td=document.createElement('td');td.textContent=String(text);
      if(ci)td.className='n';
      td.style.fontWeight='700';
      totalRow.appendChild(td);
    });
    body.appendChild(totalRow);
    counts.appendChild(body);
  }
  cut.addEventListener('change',function(){renderHisto();page=0;renderRank();});

  renderSeries();renderHisto();renderRank();
}
if(typeof document!=='undefined')init();
</script>
</body>
</html>
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description='Grafica HTML autonoma de healthscore_v3, sin dependencias externas')
    parser.add_argument('--output', type=Path,
                        default=paths.ROOT / 'reports/score_charts/healthscore_v3/index.html')
    parser.add_argument('--input-dir', type=Path, default=None,
                        help='directorio con assessments.parquet y summary.json '
                             '(por defecto reports/score_v3)')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('La salida ya existe; usa --output con una ruta nueva para conservarla')
    score_dir = args.input_dir if args.input_dir is not None else paths.ROOT / 'reports' / 'score_v3'
    payload = _reduced_payload(score_dir / ASSESSMENTS_NAME, score_dir / SUMMARY_NAME)
    page = render_chart(payload)
    size = len(page.encode('utf-8'))
    if size > MAX_BYTES:
        raise ValueError(f'el HTML supera el tope de 25 MB ({size} bytes); no se muestrean empresas')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(page)
    print(json.dumps({'output': str(args.output), 'input_dir': str(score_dir),
                      'model_version': payload['model_version'],
                      'months': len(payload['months']), 'companies': payload['total_companies'],
                      'reduced_months': payload['reduced_months'], 'bytes': size}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
