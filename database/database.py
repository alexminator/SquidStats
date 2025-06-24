import os
import logging
from sqlalchemy import (create_engine, Column, Integer, String, BigInteger, Text, DateTime, Float, inspect) # Se añade Float
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.ext.declarative import declared_attr
from typing import Tuple, Dict, Any
from dotenv import load_dotenv
from datetime import datetime, date

# Cargar variables de entorno desde .env
load_dotenv()

# Configurar logging para seguimiento
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

                                                                
Base = declarative_base()
_engine = None
_Session = None
dynamic_model_cache: Dict[str, Any] = {}

def get_table_suffix() -> str:
    return date.today().strftime("%Y%m%d")

class DailyBase(Base):
    __abstract__ = True

    @declared_attr
    def __tablename__(cls):
        return None

class User(DailyBase):
    __tablename__ = "user_base"
    id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=False)
    ip = Column(String(15), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

class Log(DailyBase):
    __tablename__ = "log_base"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    url = Column(Text, nullable=False)
    response = Column(Integer, nullable=False)
    request_count = Column(Integer, default=1)
    data_transmitted = Column(BigInteger, default=0)
    created_at = Column(DateTime, default=datetime.now)

# --- AÑADIDO: Modelo base para la tabla de métricas ---
class Metrics(DailyBase):
    __tablename__ = "metrics_base"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    cpu_usage = Column(Float)
    ram_usage_bytes = Column(BigInteger)
    swap_usage_bytes = Column(BigInteger)
    net_sent_bytes_sec = Column(BigInteger)
    net_recv_bytes_sec = Column(BigInteger)
# --- FIN AÑADIDO ---

class LogMetadata(Base):
    __tablename__ = "log_metadata"
    id = Column(Integer, primary_key=True)
    last_position = Column(BigInteger, default=0)
    last_inode = Column(BigInteger, default=0)

def get_database_url() -> str:
    db_type = os.getenv("DATABASE_TYPE", "SQLITE").upper()
    conn_str = os.getenv("DATABASE_STRING_CONNECTION", "squidstats.db")
    if db_type == "SQLITE":
        if not conn_str.startswith("sqlite:///"):
            return f"sqlite:///{conn_str}"
        return conn_str
    elif db_type in ("MYSQL", "MARIADB"):
        if conn_str.startswith("mysql://") or conn_str.startswith("mariadb://") or conn_str.startswith("mysql+pymysql://"):
            return conn_str
        raise ValueError("Para MySQL/MariaDB, especifique el string de conexión completo en DATABASE_STRING_CONNECTION")
    else:
        raise ValueError(f"Tipo de base de datos no soportado: {db_type}")

def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    db_url = get_database_url()
    _engine = create_engine(db_url, echo=False, future=True)
    return _engine

def get_session():
    global _Session
    engine = get_engine()
    if _Session is None:
        create_dynamic_tables(engine)
        _Session = sessionmaker(bind=engine)
    return _Session()

def table_exists(engine, table_name: str) -> bool:
    inspector = inspect(engine)
    return inspector.has_table(table_name)

# --- AÑADIDO: Función para obtener el nombre de la tabla de métricas del día ---
def get_metrics_table_name(date_suffix: str = None) -> str:
    if date_suffix is None:
        date_suffix = get_table_suffix()
    return f"metrics_{date_suffix}"
# --- FIN AÑADIDO ---

def create_dynamic_tables(engine):
    LogMetadata.__table__.create(engine, checkfirst=True)
    user_table_name, log_table_name = get_dynamic_table_names()
    # --- AÑADIDO: Obtener nombre de tabla de métricas ---
    metrics_table_name = get_metrics_table_name()
    
    if not table_exists(engine, user_table_name) or not table_exists(engine, log_table_name) or not table_exists(engine, metrics_table_name):
        logger.info(f"Creando tablas dinámicas para hoy: {user_table_name}, {log_table_name}, {metrics_table_name}")
        DynamicBase = declarative_base()

        class DynamicUser(DynamicBase):
            __tablename__ = user_table_name
            # ... (definición de columnas sin cambios)
            id = Column(Integer, primary_key=True)
            username = Column(String(255), nullable=False)
            ip = Column(String(15), nullable=False)
            created_at = Column(DateTime, default=datetime.now)

        class DynamicLog(DynamicBase):
            __tablename__ = log_table_name
            # ... (definición de columnas sin cambios)
            id = Column(Integer, primary_key=True)
            user_id = Column(Integer, nullable=False)
            url = Column(Text, nullable=False)
            response = Column(Integer, nullable=False)
            request_count = Column(Integer, default=1)
            data_transmitted = Column(BigInteger, default=0)
            created_at = Column(DateTime, default=datetime.now)

        # --- AÑADIDO: Clase dinámica para la tabla de métricas ---
        class DynamicMetrics(DynamicBase):
            __tablename__ = metrics_table_name
            id = Column(Integer, primary_key=True)
            timestamp = Column(DateTime, default=datetime.now, index=True)
            cpu_usage = Column(Float)
            ram_usage_bytes = Column(BigInteger)
            swap_usage_bytes = Column(BigInteger)
            net_sent_bytes_sec = Column(BigInteger)
            net_recv_bytes_sec = Column(BigInteger)
        # --- FIN AÑADIDO ---
            
        DynamicBase.metadata.create_all(engine, checkfirst=True)
                                                
def get_dynamic_table_names(date_suffix: str = None) -> Tuple[str, str]:
    if date_suffix is None:
        date_suffix = get_table_suffix()
    return f"user_{date_suffix}", f"log_{date_suffix}"

# --- AÑADIDO: Función para obtener el modelo dinámico de Métricas ---
def get_dynamic_metrics_model(date_suffix: str):
    cache_key = f"metrics_{date_suffix}"
    if cache_key in dynamic_model_cache:
        return dynamic_model_cache[cache_key]

    engine = get_engine()
    metrics_table_name = get_metrics_table_name(date_suffix)

    if not table_exists(engine, metrics_table_name):
        # La tabla debería haber sido creada por create_dynamic_tables.
        # Si no existe, podría ser un día nuevo antes de que se llame a get_session.
        create_dynamic_tables(engine)
        if not table_exists(engine, metrics_table_name):
             logger.error(f"No se pudo crear o encontrar la tabla de métricas: {metrics_table_name}")
             return None

    DynamicBase = declarative_base()
    class DynamicMetrics(DynamicBase):
        __tablename__ = metrics_table_name
        __table_args__ = {'extend_existing': True}
        id = Column(Integer, primary_key=True)
        timestamp = Column(DateTime, default=datetime.now, index=True)
        cpu_usage = Column(Float)
        ram_usage_bytes = Column(BigInteger)
        swap_usage_bytes = Column(BigInteger)
        net_sent_bytes_sec = Column(BigInteger)
        net_recv_bytes_sec = Column(BigInteger)

    dynamic_model_cache[cache_key] = DynamicMetrics
    return DynamicMetrics
# --- FIN AÑADIDO ---

def get_dynamic_models(date_suffix: str):
    # (Esta función permanece sin cambios para no afectar otras partes del código)
    cache_key = f"user_log_{date_suffix}"
    if cache_key in dynamic_model_cache:
        return dynamic_model_cache[cache_key]
        
    engine = get_engine()
    user_table_name, log_table_name = get_dynamic_table_names(date_suffix)
    
    if not table_exists(engine, user_table_name) or not table_exists(engine, log_table_name):
        return None, None

    DynamicBase = declarative_base()

    class DynamicUser(DynamicBase):
        __tablename__ = user_table_name
        id = Column(Integer, primary_key=True, autoincrement=True)
        username = Column(String(255), nullable=False)
        ip = Column(String(15), nullable=False)
        created_at = Column(DateTime, default=datetime.now)

    class DynamicLog(DynamicBase):
        __tablename__ = log_table_name
        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, nullable=False)
        url = Column(Text, nullable=False)
        response = Column(Integer, nullable=False)
        request_count = Column(Integer, default=1)
        data_transmitted = Column(BigInteger, default=0)
        created_at = Column(DateTime, default=datetime.now)

    DynamicUser.__table__ = User.__table__.tometadata(DynamicBase.metadata, name=user_table_name)
    DynamicLog.__table__ = Log.__table__.tometadata(DynamicBase.metadata, name=log_table_name)

    dynamic_model_cache[cache_key] = (DynamicUser, DynamicLog)
    return DynamicUser, DynamicLog

def clear_dynamic_model_cache():
    dynamic_model_cache.clear()