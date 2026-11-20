-- Development seed data ONLY. Do not load into a production database.
--
-- Credentials below are intentionally fake and publicly known:
--   devadmin / wolvwealth-dev  (admin, API token: insecure-dev-admin-token)
--   devuser  / wolvwealth-dev  (       API token: insecure-dev-user-token)
PRAGMA foreign_keys = ON;

INSERT INTO users(username, password, email, created) VALUES
('devadmin', '$2b$12$YigdS.IUhTQzUm/pxNjfoOpQ3m8rNKFm0xO3R2Ml0GG/DEefL/33C', 'devadmin@example.com', '2023-11-19 00:00:00'),
('devuser', '$2b$12$YigdS.IUhTQzUm/pxNjfoOpQ3m8rNKFm0xO3R2Ml0GG/DEefL/33C', 'devuser@example.com', '2023-11-19 00:00:00');

INSERT INTO tokens(owner, token, expires, uses) VALUES
('devadmin', 'insecure-dev-admin-token', '2030-01-01 00:00:00', 1000000),
('devuser', 'insecure-dev-user-token', '2030-01-01 00:00:00', 1000000);

INSERT INTO admins(username) VALUES
('devadmin');
