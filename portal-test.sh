#!/usr/bin/env bash
# portal-test.sh - test profile for the Office Hub "portal path".
#
# Two levels:
#
#   DB-level profile  (seed)      - MariaDB with stand-in admidio + flarum
#                                   databases and data/backup_db.json, for
#                                   backup dump/restore regression without
#                                   installing the PHP apps.
#
#   Full portal profile (seed-apps) - additionally installs the REAL apps:
#                                   Admidio (member portal, :8080) and Flarum
#                                   (forum, :8081), each installed headlessly
#                                   by replaying its own installer (Admidio's
#                                   HTTP wizard, Flarum's `flarum install`
#                                   CLI). This is what production runs; the
#                                   portal's own pages then get coverage too.
#
# Subcommands:
#   sudo bash portal-test.sh install      apt install mariadb + php + composer
#   bash      portal-test.sh seed         DB-level profile (fake tables)
#   bash      portal-test.sh seed-apps    full profile: real Admidio + Flarum
#   bash      portal-test.sh status       DB-level readiness
#   bash      portal-test.sh status-apps  full-profile readiness
#   bash      portal-test.sh dump         run the hub's dump_databases() once
#   bash      portal-test.sh remove       drop fake test tables + override
#   bash      portal-test.sh remove-apps  drop app DBs + app configs
#
# All credentials here are throwaway, local-only test values. data/ is
# git-ignored, so nothing sensitive lands in the repo.

set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
OVERRIDE="data/backup_db.json"
TESTPW_HUB="hub-backup-test"
TESTPW_ADM="admidio-test"
TESTPW_FLX="flarum-test"
ADMIN_USER="admin"
ADMIN_PW="Portal-Admin-Test-1"
ADMIN_MAIL="admin@example.org"
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

ROOT="$(pwd)"
SEED_SQL=$(cat <<'SQL'
CREATE DATABASE IF NOT EXISTS admidio CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS flarum  CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'admidio'@'localhost'    IDENTIFIED BY 'admidio-test';
CREATE USER IF NOT EXISTS 'flarum'@'localhost'     IDENTIFIED BY 'flarum-test';
CREATE USER IF NOT EXISTS 'hub_backup'@'localhost'  IDENTIFIED BY 'hub-backup-test';
CREATE USER IF NOT EXISTS 'hub_backup'@'127.0.0.1'  IDENTIFIED BY 'hub-backup-test';
GRANT ALL PRIVILEGES ON admidio.* TO 'admidio'@'localhost';
GRANT ALL PRIVILEGES ON flarum.*  TO 'flarum'@'localhost';
GRANT ALL PRIVILEGES ON admidio.* TO 'hub_backup'@'localhost';
GRANT ALL PRIVILEGES ON admidio.* TO 'hub_backup'@'127.0.0.1';
GRANT ALL PRIVILEGES ON flarum.*  TO 'hub_backup'@'localhost';
GRANT ALL PRIVILEGES ON flarum.*  TO 'hub_backup'@'127.0.0.1';
FLUSH PRIVILEGES;

USE admidio;
CREATE TABLE IF NOT EXISTS adm_members (
  mem_id INT AUTO_INCREMENT PRIMARY KEY,
  mem_username VARCHAR(50), mem_first_name VARCHAR(50),
  mem_last_name VARCHAR(50), mem_email VARCHAR(100),
  mem_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP);
INSERT IGNORE INTO adm_members (mem_id, mem_username, mem_first_name, mem_last_name, mem_email) VALUES
 (1,'emily.carter','Emily','Carter','emily@example.org'),
 (2,'maria.lopez','Maria','Lopez','maria@example.org'),
 (3,'sam.okafor','Sam','Okafor','sam@example.org'),
 (4,'pat.nguyen','Pat','Nguyen','pat@example.org');

USE flarum;
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50), email VARCHAR(120),
  joined_at DATETIME DEFAULT CURRENT_TIMESTAMP);
INSERT IGNORE INTO users (id, username, email) VALUES
 (1,'carpenter_joe','joe@example.org'),
 (2,'gardengail','gail@example.org'),
 (3,'tracker_tim','tim@example.org');
SQL
)

port_up() { "$PY" portcheck.py "$1" >/dev/null 2>&1; }

start_app_servers() {
  # start-hub.sh launches both PHP dev servers when php + apps/ exist
  if port_up 8080 && port_up 8081; then return 0; fi
  (setsid nohup bash start-hub.sh >/dev/null 2>&1 </dev/null &)
  for _ in $(seq 1 20); do
    port_up 8080 && port_up 8081 && return 0
    sleep 1
  done
  return 1
}

# ------------------------------------------------- Admidio headless install
# Replays the real HTTP wizard (install/installation.php). Each step stores a
# form object + CSRF token in the PHP session, so the flow is: GET the step,
# extract adm_csrf_token from the rendered page, POST the fields + token back
# with mode=check. Admidio itself writes config.php and builds its schema
# exactly as a manual install would.
adm_csrf() { grep -oP 'name="adm_csrf_token"[^>]*value="\K[^"]+' "$1" 2>/dev/null | head -1 \
          || grep -oP 'value="[^"]+"[^>]*name="adm_csrf_token"' "$1" 2>/dev/null | head -1; }

admidio_install() {
  local base="http://localhost:8080/install/installation.php"
  [ -d apps/admidio/install ] || base="http://localhost:8080/adm_program/install/installation.php"
  local H=/tmp/inst_step.html CSRF

  curl -s -c "$JAR" -b "$JAR" "$base" -o /dev/null                       # welcome

  curl -s -c "$JAR" -b "$JAR" "$base?step=connect_database" -o "$H"
  CSRF=$(adm_csrf "$H")
  curl -s -c "$JAR" -b "$JAR" "$base?step=connect_database&mode=check" \
    --data-urlencode "adm_csrf_token=$CSRF" \
    --data-urlencode "adm_db_engine=mariadb" \
    --data-urlencode "adm_db_host=127.0.0.1" \
    --data-urlencode "adm_db_port=3306" \
    --data-urlencode "adm_db_name=admidio" \
    --data-urlencode "adm_db_username=hub_backup" \
    --data-urlencode "adm_db_password=$TESTPW_HUB" \
    --data-urlencode "adm_table_prefix=adm_" \
    --data-urlencode "adm_next_page=" -o "$H" || return 1
  grep -q "SYS_INVALID_PAGE_VIEW\|status.:.error" "$H" && return 1

  curl -s -c "$JAR" -b "$JAR" "$base?step=create_organization" -o "$H"
  CSRF=$(adm_csrf "$H")
  curl -s -c "$JAR" -b "$JAR" "$base?step=create_organization&mode=check" \
    --data-urlencode "adm_csrf_token=$CSRF" \
    --data-urlencode "adm_organization_shortname=DEMO" \
    --data-urlencode "adm_organization_longname=Demo Club (Portal Test)" \
    --data-urlencode "adm_organization_email=$ADMIN_MAIL" \
    --data-urlencode "adm_organization_timezone=America/Chicago" \
    --data-urlencode "adm_next_page=" -o "$H" || return 1
  grep -q "SYS_INVALID_PAGE_VIEW\|status.:.error" "$H" && return 1

  curl -s -c "$JAR" -b "$JAR" "$base?step=create_administrator" -o "$H"
  CSRF=$(adm_csrf "$H")
  curl -s -c "$JAR" -b "$JAR" "$base?step=create_administrator&mode=check" \
    --data-urlencode "adm_csrf_token=$CSRF" \
    --data-urlencode "adm_user_first_name=Office" \
    --data-urlencode "adm_user_last_name=Admin" \
    --data-urlencode "adm_user_login=$ADMIN_USER" \
    --data-urlencode "adm_user_email=$ADMIN_MAIL" \
    --data-urlencode "adm_user_password=$ADMIN_PW" \
    --data-urlencode "adm_user_password_confirm=$ADMIN_PW" \
    --data-urlencode "adm_next_page=" -o "$H" || return 1
  grep -q "SYS_INVALID_PAGE_VIEW\|status.:.error" "$H" && return 1

  curl -s -c "$JAR" -b "$JAR" "$base?step=create_config" -o /dev/null || return 1
  [ -f apps/admidio/adm_my_files/config.php ] || return 1
  curl -s -c "$JAR" -b "$JAR" "$base?step=start_installation" -o "$H" || return 1
  grep -q "status.:.error" "$H" && return 1
  curl -s -c "$JAR" -b "$JAR" "$base?step=installation_successful" -o /dev/null || return 1
  [ -f apps/admidio/adm_my_files/config.php ]
}

# -------------------------------------------------- Flarum headless install
# Flarum's official CLI installer. Needs an EMPTY database (migrations build
# the schema), which seed-apps guarantees by dropping and recreating it.
flarum_install() {
  local cfg=/tmp/flarum-install.yml
  cat > "$cfg" <<YAML
debug: false
baseUrl: http://localhost:8081
databaseConfiguration:
  driver: mysql
  host: 127.0.0.1
  port: 3306
  database: flarum
  username: hub_backup
  password: "$TESTPW_HUB"
  prefix: flarum_
adminUser:
  username: "$ADMIN_USER"
  password: "$ADMIN_PW"
  email: "$ADMIN_MAIL"
settings:
  forum_title: "Demo Club Forum"
  forum_tagline: "Powered by the Office Hub"
YAML
  (cd apps/flarum && php flarum install -f "$cfg" --no-interaction) >/dev/null
  [ -f apps/flarum/config.php ]
}

fresh_dbs() {
  MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup 2>/dev/null \
    -e "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;" \
  || mysql -e "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;"
  MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup 2>/dev/null \
    -e "CREATE DATABASE admidio CHARACTER SET utf8mb4; CREATE DATABASE flarum CHARACTER SET utf8mb4;" \
  || mysql -e "CREATE DATABASE admidio CHARACTER SET utf8mb4; CREATE DATABASE flarum CHARACTER SET utf8mb4;"
}

case "${1:-}" in
  install)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq mariadb-server mariadb-client \
      php-mysql php-mbstring php-gd php-xml php-curl php-intl composer unzip >/dev/null
    systemctl enable --now mariadb >/dev/null 2>&1 || service mariadb start
    echo "[install] MariaDB + PHP extensions + composer installed";;

  seed)
    mysql <<<"$SEED_SQL"
    cat > "$OVERRIDE" <<JSON
{
  "databases": [
    {"name": "admidio", "user": "hub_backup", "password": "$TESTPW_HUB", "host": "127.0.0.1"},
    {"name": "flarum",  "user": "hub_backup", "password": "$TESTPW_HUB", "host": "127.0.0.1"}
  ]
}
JSON
    # keep the override owned by the human user, not root (works both via
    # sudo and via direct-root elevation, where SUDO_USER is unset)
    if [ "$(id -u)" = 0 ]; then chown "$(stat -c %U "$ROOT" 2>/dev/null || stat -c %U .)" "$OVERRIDE"; fi
    echo "[seed] fake-table DB profile written to $OVERRIDE"
    echo "       (for the DB backup round-trip; run 'seed-apps' for the real apps)";;

  seed-apps)
    # 0. apps staged?
    if [ ! -d apps/admidio ] || [ ! -d apps/flarum ]; then
      echo "[seed-apps] staging apps via setup_apps.py (first run downloads ~90MB) ..."
      python3 setup_apps.py >/dev/null 2>&1 || python3 setup_apps.py
    fi
    # 1. credential override (same as seed)
    cat > "$OVERRIDE" <<JSON
{
  "databases": [
    {"name": "admidio", "user": "hub_backup", "password": "$TESTPW_HUB", "host": "127.0.0.1"},
    {"name": "flarum",  "user": "hub_backup", "password": "$TESTPW_HUB", "host": "127.0.0.1"}
  ]
}
JSON
    # 2. servers up (Admidio's wizard runs over HTTP)
    start_app_servers || { echo "[seed-apps] could not start PHP servers on :8080/:8081"; exit 1; }
    # 3. EMPTY databases (both installers build their own schema)
    fresh_dbs
    rm -f apps/admidio/adm_my_files/config.php apps/flarum/config.php
    # 4. install each app with its own installer
    echo "[seed-apps] installing Admidio via its web wizard (headless) ..."
    admidio_install || { echo "[seed-apps] Admidio install FAILED"; exit 1; }
    echo "[seed-apps] installing Flarum via its CLI installer ..."
    flarum_install || { echo "[seed-apps] Flarum install FAILED"; exit 1; }
    echo "[seed-apps] DONE: Admidio :8080 + Flarum :8081 installed"
    echo "  portal admin login (test only): $ADMIN_USER / $ADMIN_PW"
    echo "  org long name: Demo Club (Portal Test) | forum title: Demo Club Forum";;

  status)
    fail=0
    which mysqldump >/dev/null     || { echo "  client tools:  MISSING"; fail=1; }
    port_up 3306 \
      && echo "  MariaDB :3306  up"    || { echo "  MariaDB :3306  DOWN"; fail=1; }
    [ -f "$OVERRIDE" ] && echo "  override:      $OVERRIDE present" \
                       || { echo "  override:      missing (run: bash portal-test.sh seed)"; fail=1; }
    if port_up 3306; then
      MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup -N -e \
        "SELECT CONCAT('  admidio rows: ', COUNT(*)) FROM admidio.adm_members" 2>/dev/null \
        || { echo "  admidio:       not seeded (fake tables absent - full-apps profile in use?)"; }
      MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup -N -e \
        "SELECT CONCAT('  flarum rows:  ', COUNT(*)) FROM flarum.users" 2>/dev/null \
        || { echo "  flarum:        not seeded (fake tables absent - full-apps profile in use?)"; }
    fi
    [ "$fail" = 0 ] && echo "  DB-level test profile: READY" || { echo "  DB-level test profile: NOT READY"; exit 1; }
    ;;

  status-apps)
    fail=0
    command -v php >/dev/null         || { echo "  php:           MISSING"; fail=1; }
    php -m 2>/dev/null | grep -qi pdo_mysql || { echo "  php-pdo_mysql: MISSING"; fail=1; }
    port_up 3306                      || { echo "  MariaDB :3306  DOWN"; fail=1; }
    [ -d apps/admidio ]               || { echo "  apps/admidio:  missing"; fail=1; }
    [ -d apps/flarum ]                || { echo "  apps/flarum:   missing"; fail=1; }
    [ -f apps/admidio/adm_my_files/config.php ]    && echo "  admidio:       installed (config.php present)" \
                                      || { echo "  admidio:       not installed"; fail=1; }
    [ -f apps/flarum/config.php ]     && echo "  flarum:        installed (config.php present)" \
                                      || { echo "  flarum:        not installed"; fail=1; }
    if port_up 8080 && port_up 8081; then
      A=$(curl -sL -m 10 http://localhost:8080/ | grep -c "Demo Club (Portal Test)" || true)
      F=$(curl -sL -m 10 http://localhost:8081/ | grep -c "Demo Club Forum" || true)
      [ "${A:-0}" -gt 0 ] && echo "  :8080 serves the portal (org long name found)" \
                          || { echo "  :8080 up but demo content missing"; fail=1; }
      [ "${F:-0}" -gt 0 ] && echo "  :8081 serves the forum (title found)" \
                          || { echo "  :8081 up but demo content missing"; fail=1; }
    else
      echo "  app servers:   not running (bash start-hub.sh)"; fail=1
    fi
    [ "$fail" = 0 ] && echo "  FULL portal test profile: READY" || { echo "  FULL portal test profile: NOT READY"; exit 1; }
    ;;

  dump)
    "$PY" - <<'PYEOF'
from pathlib import Path
from hub import backup
results = backup.dump_databases(Path("/tmp/portal-dump-check"))
for r in results:
    print(("  OK   " if r["ok"] else "  FAIL ") + str(r["db"]) + ": " +
          (str(r.get("bytes", 0)) + " bytes" if r["ok"] else r.get("why", "")))
raise SystemExit(0 if all(r["ok"] for r in results) else 1)
PYEOF
    ;;

  remove)
    MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup -e \
      "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;" 2>/dev/null \
      || mysql -e "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;"
    rm -f "$OVERRIDE"
    echo "[remove] test databases dropped, override removed (MariaDB server left installed)";;

  remove-apps)
    fresh_dbs 2>/dev/null || true
    MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup -e \
      "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;" 2>/dev/null \
      || mysql -e "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;"
    rm -f apps/admidio/adm_my_files/config.php apps/flarum/config.php
    echo "[remove-apps] app databases dropped + app configs removed (apps/ left staged)";;

  *)
    echo "usage: [sudo] bash portal-test.sh {install|seed|seed-apps|status|status-apps|dump|remove|remove-apps}"
    echo "  install/seed-apps first-run pieces may need elevated rights"
    exit 2;;
esac
