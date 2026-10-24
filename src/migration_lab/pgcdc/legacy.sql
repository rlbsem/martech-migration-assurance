CREATE SCHEMA legacy;
CREATE TABLE legacy.organizations(org_id text PRIMARY KEY, display_name text NOT NULL);
CREATE TABLE legacy.people(
 person_id text PRIMARY KEY, org_id text NOT NULL REFERENCES legacy.organizations DEFERRABLE INITIALLY DEFERRED,
 full_name text NOT NULL, stage text NOT NULL CHECK(stage IN ('Lead','Customer','Hold')),
 lead_score integer NOT NULL CHECK(lead_score BETWEEN 0 AND 100), channels text NOT NULL,
 subscribed integer CHECK(subscribed IN (0,1)), locale text, schema_version integer NOT NULL CHECK(schema_version>0)
);
CREATE TABLE legacy.barriers(id uuid PRIMARY KEY, epoch bigint NOT NULL CHECK(epoch>=0),source_xid text NOT NULL);
