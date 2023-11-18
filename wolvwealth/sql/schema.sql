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
  username VARCHAR(20) NOT NULL,
  opt_id INT NOT NULL AUTO_INCREMENT
  email VARCHAR(40) NOT NULL,
  filename VARCHAR(64) NOT NULL,
  password VARCHAR(256) NOT NULL,
  created DATETIME default current_timestamp,
  PRIMARY KEY (username)
);

