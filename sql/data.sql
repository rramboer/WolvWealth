PRAGMA foreign_keys = ON;

-- Example workspace so a fresh install has something to click on.
-- Safe to delete from the Workspaces page.
INSERT INTO workspaces(name) VALUES ('demo');

INSERT INTO portfolios(workspace, name, cash) VALUES ('demo', 'starter', 10000.0);

INSERT INTO holdings(portfolio_id, ticker, shares) VALUES
((SELECT id FROM portfolios WHERE workspace = 'demo' AND name = 'starter'), 'AAPL', 10.0),
((SELECT id FROM portfolios WHERE workspace = 'demo' AND name = 'starter'), 'MSFT', 5.0);
