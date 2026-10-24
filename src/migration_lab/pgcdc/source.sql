CREATE SCHEMA source_control;
CREATE TABLE source_control.gate(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 run_id text NOT NULL, fenced boolean NOT NULL DEFAULT false, epoch bigint NOT NULL DEFAULT 0);
CREATE FUNCTION source_control.guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE blocked boolean;
BEGIN
 SELECT fenced INTO STRICT blocked FROM source_control.gate WHERE singleton FOR SHARE;
 IF blocked THEN RAISE EXCEPTION 'source_write_fenced'; END IF;
 RETURN NULL;
END$$;
CREATE FUNCTION source_control.immutable_key() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='organizations' THEN
  IF NEW.org_id<>OLD.org_id THEN RAISE EXCEPTION 'source_identity_immutable'; END IF;
 ELSIF TG_TABLE_NAME='people' THEN
  IF NEW.person_id<>OLD.person_id THEN RAISE EXCEPTION 'source_identity_immutable'; END IF;
 END IF;
 RETURN NEW;
END$$;
CREATE FUNCTION source_control.no_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'truncate_not_supported'; END$$;
CREATE FUNCTION source_control.identity() RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('system_id',(pg_control_system()).system_identifier::text,
 'database',current_database(),'database_oid',(SELECT oid FROM pg_database WHERE datname=current_database()),
 'run_id',(SELECT run_id FROM source_control.gate WHERE singleton))
$$;
CREATE TRIGGER fence_org BEFORE INSERT OR UPDATE OR DELETE ON legacy.organizations
 FOR EACH STATEMENT EXECUTE FUNCTION source_control.guard();
CREATE TRIGGER fence_person BEFORE INSERT OR UPDATE OR DELETE ON legacy.people
 FOR EACH STATEMENT EXECUTE FUNCTION source_control.guard();
CREATE TRIGGER key_org BEFORE UPDATE ON legacy.organizations FOR EACH ROW EXECUTE FUNCTION source_control.immutable_key();
CREATE TRIGGER key_person BEFORE UPDATE ON legacy.people FOR EACH ROW EXECUTE FUNCTION source_control.immutable_key();
CREATE TRIGGER truncate_org BEFORE TRUNCATE ON legacy.organizations FOR EACH STATEMENT EXECUTE FUNCTION source_control.no_truncate();
CREATE TRIGGER truncate_person BEFORE TRUNCATE ON legacy.people FOR EACH STATEMENT EXECUTE FUNCTION source_control.no_truncate();

CREATE FUNCTION source_control.gate_status() RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('fenced',fenced,'epoch',epoch) FROM source_control.gate WHERE singleton
$$;
