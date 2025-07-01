# parsers/connections.py

import re
from collections import defaultdict

# ==============================================================================
# NOTA IMPORTANTE SOBRE LA FUENTE DE DATOS
#
# Este parser procesa la salida del comando de Squid `mgr:client_list`.
# Esta fuente de datos SOLO muestra conexiones activas y persistentes en un
# momento dado (ej. un túnel HTTPS con estado TCP_TUNNEL).
#
# NO CONTIENE eventos de transacción instantáneos como TCP_DENIED o TCP_MISS.
# Esos estados solo se registran en el fichero `access.log` y son procesados
# por los parsers en `log.py` para la vista de actividad histórica.
# ==============================================================================

REGEX_MAP = {
    "fd": re.compile(r"FD (\d+)"),
    "uri": re.compile(r"uri (.+)"),
    "username": re.compile(r"username (.+)"),
    "logType": re.compile(r"logType (.+)"),
    "start": re.compile(r"start ([\d.]+)"),
    "elapsed_time": re.compile(r"start .*?\(([\d.]+) seconds ago\)"),
    "client_ip": re.compile(r"remote: ([\d.]+:\d+)"), # IP del cliente
    "proxy_local_ip": re.compile(r"local: ([\d.]+:\d+)"), # IP del proxy
    "fd_read": re.compile(r"read (\d+)"),
    "fd_wrote": re.compile(r"wrote (\d+)"),
    "nrequests": re.compile(r"nrequests: (\d+)"),
    "delay_pool": re.compile(r"delay_pool (\d+)")
}

def parse_raw_data(raw_data):
    """Analiza datos crudos de conexiones activas de Squid y retorna una lista de conexiones estructuradas."""
    connections = []
    # Se ignora el primer elemento vacío que resulta del split
    blocks = raw_data.split("Connection:")[1:]
    
    for block in blocks:
        try:
            connection = parse_connection_block(block)
            connections.append(connection)
        except Exception as e:
            # Imprime un error pero permite que el script continúe
            print(f"Error parseando bloque de conexión activa: {e}\n{block[:100]}...")
    
    return connections

def parse_connection_block(block):
    """Procesa un bloque individual de conexión activa."""
    conn = {}
    
    # Extraer campos usando el mapa de expresiones regulares
    for key, regex in REGEX_MAP.items():
        if key not in ["fd_read", "fd_wrote", "nrequests", "delay_pool", "fd_total"]:
            match = regex.search(block)
            conn[key] = match.group(1).strip() if match else "N/A"
    
    # Manejo específico para campos numéricos
    conn["fd_read"] = int(REGEX_MAP["fd_read"].search(block).group(1)) if REGEX_MAP["fd_read"].search(block) else 0
    conn["fd_wrote"] = int(REGEX_MAP["fd_wrote"].search(block).group(1)) if REGEX_MAP["fd_wrote"].search(block) else 0
    conn["fd_total"] = conn["fd_read"] + conn["fd_wrote"]
    
    conn["nrequests"] = int(REGEX_MAP["nrequests"].search(block).group(1)) if REGEX_MAP["nrequests"].search(block) else 0
    conn["delay_pool"] = int(REGEX_MAP["delay_pool"].search(block).group(1)) if REGEX_MAP["delay_pool"].search(block) else "N/A"
    
    # Limpiamos el puerto de la IP del cliente para una visualización más limpia en la UI.
    if conn.get("client_ip") and ":" in conn["client_ip"]:
        conn["client_ip"] = conn["client_ip"].split(":")[0]
        
    return conn

def group_by_user(connections):
    """
    Agrupa las conexiones activas por nombre de usuario.
    Filtra los usuarios no identificados (con username '-', 'N/A', etc.).
    """
    ANONYMOUS_INDICATORS = {"-", "N/A", "", "anonymous", "unknown", "guest"}
    
    # Usamos defaultdict para inicializar automáticamente la estructura anidada.
    grouped = defaultdict(lambda: {"client_ip": "No disponible", "connections": []})
    
    for connection in connections:
        user = connection.get("username")
        
        # Si el usuario no es válido o es un indicador de anónimo, se ignora la conexión.
        if not user or user in ANONYMOUS_INDICATORS:
            continue
       
        # Si es la primera vez que vemos a este usuario, guardamos su IP.
        if not grouped[user]["connections"]:
            grouped[user]["client_ip"] = connection.get("client_ip", "No disponible")
            
        # Agregamos la conexión actual a la lista de conexiones del usuario.
        grouped[user]["connections"].append(connection)
    
    # Se convierte a un dict normal para evitar comportamientos inesperados de defaultdict en el template.
    return dict(grouped)