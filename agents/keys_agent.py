import os

KEYS = {
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
    "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
    "SUPABASE_SERVICE_KEY": os.environ.get("SUPABASE_SERVICE_KEY", ""),
    "PAYPAL_CLIENT_ID": os.environ.get("PAYPAL_CLIENT_ID", ""),
    "PAYPAL_CLIENT_SECRET": os.environ.get("PAYPAL_CLIENT_SECRET", ""),
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
