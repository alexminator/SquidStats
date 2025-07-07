#! /bin/bash

function error() {
    echo -e "\n\033[1;41m$1\033[0m\n"
}

function ok() {
    echo -e "\n\033[1;42m$1\033[0m\n"
}

function checkSudo() {
    if [ "$EUID" -ne 0 ]; then
        error "ERROR: Este script debe ejecutarse con privilegios de superusuario.\nPor favor, ejecútelo con: sudo $0"
        exit 1
    fi
}

function setupVenv() {
    local venv_dir="/opt/squidstats/venv"

    if [ -d "$venv_dir" ]; then
        echo "Entorno virtual ya existe en $venv_dir"
        return 0
    fi

    echo "Creando entorno virtual Python en $venv_dir"
    python3 -m venv "$venv_dir"

    if [ $? -ne 0 ]; then
        error "Error al crear el entorno virtual"
        return 1
    fi

    ok "Entorno virtual creado correctamente"
    return 0
}

function installDependencies() {
    local venv_dir="/opt/squidstats/venv"

    if [ ! -d "$venv_dir" ]; then
        error "El entorno virtual no existe en $venv_dir"
        return 1
    fi

    echo "Activando entorno virtual y instalando dependencias..."
    source "$venv_dir/bin/activate"

    pip install --upgrade pip
    pip install -r /opt/squidstats/requirements.txt

    if [ $? -ne 0 ]; then
        error "Error al instalar dependencias"
        deactivate
        return 1
    fi

    ok "Dependencias instaladas correctamente en el entorno virtual"
    deactivate
    return 0
}

function checkPackages() {
    local paquetes=("git" "python3" "python3-pip" "python3-venv" "python3-pymysql" "libmariadb-dev" "curl")
    local faltantes=()

    for pkg in "${paquetes[@]}"; do
        if ! dpkg -l | grep -q "^ii  $pkg "; then
            faltantes+=("$pkg")
        fi
    done

    if [ ${#faltantes[@]} -ne 0 ]; then
        echo "Instalando paquetes faltantes: ${faltantes[*]}"
        apt-get update

        if ! apt-get install -y "${faltantes[@]}"; then
            error "ERROR: Compruebe la versión de su OS se recomienda Ubuntu20.04+ o Debian12+"
            exit 1
        fi

        ok "Paquetes instalados correctamente"
    else
        echo "Todos los paquetes necesarios ya están instalados"
    fi
}

function updateOrCloneRepo() {
    local repo_url="https://github.com/alexminator/SquidStats.git"
    local destino="/opt/squidstats"
    local branch="fusion"
    local env_exists=false

    if [ -d "$destino" ]; then
        echo "El directorio $destino ya existe, intentando actualizar con git pull..."
        cd "$destino"

        if [ -d ".git" ]; then
            if [ -f ".env" ]; then
                env_exists=true
                echo ".env existente detectado, se preservará"
                cp .env /tmp/.env.backup
            fi

            if git fetch origin "$branch" && git checkout "$branch" && git pull origin "$branch"; then
                [ "$env_exists" = true ] && mv /tmp/.env.backup .env
                echo "? Repositorio actualizado exitosamente en la rama '$branch'"
                return 0
            else
                echo "? Error al actualizar el repositorio, se procederá a clonar de nuevo"
                cd ..
                rm -rf "$destino"
            fi
        else
            echo "?? El directorio existe pero no es un repositorio git, se procederá a clonar de nuevo"
            rm -rf "$destino"
        fi
    fi

    echo "?? Clonando repositorio por primera vez desde la rama '$branch'..."
    if git clone --branch "$branch" "$repo_url" "$destino"; then
        chown -R $USER:$USER "$destino"
        echo "? Repositorio clonado exitosamente en $destino"

        if [ "$env_exists" = true ] && [ -f /tmp/.env.backup ]; then
            mv /tmp/.env.backup "$destino/.env"
            echo "?? Archivo .env restaurado"
        fi

        return 0
    else
        echo "? Error al clonar el repositorio"
        return 1
    fi
}

function moveDB() {
    local databaseSQlite="/opt/squidstats/squidstats.db"
    local env_file="/opt/squidstats/.env"
    local current_version=0

    current_version=$(grep -E '^VERSION\s*=' "$env_file" | cut -d= -f2 | tr -dc '0-9' || echo 0)

    if ! grep -qE '^VERSION\s*=' "$env_file"; then
      echo "VERSION=2" >> "$env_file"
        echo "Eliminando base de datos antigua por actualización..."
        rm -rf "$databaseSQlite"
        ok "Base de datos antigua eliminada"
    else
        echo "Base de datos no requiere actualización"
    fi

    if [ -f "$databaseSQlite" ] && [ "$current_version" -lt 2 ]; then
        echo "Eliminando base de datos antigua por actualización..."
        rm -rf "$databaseSQlite"
        ok "Base de datos antigua eliminada"
    else
        echo "Base de datos no requiere actualización"
    fi

    return 0
}

function createEnvFile() {
    local env_file="/opt/squidstats/.env"

    if [ -f "$env_file" ]; then
        echo "El archivo .env ya existe en $env_file."
        return 0
    else
        echo "Creando archivo de configuración .env..."
        # --- INICIO DE LA MODIFICACIÓN: Apuntar SQUID_LOG al nuevo fichero ---
        cat > "$env_file" << EOF
VERSION=2
SQUID_HOST=127.0.0.1
SQUID_PORT=3128
FLASK_DEBUG=True
DATABASE_TYPE=SQLITE
SQUID_LOG=/var/log/squid/squidstats.log
DATABASE_STRING_CONNECTION=/opt/squidstats/squidstats.db
REFRESH_INTERVAL=60
EOF
        # --- FIN DE LA MODIFICACIÓN ---
        ok "Archivo .env creado correctamente en $env_file"
        return 0
    fi
}

function createService() {
    local service_file="/etc/systemd/system/squidstats.service"

    if [ -f "$service_file" ]; then
        echo "El servicio ya existe en $service_file, no se realizan cambios."
        return 0
    fi

    echo "Creando servicio en $service_file..."
    cat > "$service_file" << EOF
[Unit]
Description=SquidStats Web Application
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/squidstats
ExecStart=/opt/squidstats/venv/bin/python3 /opt/squidstats/app.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/squidstats/.env
Environment="PATH=/opt/squidstats/venv/bin:/usr/sbin:/usr/bin:/bin:$PATH"

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable squidstats.service
    systemctl start squidstats.service
    ok "Servicio creado y iniciado correctamente"
}

function configureDatabase() {
    local env_file="/opt/squidstats/.env"

    echo -e "\n\033[1;44mCONFIGURACIÓN DE BASE DE DATOS\033[0m"
    echo "Seleccione el tipo de base de datos:"
    echo "1) SQLite (por defecto)"
    echo "2) MariaDB (necesitas tener mariadb ejecutándose)"

    while true; do
        read -p "Opción [1/2]: " choice
        case $choice in
            1|"") break;;
            2) break;;
            *) error "Opción inválida. Intente nuevamente.";;
        esac
    done

    case $choice in
        2)
            while true; do
                read -p "Ingrese cadena de conexión (mysql+pymysql://user:clave@host:port/db): " conn_str

                if [[ "$conn_str" != mysql+pymysql://* ]]; then
                    error "Formato inválido. Debe comenzar con: mysql+pymysql://"
                    continue
                fi

                validation_result=$(python3 /opt/squidstats/utils/validateString.py "$conn_str" 2>&1)
                exit_code=$?

                if [[ $exit_code -eq 0 ]]; then
                    sed -i "s|^DATABASE_TYPE=.*|DATABASE_TYPE=MARIADB|" "$env_file"

                    escaped_conn_str=$(printf '%s\n' "$validation_result" | sed -e 's/[\/&]/\\&/g')
                    sed -i "s|^DATABASE_STRING_CONNECTION=.*|DATABASE_STRING_CONNECTION=$escaped_conn_str|" "$env_file"

                    ok "Configuración MariaDB actualizada!"
                    break
                else
                    error "Error en la cadena:\n${validation_result#ERROR: }"
                fi
            done
            ;;
        *)
            sqlite_path="/opt/squidstats/squidstats.db"
            sed -i "s|^DATABASE_TYPE=.*|DATABASE_TYPE=SQLITE|" "$env_file"
            sed -i "s|^DATABASE_STRING_CONNECTION=.*|DATABASE_STRING_CONNECTION=$sqlite_path|" "$env_file"
            ok "Configuración SQLite establecida!"
            ;;
    esac
}

# --- INICIO DE LA MODIFICACIÓN: La función ahora añade un log en vez de modificar el existente ---
function patchSquidConf() {
    local squid_conf=""

    if [ -f "/etc/squid/squid.conf" ]; then
        squid_conf="/etc/squid/squid.conf"
    elif [ -f "/etc/squid3/squid.conf" ]; then
        squid_conf="/etc/squid3/squid.conf"
    else
        error "Debe tener instalado un servidor proxy Squid. No se encontró squid.conf"
        return 1
    fi

    cp "$squid_conf" "${squid_conf}.back"
    ok "Backup realizado: ${squid_conf}.back"

    # Añade el formato de log 'detailed' solo si no existe
    if ! grep -q '^logformat[[:space:]]\+detailed' "$squid_conf"; then
        echo -e "\n# Formato de log para SquidStats\nlogformat detailed %ts.%03tu|%>a|%ui|%un|[%tl]|%rm|%ru|HTTP/%rv|%>Hs|%<st|%mt|%<a|%<rm|%Ss/%Sh" >> "$squid_conf"
        ok "Se agregó logformat detailed"
    else
        echo "logformat detailed ya existe."
    fi

    # Añade la directiva para el nuevo fichero de log de SquidStats si no existe
    local stats_log_directive="access_log daemon:/var/log/squid/squidstats.log detailed !manager"
    if ! grep -q 'squidstats\.log' "$squid_conf"; then
        echo "Añadiendo directiva de log para SquidStats..."
        echo -e "\n# Log para SquidStats\n$stats_log_directive" >> "$squid_conf"
        ok "Se agregó la directiva de log para squidstats.log"
    else
        echo "La directiva de log para SquidStats ya existe."
    fi
}
# --- FIN DE LA MODIFICACIÓN ---

function main() {
    checkSudo

     if [ "$1" = "--update" ]; then
      echo "Actualizando Servicio..."
      updateOrCloneRepo
      patchSquidConf
      systemctl restart squid.service
      systemctl restart squidstats.service

      ok "Actualizacion completada! Acceda en: \033[1;37mhttp://IP:5000\033[0m"
    else
      echo "Instalando aplicación web..."
      checkPackages
      updateOrCloneRepo
      patchSquidConf
      setupVenv
      installDependencies
      createEnvFile
      configureDatabase
      moveDB
      createService

      # Reiniciar squid para aplicar cambios en squid.conf
      echo "Reiniciando Squid para aplicar la nueva configuración de logs..."
      systemctl restart squid.service

      ok "Instalación completada! Acceda en: \033[1;37mhttp://IP:5000\033[0m"
    fi
}

case "$1" in
    "--update")
        main "$1"
        ;;
    "")
        main
        ;;
    *)
        echo "Parámetro no reconocido: $1"
        echo "Uso: $0 --update"
        exit 1
        ;;
esac