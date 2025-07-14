# services/auditoria_service.py
import sys
from sqlalchemy import inspect, text, func
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Tuple
from collections import defaultdict

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from database.database import get_session, get_engine, ActiveConnectionSnapshot

SOCIAL_MEDIA_DOMAINS = {
    'YouTube': [ 'youtube.com', 'ytimg.com', 'googlevideo.com', 'yt3.ggpht.com', 'youtubei.googleapis.com', 'youtube-ui.l.google.com', 'youtube.googleapis.com' ],
    'Facebook': [ 'facebook.com', 'fbcdn.net', 'facebook.net', 'fbsbx.com', 'fbpigeon.com', 'fb.com', 'facebook-hardware.com' ],
    'Pinterest': [ 'pinterest.com', 'pinimg.com', 'cdx.cedexis.net', 'pinterest.net', 'pinterest.pt', 'pinterest.cl', 'pinterest.info' ],
    'Instagram': [ 'instagram.com', 'cdninstagram.com', 'z-p42-chat-e2ee-ig.facebook.com', 'mqtt-ig-p4.facebook.com', 'z-p42-chat-e2ee-ig-fallback.facebook.com', 'ig.me', 'instagram.am', 'igsonar.com' ],
    'Telegram': [ 'telegram.org', 't.me', 'telegram.me', 'tg.dev', 'telesco.pe' ],
    'WhatsApp': [ 'whatsapp.net', 'whatsapp.com', 'wa.me', 'wl.co', 'whatsappbrand.com', 'whatsapp-plus.info', 'whatsapp-plus.me', 'whatsapp-plus.net', 'whatsapp.cc', 'whatsapp.info', 'whatsapp.org', 'whatsapp.tv' ],
    'Twitter/X': [ 'twitter.com', 't.co', 'anuncios-twitter.com', 'twimg.com', 'x.com', 'pscp.tv', 'twtrdns.net', 'twttr.com', 'periscopio.tv', 'twitpic.com', 'tweetdeck.com', 'twitter.co', 'twitterinc.com', 'twitteroauth.com', 'twitterstat.us' ]
}

def _get_tables_in_range(inspector, start_date: datetime, end_date: datetime) -> List[Tuple[str, str]]:
    all_db_tables, log_tables_in_range = inspector.get_table_names(), []
    current_date = start_date
    while current_date <= end_date:
        date_suffix = current_date.strftime("%Y%m%d")
        log_table, user_table = f'log_{date_suffix}', f'user_{date_suffix}'
        if log_table in all_db_tables and user_table in all_db_tables:
            log_tables_in_range.append((log_table, user_table))
        current_date += timedelta(days=1)
    return log_tables_in_range

def _execute_union_query(db: Session, tables: List[Tuple[str, str]], where_clause: str, params: Dict, order_by: str) -> List[Any]:
    select_clauses = [f"SELECT u.username, u.ip, l.url, l.response, l.data_transmitted, l.created_at, '{t.split('_')[1]}' as log_date FROM {t} l JOIN {u} u ON l.user_id = u.id" for t, u in tables]
    full_query_str = f"SELECT username, ip, url, response, data_transmitted, created_at, log_date FROM ({ ' UNION ALL '.join(select_clauses) }) as all_logs WHERE {where_clause} ORDER BY {order_by}"
    try:
        return db.execute(text(full_query_str), params).fetchall()
    except SQLAlchemyError as e:
        print(f"Error en _execute_union_query: {e}"); raise

def find_by_keyword(db: Session, start_str: str, end_str: str, keyword: str, username: str = None) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    select_clauses = [f"SELECT u.username, u.ip, l.url, l.data_transmitted, l.created_at, '{t.split('_')[1]}' as log_date FROM {t} l JOIN {u} u ON l.user_id = u.id" for t, u in tables]
    where_clause = "url LIKE :keyword"
    params = {'keyword': f'%{keyword}%'}
    if username: where_clause += " AND username = :username"; params['username'] = username
    full_query_str = f"SELECT log_date, username, ip, url, COUNT(*) as access_count, SUM(data_transmitted) as total_data, MAX(created_at) as last_seen FROM ({ ' UNION ALL '.join(select_clauses) }) as all_logs WHERE {where_clause} GROUP BY log_date, username, ip, url ORDER BY username, log_date DESC, access_count DESC"
    try:
        return {"results": [dict(row._mapping) for row in db.execute(text(full_query_str), params).fetchall()]}
    except SQLAlchemyError as e: print(f"Error en find_by_keyword: {e}"); raise

def find_social_media_activity(db: Session, start_str: str, end_str: str, sites: List[str], username: str = None) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    domain_list = [d for site in sites if site in SOCIAL_MEDIA_DOMAINS for d in SOCIAL_MEDIA_DOMAINS[site]]
    if not domain_list: return {"error": "No se especificaron dominios válidos."}
    like_conditions, params, param_index = [], {}, 0
    for domain in domain_list:
        like_conditions.append(f"(url LIKE :p{param_index}_sub_slash OR url LIKE :p{param_index}_sub_colon OR url LIKE :p{param_index}_sub_exact OR url LIKE :p{param_index}_proto_slash OR url LIKE :p{param_index}_proto_colon OR url LIKE :p{param_index}_proto_exact)")
        for key, val in {f'p{param_index}_sub_slash': f'%.{domain}/%', f'p{param_index}_sub_colon': f'%.{domain}:%', f'p{param_index}_sub_exact': f'%.{domain}', f'p{param_index}_proto_slash': f'%//{domain}/%', f'p{param_index}_proto_colon': f'%//{domain}:%', f'p{param_index}_proto_exact': f'%//{domain}'}.items(): params[key] = val
        param_index += 1
    where_clause = f"({' OR '.join(like_conditions)})"
    if username: where_clause += " AND username = :username"; params['username'] = username
    select_clauses = [f"SELECT u.username, u.ip, l.url, l.data_transmitted, l.created_at, '{t.split('_')[1]}' as log_date FROM {t} l JOIN {u} u ON l.user_id = u.id" for t, u in tables]
    full_query_str = f"SELECT log_date, username, ip, url, COUNT(*) as access_count, SUM(data_transmitted) as total_data, MAX(created_at) as last_seen FROM ({ ' UNION ALL '.join(select_clauses) }) as all_logs WHERE {where_clause} GROUP BY log_date, username, ip, url ORDER BY username, log_date DESC, access_count DESC"
    try:
        return {"results": [dict(row._mapping) for row in db.execute(text(full_query_str), params).fetchall()]}
    except SQLAlchemyError as e: print(f"Error en find_social_media_activity: {e}"); raise

def find_by_ip(db: Session, start_str: str, end_str: str, ip_address: str) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    select_clauses = [f"SELECT u.username, u.ip, l.url, l.data_transmitted, l.created_at, '{t.split('_')[1]}' as log_date FROM {t} l JOIN {u} u ON l.user_id = u.id WHERE u.ip = :ip_address" for t, u in tables]
    full_query_str = f"SELECT log_date, username, ip, url, COUNT(*) as access_count, SUM(data_transmitted) as total_data, MAX(created_at) as last_seen FROM ({ ' UNION ALL '.join(select_clauses) }) as all_logs GROUP BY log_date, username, ip, url ORDER BY username, log_date DESC, access_count DESC"
    try:
        return {"results": [dict(row._mapping) for row in db.execute(text(full_query_str), {'ip_address': ip_address}).fetchall()]}
    except SQLAlchemyError as e: print(f"Error en find_by_ip: {e}"); raise

def find_by_response_code(db: Session, start_str: str, end_str: str, code: int, username: str = None) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    select_clauses = [f"SELECT u.username, u.ip, l.url, l.data_transmitted, l.response, l.created_at, '{t.split('_')[1]}' as log_date FROM {t} l JOIN {u} u ON l.user_id = u.id" for t, u in tables]
    where_clause, params = "response = :code", {'code': code}
    if username: where_clause += " AND username = :username"; params['username'] = username
    full_query_str = f"SELECT log_date, username, ip, url, response, COUNT(*) as access_count, SUM(data_transmitted) as total_data, MAX(created_at) as last_seen FROM ({ ' UNION ALL '.join(select_clauses) }) as all_logs WHERE {where_clause} GROUP BY log_date, username, ip, url, response ORDER BY username, log_date DESC, access_count DESC"
    try:
        return {"results": [dict(row._mapping) for row in db.execute(text(full_query_str), params).fetchall()]}
    except SQLAlchemyError as e: print(f"Error en find_by_response_code: {e}"); raise

def get_daily_activity(db: Session, date_str: str, username: str) -> Dict[str, Any]:
    try: selected_date = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError: return {"error": "Formato de fecha inválido."}
    date_suffix = selected_date.strftime("%Y%m%d")
    log_table, user_table = f'log_{date_suffix}', f'user_{date_suffix}'
    if not all(table in inspect(db.get_bind()).get_table_names() for table in [log_table, user_table]): return {"total_requests": 0, "hourly_activity": []}
    query = text(f"SELECT HOUR(l.created_at) as hour_of_day, COUNT(*) as request_count FROM {log_table} l JOIN {user_table} u ON l.user_id = u.id WHERE u.username = :username GROUP BY hour_of_day ORDER BY hour_of_day ASC")
    try:
        results = db.execute(query, {'username': username}).fetchall()
        hourly_counts, total_requests = [0] * 24, 0
        for row in results:
            if 0 <= row.hour_of_day < 24: hourly_counts[row.hour_of_day] = row.request_count; total_requests += row.request_count
        return {"total_requests": total_requests, "hourly_activity": hourly_counts}
    except SQLAlchemyError as e: print(f"Error en get_daily_activity: {e}"); return {"error": "Error en la BD al calcular la actividad."}

def get_duration_by_site(db: Session, start_str: str, end_str: str, username: str) -> Dict[str, Any]:
    """
    Consulta la duración máxima de conexión y la primera hora de petición por sitio.
    Esta función sigue siendo utilizada para poblar la tabla.
    """
    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').replace(hour=0, minute=0, second=0)
        end_date = datetime.strptime(end_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except ValueError:
        return {"error": "Formato de fecha inválido."}
    
    try:
        results = db.query(
            ActiveConnectionSnapshot.uri,
            func.max(ActiveConnectionSnapshot.elapsed_seconds).label('max_duration_seconds'),
            func.sum(ActiveConnectionSnapshot.data_transmitted).label('total_data'),
            func.min(ActiveConnectionSnapshot.snapshot_time).label('first_request_time')
        ).filter(
            ActiveConnectionSnapshot.username == username,
            ActiveConnectionSnapshot.snapshot_time.between(start_date, end_date)
        ).group_by(
            ActiveConnectionSnapshot.uri
        ).order_by(
            func.max(ActiveConnectionSnapshot.elapsed_seconds).desc()
        ).all()

        duration_data = [
            {
                "url": row.uri,
                "max_duration_seconds": row.max_duration_seconds,
                "total_data": row.total_data,
                "first_request_time": row.first_request_time.isoformat() if row.first_request_time else None
            } for row in results
        ]
        return {"results": duration_data}
    except SQLAlchemyError as e:
        print(f"Error en get_duration_by_site: {e}")
        return {"error": "Error en la BD al consultar la duración de conexiones."}

# --- INICIO DE LA MODIFICACIÓN: Nueva función para obtener datos para el gráfico ---
def get_connection_events_for_sites(db: Session, start_str: str, end_str: str, username: str, sites: List[str]) -> Dict[str, Any]:
    """
    Obtiene todos los eventos de conexión individuales (sin agregar) para una lista
    de sitios específicos. Estos datos se usarán para construir el nuevo gráfico.
    """
    if not sites:
        return {"events": []}
    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').replace(hour=0, minute=0, second=0)
        end_date = datetime.strptime(end_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except ValueError:
        return {"error": "Formato de fecha inválido."}

    try:
        results = db.query(
            ActiveConnectionSnapshot.uri,
            ActiveConnectionSnapshot.snapshot_time,
            ActiveConnectionSnapshot.elapsed_seconds,
            ActiveConnectionSnapshot.data_transmitted
        ).filter(
            ActiveConnectionSnapshot.username == username,
            ActiveConnectionSnapshot.snapshot_time.between(start_date, end_date),
            ActiveConnectionSnapshot.uri.in_(sites)  # Filtra solo por los sitios seleccionados
        ).order_by(
            ActiveConnectionSnapshot.snapshot_time.asc()
        ).all()

        connection_events = [
            {
                "url": row.uri,
                "snapshot_time": row.snapshot_time.isoformat(),
                "duration_seconds": row.elapsed_seconds,
                "data_transmitted": row.data_transmitted
            } for row in results if row.elapsed_seconds is not None and row.data_transmitted is not None
        ]
        return {"events": connection_events}
    except SQLAlchemyError as e:
        print(f"Error en get_connection_events_for_sites: {e}")
        return {"error": "Error en la BD al consultar los eventos de conexión."}
# --- FIN DE LA MODIFICACIÓN ---

def get_all_usernames(db: Session) -> List[str]:
    all_tables = inspect(db.get_bind()).get_table_names()
    user_tables = [t for t in all_tables if t.startswith('user_') and len(t) == 13]
    if not user_tables: return []
    union_query = " UNION ".join([f"SELECT username FROM {table}" for table in user_tables])
    full_query = text(f"SELECT DISTINCT username FROM ({union_query}) as all_users WHERE username IS NOT NULL AND username != '' AND username != '-' ORDER BY username")
    try:
        return [row[0] for row in db.execute(full_query).fetchall()]
    except SQLAlchemyError as e: print(f"Error al obtener todos los usuarios: {e}"); return []

def get_user_activity_summary(db: Session, username: str, start_str: str, end_str: str) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    select_clauses = [f"SELECT l.url, l.data_transmitted, l.request_count, l.response FROM {t} l JOIN {u} u ON l.user_id = u.id WHERE u.username = :username" for t, u in tables]
    full_query = text(" UNION ALL ".join(select_clauses))
    try:
        results = db.execute(full_query, {'username': username}).fetchall()
        if not results: return {'total_requests': 0, 'total_data_gb': 0, 'top_domains': [], 'response_summary': []}
        total_requests, total_data = sum(r.request_count for r in results), sum(r.data_transmitted for r in results)
        domain_counts, response_counts = defaultdict(int), defaultdict(int)
        for row in results:
            try: domain_counts[row.url.split('//')[-1].split('/')[0].split(':')[0]] += row.request_count
            except: pass
            response_counts[row.response] += row.request_count
        sorted_domains, sorted_responses = sorted(domain_counts.items(), key=lambda i: i[1], reverse=True), sorted(response_counts.items(), key=lambda i: i[1], reverse=True)
        return {"total_requests": total_requests, "total_data_gb": round(total_data / (1024**3), 2), "top_domains": [{"domain": d, "count": c} for d, c in sorted_domains[:5]], "response_summary": [{"code": c, "count": cnt} for c, cnt in sorted_responses]}
    except SQLAlchemyError as e: return {"error": str(e)}

def get_top_users_by_data(db: Session, start_str: str, end_str: str, limit: int = 10) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    select_clauses = [f"SELECT u.username, l.data_transmitted FROM {t} l JOIN {u} u ON l.user_id = u.id WHERE u.username != '-'" for t, u in tables]
    full_query = text(f"SELECT username, SUM(data_transmitted) as total_data FROM ({ ' UNION ALL '.join(select_clauses) }) as all_logs GROUP BY username ORDER BY total_data DESC LIMIT :limit")
    try:
        return {"top_users": [{"username": r.username, "total_data_gb": float(round((r.total_data or 0) / (1024**3), 2))} for r in db.execute(full_query, {'limit': limit}).fetchall()]}
    except SQLAlchemyError as e: return {"error": str(e)}

def find_denied_access(db: Session, start_str: str, end_str: str, username: str = None) -> Dict[str, Any]:
    start_date, end_date = datetime.strptime(start_str, '%Y-%m-%d'), datetime.strptime(end_str, '%Y-%m-%d')
    tables = _get_tables_in_range(inspect(db.get_bind()), start_date, end_date)
    if not tables: return {"error": "No hay datos para las fechas seleccionadas."}
    where_clause, params = "response = 403", {}
    if username: where_clause += " AND username = :username"; params['username'] = username
    return {"results": [dict(row._mapping) for row in _execute_union_query(db, tables, where_clause, params, "log_date DESC, username")]}