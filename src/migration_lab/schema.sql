CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests(command_id TEXT PRIMARY KEY, command_hash TEXT NOT NULL, seq INTEGER UNIQUE NOT NULL,
    event_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY, command_id TEXT UNIQUE NOT NULL,
    command_hash TEXT NOT NULL, payload TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transfer_attempts(id INTEGER PRIMARY KEY, at REAL NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_org(org_id TEXT PRIMARY KEY, display_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_person(person_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES legacy_org(org_id) DEFERRABLE INITIALLY DEFERRED,
    full_name TEXT NOT NULL, stage TEXT NOT NULL CHECK(stage IN ('Lead','Customer','Hold')),
    lead_score INTEGER NOT NULL CHECK(lead_score BETWEEN 0 AND 100), channels TEXT NOT NULL,
    subscribed INTEGER CHECK(subscribed IN (0,1)), locale TEXT, schema_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS modern_company(company_key TEXT PRIMARY KEY, legal_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS modern_contact(contact_key TEXT PRIMARY KEY,
    company_key TEXT NOT NULL REFERENCES modern_company(company_key) DEFERRABLE INITIALLY DEFERRED,
    display_name TEXT NOT NULL, lifecycle TEXT NOT NULL CHECK(lifecycle IN ('P','C','H','A')),
    engagement_score INTEGER NOT NULL CHECK(engagement_score BETWEEN 0 AND 100), channels_json TEXT NOT NULL,
    subscription_state TEXT NOT NULL CHECK(subscription_state IN ('yes','no','unknown')),
    preferred_locale TEXT, schema_version INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS changes_no_update BEFORE UPDATE ON changes BEGIN SELECT RAISE(ABORT,'immutable change log'); END;
CREATE TRIGGER IF NOT EXISTS changes_no_delete BEFORE DELETE ON changes BEGIN SELECT RAISE(ABORT,'immutable change log'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_update BEFORE UPDATE ON requests BEGIN SELECT RAISE(ABORT,'immutable request ledger'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_delete BEFORE DELETE ON requests BEGIN SELECT RAISE(ABORT,'immutable request ledger'); END;
