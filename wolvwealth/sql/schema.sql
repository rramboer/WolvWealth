PRAGMA foreign_keys = ON;

CREATE TABLE users (
  username VARCHAR(20) NOT NULL,
  fullname VARCHAR(40) NOT NULL,
  email VARCHAR(40) NOT NULL,
  filename VARCHAR(64) NOT NULL,
  password VARCHAR(256) NOT NULL,
  created DATETIME default current_timestamp,
  PRIMARY KEY (username)
);

CREATE TABLE optimizations (
  optimize_id INT NOT NULL AUTO_INCREMENT
  username VARCHAR(20) NOT NULL,
  portfolio_id INT NOT NULL,
  portfolio_name VARCHAR(20) NOT NULL,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(owner) REFERENCES users(username) ON DELETE CASCADE,
  FOREIGN KEY(portfolio) REFERENCES portfolio(portfolio_id) ON DELETE CASCADE,
  PRIMARY KEY(optimize_id)
);

CREATE TABLE portfolio (
  portfolio_id INT NOT NULL AUTO_INCREMENT,
  investments VARCHAR(2048) NOT NULL,
  FOREIGN KEY(owner) REFERENCES users(username) ON DELETE CASCADE,
  PRIMARY KEY(portfolio_id)
)
