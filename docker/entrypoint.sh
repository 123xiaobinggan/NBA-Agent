#!/usr/bin/env bash
set -euo pipefail

export DEEPSEEK_API_KEY="${API_KEY:-${DEEPSEEK_API_KEY:-}}"
export NBA_DB_HOST="${NBA_DB_HOST:-127.0.0.1}"
export NBA_DB_PORT="${NBA_DB_PORT:-3306}"
export NBA_DB_USER="${NBA_DB_USER:-nba_agent}"
export NBA_DB_PASSWORD="${NBA_DB_PASSWORD:-nba_agent}"
export NBA_DB_NAME="${NBA_DB_NAME:-nba}"
export NBA_AGENT_MODEL="${NBA_AGENT_MODEL:-deepseek-chat}"

if [[ -z "${DEEPSEEK_API_KEY}" ]]; then
  echo "ERROR: API_KEY or DEEPSEEK_API_KEY is required." >&2
  exit 1
fi

mkdir -p /run/mysqld /var/lib/mysql /app/outputs/charts
chown -R mysql:mysql /run/mysqld /var/lib/mysql

if [[ ! -d /var/lib/mysql/mysql ]]; then
  echo "Initializing MariaDB data directory..."
  mariadb-install-db --user=mysql --datadir=/var/lib/mysql --skip-test-db >/dev/null
fi

echo "Starting MariaDB..."
mariadbd --user=mysql --datadir=/var/lib/mysql --bind-address=127.0.0.1 --port=3306 &
MYSQL_PID="$!"

cleanup() {
  mysqladmin --protocol=socket -uroot shutdown >/dev/null 2>&1 || true
  wait "$MYSQL_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for i in $(seq 1 60); do
  if mysqladmin --protocol=socket -uroot ping >/dev/null 2>&1; then
    break
  fi
  if [[ "$i" -eq 60 ]]; then
    echo "ERROR: MariaDB did not become ready." >&2
    exit 1
  fi
  sleep 1
done

mysql --protocol=socket -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`${NBA_DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${NBA_DB_USER}'@'%' IDENTIFIED BY '${NBA_DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${NBA_DB_USER}'@'localhost' IDENTIFIED BY '${NBA_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${NBA_DB_NAME}\`.* TO '${NBA_DB_USER}'@'%';
GRANT ALL PRIVILEGES ON \`${NBA_DB_NAME}\`.* TO '${NBA_DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

if [[ ! -f /var/lib/mysql/.nba_data_loaded ]]; then
  echo "Creating schema..."
  NBA_SCHEMA_OUTPUT=/tmp/nba_schema.sql python /app/docker/init_db.py
  mysql --protocol=socket -uroot "${NBA_DB_NAME}" < /tmp/nba_schema.sql

  echo "Loading NBA CSV data..."
  cd /app
  python sql/load_data_to_seasons.py 
  python sql/load_data_to_tams.py 
  python sql/load_data_to_team_aliases.py 
  python sql/load_data_to_players.py 
  python sql/load_data_to_player_name_aliases.py 
  python sql/load_data_to_games.py 
  python sql/load_data_to_team_game_stats.py 
  python sql/load_data_to_period_scores.py 
  python sql/load_data_to_player_season_stats.py 
  python sql/load_data_to_season_awards.py 
  python sql/load_data_to_draft_records.py 
  python sql/load_data_to_draft_combine_measurements.py 
  touch /var/lib/mysql/.nba_data_loaded
else
  echo "NBA data already loaded; skipping import."
fi

echo "Starting NBA Data Agent web service on 0.0.0.0:5000..."
cd /app
exec flask --app app.server run --host 0.0.0.0 --port 5000
