-- WolvWealth database schema.
--
-- All DATETIME values are stored as UTC strings ("YYYY-MM-DD HH:MM:SS") and
-- compared lexically against sqlite's datetime('now'). Convert to local time
-- only for display.
PRAGMA foreign_keys = ON;

CREATE TABLE users(
  username VARCHAR(20) NOT NULL,
  password VARCHAR(256) NOT NULL,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  email VARCHAR(256) NOT NULL,
  PRIMARY KEY(username)
);

CREATE TABLE tokens(
    owner VARCHAR(20) NOT NULL,
    token VARCHAR(32) NOT NULL,
    expires DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uses INT NOT NULL,
    PRIMARY KEY(token),
    FOREIGN KEY(owner) REFERENCES users(username) ON DELETE CASCADE
);

CREATE TABLE admins(
    username VARCHAR(20) NOT NULL,
    PRIMARY KEY(username),
    FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
);

CREATE INDEX idx_tokens_owner ON tokens(owner);
CREATE INDEX idx_users_email ON users(email);
