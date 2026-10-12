-- Imitación mínima de Supabase para probar migraciones en un Postgres local.
-- Se corre como superusuario, una vez por base.
--
-- Lo que imita:
--   · roles anon / authenticated / service_role (este último con BYPASSRLS)
--   · ff_dueno: el `postgres` de Supabase. NO es superusuario. Es quien
--     corre el SQL editor y quien es dueño de las tablas.
--   · supabase_admin: superusuario con default privileges sobre public,
--     igual que en Supabase. pg_dump los incluye y ff_dueno no los puede
--     restaurar: esa es la trampa que la restauración tiene que esquivar.
--   · auth.uid() leído del JWT simulado, y auth.users.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='supabase_admin') THEN CREATE ROLE supabase_admin SUPERUSER NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ff_dueno') THEN
    CREATE ROLE ff_dueno LOGIN NOSUPERUSER CREATEROLE CREATEDB BYPASSRLS;
  END IF;
END $$;
GRANT anon, authenticated, service_role TO ff_dueno;

CREATE SCHEMA IF NOT EXISTS auth;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role, ff_dueno;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS UUID LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
GRANT EXECUTE ON FUNCTION auth.uid() TO anon, authenticated, service_role, ff_dueno;
CREATE TABLE IF NOT EXISTS auth.users (id UUID PRIMARY KEY, email TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
GRANT SELECT, INSERT ON auth.users TO ff_dueno;

ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public
  GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public
  GRANT ALL ON FUNCTIONS TO anon, authenticated, service_role;
