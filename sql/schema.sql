PRAGMA foreign_keys = ON;

-- A workspace is a person using this machine (e.g. "ryan", "my-brother").
-- No passwords: anyone at the keyboard can open any workspace.
CREATE TABLE workspaces(
  name VARCHAR(64) NOT NULL,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(name)
);

-- A portfolio belongs to one workspace (e.g. "long-term", "lottery-tickets").
CREATE TABLE portfolios(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace VARCHAR(64) NOT NULL,
  name VARCHAR(64) NOT NULL,
  cash REAL NOT NULL DEFAULT 0,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(workspace, name),
  FOREIGN KEY(workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

-- Current holdings of a portfolio: share counts per ticker.
CREATE TABLE holdings(
  portfolio_id INTEGER NOT NULL,
  ticker VARCHAR(16) NOT NULL,
  shares REAL NOT NULL,
  PRIMARY KEY(portfolio_id, ticker),
  FOREIGN KEY(portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
);
