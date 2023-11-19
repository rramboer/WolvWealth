PRAGMA foreign_keys = ON;

CREATE TABLE users (
  username VARCHAR(20) NOT NULL,
  fullname VARCHAR(40),
  email VARCHAR(40) NOT NULL,
  filename VARCHAR(64),
  password VARCHAR(256) NOT NULL,
  created DATETIME default current_timestamp,
  PRIMARY KEY (username)
);
CREATE TABLE optimizations (
  optimize_id INTEGER PRIMARY KEY AUTOINCREMENT,
  portfolio_id INT NOT NULL,
  portfolio_name VARCHAR(20) NOT NULL,
  results VARCHAR(2048) NOT NULL,
  username VARCHAR(20) NOT NULL,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE,
  FOREIGN KEY(portfolio_id) REFERENCES portfolios(portfolio_id) ON DELETE CASCADE
);
CREATE TABLE portfolios (
  portfolio_id INTEGER PRIMARY KEY AUTOINCREMENT,
  investments VARCHAR(2048) NOT NULL,
  owner VARCHAR(20) NOT NULL,
  FOREIGN KEY(owner) REFERENCES users(username) ON DELETE CASCADE
);
