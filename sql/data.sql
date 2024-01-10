PRAGMA foreign_keys = ON;

INSERT INTO users(username, password, email, created) VALUES 
('rramboer', '$2b$12$wiiEvz7U.ovEUCY88vLzyeYnBVQkh6rSz0fc5DR5Hxk2VtJ/ub1EC', 'ryanramboer@gmail.com', '2023-11-19 00:00:00'),
('cvano', '$2b$12$Gkob/WzSEZJMeb5an3ZN5uT9P90np/88vnQwJDM.Lc8mx.2N6jiGm', 'cvano@umich.edu', '2023-11-19 00:00:00'),
('nvano', '$2b$12$sEodwM/WSt3oLojlcSYM..pVvAb0.wTtlubmzcNDF6eQCcsJLP8De', 'nvano@umich.edu', '2023-11-19 00:00:00'),
('amshand', '$2b$12$s6QBOGdTBSLonqkqj.I6juFKT8IQK06w3E1rLQi829A/5/2k8bUP2', 'amshand@umich.edu', '2023-11-19 00:00:00');

INSERT INTO tokens(owner, token, expires, uses) VALUES
('rramboer', 'qD7jUXoAwEUJzMwRIGJBLdfZ5b_0-2-K-MPPBQfAW8w', '2123-11-19 00:00:00', 1000000000),
('cvano', '0lj8CCK4NaGqqantoIaaXi7Qsh9C_lR2Ckc7YrVT4ns', '2123-11-19 00:00:00', 1000000000),
('nvano', 'al4yxPX6cjckJAtGvK1zNlaF9ToVszXpizrhRjghWOM', '2123-11-19 00:00:00', 1000000000),
('amshand', 'oLg6DDoF3SiaK1LIn8kmsP5RTWffhEC9RNk4zBHrMPY', '2123-11-19 00:00:00', 1000000000);

INSERT INTO admins(username) VALUES
('rramboer'),
('cvano'),
('nvano'),
('amshand');