CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE memberships (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id TEXT NOT NULL,
    PRIMARY KEY (user_id, company_id)
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE TABLE rate_limits (
    key TEXT PRIMARY KEY,
    hits INTEGER NOT NULL,
    resets_at INTEGER NOT NULL
);
