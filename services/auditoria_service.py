# services/auditoria_service.py
import sys
from sqlalchemy import inspect, text, func
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Tuple

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from database.database import get_session, get_engine


def _get_tables_in_range(inspector, start_date: datetime, end_date: datetime) -> List[Tuple[str, str]]:
    """
    Función de ayuda para obtener una lista de tuplas (tabla_log, tabla_user)
    que existen en la base de datos dentro del rango de fechas especificado.
    """
    all_db_tables = inspector.get_table_names()
    log_tables_in_range = []
    
    current_date = start_date
    while current_date <= end_date:
        date_suffix = current_date.strftime("%Y%m%d")
        log_table = f'log_{date_suffix}'
        user_table = f'user_{date_suffix}'
        
        if log_table in all_db_tables and user_table in all_db_tables:
            log_tables_in_range.append((log_table, user_table))
            
        current_date += timedelta(days=1)
        
    return log_tables_in_range

def get_all_usernames(db: Session) -> List[str]:
    """
    Obtiene una lista de todos los nombres de usuario únicos de todas las tablas de usuarios.
    """
    engine = db.get_bind()
    inspector = inspect(engine)
    all_tables = inspector.get_table_names()
    user_tables = [t for t in all_tables if t.startswith('user_') and len(t) == 13]
    
    if not user_tables:
        return []

    union_query = " UNION ".join([f"SELECT username FROM {table}" for table in user_tables])
    full_query = text(f"SELECT DISTINCT username FROM ({union_query}) as all_users WHERE username != '-' ORDER BY username")
    
    try:
        result = db.execute(full_query).fetchall()
        return [row[0] for row in result]
    except SQLAlchemyError as e:
        print(f"Error al obtener todos los usuarios: {e}")
        return []

def get_user_activity_summary(db: Session, username: str, start_str: str, end_str: str) -> Dict[str, Any]:
    """
    Genera un resumen de actividad para un usuario específico en un rango de fechas.
    """
    start_date = datetime.strptime(start_str, '%Y-%m-%d')
    end_date = datetime.strptime(end_str, '%Y-%m-%d')
    inspector = inspect(db.get_bind())
    tables = _get_tables_in_range(inspector, start_date, end_date)

    if not tables:
        return {"error": "No hay datos para las fechas seleccionadas."}

    select_clauses = []
    for log_table, user_table in tables:
        select_clauses.append(
            f"SELECT l.url, l.data_transmitted, l.request_count FROM {log_table} l JOIN {user_table} u ON l.user_id = u.id WHERE u.username = :username"
        )
    
    full_query = text(" UNION ALL ".join(select_clauses))
    
    try:
        results = db.execute(full_query, {'username': username}).fetchall()
        if not results:
            return {'total_requests': 0, 'total_data_gb': 0, 'top_domains': [], 'activity': []}

        total_requests = sum(r.request_count for r in results)
        total_data = sum(r.data_transmitted for r in results)
        domain_counts = {}
        
        for row in results:
            try:
                domain = row.url.split('//')[-1].split('/')[0].split(':')[0]
                domain_counts[domain] = domain_counts.get(domain, 0) + row.request_count
            except:
                continue
        
        sorted_domains = sorted(domain_counts.items(), key=lambda item: item[1], reverse=True)
        
        return {
            "total_requests": total_requests,
            "total_data_gb": round(total_data / (1024**3), 2),
            "top_domains": [{"domain": d, "count": c} for d, c in sorted_domains[:5]],
            "activity": [{"url": r.url, "data": r.data_transmitted} for r in results[:100]] 
        }

    except SQLAlchemyError as e:
        return {"error": str(e)}

def get_top_users_by_data(db: Session, start_str: str, end_str: str, limit: int = 10) -> Dict[str, Any]:
    """
    Obtiene los N usuarios que más datos han consumido en un rango de fechas.
    """
    start_date = datetime.strptime(start_str, '%Y-%m-%d')
    end_date = datetime.strptime(end_str, '%Y-%m-%d')
    inspector = inspect(db.get_bind())
    tables = _get_tables_in_range(inspector, start_date, end_date)

    if not tables:
        return {"error": "No hay datos para las fechas seleccionadas."}

    select_clauses = []
    for log_table, user_table in tables:
        select_clauses.append(
            f"SELECT u.username, l.data_transmitted FROM {log_table} l JOIN {user_table} u ON l.user_id = u.id WHERE u.username != '-'"
        )
        
    full_query = text(f"""
        SELECT username, SUM(data_transmitted) as total_data
        FROM ({ " UNION ALL ".join(select_clauses) }) as all_logs
        GROUP BY username
        ORDER BY total_data DESC
        LIMIT :limit
    """)
    
    try:
        results = db.execute(full_query, {'limit': limit}).fetchall()
        # --- INICIO DE LA MODIFICACIÓN ---
        # Se convierte explícitamente el resultado a float() para asegurar que JSON
        # lo serialize como un número y no como un string.
        top_users_list = [{
            "username": r.username, 
            "total_data_gb": float(round((r.total_data or 0) / (1024**3), 2))
        } for r in results]
        
        return {"top_users": top_users_list}
        # --- FIN DE LA MODIFICACIÓN ---
    except SQLAlchemyError as e:
        return {"error": str(e)}

def find_denied_access(db: Session, start_str: str, end_str: str, username: str = None) -> Dict[str, Any]:
    """
    Busca todos los intentos de acceso denegados (código 403) en un rango de fechas,
    opcionalmente filtrado por un usuario específico.
    """
    start_date = datetime.strptime(start_str, '%Y-%m-%d')
    end_date = datetime.strptime(end_str, '%Y-%m-%d')
    inspector = inspect(db.get_bind())
    tables = _get_tables_in_range(inspector, start_date, end_date)

    if not tables:
        return {"error": "No hay datos para las fechas seleccionadas."}

    select_clauses = []
    for log_table, user_table in tables:
        date_str = log_table.split('_')[1]
        select_clauses.append(
            f"SELECT u.username, l.url, '{date_str}' as log_date FROM {log_table} l JOIN {user_table} u ON l.user_id = u.id WHERE l.response = 403"
        )
    
    user_filter = ""
    params = {}
    if username:
        user_filter = "WHERE username = :username"
        params['username'] = username

    full_query = text(f"""
        SELECT username, url, log_date
        FROM ({ " UNION ALL ".join(select_clauses) }) as all_denied
        {user_filter}
        ORDER BY log_date DESC, username
    """)

    try:
        results = db.execute(full_query, params).fetchall()
        return {
            "denied_access": [
                {
                    "date": datetime.strptime(r.log_date, "%Y%m%d").strftime("%Y-%m-%d"),
                    "username": r.username, 
                    "url": r.url
                } for r in results
            ]
        }
    except SQLAlchemyError as e:
        return {"error": str(e)}