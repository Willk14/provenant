-- The API and worker connect as provenant_app, a role that is not the table owner, so row-level security
-- applies to them. Migrations run as the owner (provenant) and grant this role what it needs.
-- Local compose only: in a hosted database, create the role with a generated password.
CREATE ROLE provenant_app LOGIN PASSWORD 'provenant_app_dev';
