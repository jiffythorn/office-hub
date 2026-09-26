#!/usr/bin/env bash
# portal-test.sh - MariaDB test profile for the Office Hub "portal path".
#
# The product backs up the Admidio member database and the Flarum forum
# database (mysqldump) and can restore them (--restore-databases). This script
# stands in for those apps on a dev box: it installs MariaDB, creates two
# small realistic databases (admidio + flarum) with a dedicated hub_backup
# account, and writes data/backup_db.json - the credential override the hub's
# backup code already supports for exactly this purpose. No portal apps are
# installed; only the database layer the backup code touches.
#
# Subcommands:
#   sudo bash portal-test.sh install   apt install mariadb-server + client tools
#   sudo bash portal-test.sh seed      create test DBs, hub_backup user, override file
#   bash      portal-test.sh status    everything up? DBs populated? override valid?
#   bash      portal-test.sh dump      run the hub's own dump_databases() once
#   bash      portal-test.sh remove    drop the test DBs + user, remove override
#
# The seed data is deliberately recognizable (Emily Carter, dues rows) so
# tests can prove a restore actually brought real rows back.

set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
OVERRIDE="data/backup_db.json"
TESTPW_HUB="hub-backup-test"
TESTPW_ADM="admidio-test"
TESTPW_FLX="flarum-test"

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

case "${1:-}" in
  install)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq mariadb-server mariadb-client >/dev/null
    systemctl enable --now mariadb >/dev/null 2>&1 || service mariadb start
    echo "[install] MariaDB installed and running";;

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
    [ -n "${SUDO_USER:-}" ] && chown "$SUDO_USER" "$OVERRIDE"
    echo "[seed] databases admidio + flarum created; override written to $OVERRIDE"
    echo "       (these test credentials are throwaway; production overrides use real ones)";;

  status)
    fail=0
    which mysqldump >/dev/null     || { echo "  client tools:  MISSING"; fail=1; }
    "$PY" portcheck.py 3306 >/dev/null 2>&1 \
      && echo "  MariaDB :3306  up"    || { echo "  MariaDB :3306  DOWN"; fail=1; }
    [ -f "$OVERRIDE" ] && echo "  override:      $OVERRIDE present" \
                       || { echo "  override:      missing (run: sudo bash portal-test.sh seed)"; fail=1; }
    if "$PY" portcheck.py 3306 >/dev/null 2>&1; then
      MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup -N -e \
        "SELECT CONCAT('  admidio rows: ', COUNT(*)) FROM admidio.adm_members" 2>/dev/null \
        || { echo "  admidio:       not seeded"; fail=1; }
      MYSQL_PWD="$TESTPW_HUB" mysql -h 127.0.0.1 -u hub_backup -N -e \
        "SELECT CONCAT('  flarum rows:  ', COUNT(*)) FROM flarum.users" 2>/dev/null \
        || { echo "  flarum:        not seeded"; fail=1; }
    fi
    [ "$fail" = 0 ] && echo "  portal test profile: READY" || { echo "  portal test profile: NOT READY"; exit 1; }
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
    mysql -h 127.0.0.1 -u hub_backup -p"$TESTPW_HUB" -e \
      "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;" 2>/dev/null \
      || sudo mysql -e "DROP DATABASE IF EXISTS admidio; DROP DATABASE IF EXISTS flarum;"
    rm -f "$OVERRIDE"
    echo "[remove] test databases dropped, override removed (MariaDB server left installed)";;

  *)
    echo "usage: [sudo] bash portal-test.sh {install|seed|status|dump|remove}"
    echo "  install/seed need sudo; status/dump/remove do not"
    exit 2;;
esac
