#!/bin/bash
echo "=== Super System Setup ==="
echo "Step 1: Installing Python dependencies..."
pip install anthropic langgraph fastapi uvicorn httpx python-dotenv supabase --quiet
echo "Step 2: Setting up project config..."
cat > config/settings.py << 'PYEOF'
PROJECT_ID = "super-system-500410"
PROJECT_NAME = "Super System"
VERSION = "0.1.0"
PYEOF
echo "Step 3: Creating Master Agent skeleton..."
cat > agents/master_agent.py << 'PYEOF'
from anthropic import Anthropic
client = Anthropic()
print("Master Agent ready")
PYEOF
echo "=== Setup Complete ==="
