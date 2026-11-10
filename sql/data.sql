PRAGMA foreign_keys = ON;

-- No seed accounts. Register at /register/ (registering also issues an API
-- key, shown on /account/), then make that account an admin with:
--   sqlite3 var/wolvwealth.sqlite3 "INSERT INTO admins(username) VALUES ('your-username');"
