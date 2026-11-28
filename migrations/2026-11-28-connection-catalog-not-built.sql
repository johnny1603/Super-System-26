-- 2026-11-28: three rows promised a connection that has no code behind it.
-- Merchant Center was retired on 2026-10-21; Creatomate and Uppbeat never had an integration.
-- The code already refuses to offer them whatever this table says (core/connection_catalog.NOT_BUILT);
-- this makes the table say the same thing. Safe to run more than once.
update connection_catalog
   set enabled = false, signup_url = '', pricing_url = '', updated_at = now()
 where key in ('merchant_center', 'creatomate', 'uppbeat');