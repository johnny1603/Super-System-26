-- Connections onboarding through the chat (core/connection_catalog.py, 2026-11-01).
--
-- One row per connection. Agents read sign-up and plan links ONLY from here;
-- the owner edits them on /admin/connections/ without a deploy.
-- tier: included (nothing for the client to open — shows nothing),
--       free_account (the client opens a free account),
--       paid_account (the client opens a paid account and pays the vendor directly).
-- Every URL below was opened live on the vendor's own site on 2026-11-01.
-- A row whose link could not be verified has it empty and enabled=false: the
-- chat then hands the client to a person instead of a link.
--
-- Idempotent: the table is created if missing, seeds never overwrite an edit.

create table if not exists connection_catalog (
  key                   text primary key,
  tier                  text not null check (tier in ('included', 'free_account', 'paid_account')),
  signup_url            text not null default '',
  pricing_url           text not null default '',
  recommended_plan_note text not null default '',
  help_notes            text not null default '',
  enabled               boolean not null default true,
  updated_at            timestamptz not null default now()
);

insert into connection_catalog (key, tier, signup_url, pricing_url, recommended_plan_note, help_notes, enabled) values
  ('ahrefs',           'included', '', '', '', '', true),
  ('ai_engine',        'included', '', '', '', '', true),
  ('google_places',    'included', '', '', '', '', true),
  ('outreach_sending', 'included', '', '', '', '', true),
  ('landing_pages',    'included', '', '', '', '', true),

  ('google_ads', 'free_account', 'https://ads.google.com/home/', '', '',
   'נכנסים עם חשבון הגוגל של העסק ופותחים חשבון פרסום. אם המסך מבקש ליצור קמפיין ראשון, אפשר לעצור שם — את הקמפיינים נבנה אנחנו.', true),
  ('youtube', 'free_account', 'https://www.youtube.com/create_channel', '', '',
   'נכנסים עם חשבון הגוגל של העסק, בוחרים שם לערוץ ולוחצים ליצירה.', true),
  ('tag_manager', 'free_account', 'https://tagmanager.google.com/', '', '',
   'נכנסים עם חשבון הגוגל של העסק, יוצרים חשבון חדש עם שם העסק, ובמיכל בוחרים Web וכותבים את כתובת האתר.', true),
  ('google_drive', 'free_account', 'https://accounts.google.com/signup', '', '',
   'כל חשבון גוגל כולל Drive. פותחים חשבון גוגל לעסק, וזהו.', true),
  ('gmail', 'free_account', 'https://accounts.google.com/signup', '', '',
   'פותחים חשבון גוגל לעסק — כתובת ה-Gmail נוצרת איתו.', true),
  ('search_console', 'free_account', 'https://search.google.com/search-console/about', '', '',
   'נכנסים עם חשבון הגוגל של העסק, מוסיפים את כתובת האתר ומאמתים לפי ההוראות במסך.', true),
  ('analytics', 'free_account', 'https://analytics.google.com/analytics/web/?authuser=0#/provision/SignUp', '', '',
   'נכנסים עם חשבון הגוגל של העסק, יוצרים חשבון ונכס עם שם העסק ובוחרים Web.', true),
  ('merchant_center', 'free_account', 'https://www.google.com/retail/solutions/merchant-center/', '', '',
   'נכנסים עם חשבון הגוגל של העסק וממלאים את פרטי העסק והחנות.', true),
  ('tiktok', 'free_account', 'https://www.tiktok.com/signup', '', '',
   'נרשמים עם מייל או טלפון של העסק, ובהגדרות עוברים לחשבון עסקי.', true),
  ('meta', 'free_account', '', '', '', '', false),

  ('manychat', 'paid_account', 'https://manychat.com/signup', 'https://manychat.com/pricing',
   'מתחילים במסלול החינמי. נמליץ לשדרג רק אם האוטומציה שנבנה יחד תצטרך את זה.',
   'נרשמים עם חשבון הפייסבוק או האינסטגרם של העסק ומחברים את העמוד.', true),
  ('higgsfield', 'paid_account', '', 'https://higgsfield.ai/pricing',
   'Starter — מספיק כדי להתחיל.',
   'פותחים חשבון, בוחרים מסלול, ובעמוד מפתחות ה-API יוצרים מפתח ומעתיקים אותו.', false),
  ('elevenlabs', 'paid_account', 'https://elevenlabs.io/app/sign-up', 'https://elevenlabs.io/pricing',
   'Starter — המסלול הזול ביותר שכולל שיבוט קול (Instant Voice Cloning).',
   'נרשמים, בוחרים מסלול, ובתפריט החשבון נכנסים ל-API Keys, יוצרים מפתח ומעתיקים אותו מיד.', true),
  ('creatomate', 'paid_account', 'https://creatomate.com/sign-in', 'https://creatomate.com/pricing',
   'Essential — ומתחילים בתקופת ניסיון חינמית, בלי כרטיס אשראי.',
   'נרשמים עם מייל או חשבון גוגל, ובהגדרות הפרויקט מעתיקים את מפתח ה-API.', true),
  ('uppbeat', 'paid_account', '', 'https://uppbeat.io/pricing',
   'Pro — המסלול שמכסה שימוש מסחרי.',
   'נרשמים, בוחרים מסלול ומתחברים לחשבון.', false),
  ('website', 'paid_account', '', '',
   'אין חשבון לפתוח: האחסון כלול בתשלום החודשי שלכם אלינו.',
   'אנחנו מקימים, מארחים ומתחזקים את האתר בשבילכם.', true)
on conflict (key) do nothing;
