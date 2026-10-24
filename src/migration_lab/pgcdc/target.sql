CREATE SCHEMA inbox;
CREATE SCHEMA modern;
CREATE SCHEMA target_control;
CREATE TABLE inbox.changes(seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 apply_xid text NOT NULL, table_name text NOT NULL, operation text NOT NULL,
 old_row jsonb, new_row jsonb, native_origin boolean NOT NULL);
CREATE INDEX inbox_transaction ON inbox.changes(apply_xid,seq);
CREATE FUNCTION inbox.capture() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT pg_replication_origin_session_is_setup() THEN RAISE EXCEPTION 'native_replication_origin_required'; END IF;
 INSERT INTO inbox.changes(apply_xid,table_name,operation,old_row,new_row,native_origin)
 VALUES(pg_current_xact_id()::text,TG_TABLE_NAME,TG_OP,
 CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END,
 CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END,true);
 RETURN NULL;
END$$;
CREATE TRIGGER capture_org AFTER INSERT OR UPDATE OR DELETE ON legacy.organizations FOR EACH ROW EXECUTE FUNCTION inbox.capture();
CREATE TRIGGER capture_person AFTER INSERT OR UPDATE OR DELETE ON legacy.people FOR EACH ROW EXECUTE FUNCTION inbox.capture();
CREATE TRIGGER capture_barrier AFTER INSERT ON legacy.barriers FOR EACH ROW EXECUTE FUNCTION inbox.capture();
ALTER TABLE legacy.organizations ENABLE REPLICA TRIGGER capture_org;
ALTER TABLE legacy.people ENABLE REPLICA TRIGGER capture_person;
ALTER TABLE legacy.barriers ENABLE REPLICA TRIGGER capture_barrier;
CREATE TABLE modern.companies(company_key text PRIMARY KEY,legal_name text NOT NULL);
CREATE TABLE modern.contacts(contact_key text PRIMARY KEY,
 company_key text NOT NULL REFERENCES modern.companies DEFERRABLE INITIALLY DEFERRED,
 display_name text NOT NULL, lifecycle text NOT NULL CHECK(lifecycle IN ('P','C','H','A')),
 engagement_score integer NOT NULL CHECK(engagement_score BETWEEN 0 AND 100),
 channels_json jsonb NOT NULL CHECK(jsonb_typeof(channels_json)='array'),
 subscription_state text NOT NULL CHECK(subscription_state IN ('unknown','yes','no')),
 preferred_locale text, schema_version integer NOT NULL CHECK(schema_version IN (1,2)));
CREATE TABLE target_control.state(singleton boolean PRIMARY KEY CHECK(singleton),
 binding jsonb NOT NULL, cursor bigint NOT NULL DEFAULT 0, chain text NOT NULL,
 barrier uuid, native_floor pg_lsn NOT NULL DEFAULT '0/0', mapping integer NOT NULL CHECK(mapping IN (1,2)), phase text NOT NULL DEFAULT 'catchup');
CREATE TABLE target_control.receipts(apply_xid text PRIMARY KEY,first_seq bigint NOT NULL,
 last_seq bigint NOT NULL UNIQUE,input_hash text NOT NULL,previous_chain text NOT NULL,chain text NOT NULL,
 row_count integer NOT NULL, result jsonb NOT NULL);
CREATE TABLE target_control.reports(seal text PRIMARY KEY,body jsonb NOT NULL);
CREATE FUNCTION target_control.immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable_native_record'; END$$;
CREATE TRIGGER immutable_inbox BEFORE UPDATE OR DELETE ON inbox.changes FOR EACH ROW EXECUTE FUNCTION target_control.immutable();
CREATE TRIGGER immutable_receipt BEFORE UPDATE OR DELETE ON target_control.receipts FOR EACH ROW EXECUTE FUNCTION target_control.immutable();
CREATE TRIGGER immutable_report BEFORE UPDATE OR DELETE ON target_control.reports FOR EACH ROW EXECUTE FUNCTION target_control.immutable();
CREATE FUNCTION target_control.native_status() RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('subscription_oid',s.oid,'enabled',s.subenabled,'slot',s.subslotname,
 'publications',s.subpublications,'streaming',s.substream,
 'synchronous_commit',s.subsynccommit,'two_phase',s.subtwophasestate,
 'relations_ready',(SELECT count(*)=3 AND bool_and(r.srsubstate='r') FROM pg_subscription_rel r WHERE r.srsubid=s.oid),
 'remote_lsn',coalesce((SELECT remote_lsn::text FROM pg_replication_origin_status WHERE external_id='pg_'||s.oid),'0/0'))
 FROM pg_subscription s WHERE s.subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())
 AND s.subname='migration_sub'
$$;
