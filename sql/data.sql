PRAGMA foreign_keys = ON;

INSERT INTO users(username, password) VALUES 
('rramboer', '$2b$12$wiiEvz7U.ovEUCY88vLzyeYnBVQkh6rSz0fc5DR5Hxk2VtJ/ub1EC'),
('cvano', '$2b$12$Gkob/WzSEZJMeb5an3ZN5uT9P90np/88vnQwJDM.Lc8mx.2N6jiGm'),
('nvano', '$2b$12$sEodwM/WSt3oLojlcSYM..pVvAb0.wTtlubmzcNDF6eQCcsJLP8De'),
('amshand', '$2b$12$s6QBOGdTBSLonqkqj.I6juFKT8IQK06w3E1rLQi829A/5/2k8bUP2');

INSERT INTO tokens(owner, token, expires, uses) VALUES
('rramboer', 'qD7jUXoAwEUJzMwRIGJBLdfZ5b_0-2-K-MPPBQfAW8w', datetime('now', '+100 years'), 1000000000),
('cvano', '0lj8CCK4NaGqqantoIaaXi7Qsh9C_lR2Ckc7YrVT4ns', datetime('now', '+100 years'), 1000000000),
('nvano', 'al4yxPX6cjckJAtGvK1zNlaF9ToVszXpizrhRjghWOM', datetime('now', '+100 years'), 1000000000),
('amshand', 'oLg6DDoF3SiaK1LIn8kmsP5RTWffhEC9RNk4zBHrMPY', datetime('now', '+100 years'), 1000000000);

INSERT INTO admins(username) VALUES
('rramboer'),
('cvano'),
('nvano'),
('amshand');