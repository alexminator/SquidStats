# ------------------- MONKEY PATCHING PARA EVENTLET -------------------
import eventlet
eventlet.monkey_patch()

# ------------------- IMPORTACIONES FLASK Y EXTENSIONES -------------------
from flask import Flask, render_template, request, redirect, jsonify, render_template_string
from flask_socketio import SocketIO, emit
from flask_apscheduler import APScheduler

# ------------------- UTILIDADES Y SERVICIOS PERSONALIZADOS -------------------
# --- MODIFICADO: Importar get_dynamic_metrics_model ---
from database.database import (
    create_dynamic_tables, get_engine, get_session, 
    get_dynamic_models, get_dynamic_metrics_model
)
from parsers.connections import parse_raw_data, group_by_user
from services.fetch_data import fetch_squid_data
from parsers.cache import fetch_squid_cache_stats
from parsers.log import process_logs, find_last_parent_proxy
from services.fetch_data_logs import get_users_logs, get_users_with_logs_by_date
from services.system_info import (
    get_network_info, get_os_info, get_uptime, get_ram_info,
    get_swap_info, get_cpu_info, get_squid_version, get_timezone, get_network_stats
)
from services.get_reports import get_important_metrics, get_metrics_by_date_range
from utils.colors import color_map
from utils.updateSquid import update_squid
from utils.updateSquidStats import updateSquidStats
# --- INICIO DE LA MODIFICACIÓN ---
# Se importa la nueva función de auditoría para redes sociales.
from services.auditoria_service import (
    get_all_usernames,
    get_user_activity_summary,
    get_top_users_by_data,
    find_denied_access,
    find_by_keyword,
    find_by_ip,
    find_by_response_code,
    find_social_media_activity  # <--- NUEVA IMPORTACIÓN
)
# --- FIN DE LA MODIFICACIÓN ---
from flask import jsonify
# ------------------- PAQUETES ESTÁNDAR -------------------
from dotenv import load_dotenv
from datetime import datetime, timezone
import socket
import sys
import os
import logging
import time
from threading import Lock
import psutil 

# ------------------- CONFIGURACIÓN -------------------
class Config:
    SCHEDULER_API_ENABLED = True

load_dotenv()

# --- Función de utilidad para convertir tamaños a bytes ---
def size_to_bytes(size_str):
    """Convierte un string como '2.5 GB' a bytes."""
    if not isinstance(size_str, str):
        return 0
    
    size_str = size_str.strip().upper()
    
    try:
        if 'G' in size_str:
            value = float(size_str.replace('GB', '').strip())
            return int(value * 1024**3)
        if 'M' in size_str:
            value = float(size_str.replace('MB', '').strip())
            return int(value * 1024**2)
        if 'K' in size_str:
            value = float(size_str.replace('KB', '').strip())
            return int(value * 1024)
        
        return int(float(size_str.replace('B', '').strip()))
    except (ValueError, TypeError):
        return 0


# ------------------- INICIALIZACIÓN APP -------------------
app = Flask(__name__, static_folder='./static')
app.config.from_object(Config())
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = os.urandom(24).hex()

# ------------------- INICIALIZACIÓN SOCKETIO -------------------
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ------------------- SCHEDULER -------------------
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# ------------------- LOGGING -------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================================================================
# Variables globales y configuración inicial
# ======================================================================
realtime_data_lock = Lock()
realtime_cache_stats = {}
realtime_system_info = {}
parent_proxy_lock = Lock()

# Detección del proxy padre una sola vez al iniciar
log_file_path = os.getenv("SQUID_LOG", "/var/log/squid/access.log")
logger.info("Realizando detección inicial del proxy padre...")
g_parent_proxy_ip = find_last_parent_proxy(log_file_path)
if g_parent_proxy_ip:
    logger.info(f"Proxy padre detectado con IP: {g_parent_proxy_ip}. Esta configuración se mantendrá fija.")
else:
    logger.info("No se detectó un proxy padre en los logs recientes. Asumiendo conexión directa.")

# Se llama una vez para asegurar que las tablas del día se creen al arrancar
with app.app_context():
    create_dynamic_tables(get_engine())

# ======================================================================
# Hilo para actualización periódica de datos
# ======================================================================
def realtime_data_thread():
    """
    MODIFICADO: Ahora, además de emitir por WebSocket, este hilo guarda
    las métricas en la base de datos cada 5 segundos.
    """
    global realtime_cache_stats, realtime_system_info
    
    last_net_counters = psutil.net_io_counters()
    last_check_time = time.time()

    while True:
        try:
            cache_data = fetch_squid_cache_stats()
            cache_stats = vars(cache_data) if hasattr(cache_data, '__dict__') else cache_data
            
            current_time = time.time()
            current_net_counters = psutil.net_io_counters()
            time_delta = current_time - last_check_time

            if time_delta > 0:
                bytes_sent_sec = (current_net_counters.bytes_sent - last_net_counters.bytes_sent) / time_delta
                bytes_recv_sec = (current_net_counters.bytes_recv - last_net_counters.bytes_recv) / time_delta
            else:
                bytes_sent_sec = 0
                bytes_recv_sec = 0

            last_net_counters = current_net_counters
            last_check_time = current_time

            utc_now = datetime.now(timezone.utc)
            system_info = {
                'hostname': socket.gethostname(),
                'ips': get_network_info(),
                'os': get_os_info(),
                'uptime': get_uptime(),
                'ram': get_ram_info(),
                'swap': get_swap_info(),
                'cpu': get_cpu_info(),
                'python_version': sys.version.split()[0],
                'squid_version': get_squid_version(),
                'timezone': get_timezone(),
                'local_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'timestamp_utc': utc_now.isoformat()
            }

            # --- Guardar métricas en la Base de Datos ---
            try:
                date_suffix = datetime.now().strftime('%Y%m%d')
                MetricsModel = get_dynamic_metrics_model(date_suffix)
                
                if MetricsModel:
                    db_session = get_session()
                    
                    # --- Se guarda el timestamp en UTC para consistencia. ---
                    # Usar UTC en la base de datos evita problemas de zona horaria.
                    new_metric = MetricsModel(
                        timestamp=utc_now,
                        cpu_usage=float(system_info['cpu']['usage'].replace('%','')),
                        ram_usage_bytes=size_to_bytes(system_info['ram']['used']),
                        swap_usage_bytes=size_to_bytes(system_info['swap']['used']),
                        net_sent_bytes_sec=int(bytes_sent_sec),
                        net_recv_bytes_sec=int(bytes_recv_sec)
                    )
                    
                    db_session.add(new_metric)
                    db_session.commit()
                    db_session.close()
            except Exception as e:
                logger.error(f"Error al guardar métricas en la BD: {str(e)}")

            with realtime_data_lock:
                realtime_cache_stats = cache_stats
                realtime_system_info = system_info
            
            socketio.emit('system_update', {
                'cache_stats': cache_stats,
                'system_info': system_info,
                'network_stats': {
                    'up_mbps': round((bytes_sent_sec * 8) / 1_000_000, 2),
                    'down_mbps': round((bytes_recv_sec * 8) / 1_000_000, 2)
                }
            })
            
        except Exception as e:
            logger.error(f"Error en hilo de datos en tiempo real: {str(e)}")
        
        eventlet.sleep(5)

# ======================================================================
# Manejador de conexión WebSocket
# ======================================================================
@socketio.on('connect')
def handle_connect():
    logger.info(f"Cliente conectado: {request.sid}")
    
    with realtime_data_lock:
        cache_stats = realtime_cache_stats
        system_info = realtime_system_info

    if cache_stats or system_info:
        socketio.emit('system_update', {
            'cache_stats': cache_stats,
            'system_info': system_info
        }, to=request.sid)

# ------------------- NO CACHÉ PARA RESPUESTAS -------------------
@app.after_request
def set_response_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ------------------- RUTA PRINCIPAL -------------------
@app.route('/')
def index():
    try:
        raw_data = fetch_squid_data()
        if 'Error' in raw_data:
            logger.error(f"Failed to fetch Squid data: {raw_data}")
            return render_template('error.html', message="Error connecting to Squid"), 500

        connections = parse_raw_data(raw_data)
        grouped_connections = group_by_user(connections)

        with parent_proxy_lock:
            parent_ip = g_parent_proxy_ip

        squid_version = get_squid_version()
        network_info = get_network_info()
        squid_ip = "No disponible"
        if isinstance(network_info, list) and network_info:
            squid_ip = network_info[0].get('ip', 'No disponible')
        
        return render_template(
            'index.html',
            grouped_connections=grouped_connections,
            parent_proxy_ip=parent_ip,
            squid_ip=squid_ip,
            squid_version=squid_version,
            page_icon='favicon.ico',
            page_title='Inicio Dashboard'
        )
    except Exception as e:
        logger.error(f"Unexpected error in index route: {str(e)}")
        return render_template('error.html', message="An unexpected error occurred"), 500

# ------------------- RUTA AJAX PARA ACTUALIZAR CONEXIONES -------------------
@app.route('/actualizar-conexiones')
def actualizar_conexiones():
    try:
        raw_data = fetch_squid_data()
        if 'Error' in raw_data:
            logger.error(f"Failed to fetch Squid data: {raw_data}")
            return "Error", 500

        connections = parse_raw_data(raw_data)
        grouped_connections = group_by_user(connections)

        with parent_proxy_lock:
            parent_ip = g_parent_proxy_ip
            
        squid_version = get_squid_version()
        network_info = get_network_info()
        squid_ip = "No disponible"
        if isinstance(network_info, list) and network_info:
            squid_ip = network_info[0].get('ip', 'No disponible')
            
        return render_template(
            'partials/conexiones.html', 
            grouped_connections=grouped_connections, 
            parent_proxy_ip=parent_ip,
            squid_ip=squid_ip,
            squid_version=squid_version
        )
    except Exception as e:
        logger.error(f"Unexpected error in /actualizar-conexiones route: {str(e)}")
        return "Error interno", 500

# ------------------- VISTA DE ESTADÍSTICAS -------------------
@app.route('/stats')
def cache_stats():
    try:
        with realtime_data_lock:
            stats_data = realtime_cache_stats if realtime_cache_stats else {}
            system_info = realtime_system_info if realtime_system_info else {}
        
        if not stats_data:
            data = fetch_squid_cache_stats()
            stats_data = vars(data) if hasattr(data, '__dict__') else data
        if not system_info:
            system_info = {
                'hostname': socket.gethostname(), 'ips': get_network_info(),
                'os': get_os_info(), 'uptime': get_uptime(), 'ram': get_ram_info(),
                'swap': get_swap_info(), 'cpu': get_cpu_info(),
                'python_version': sys.version.split()[0],
                'squid_version': get_squid_version(), 'timezone': get_timezone(),
                'local_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        network_stats = get_network_stats()
        logger.info("Successfully fetched cache statistics and system info")
        return render_template(
            'cacheView.html',
            cache_stats=stats_data,
            system_info=system_info,
            network_stats=network_stats,
            page_icon='statistics.ico',
            page_title='Estadísticas del Sistema'
        )
    except Exception as e:
        logger.error(f"Error in /stats: {str(e)}")
        return render_template('error.html', message="Error retrieving cache statistics or system info"), 500

@app.route('/api/metrics/today')
def get_today_metrics():
    db = None
    try:
        date_suffix = datetime.now().strftime('%Y%m%d')
        MetricsModel = get_dynamic_metrics_model(date_suffix)
        
        if not MetricsModel:
            logger.warning(f"No se encontró el modelo de métricas para {date_suffix}")
            return jsonify([])

        db = get_session()
        metrics = db.query(MetricsModel).order_by(MetricsModel.timestamp.asc()).all()

        results = []
        for m in metrics:
            aware_timestamp = m.timestamp.replace(tzinfo=timezone.utc)
            
            results.append({
                "timestamp": aware_timestamp.isoformat(),
                "cpu_usage": m.cpu_usage,
                "ram_usage_bytes": m.ram_usage_bytes,
                "swap_usage_bytes": m.swap_usage_bytes,
                "net_sent_bytes_sec": m.net_sent_bytes_sec,
                "net_recv_bytes_sec": m.net_recv_bytes_sec,
            })
            
        return jsonify(results)
    except Exception as e:
        logger.error(f"Error en API de métricas: {e}", exc_info=True)
        return jsonify({"error": "No se pudieron obtener las métricas"}), 500
    finally:
        if db:
            db.close()


# ------------------- VISTA DE LOGS DE USUARIOS -------------------
@app.route('/logs')
def logs():
    return render_template('logsView.html',
                           page_icon='user.ico',
                           page_title='Actividad usuarios')

# ------------------- VISTA DE REPORTES -------------------
@app.route('/reports')
def reports():
    db = None
    try:
        db = get_session()
        current_date = datetime.now().strftime("%Y%m%d")
        logger.info(f"Generando reportes para la fecha: {current_date}")
        UserModel, LogModel = get_dynamic_models(current_date)

        if not UserModel or not LogModel:
            return render_template('error.html', message="Error al cargar datos para reportes"), 500

        metrics = get_important_metrics(db, UserModel, LogModel)

        if not metrics:
            return render_template('error.html', message="No hay datos disponibles para reportes"), 404

        http_codes = metrics.get('http_response_distribution', [])
        http_codes = sorted(http_codes, key=lambda x: x['count'], reverse=True)
        main_codes = http_codes[:8]
        other_codes = http_codes[8:]

        if other_codes:
            other_count = sum(item['count'] for item in other_codes)
            main_codes.append({'response_code': 'Otros', 'count': other_count})

        metrics['http_response_distribution_chart'] = {
            'labels': [str(item['response_code']) for item in main_codes],
            'data': [item['count'] for item in main_codes],
            'colors': [color_map.get(str(item['response_code']), color_map['Otros']) for item in main_codes]
        }

        return render_template('reports.html', metrics=metrics, page_icon='bar.ico', page_title='Reportes y gráficas')
    except Exception as e:
        logger.error(f"Error en ruta /reports: {str(e)}", exc_info=True)
        return render_template('error.html', message="Error interno generando reportes"), 500
    finally:
        if db:
            db.close()

# ------------------- API: LOGS POR FECHA -------------------
@app.route('/get-logs-by-date', methods=['POST'])
def get_logs_by_date():
    db = None
    try:
        data = request.get_json()
        date_str = data.get('date')
        page = data.get('page', 1)
        per_page = data.get('per_page', 15)
        # --- INICIO DE LA MODIFICACIÓN ---
        # Se obtiene el término de búsqueda desde la petición JSON del frontend.
        # Si no se envía, su valor es None.
        search_query = data.get('search', None)
        # --- FIN DE LA MODIFICACIÓN ---
        selected_date = datetime.strptime(date_str, '%Y-%m-%d')
        date_suffix = selected_date.strftime('%Y%m%d')

        db = get_session()
        # --- INICIO DE LA MODIFICACIÓN ---
        # Se pasa el nuevo parámetro 'search_query' a la función que obtiene los datos.
        users_data = get_users_logs(db, date_suffix, page=page, per_page=per_page, search_query=search_query)
        # Se devuelve el objeto de paginación COMPLETO, que incluye 'total_pages', 'page', etc.
        # Esto es fundamental para que el frontend pueda construir los controles de paginación.
        return jsonify(users_data)
        # --- FIN DE LA MODIFICACIÓN ---
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400
    except Exception as e:
        logger.error(f"Error en get-logs-by-date: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if db:
            db.close()

# ------------------- INSTALACIÓN DE SQUID -------------------
@app.route('/install', methods=['POST'])
def install_package():
    success = update_squid()
    return redirect('/')

# ------------------- ACTUALIZACIÓN DE STATS -------------------
@app.route('/update', methods=['POST'])
def update_web():
    success = updateSquidStats()
    return redirect('/')

# ------------------- VISTA DE AUDITORIAS -------------------
@app.route('/auditoria', methods=['GET'])
def auditoria_logs():
    return render_template(
        'auditor.html',
        page_icon='magnifying-glass.ico',
        page_title='Centro de Auditoría'
    )

@app.route('/api/all-users', methods=['GET'])
def api_get_all_users():
    """Endpoint para obtener una lista de todos los usuarios para los filtros del frontend."""
    db = get_session()
    try:
        users = get_all_usernames(db)
        return jsonify(users)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/run-audit', methods=['POST'])
def api_run_audit():
    """Endpoint principal que ejecuta la auditoría solicitada desde el frontend."""
    data = request.get_json()
    audit_type = data.get('audit_type')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    username = data.get('username')
    keyword = data.get('keyword')
    ip_address = data.get('ip_address')
    response_code = data.get('response_code')
    
    # --- INICIO DE LA MODIFICACIÓN ---
    # Se obtiene el parámetro social_media_sites del formulario.
    # Puede ser una lista de sitios seleccionados.
    social_media_sites = data.get('social_media_sites')
    # --- FIN DE LA MODIFICACIÓN ---

    db = get_session()
    try:
        if audit_type == 'user_summary':
            if not username: return jsonify({"error": "Se requiere un nombre de usuario."}), 400
            result = get_user_activity_summary(db, username, start_date, end_date)
        elif audit_type == 'top_users_data':
            result = get_top_users_by_data(db, start_date, end_date)
        elif audit_type == 'denied_access':
            result = find_denied_access(db, start_date, end_date, username)
        elif audit_type == 'keyword_search':
            if not keyword: return jsonify({"error": "Se requiere una palabra clave."}), 400
            result = find_by_keyword(db, start_date, end_date, keyword, username)
        # --- INICIO DE LA MODIFICACIÓN ---
        # Se añade la lógica para manejar el nuevo tipo de auditoría.
        elif audit_type == 'social_media_activity':
            if not social_media_sites: return jsonify({"error": "Debe seleccionar al menos una red social."}), 400
            result = find_social_media_activity(db, start_date, end_date, social_media_sites, username)
        # --- FIN DE LA MODIFICACIÓN ---
        elif audit_type == 'ip_activity':
            if not ip_address: return jsonify({"error": "Se requiere una dirección IP."}), 400
            result = find_by_ip(db, start_date, end_date, ip_address)
        elif audit_type == 'response_code_search':
            if not response_code: return jsonify({"error": "Se requiere un código de respuesta."}), 400
            result = find_by_response_code(db, start_date, end_date, int(response_code), username)
        else:
            return jsonify({"error": "Tipo de auditoría no válido."}), 400
        
        return jsonify(result)

    except Exception as e:
        # Imprimir el error en el log del servidor para depuración
        print(f"Error en la API de auditoría: {e}")
        return jsonify({"error": "Ocurrió un error interno en el servidor."}), 500
    finally:
        db.close()

# ------------------- REPORTES POR RANGO -------------------
@app.route('/reports-range', methods=['POST'])
def reports_by_range():
    db = None
    try:
        start_date = request.json.get('start_date')
        end_date = request.json.get('end_date')
        if not start_date or not end_date:
            return jsonify({'error': 'Fechas requeridas'}), 400
        db = get_session()
        metrics = get_metrics_by_date_range(start_date, end_date, db)
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Error en reports-range: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if db:
            db.close()

# ------------------- PROCESAMIENTO DE LOGS AUTOMÁTICO -------------------
@scheduler.task('interval', id='do_job_1', seconds=30, misfire_grace_time=900)
def init_scheduler():
    log_file = os.getenv("SQUID_LOG", "/var/log/squid/access.log")
    if not os.path.exists(log_file):
        logger.error(f"Archivo de log no encontrado: {log_file}")
        return
    process_logs(log_file)

# ------------------- FILTROS DE PLANTILLA -------------------
@app.template_filter('format_bytes')
def format_bytes_filter(value):
    value = int(value)
    if value >= 1024**3:
        return f"{(value / (1024**3)):.2f} GB"
    elif value >= 1024**2:
        return f"{(value / (1024**2)):.2f} MB"
    elif value >= 1024:
        return f"{(value / 1024):.2f} KB"
    return f"{value} bytes"

@app.template_filter('divide')
def divide_filter(numerator, denominator, precision=2):
    try:
        num = float(numerator)
        den = float(denominator)
        if den == 0:
            return 0.0
        return round(num / den, precision)
    except (TypeError, ValueError) as e:
        logger.error(f"Error en filtro divide: {str(e)}")
        return 0.0
    
@app.template_filter('fromtimestamp')
def fromtimestamp_filter(s):
    try:
        return datetime.fromtimestamp(float(s)).strftime('%H:%M:%S')
    except (ValueError, TypeError):
        return s
    
def create_tables():
    engine = get_engine()
    create_dynamic_tables(engine)

scheduler.add_job(id='create_tables_daily', func=create_tables, trigger='cron', hour=0, minute=0) 

@app.route('/logs/fragment')
def logs_fragment():
    db = None
    try:
        db = get_session()
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 15, type=int)
        users_data = get_users_logs(db, page=page, per_page=per_page)
        if request.headers.get('Accept') == 'application/json':
            return jsonify(users_data)
        html = render_template('components/logs.html',
            users_data=users_data['users'],
            current_page=users_data['page'],
            per_page=users_data['per_page'],
            total_pages=users_data['total_pages'],
            total=users_data['total']
        )
        return html
    except Exception as e:
        logger.error(f"Error en logs_fragment: {e}")
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'users': [], 'total': 0, 'page': 1, 'per_page': 15, 'total_pages': 1}), 500
        return "<div class='text-red-500 text-center p-4 col-span-full'>Error al cargar los datos</div>", 500
    finally:
        if db:
            db.close()

from flask import Blueprint, render_template, request
from datetime import date
from services.fetch_data_logs import get_metrics_for_date

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/dashboard')
def dashboard():
    date_str = request.args.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    metrics = get_metrics_for_date(selected_date)

    return render_template(
        'components/graph_reports.html',
        metrics=metrics,
        selected_date=selected_date
    )

app.register_blueprint(reports_bp)

# ------------------- EJECUCIÓN DE LA APLICACIÓN -------------------
if __name__ == "__main__":
    socketio.start_background_task(realtime_data_thread)
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    socketio.run(app, debug=debug_mode, host='0.0.0.0', port=5000)