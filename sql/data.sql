PRAGMA foreign_keys = ON;

INSERT INTO users(username, password, email, created) VALUES 
('rramboer', '$2b$12$wiiEvz7U.ovEUCY88vLzyeYnBVQkh6rSz0fc5DR5Hxk2VtJ/ub1EC', 'ryanramboer@gmail.com', '2023-11-19 00:00:00'),
('cvano', '$2b$12$Gkob/WzSEZJMeb5an3ZN5uT9P90np/88vnQwJDM.Lc8mx.2N6jiGm', 'cvano@umich.edu', '2023-11-19 00:00:00'),
('nvano', '$2b$12$sEodwM/WSt3oLojlcSYM..pVvAb0.wTtlubmzcNDF6eQCcsJLP8De', 'nvano@umich.edu', '2023-11-19 00:00:00');

INSERT INTO tokens(owner, token, expires, uses) VALUES
('rramboer', 'sXcu2A_Sic09hbBqmRsWKQ', '2123-11-19 00:00:00', 1000000000),
('cvano', '_P7ZI-CiSeLd_xY38R_KLQ', '2123-11-19 00:00:00', 1000000000),
('nvano', 'YNMjoPg6FP7FIWNRjmnylA', '2123-11-19 00:00:00', 1000000000);

INSERT INTO admins(username) VALUES
('rramboer'),
('cvano'),
('nvano');