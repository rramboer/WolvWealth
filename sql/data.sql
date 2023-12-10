PRAGMA foreign_keys = ON;

INSERT INTO users(username, password) VALUES 
('awdeorio', 'sha512$a45ffdcc71884853a2cba9e6bc55e812$c739cef1aec45c6e345c8463136dc1ae2fe19963106cf748baf87c7102937aa96928aa1db7fe1d8da6bd343428ff3167f4500c8a61095fb771957b4367868fb8'),
('cvano', 'sha512$a45ffdcc71884853a2cba9e6bc55e812$c739cef1aec45c6e345c8463136dc1ae2fe19963106cf748baf87c7102937aa96928aa1db7fe1d8da6bd343428ff3167f4500c8a61095fb771957b4367868fb8');

INSERT INTO tokens(owner, token, expires, uses) VALUES
('awdeorio', 'awdeorio_token', datetime('now', '-1 day'), 0),
('cvano', 'cvano_super_special_secret_token!', datetime('now', '+100 years'), 1000000000);