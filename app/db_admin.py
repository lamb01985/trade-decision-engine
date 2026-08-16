"""One-off production database bootstrap commands; never run at app startup."""
from __future__ import annotations

import os

import psycopg
from psycopg import sql


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> None:
    host = required("DB_HOST")
    database = os.getenv("DB_NAME", "trade_decision_engine")
    admin_user = required("DB_ADMIN_USERNAME")
    admin_password = required("DB_ADMIN_PASSWORD")
    migrator_password = required("DB_MIGRATOR_PASSWORD")
    app_password = required("DB_APP_PASSWORD")
    ca_path = os.getenv("DB_SSL_ROOT_CERT", "/app/certs/aws-rds-global-bundle.pem")

    with psycopg.connect(host=host, port=5432, dbname=database, user=admin_user,
                         password=admin_password, sslmode="verify-full", sslrootcert=ca_path,
                         connect_timeout=10, autocommit=True) as connection:
        with connection.cursor() as cursor:
            for role, password in (("tde_migrator", migrator_password), ("tde_app", app_password)):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                statement = sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}") if cursor.fetchone() else \
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}")
                cursor.execute(statement.format(sql.Identifier(role), sql.Literal(password)))

            cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO tde_migrator, tde_app")
                           .format(sql.Identifier(database)))
            cursor.execute("GRANT USAGE, CREATE ON SCHEMA public TO tde_migrator")
            cursor.execute("GRANT USAGE ON SCHEMA public TO tde_app")
            cursor.execute("ALTER SCHEMA public OWNER TO tde_migrator")
    # PostgreSQL only permits a role (or one of its members) to alter that role's
    # default privileges. Run this as the schema-owning migrator, not as RDS master.
    with psycopg.connect(host=host, port=5432, dbname=database, user="tde_migrator",
                         password=migrator_password, sslmode="verify-full", sslrootcert=ca_path,
                         connect_timeout=10, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                           "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tde_app")
            cursor.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                           "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO tde_app")
    print("Database roles and least-privilege grants are ready.")


if __name__ == "__main__":
    main()
