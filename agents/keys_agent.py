import os

KEYS = {
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
    "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
    "SUPABASE_SERVICE_KEY": os.environ.get("SUPABASE_SERVICE_KEY", ""),
    "PAYPAL_CLIENT_ID": os.environ.get("PAYPAL_CLIENT_ID", ""),
    "PAYPAL_CLIENT_SECRET": os.environ.get("PAYPAL_CLIENT_SECRET", ""),
    "SESSION_SECRET_KEY": os.environ.get("SESSION_SECRET_KEY", ""),
    "GMAIL_APP_PASSWORD": os.environ.get("GMAIL_APP_PASSWORD", ""),
    "ADMIN_KEY": os.environ.get("ADMIN_KEY", ""),
    "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD", ""),
    "GOOGLE_ADS_DEVELOPER_TOKEN": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
    "GOOGLE_ADS_SERVICE_ACCOUNT_JSON": os.environ.get("GOOGLE_ADS_SERVICE_ACCOUNT_JSON", ""),
    "GOOGLE_OAUTH_CLIENT_ID": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
    "GOOGLE_OAUTH_CLIENT_SECRET": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
    "META_APP_ID": os.environ.get("META_APP_ID", ""),
    "META_APP_SECRET": os.environ.get("META_APP_SECRET", ""),
}

def get_key(name):
    key = KEYS.get(name) or os.environ.get(name, "")
    if not key:
        raise ValueError(f"Missing key: {name}")
    return key

def inject_all_keys():
    for name, value in KEYS.items():
        if value:
            os.environ[name] = value

def validate_keys():
    missing = [k for k, v in KEYS.items() if not v]
    if missing:
        print(f"⚠️ Missing keys: {missing}")
        return False
    print("✅ All keys valid")
    return True

inject_all_keys()
