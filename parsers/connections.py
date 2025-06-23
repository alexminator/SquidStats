# parsers/connections.py

import re
from collections import defaultdict

# --- MODIFICACIÓN ---
# Se ha renombrado 'remote' a 'client_ip' para que el nombre sea más descriptivo.
# 'client_ip' corresponde a la IP del usuario que hace la petición.
# 'proxy_local_ip' es la IP que usa el proxy para esa conexión específica.
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
    """Analiza datos crudos de Squid y retorna conexiones estructuradas"""
    connections = []
    # Se ignora el primer elemento vacío que resulta del split
    blocks = raw_data.split("Connection:")[1:]
    
    for block in blocks:
        try:
            connection = parse_connection_block(block)
            connections.append(connection)
        except Exception as e:
            # Imprime un error pero permite que el script continúe
            print(f"Error parseando bloque: {e}\n{block[:100]}...")
    
    return connections

def parse_connection_block(block):
    """Procesa un bloque individual de conexión"""
    conn = {}
    
    # Extraer campos usando el mapa de expresiones regulares
    for key, regex in REGEX_MAP.items():
        if key not in ["fd_read", "fd_wrote", "nrequests", "delay_pool", "fd_total"]:
            match = regex.search(block)
            conn[key] = match.group(1) if match else "N/A"
    
    # Manejo específico para campos numéricos
    conn["fd_read"] = int(REGEX_MAP["fd_read"].search(block).group(1)) if REGEX_MAP["fd_read"].search(block) else 0
    conn["fd_wrote"] = int(REGEX_MAP["fd_wrote"].search(block).group(1)) if REGEX_MAP["fd_wrote"].search(block) else 0
    conn["fd_total"] = conn["fd_read"] + conn["fd_wrote"]
    
    conn["nrequests"] = int(REGEX_MAP["nrequests"].search(block).group(1)) if REGEX_MAP["nrequests"].search(block) else 0
    conn["delay_pool"] = int(REGEX_MAP["delay_pool"].search(block).group(1)) if REGEX_MAP["delay_pool"].search(block) else "N/A"
    
    # --- AÑADIDO ---
    # Limpiamos el puerto de la IP del cliente para una visualización más limpia en la UI.
    # Por ejemplo, de "192.168.33.31:54879" pasará a ser "192.168.33.31".
    if conn.get("client_ip") and ":" in conn["client_ip"]:
        conn["client_ip"] = conn["client_ip"].split(":")[0]
        
    return conn

def group_by_user(connections):
    """
    Agrupa conexiones por usuario.
    --- CAMBIO CLAVE ---
    La estructura de datos devuelta ahora es un diccionario anidado.
    Esto nos permite almacenar la IP del cliente junto con su lista de conexiones.
    Formato de salida: {'nombre_usuario': {'client_ip': '192.168.1.10', 'connections': [...]}}
    """
    ANONYMOUS_INDICATORS = {
        None, "", "-", "Anónimo", "N/A", "anonymous", "Anonymous", 
        "unknown", "guest", "none", "null"
    }
    
    # Usamos defaultdict para inicializar automáticamente la estructura anidada.
    grouped = defaultdict(lambda: {"client_ip": "No disponible", "connections": []})
    
    for connection in connections:
        user = connection.get("username")
        
        # --- Lógica de filtrado de usuarios anónimos (robusta) ---
        if user is None:
            continue
        if not isinstance(user, str):
            user = str(user)
        user_normalized = user.strip().lower()
        is_anonymous = (
            not user_normalized or
            user_normalized in (indicator.lower() for indicator in ANONYMOUS_INDICATORS if indicator is not None)
        )
        if is_anonymous:
            continue
       
        # Si es la primera vez que vemos a este usuario en este lote,
        # guardamos su IP. Tomamos la de la primera conexión encontrada.
        if not grouped[user]["connections"]:
            grouped[user]["client_ip"] = connection.get("client_ip", "No disponible")
            
        # Agregamos la conexión actual a la lista de conexiones del usuario.
        grouped[user]["connections"].append(connection)
    
    # Se convierte a un dict normal para evitar comportamientos inesperados de defaultdict en el template.
    return dict(grouped)